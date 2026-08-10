# Tech Context - HealthCore Digital

## Stack tecnologico
- Frontend principal en Next.js 16 (App Router) con React 19 y TypeScript.
- Estilos con Tailwind CSS v4 via PostCSS.
- Calidad de codigo con ESLint 9 + configuraciones de Next (core web vitals y TypeScript).
- Capa de consumo de datos via fetch HTTP hacia API externa configurada con NEXT_PUBLIC_API_URL.
- Repositorio con estructura multi-area: app web en apps/talent-pipeline-tracker y utilidades/tipos en raiz.
- Backend API en FastAPI con TinyDB para persistencia operativa local de proveedores, usuarios y perfiles.
- Seguridad backend con JWT stateless (python-jose), hash de contrasenas con passlib+bcrypt y dependencia OAuth2PasswordBearer.
- Envio de correo transaccional con Resend (SDK python) para el flujo de restablecimiento de contrasena (AUTH-03); API key y remitente por variables de entorno.

## Decisiones de arquitectura tomadas
- Separacion por capas en la app web:
  - UI/rutas en app y components.
  - Integracion API centralizada en services/api.ts.
  - Contratos de datos tipados en types/tracker.ts.
- Estrategia hibrida server/client en Next.js:
  - Rutas App Router para navegacion y estructura.
  - Componentes cliente para interactividad, filtros y estado local.
- Integracion API-first sin backend propio en este modulo:
  - CRUD de candidatos y notas depende de servicio remoto.
- Normalizacion defensiva de payloads:
  - Adaptacion de campos heterogeneos (por ejemplo stage/step, candidate_id/record_id).
  - Manejo explicito de errores 422 para diagnostico rapido.
- Resiliencia de interfaz:
  - Fallback de datos de respaldo en dashboard para no bloquear visualizacion en errores de red.

## Restricciones tecnicas
- Dependencia de API externa: disponibilidad y consistencia de datos condicionadas por servicio remoto.
- Cambios de ruptura en Next.js 16: es obligatorio validar convenciones y APIs vigentes antes de implementar features nuevas.
- Calidad de tipado mejorable: existen usos de any que deben migrarse a unknown + narrowing para robustez.
- Divergencia de modelo de datos entre respuestas de API: requiere mantener mapeadores y validaciones en cliente.
- Alcance actual enfocado en frontend: no hay capa de backend local para control de seguridad avanzada, auditoria o reintentos transaccionales.

