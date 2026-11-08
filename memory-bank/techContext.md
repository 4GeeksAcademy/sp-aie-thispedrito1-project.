# Tech Context - HealthCore Digital

Stack, decisiones transversales y restricciones. El detalle técnico de cada módulo (reglas, gotchas, comandos) vive en el `CLAUDE.md` de su carpeta; el índice está en el `CLAUDE.md` de la raíz. Aquí no se repite.

## Stack tecnológico

**Frontend**
- Next.js 16.4.3 (App Router) + React 19 + TypeScript en `uis/backoffice`, `uis/website` y `apps/talent-pipeline-tracker`. Tailwind CSS v4. ESLint 9 solo en el tracker.
- Tests con Jest + ts-jest + jsdom, sin `@testing-library`.

**Backend** (`services/api`, venv Python 3.12)
- FastAPI 0.115.14 (Starlette 0.46.2). Dos bases: TinyDB (usuarios, perfiles, proveedores, incidencias, roles, federada) y Supabase/PostgreSQL vía SQLModel (todo lo demás).
- Auth: JWT HS256 con python-jose, bcrypt con passlib, PyJWT + cryptography para Microsoft Entra. Correo con Resend.
- Cola: Celery 5.6 + Redis 7.4 + Flower 2.0. Orquestación de datos: Prefect `>=3.4,<4` (API efímera, sin servidor).
- IA: SDK `openai` contra el proxy LiteLLM de 4Geeks; embeddings `pplx-embed-v1-0.6b` (1024 dim) y generación `deepseek-v4-flash`. Qdrant 1.16.3. Sin LangChain/LlamaIndex en el RAG.
- Agentes: `langgraph==0.6.11`, `langgraph-checkpoint==3.0.1`, `langgraph-checkpoint-postgres==3.0.5` (+ `psycopg[binary]` 3, solo para el checkpointer), `langgraph-checkpoint-sqlite==3.0.3`.
- MCP: SDK oficial `mcp` 1.30 (FastMCP), `mcpauth==0.2.0b1`, `langchain-mcp-adapters` 0.3.2; Logto Cloud como emisor OAuth.
- Documentos: `markitdown[pdf]==0.1.8`, `py-readability-metrics==1.4.5`, pdfplumber, fpdf2.
- ML (en `requirements-ml.txt`, fuera de la imagen de la API): scikit-learn 1.6.1, matplotlib 3.9.4.

**Infraestructura**
- Docker Compose para desarrollo; puertos solo en `127.0.0.1`; contenedores Python como `healthcore` (UID 1000). Scheduler con supercronic.
- Supabase plan gratuito, proyecto único `healthcore-data` (eu-central-1).

## Decisiones de arquitectura transversales

- **Monolito FastAPI**: todos los dominios cuelgan de `services/api`; los módulos de `services/*` y `data/pipelines/*` se montan como routers. El backoffice usa una sola URL (`NEXT_PUBLIC_API_URL` o `/api-proxy`).
- **Usuarios solo en TinyDB**; `user_id` sirve como `user_uuid` de referencia en Supabase. Rol y departamento se resuelven en cada petición.
- **Autorización centralizada** en `services/api/permissions.py` (rol, luego departamento). Los módulos agénticos usan además su propio directorio organizativo (`onboarding_staff`, `fc_staff`), nunca el admin de la plataforma.
- **Grafos sin acceso a la base de datos**: los grafos de LangGraph reciben y devuelven datos; persisten los runners. Lo anterior a un `interrupt()` es puro.
- **Reglas de negocio y de cumplimiento en código, no en prompts.** El modelo planifica y redacta; nunca calcula. Todo número que redacta debe existir en los hechos.
- **Minimización de datos**: nada de texto libre de usuarios en logs, traces ni auditoría (solo huellas SHA-256 o HMAC). PHI detectada por reglas deterministas (`phi_guard`), no por el modelo.
- **Auditoría append-only** con cadena de hashes (`services/audit/`) para acciones sensibles.
- **Idempotencia y locks por restricción de base de datos** (índices únicos parciales), no por consulta previa.
- **Tareas asíncronas** en Celery con ids, nunca con datos de negocio; reintentos acotados y DLQ.
- **Gestión de errores transversal**: mensajes centralizados en las capas HTTP de los frontends; handler global 500 en el backend; scripts con `stderr` y código de salida.
- **Tests sin red ni servicios reales**: TinyDB temporal, SQLite en memoria en lugar de Supabase, dobles de modelo; las pruebas reales se hacen a mano y quedan documentadas en `docs/`.

## Restricciones técnicas

- Estado en memoria de proceso (caché, rate limit, ventana de brechas, hub del chat, candados de TinyDB): correcto con un worker de uvicorn; con varios haría falta Redis.
- Proveedor LLM y Supabase gratuito sin BAA/DPA: solo datos sintéticos.
- Proxy LLM lento e inestable (5-20 s por llamada con deepseek; modelos bloqueados sin aviso).
- Versiones viejas conocidas con avisos de seguridad: LangGraph 0.6.11 (deserialización de checkpoints), Starlette 0.46.2, python-jose 3.3.0.
- Celery prefork no funciona en macOS (worker nativo con `--pool=solo`).
- Tracker de candidatos: depende de una API externa con payloads heterogéneos (`stage`/`step`), resuelto con normalización defensiva.

## Implicaciones para implementaciones futuras

- Nueva ruta protegida: `Depends(require_capability(...))` y, si el dato tiene departamento, `ensure_in_scope`/`department_scope`.
- Nueva acción sensible: registrarla con `services/audit/writer.record` dentro de la misma transacción.
- Nuevo listado de API: esquema propio derivado de lo que lee la pantalla; `test_serialization.py` vigila filtraciones.
- Nuevo identificador en textos salientes: sin series largas de dígitos (`phi_guard` lo tomaría por teléfono o NHS number).
- Nuevo color en el backoffice: token CSS, nunca hex en `.tsx`.
- Mantener la deuda de tipado en descenso (`any` → `unknown` + narrowing).
