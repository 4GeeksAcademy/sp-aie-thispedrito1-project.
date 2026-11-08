# CLAUDE.md

Monorepo del proyecto transversal del track AI Engineering de 4Geeks Academy para la empresa ficticia **HealthCore** (red sanitaria en EE. UU. y Reino Unido). Briefing de negocio en `CONTEXT.md`; arquitectura backend en `docs/ARCHITECTURE_PROPOSAL.md`. Se construye por hitos (una rama por clase, apiladas).

Dominio sanitario: cualquier feature que toque datos de pacientes/usuarios considera HIPAA y UK GDPR (minimización, trazabilidad, nada sensible en logs). Los pacientes del proyecto son sintéticos (`P-0001`, nunca un nombre en casos de prueba).

## Reglas obligatorias (AGENTS.md)

`AGENTS.md` es la fuente de verdad de gobernanza:

1. **Inicio de sesión**: leer `memory-bank/projectbrief.md`, `techContext.md` y `progress.md`.
2. **Rutas protegidas** (no tocar sin confirmación): `node_modules/`, `.git/`, `package-lock.json`, `apps/talent-pipeline-tracker/AGENTS.md`, credenciales/secretos.
3. **Antes de cada commit**: una sola feature, memory-bank actualizado si aplica, validación más estrecha posible, revisar el diff staged.
4. Reglas complementarias en `.agents/rules/development-guardrails.md` y skills en `.agents/rules/skills/`.

Next.js instalado es 16.x con breaking changes: leer la guía de `node_modules/next/dist/docs/` antes de escribir código en `uis/backoffice`, `uis/website` o `apps/talent-pipeline-tracker`.

## Índice de módulos

Cada módulo tiene su `CLAUDE.md` con comandos, mapa del código, reglas que no hay que romper y gotchas. Se carga solo al leer un archivo de esa carpeta: **si trabajas desde un test de `tests/` o un script de `scripts/`, lee antes el `CLAUDE.md` del módulo.**

| Módulo | CLAUDE.md | Tests | Diseño |
|---|---|---|---|
| Backend: auth, usuarios, roles, federada, incidencias, inventario, telemetría, caché, rate limit | `services/api/CLAUDE.md` | `services/api/tests/` | `docs/serialization-audit.md`, `docs/roles-permissions/`, `docs/federated-auth/`, `docs/telemetry/`, `CACHING_REPORT.md` |
| Agente LangGraph: tools, memoria, guardarraíles, chat WS | `services/agent/CLAUDE.md` | `tests/pipelines/test_agent_*.py` | `docs/agent/`, `docs/websocket-chat.md` |
| Servidor MCP | `mcps/healthcore/CLAUDE.md` | `services/api/tests/test_mcp_server.py` | `docs/mcp/mcp-server.md` |
| Pipeline de negocio, RAG, modelo de ventas | `data/CLAUDE.md` | `tests/pipelines/` | `data/pipelines/PIPELINE_DESIGN.md`, `docs/rag/`, `docs/sales-forecast.md` |
| RFPs (Partes 1-3 + SSE) | `data/pipelines/rfp_intake/CLAUDE.md` (+ `services/rfp_intake/CLAUDE.md`) | `tests/pipelines/test_rfp_*.py` | `docs/rfp-*.md`, `docs/sse-notifications.md` |
| Cola Celery | `services/tasks/CLAUDE.md` | `services/api/tests/test_async_tasks.py` | `docs/async-tasks.md` |
| Job nocturno | `services/job_runner/CLAUDE.md` | `tests/jobs/` | `docs/nightly-export.md` |
| Incorporaciones | `services/onboarding/CLAUDE.md` | `tests/onboarding/` | `docs/onboarding/` |
| Proveedores y contratos | `services/supplier_contracts/CLAUDE.md` | `tests/supplier_contracts/` | `docs/supplier-contracts/` |
| Cierre financiero | `services/financial_close/CLAUDE.md` | `tests/financial_close/` | `docs/financial-close/` |
| Registro de auditoría + pacientes | `services/audit/CLAUDE.md` | `services/api/tests/test_audit_log.py` | `docs/audit-log/` |
| Backoffice | `uis/backoffice/CLAUDE.md` | `uis/backoffice/__tests__/` | `AUDIT.md`, `REPORT.md` |
| Web corporativa | `uis/website/CLAUDE.md` | — | `AUDIT.md`, `REPORT.md` |
| Endurecimiento del servidor | `infra/CLAUDE.md` | `tests/security/` | `docs/security/` |

Otros: `apps/talent-pipeline-tracker` (tracker de candidatos contra API externa, único con ESLint; su `CLAUDE.md` importa `AGENTS.md`). `packages/shared/incidents_validation/` (reglas Python de incidencias que usan la API, `scripts/analyze.py` y `scripts/seed_incidents.py`: toda regla nueva de incidencias va aquí). `packages/shared/business-logic/` (TS del hito 2, importado por ruta relativa; editar el `.ts`, no los `.js`/`.d.ts` versionados). `index.html`, `application.html`, `validation.js`, `server.py` en la raíz y `uis/web/` son legado del hito 1. `skills/` y `workflows/` son plantillas del curso.

## Dónde documentar (para que esto no vuelva a crecer)

- Regla, comando o gotcha de un módulo → **su** `CLAUDE.md`, en la sección que toque.
- Diseño largo, mediciones, evidencias → `docs/`.
- Memory-bank: solo estado, decisiones transversales, riesgos y próximos pasos.
- Nunca: historial de ramas o fechas (git lo guarda), conteos de tests, cómo se descubrió un bug (solo la regla que dejó).

## Entorno y comandos comunes