## Implicaciones para implementaciones futuras
- Priorizar contrato canonico de Candidate y estandarizar campos de pipeline.
- Reducir deuda tecnica de tipado en componentes y servicios.
- Evaluar BFF o backend propio para control de seguridad, trazabilidad y cumplimiento.
- Mantener arquitectura por capas para escalar a nuevos modulos del dominio HealthCore.
- Mantener User/Profile exclusivamente en TinyDB y reutilizar user_id como user_uuid de referencia en otros modulos para evitar migraciones inconsistentes de auth.
- Single-use de tokens de restablecimiento via tabla TinyDB password_resets (registro de jti consumidos); tokens de reset son JWT firmados con claim type=reset, jti y expiracion corta (RESET_TOKEN_EXPIRE_MINUTES).
- Validacion compartida Python centralizada en packages/shared/incidents_validation (movida desde shared/incidents_analysis): csv_analysis.py para el CSV legacy y incident_rules.py para el modelo Incident; la consumen API, analizador y seed sin duplicacion.
- Gestor de incidencias: tabla TinyDB incidents con ciclo de vida open -> in_progress -> resolved/discarded; errores de validacion de la API como 400 con detail.errors[{field,message}] (sin Pydantic en entrada) y handler global de excepciones que devuelve 500 generico sin stack trace.
- Seed idempotente de incidencias historicas via campo interno source_id (incident_id del CSV), oculto en las respuestas de la API.
- Compatibilidad local: requirements.txt incluye eval-type-backport solo para Python < 3.10 (la maquina local usa 3.9; el proyecto se desarrollo con 3.12).
- Testing: pytest + httpx en services/api/tests (conftest con TinyDB temporal y secreto JWT de test seteados antes de importar la app; email de reset sustituido con monkeypatch) y Jest + ts-jest + jsdom en uis/backoffice/__tests__ (moduleFileExtensions prioriza .ts sobre los .js compilados de src/). Plan y resultados en TESTING.md; dev-deps declaradas en pyproject.toml ([dependency-groups] dev) para que uv run pytest funcione en Codespaces.
- Estrategia transversal de gestion de errores: mapeo central de errores a mensajes legibles en las capas HTTP de los frontends (http.ts y api.ts) con ApiFieldError para errores por campo (formato propio detail.errors y 422 de FastAPI); scripts con stderr y exit codes; sin console.log de datos de usuarios. El fallback de datos demo del dashboard del tracker se mantiene pero con aviso explicito y boton de reintento.
- Arquitectura de doble base de datos en services/api (Hito 5, gestor de inventario): TinyDB sigue siendo la unica fuente de usuarios/auth; los datos de negocio (MedicalSupply, SupplyDelivery, SupplyConsumption) viven en Supabase (PostgreSQL) via SQLModel, con engine cacheado y sesion inyectada por peticion (get_inventory_db, Depends, sin sesiones globales). current_stock nunca se almacena: se calcula en cada lectura sumando entregas y restando consumos (inventory_repository.get_current_stock). Los tests de este modulo no tocan Supabase: sobreescriben la dependencia con una SQLite en memoria (StaticPool) por test, mismo principio que la TinyDB temporal de los tests existentes.
- Leccion tecnica: dos dominios del mismo archivo models.py pueden compartir nombre de campo (country) con conjuntos de valores distintos (Country de proveedores es USA/UK; SupplyCountry de inventario es US/UK) — no reutilizar un enum existente sin verificar el contrato exacto del CONTEXT del hito correspondiente.
- Scripts standalone que necesiten DATABASE_URL deben poder cargar services/api/.env por si mismos: load_dotenv() vive ahora en database.py (ademas de security.py), porque un script que no importa security.py (como seed_inventory.py) no heredaba las variables de entorno del .env.
- uis/backoffice no necesita una variable de entorno nueva por cada dominio de API nueva: todo el backend (auth, suppliers, incidents, inventory) es un unico FastAPI monolitico en services/api, asi que services/http.ts (NEXT_PUBLIC_API_URL o el proxy /api-proxy de next.config.ts) ya sirve para cualquier modulo nuevo. El README de un hito puede sugerir una variable dedicada (ej. NEXT_PUBLIC_INVENTORY_API_URL) que no aplica a esta arquitectura real.
- Paginas cliente de uis/backoffice: evitar next/navigation useSearchParams para leer un query param opcional simple, porque obliga a envolver la pagina en <Suspense> en el App Router; leer window.location.search dentro de un useEffect logra lo mismo sin ese requisito ni perder renderizado estatico (usado en app/inventory/orders/{inbound,outbound}/page.tsx para el preselect ?supply_id=).
- Rediseno visual de uis/backoffice, tema "Supply Manifest console" (decidido entre dos propuestas pitcheadas como artifact HTML, ver mensaje al usuario del 2026-08-09): oscuro y denso por defecto — consola de operaciones, no dashboard de marketing —, con toggle real a modo claro. Todo vive como tokens CSS en app/globals.css (--bg, --panel, --text, --muted, --brand ambar #FF8A3D en oscuro / #C2570A en claro, --line, semanticos --critical/--warning/--ok, --success-*/--error-*, --led-glow que se anula a 0px en claro). Regla: nunca hardcodear un color hex en un .tsx del backoffice, siempre var(--token) — un grep de `#[0-9a-fA-F]{3,6}` en app/ y components/ debe volver vacio salvo los fallbacks `var(--x, #fallback)` (nunca se usan, el token siempre esta definido). Distincion deliberada de lenguaje visual: nivel de stock (severidad, cuanto) usa LED cuadrado + numero en negrita monoespaciada (clases .led/.led-critical/.led-warning/.led-ok, ver app/inventory/products/page.tsx); estado categorico (tipo de orden, estado de incidencia, active/suspended de proveedor) usa la pildora .status-badge con fondo semantico traslucido — son dos lenguajes visuales a proposito, no inconsistencia.
- Toggle claro/oscuro (components/ThemeToggle.tsx): atributo `data-theme` en `<html>` ("light" o ausente=oscuro), persistido en localStorage bajo la clave `healthcore.theme`. Para evitar parpadeo (flash del tema oscuro antes de aplicar el claro guardado) hay un `<script>` inline sincrono en el `<head>` de app/layout.tsx que lee localStorage y setea el atributo antes del primer paint — eso desalinea el HTML servido por SSR (sin atributo) del DOM real en el momento de hidratar, así que `<html>` lleva `suppressHydrationWarning` a proposito (si no, React tira un error de hydration mismatch en consola, inofensivo pero ruidoso).
- Traduccion completa del backoffice al espanol (decision explicita del usuario el 2026-08-09, incluyendo la seccion de Incidents que un CONTEXT anterior pedia en ingles para evaluacion — se aparta de esa nota a proposito, confirmado con el usuario). Los mensajes de error que vienen literalmente del backend (ej. "Insufficient stock for supply...") NO se traducen: son el contrato de texto exacto que exige el CONTEXT del hito de backend para el evaluador, y ademas el frontend hace pattern-matching sobre esas palabras en ingles (`.includes("stock")`) para decidir donde mostrar el error — solo se tradujeron las cadenas de UI escritas por el frontend.
- Docker (2026-08-10, ver seccion dedicada en CLAUDE.md): un contenedor dev con bind mount necesita replicar la misma profundidad relativa que el codigo ya asume en el host — si un modulo importa algo con `../../../algo` o hace `Path(__file__).resolve().parents[N]`, ese `algo` tiene que existir montado en la ruta equivalente dentro del contenedor, no solo la carpeta del propio servicio. `node_modules` necesita su propio volumen anonimo por app cuando se bind-montea la carpeta padre, o el mount tapa los paquetes instalados dentro de la imagen (Linux) con los del host. Turbopack no es fiable dentro de Docker Desktop en Mac (bind mounts via virtiofs le hacen entrar en panico en su grafo de tareas incremental) — usar `--webpack` explicitamente en contenedores, y aun con webpack activar `watchOptions.pollIntervalMs` (Next 16; el viejo env var WATCHPACK_POLLING ya no es el mecanismo documentado) porque los eventos de sistema de archivos tampoco se reenvian de forma fiable. Cambiar de bundler (Turbopack a webpack) sin borrar el `.next` viejo de esa app dejaba una cache en formato incompatible que rompia el hot reload sin ningun error visible en logs.