- **Python**: un solo venv, `services/api/.venv` (Python 3.12). Los scripts y tests de la raíz se lanzan desde la raíz con `services/api/.venv/bin/python`. `uv` está en `~/.local/bin`.
- **pytest**: en `services/api`, `pyproject.toml` pone `addopts="-q"` y oculta el resumen; usar `-o addopts=""`. Plan de tests en `TESTING.md`.
- **Next.js**: en cada app `npm run dev` / `npm run build` / `npm run lint`. En la raíz, `npm run typecheck` falla porque también revisa `uis/**`.
- `python analyze.py services/incidents-healthcore.csv` analiza el CSV legado. `services/api/.venv/bin/python scripts/seed_incidents.py` siembra el histórico (idempotente; después `/api/incidents/summary` da 94 total, 28/52/14 por estado y 61/20/13 por categoría).

### Docker

```bash
cp .env.example .env && docker compose up --build    # api (8000) + uis (website 3000 + backoffice 3001)
```

Servicios que se levantan por nombre y sin `restart:`: `scheduler`, `redis`, `worker`, `flower`, `qdrant`. Todos los puertos en `127.0.0.1`. El backoffice llega a la API por `BACKEND_API_URL=http://api:8000`, nunca `localhost`. `api` monta `./services`, `./data`, `packages/` y `uis/web/` completos (el código importa fuera de su carpeta).

Gotchas: volúmenes anónimos para cada `node_modules`; `libpq5` en la imagen del backend y `libc6-compat` en la de `uis` (Alpine); `sh start.sh`, no `./start.sh`; dentro de Docker, webpack con polling (Turbopack entra en pánico sobre virtiofs) y borrar `.next` al cambiar de bundler; los Dockerfiles viven en `uis/`, `services/api/`, `services/worker/` y `services/scheduler/`.

## Gotchas transversales

- **Supabase**: un solo proyecto, `healthcore-data` (eu-central-1, ref `klaxalwsyrucwszvdkhu`). El otro hueco del plan gratuito es una app en producción del usuario: no tocarla. `database-auditory` (eu-west-1) está pausado; tablas viejas archivadas en el esquema `archive_hito5`. Usar siempre la cadena del **pooler** (`aws-0-<region>.pooler.supabase.com:6543`, usuario `postgres.<ref>`): la directa solo tiene IPv6. Se pausa tras ~7 días sin uso (`tenant/user … not found`, ni resuelve DNS): restaurarlo desde el dashboard.
- **Dos `.env` con `DATABASE_URL`**: el de la raíz (Docker) y `services/api/.env` (nativo). `load_dotenv` no sobrescribe, así que si divergen ganan los contenedores. Deben apuntar al mismo proyecto.
- **Proxy LLM de 4Geeks** (LiteLLM, `llm.4geeks.ai`): `GENERATION_MODEL` = `madrid-spain/openrouter/deepseek/deepseek-v4-flash` (lento, 5-20 s por llamada). Bloqueados o inestables: `gpt-5.6-luna` (403), `glm-5.3-flash`, `mimo-v2.5`. Un 401 "Invalid proxy server token" = clave caducada, se regenera en la plataforma. Si falla, proponer una alternativa rápido, sin investigar variantes.
- **Celery nativo en macOS: `--pool=solo`** (prefork muere antes de entrar en la tarea).
- **Tests de la raíz**: las carpetas de `tests/` no son paquetes. Nunca `from conftest import …` (con la batería completa llega el `conftest.py` de otra carpeta); ayudantes en archivos con nombre propio y tests con nombres únicos. Fixtures de SQLite con `create_all(tables=[...])`, nunca el metadata entero (falla con "unknown database reporting"). Incluir `AuditLogEntry.__table__` si se tocan procesos auditados.
- **Verificar a mano sin ensuciar datos**: API con `SUPPLIERS_DB_PATH` a una TinyDB temporal y token de `create_access_token` (cargando `services/api/.env`). La TinyDB versionada no tiene admin ni incidencias.
- **Docker Desktop** puede quedar a medias (`docker info` responde pero `docker ps` da 500 o se cuelga). Antes de reiniciarlo, comprobar que el runner de producción está libre (`pgrep -fl Runner.Worker` vacío). Contenedor con "RWLayer … unexpectedly nil": `docker compose rm -sf <servicio>` y recrearlo.
- **Disco limitado**: nada corriendo de forma permanente; parar servicios y limpiar imágenes tras verificar. Si el repo vuelve a estar en una carpeta de iCloud, los imports y `git status` se cuelgan mientras iCloud descarga archivos.
- **Claude in Chrome** solo sube archivos del scratchpad de la sesión.
- Los BRIEFINGs de cada clase están en el repo del currículo (`content/contexts/<clase>/`); el de Descargas suele ser el de otra clase.

## Convenciones

- **Errores, frontends**: los mensajes al usuario se generan en `uis/backoffice/services/http.ts` y `apps/talent-pipeline-tracker/services/api.ts`. Nunca códigos de estado, stack traces ni JSON crudo. Errores por campo con `ApiFieldError`. Toda carga async con tres estados, `finally` para el loading, y CTA en el error. Prohibido `console.log` con datos de usuarios.
- **Errores, backend**: `try/except` acotados, `HTTPException` con JSON limpio, handler global 500 genérico. Nada sensible en respuestas ni en logs (nunca emails; enmascarar con `_redact_emails`).
- **Scripts Python**: errores a `stderr` y salida ≠ 0 en fallo crítico.
- Documentación y mensajes en español; código e identificadores en inglés.
- Commits `feat(scope): …` / `chore(scope): …`, uno por feature, PR por rama `feature/*`.
- TypeScript: evitar `any` (migrar a `unknown` + narrowing).
