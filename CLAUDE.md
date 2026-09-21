# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es este proyecto

Monorepo del proyecto transversal del track AI Engineering de 4Geeks Academy para la empresa ficticia **HealthCore** (red sanitaria en EE.UU. y Reino Unido). El briefing de negocio está en `CONTEXT.md` y la propuesta de arquitectura backend en `docs/ARCHITECTURE_PROPOSAL.md`. Los entregables se construyen por hitos (web, programación, Next.js, backend, etc. — ver README.md) y el historial de git refleja esa progresión.

Dominio sanitario: cualquier feature que toque datos de pacientes/usuarios debe considerar HIPAA y UK GDPR (minimización de datos, trazabilidad, no exponer datos sensibles en logs).

## Reglas obligatorias (AGENTS.md)

`AGENTS.md` en la raíz es la fuente de verdad de gobernanza. Resumen operativo — leer el archivo completo ante cualquier duda:

1. **Inicio de sesión**: leer en orden `memory-bank/projectbrief.md`, `memory-bank/techContext.md` y `memory-bank/progress.md` antes de proponer o implementar cambios.
2. **Rutas protegidas** (no modificar sin confirmación explícita): `node_modules/`, `.git/`, `package-lock.json`, `apps/talent-pipeline-tracker/AGENTS.md`, y cualquier archivo de credenciales/secretos.
3. **Antes de cada commit**: alcance acotado a una sola feature; actualizar el memory-bank si el cambio afecta objetivos, arquitectura o estado; ejecutar la validación más estrecha posible; revisar el diff staged; un commit por feature.
4. Reglas complementarias en `.agents/rules/development-guardrails.md` y skills operativas en `.agents/rules/skills/` (memory-bank-sync y pre-commit-readiness).

`apps/talent-pipeline-tracker/AGENTS.md` advierte: la versión de Next.js instalada (16.x) tiene breaking changes respecto al conocimiento entrenado — leer la guía relevante en `node_modules/next/dist/docs/` antes de escribir código Next.js. Aplica también a `uis/backoffice` y `uis/website` (misma versión).

## Comandos

No hay workspace runner en la raíz: cada app tiene su propio `package.json` y se trabaja con `cd` a su carpeta.

### Backend FastAPI (`services/api`)

```bash
cd services/api
python3 -m venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env      # obligatorio: JWT_SECRET_KEY real + DATABASE_URL de Supabase
python seed.py             # datos iniciales (suppliers, TinyDB)
python seed_inventory.py   # datos iniciales de inventario (Supabase)
uvicorn main:app --reload --port 8000
```

- Swagger en `http://localhost:8000/docs`; flujo manual de verificación de auth documentado en `services/api/README.md`.
- Carga `services/api/.env` automáticamente al arrancar; secretos solo por variables de entorno.
- `DATABASE_URL` (Supabase, Transaction pooler / URI) es obligatoria para el módulo de inventario; sin ella, el resto de la API sigue funcionando y el arranque solo avisa por stderr (ver `main.py`).

### Apps Next.js (`apps/talent-pipeline-tracker`, `uis/backoffice`, `uis/website`)

En cada carpeta: `npm run dev` / `npm run build` / `npm run lint`.

- `apps/talent-pipeline-tracker` es la única con ESLint configurado (`eslint.config.mjs`); en `uis/backoffice` el script `lint` en realidad ejecuta `next build`.
- Las apps que consumen API usan `NEXT_PUBLIC_API_URL` (backoffice y tracker).

### Raíz (utilidades TypeScript del hito 2)

```bash
npm run typecheck   # tsc --noEmit — actualmente falla porque barre también uis/**
npm run dev         # tsx packages/shared/business-logic/demo.ts
```

### Analizador de incidentes

```bash
python analyze.py services/incidents-healthcore.csv
```

### Seed del gestor de incidencias

```bash
services/api/.venv/bin/python scripts/seed_incidents.py
```

Carga el histórico del CSV como incidencias `origin=customer` aplicando las transformaciones del CONTEXT del hito (mapeo de estados, categorías y sedes). Es idempotente (usa el `incident_id` del CSV como `source_id` interno). Tras ejecutarlo, `/api/incidents/summary` debe dar: 94 total; open 28 / resolved 52 / discarded 14; patient_experience 61 / billing_error 20 / other 13.

### Pipeline de desempeño de negocio (Prefect)

```bash
services/api/.venv/bin/python data/pipelines/pipeline.py                            # último mes cerrado
services/api/.venv/bin/python data/pipelines/pipeline.py --month-start 2026-08-01   # un mes concreto
```

Desde la raíz del repo y con el venv de la API (Prefect `>=3.4,<4` está en `services/api/requirements.txt`; lee `DATABASE_URL` de `services/api/.env`). No hace falta servidor de Prefect: usa su API efímera en el propio proceso. Escribe en `reporting.*` de Supabase y deja un resumen de calidad en `data/eval/monthly_clinic_supply_performance/` (en `.gitignore`). Detalle en la sección de arquitectura y en `data/pipelines/PIPELINE_DESIGN.md`.

### Job nocturno de telemetría (Ticket #DEV-53)

```bash
services/api/.venv/bin/python scripts/nightly_export.py                         # ayer (UTC)
TARGET_DATE=2026-08-21 services/api/.venv/bin/python scripts/nightly_export.py  # un día concreto
docker compose up -d --build scheduler                                          # disparo automático 02:15 UTC
```

Desde la raíz. Exporta `telemetry_events` del día a `data/raw/telemetry_YYYY-MM-DD.csv` (si no existe), lanza el pipeline de negocio como subproceso y registra la ejecución en `job_runs`. Detalle, logs reales y cuerpo del PR en `docs/nightly-export.md`.

### Cola de tareas asíncronas (Ticket #DEV-55)

```bash
docker compose up -d --build redis worker flower                                              # broker + worker + Flower (localhost:5555)
docker compose stop worker flower redis                                                       # parar (warm shutdown)
services/api/.venv/bin/celery -A services.celery_app worker --loglevel=INFO --concurrency=1  # worker nativo, desde la RAÍZ
services/api/.venv/bin/celery -A services.celery_app flower --port=5555                       # Flower nativo, desde la RAÍZ
```

`POST /reporting/pipeline-runs` encola y responde 202 con `task_id`; el estado se consulta en `GET /tasks/{task_id}` (solo admin). `REDIS_URL` por defecto `redis://localhost:6379/0` en nativo; en Docker lo fija el compose. Detalle, mediciones y demo de la DLQ en `docs/async-tasks.md`.

### Tests (ver TESTING.md en la raíz para el plan completo)

```bash
# Tests del job nocturno (33, SQLite, sin FastAPI; incluye dos procesos reales a la vez): desde la RAÍZ
services/api/.venv/bin/python -m pytest tests/jobs

# Tests unitarios del pipeline de negocio (10 tests, sin base de datos): desde la RAÍZ del repo
services/api/.venv/bin/python -m pytest tests/pipelines/test_pipeline.py

# Backend (167 tests): desde services/api, con el venv activado
python -m pytest            # o: uv run pytest (en Codespaces)
python -m pytest --cov      # cobertura: auth ≥70%, backoffice ≥60%, total ~77% (bajó de ~81% al sumar telemetría: rutas de startup con Supabase real, dificiles de cubrir sin conexión — no hay --cov-fail-under que lo bloquee)

# Frontend (84 tests): desde uis/backoffice
npm test                    # o: npx jest --coverage
```

Los tests de pytest usan una TinyDB temporal (`SUPPLIERS_DB_PATH`) y un secreto JWT de test definidos en `tests/conftest.py` **antes** de importar la app — nunca tocan `data/suppliers.db.json` ni requieren `.env`. El email de reset se sustituye con `monkeypatch`. Los tests de inventario (`tests/test_inventory.py`) siguen el mismo principio pero con Supabase: la fixture `client` de `conftest.py` sobreescribe `get_inventory_db` con una SQLite en memoria (`StaticPool`) creada y destruida por test — nunca tocan la base de Supabase real ni requieren `DATABASE_URL`. Los tests de Jest priorizan `.ts` sobre los artefactos `.js` compilados (`moduleFileExtensions` en `jest.config.js`). Toda feature nueva debe añadir sus casos (feliz/límite/fallo) a la batería y mantener los umbrales de cobertura.

### Docker (desarrollo contenedorizado)

```bash
cp .env.example .env   # o copia tus valores reales de services/api/.env + añade BACKEND_API_URL
docker compose up --build
```

Levanta dos servicios en la red `healthcore-net`: `api` (FastAPI, puerto 8000) y `uis` (un solo contenedor Node que arranca `website` en 3000 y `backoffice` en 3001 vía `uis/start.sh`). Además, en la misma red: `scheduler` (job nocturno, #DEV-53) y `redis` + `worker` + `flower` (cola de tareas, #DEV-55); estos cuatro se levantan por nombre y no llevan `restart:`. `uis/backoffice` habla con `api` por nombre de servicio Docker (`BACKEND_API_URL=http://api:8000`, consumido por el rewrite proxy de `next.config.ts`), nunca por `localhost`. Los `Dockerfile` viven en `uis/` y `services/api/` (no en `services/` — ahí solo hay un CSV); `docker-compose.yml` va en la raíz.

Montajes en tiempo de ejecución más allá de la carpeta propia de cada servicio, porque el código ya alcanzaba fuera de su carpeta desde antes de dockerizar: `packages/` (contiene tanto `packages/shared/incidents_validation` que `services/api/main.py` importa vía un `sys.path` insert a la raíz del repo, como `packages/shared/business-logic` que `uis/backoffice` importa por ruta relativa) y `uis/web/` (página estática legacy que `main.py` sirve en `GET /`). Sin estos montajes esas rutas concretas fallan aunque el resto de la plataforma funcione.

Gotchas reales encontrados construyendo esto (todos ya resueltos en los archivos, documentados aquí para no repetir la depuración):
- **Volúmenes anónimos para `node_modules`**: sin `- /app/uis/website/node_modules` y `- /app/uis/backoffice/node_modules` en el compose, el bind mount de `./uis` tapa los `node_modules` de Linux instalados en la imagen con los del host (Mac) o con nada.
- **`libpq5`** instalado en el Dockerfile del backend: `psycopg2-binary` a veces necesita la lib de sistema aunque el wheel sea "binary".
- **`libc6-compat`** en el Dockerfile de `uis` (Alpine): el binario nativo de SWC/Next.js necesita glibc, que musl no provee sin este shim.
- **`start.sh` se invoca como `sh start.sh`, no `./start.sh`**: al montarse en vivo, el bit de ejecución del archivo del host puede no sobrevivir al bind mount.
- **Turbopack entra en pánico sobre virtiofs**: Docker Desktop en Mac no siempre reenvía bien los eventos de sistema de archivos al contenedor por virtiofs, y el grafo de tareas incremental de Turbopack colapsa (`inner_of_upper_lost_followers`). Por eso `start.sh` fuerza `--webpack` en vez del Turbopack por defecto — solo dentro de Docker, el dev nativo sigue usando Turbopack.
- **Polling de archivos**: por la misma razón de virtiofs, webpack tampoco detecta cambios de forma fiable sin ayuda. `next.config.ts` de ambas apps activa `watchOptions.pollIntervalMs` cuando `DOCKER_DEV=true` (variable puesta en el compose, nunca en dev nativo).
- **Caché `.next` incompatible entre Turbopack y webpack**: si una app corrió antes con Turbopack (deja `.next/dev/...`) y luego se cambia a webpack, esa caché vieja puede impedir que el watcher detecte cambios sin dar ningún error visible. Si el hot reload deja de funcionar tras cambiar de bundler, borrar el `.next` de esa app específica y reiniciar.

## Arquitectura

### Backend — `services/api`

FastAPI + TinyDB (archivo único `services/api/data/suppliers.db.json` con tablas `suppliers`, `users`, `profiles`, `password_resets`; ruta configurable con `SUPPLIERS_DB_PATH`).

- `main.py`: app, CORS, monta routers, handler global de excepciones (500 genérico, nunca stack traces) y expone el análisis de incidentes (`/api/incidents/analyze`, `/api/incidents/results/export`) usando `packages/shared/incidents_validation` (agrega la raíz del repo a `sys.path`).
- `routes/`: un router por dominio (`auth`, `users`, `profiles`, `suppliers`, `incidents`).
- Gestor de incidencias (`routes/incidents.py` + `incident_repository.py`): CRUD bajo `/api/incidents` (crear, listar con filtros, detalle, `PATCH /{id}/status` con ciclo de vida open → in_progress → resolved/discarded, `GET /summary`). La validación de entrada NO usa Pydantic: usa `validate_incident_payload`/`validate_status_transition` del paquete compartido para devolver `400` con `{"errors": [{"field","message"}]}` (requisito de la rúbrica; los campos y valores válidos vienen del CONTEXT del hito). `GET /summary` está declarado antes que `GET /{id}` a propósito.
- `security.py`: JWT stateless (python-jose, HS256), hash bcrypt vía passlib, `get_current_user` como dependencia de protección, helpers `require_admin`/`ensure_self_or_admin`. Tokens de reset de contraseña: JWT con `type=reset` + `jti`, single-use registrado en tabla `password_resets`.
- `auth_repository.py` / `repository.py`: acceso a datos (patrón repositorio sobre TinyDB).
- `email_service.py`: correo transaccional con Resend para el flujo de reset (AUTH-03). `/auth/forgot-password` responde siempre 200 (anti-enumeración).
- Decisión registrada en techContext: `User`/`Profile` viven solo en TinyDB; reutilizar `user_id` como `user_uuid` de referencia en otros módulos.
- Gestor de inventario (Hito 5, `routes/inventory.py` + `inventory_repository.py` + `inventory_models.py`): segunda conexión de base de datos, a Supabase (PostgreSQL) vía SQLModel — conviven con TinyDB en `database.py` (`get_inventory_engine`/`get_inventory_db`, sesión por petición vía `Depends`). CRUD bajo `/inventory` para `MedicalSupply`, `SupplyDelivery` y `SupplyConsumption` (nombres del CONTEXT del hito, no los genéricos `Product`/`InboundOrder`/`OutboundOrder` del README). `current_stock` es siempre calculado (`SUM(deliveries) - SUM(consumptions)`, en `inventory_repository.get_current_stock`), nunca una columna editable; un consumo que dejaría stock negativo se rechaza con `400` antes de escribir. `country` en este módulo usa `"US"/"UK"` (enum `SupplyCountry` en `models.py`) — distinto del `Country` de proveedores (`"USA"/"UK"`), mismo nombre de campo pero dominios de valores distintos. `SQLModel.metadata.create_all()` se ejecuta en el `startup` de `main.py` de forma no fatal: si `DATABASE_URL` falta o Supabase no responde, solo lo avisa por stderr y el resto de la API sigue viva.
- Caching (Hito de optimización de rendimiento, rama `feat/caching-optimisation`, ver `CACHING_REPORT.md` en la raíz para el detalle completo con mediciones reales): `cache.py` expone un `TTLCache` en memoria (diccionario + `Lock`, singleton compartido entre routers — los handlers son `def` síncronos que FastAPI corre en threadpool). Cachea `GET /inventory/products` (30s; evita el N+1 de `get_current_stock` contra Supabase) y `GET /api/incidents/summary` (60s; evita barrer la tabla de incidencias 5 veces). Cada endpoint de escritura relevante invalida su clave explícitamente — nunca se depende solo del TTL. `main.py` también tiene un `timing_middleware` que loguea `método path → status | ms` de cada petición (usado para decidir estos dos candidatos con evidencia, no intuición). Gotcha real: la caché es un singleton de proceso, así que `tests/conftest.py` necesita `cache.clear()` en la fixture `clean_db` autouse — si no, tests que resetean la base de datos directamente (sin pasar por los endpoints que invalidan) reciben resultados cacheados de un test anterior.

### Serialización de la API — `docs/serialization-audit.md`

Auditoría de serialización del backend (rama `feat/serialization-audit` sobre `main`, 2026-09-07). Los 37 endpoints de `services/api` tienen contrato de respuesta explícito; el documento lista cada uno con su estado original y la decisión razonada.

Reglas que quedan fijadas y conviene no romper:

- **Un modelo, varios contratos.** Detalle, listado y escritura tienen esquemas distintos y no se reutilizan entre sí: `UserPublic` (detalle, con email) / `UserListItem` (listado, sin email) / `UserRegistered` (registro); `Supplier` / `SupplierListItem`; `IncidentRead` / `IncidentListItem`. **El esquema del listado lo define el consumidor, no el modelo**: se derivaron mirando qué campos lee el componente del backoffice, no qué columnas tiene la tabla. Al añadir un listado nuevo, hacer lo mismo.
- **`response_model` no se aplica a ciegas.** `GET /` sirve el HTML de `uis/web` y `GET /api/incidents/results/export` devuelve un CSV: ahí va `response_class` + el content-type en `responses`, porque un `response_model` prometería JSON que nunca llega. Los dos `DELETE` responden 204, que no lleva cuerpo por definición.
- **Los flujos de auth no autenticados no devuelven email** (registro, login, token, forgot, reset). `GET /auth/me` sí puede: el llamante está autenticado y el correo es suyo.
- **`services/api/tests/test_serialization.py` es el guardián de toda la superficie.** Tres de sus tests recorren el esquema OpenAPI completo en vez de una lista escrita a mano, así que cubren rutas que todavía no existen: si alguien añade un endpoint que filtra `hashed_password` o que devuelve JSON sin esquema, falla solo. `access_token` está excluido de la lista de prohibidos a propósito — es el propósito de `/auth/login`.
- **Fixture `admin_headers`** en `tests/conftest.py`: crea un admin por el repositorio, porque `POST /users` siempre crea rol `user` (intencionado). Necesaria desde que `GET /users` exige `require_admin`.
- **`user_uuid` se mantiene en inventario a propósito**, contra la recomendación genérica sobre claves foráneas en bruto: es el rastro de auditoría que exigen HIPAA/UK GDPR, la tabla de `/inventory/orders` lo muestra, y no hay objeto usuario anidado que ofrecer — los usuarios viven en TinyDB y las órdenes en Supabase.
- **Cambio de comportamiento**: `GET /users` pasa a exigir `require_admin`. Antes lo servía cualquier usuario autenticado devolviendo el email de toda la organización. Residual documentado y **no** resuelto: `GET /users/{id}` sigue devolviendo el email de cualquier cuenta a cualquier autenticado.
- Impacto en el frontend: `uis/backoffice/types/` tiene ahora `IncidentListItem` y `SupplierListItem` junto a los tipos completos, y `AuthProfile` ya no declara `id` ni `user_id`. Al recortar un campo de la API, actualizar el tipo — `tsc --noEmit` localiza los puntos que asumían la proyección completa.

### Frontends

- `uis/website`: web corporativa pública (hito 1 migrado a React); contenido en `data/content.ts`, componentes de sección en `components/`.
- `uis/backoffice`: backoffice de proveedores + auth completa (login, registro, forgot/reset password, perfil, change password) contra `services/api`. Capa de servicios en `services/` (`http.ts`, `authApi.ts`, `suppliersApi.ts`, `incidentsApi.ts`, `inventoryApi.ts`, `telemetryReportApi.ts`, `session.ts`) y tipos en `types/`. Incluye el gestor de incidencias (`/incidents` listado con filtros y cambio de estado con revert, `/incidents/new` formulario con aviso obligatorio de no introducir datos de pacientes, `/incidents/summary` métricas). Interfaz íntegramente en español (traducida el 2026-08-09 por decisión explícita del usuario; el CONTEXT original de ese hito pedía inglés para Incidents — tenerlo en cuenta si se vuelve a evaluar contra esa rúbrica). Los mensajes de error que llegan literalmente de la API (p. ej. `"Insufficient stock for supply..."`) no se traducen: son el contrato de texto exacto que exige el CONTEXT del backend. `http.ts` lanza `ApiFieldError` cuando la API responde 400 con errores por campo.
- Tema visual "Supply Manifest console" (`app/globals.css`): oscuro y denso por defecto (acento ámbar `#FF8A3D`, LEDs cuadrados para nivel de stock, tipografía monoespaciada para SKUs/IDs/cantidades), con **toggle real a modo claro** (`components/ThemeToggle.tsx`, atributo `data-theme` en `<html>`, persistido en `localStorage`, sin flash gracias a un script inline en el `<head>` — por eso `<html>` lleva `suppressHydrationWarning`). Todo color vive como token CSS (`--bg`, `--panel`, `--text`, `--muted`, `--brand`, `--line`, `--critical`/`--warning`/`--ok`, `--success-*`/`--error-*`); nunca hardcodear un hex en un `.tsx` del backoffice.
- Interfaz de inventario (Hito 5 backoffice, `app/inventory/`): 4 pantallas sobre la API de `/inventory` — `products` (lista con `current_stock` y LED de color por nivel, umbrales en `types/inventory.ts::getStockLevel`), `orders/inbound` y `orders/outbound` (formularios; el de salida muestra el stock del producto seleccionado en tiempo real, tomándolo de la lista ya cargada — no hace una petición extra — y bloquea el envío si la cantidad supera el stock, antes de tocar la API) y `orders` (historial de solo lectura, entradas/salidas con badge, más un detalle expandible por fila — ver punto de lazy loading abajo). Sin variable de entorno nueva: reutiliza `NEXT_PUBLIC_API_URL`/proxy de `http.ts`, el mismo backend que proveedores e incidencias. Rutas sin prefijo `/backoffice` (la app ya es el backoffice), a diferencia de lo sugerido en el README del hito. `clinic_id` es un número simple (1-12): el CONTEXT no da un catálogo de nombres de clínica para este módulo.
- Lazy loading con `next/dynamic` (mismo hito de caching, ver `CACHING_REPORT.md`): `ProviderForm` en `/suppliers` vive detrás de un botón "+ Añadir proveedor", y `OrderDetailPanel` en `/inventory/orders` (IDs internos + timestamp ISO completo, de auditoría) se carga al expandir una fila. Gotcha real de Next.js 16 (confirmado en `node_modules/next/dist/docs/01-app/02-guides/lazy-loading.md`, no por intuición): `next/dynamic` solo da code-splitting real cuando se llama desde un **Client Component** — un Server Component que importa dinámicamente un Client Component no obtiene code-splitting automático, y `ssr: false` directamente no está permitido fuera de un Client Component. Por eso la portada del backoffice (`app/page.tsx`, Server Component) no lleva ningún `next/dynamic` pese a tener contenido secundario (tabla "CME snapshot") — aplicarlo ahí no habría dado ningún beneficio real.
- `apps/talent-pipeline-tracker`: tracker de candidatos (hito 3/4) contra API externa; integración centralizada en `services/api.ts`, contratos en `types/tracker.ts`, con normalización defensiva de payloads (campos heterogéneos tipo `stage`/`step`, manejo explícito de 422) y fallback de datos en el dashboard.
- Patrón común: App Router con componentes cliente para interactividad, `AuthGuard` para rutas protegidas, sesión en `services/session.ts`.
- `uis/web/index.html`: UI estática servida por el backend en `/`.

### Telemetría — `docs/telemetry/` (plan) + captura real instrumentada

Plan de diseño del hito de telemetría (rama `feat/telemetry-design-plan` sobre `feat/caching-optimisation`, 2026-08-12): `telemetry-plan.md` + `event-schemas.json` (24 eventos tras la instrumentación de abajo — 23 originales + `web_vital_recorded`), cubriendo inventario, incidencias, proveedores, autenticación, rendimiento, errores de frontend y navegación, todos bajo un Event Envelope común (`eventId`, `timestamp`, `sessionId`, `userId`, `event_type`, `schemaVersion`, `requestId`, `properties`) y con allowlist de propiedades por evento (nada fuera de lista se emite). Decisión stream/batch justificada por urgencia de negocio, no preferencia técnica (7 eventos en stream: `stock_threshold_triggered`, `incident_created`, `supplier_status_changed`, `login_failed`, `password_reset_requested`, `api_error_response`, `frontend_error_captured`). Decisión de minimización de datos deliberada: `incident_created` excluye `title`/`description` del allowlist (riesgo de PHI en texto libre pese al aviso ya existente en la UI de `/incidents/new`); los eventos de auth con un identificador de usuario (`login_failed`) lo emiten como hash HMAC-SHA256, nunca en texto plano.

Captura real instrumentada (rama `feat/telemetry-capture` sobre `feat/telemetry-design-plan`, 2026-08-14, Fase 1+2+3 del brief de la clase). Backend: `POST /telemetry/events` (`routes/telemetry.py`) era un stub sin auth y sin persistencia — solo logueaba count + `event_type` por evento y respondía `{"received": N}` (la persistencia real llegó después, ver el párrafo de "Almacenamiento" más abajo). `telemetry_service.py` es el sink compartido (`log_event`/`emit_backend_event`) que usan tanto ese endpoint como los eventos que el propio backend detecta (login, errores 5xx, umbrales de inventario) — evita que el backend tenga que hacerse un POST a sí mismo. Antes de instrumentar hubo que resolver con una migración mínima las tres brechas de esquema que documentaba la entrada anterior de este archivo: `expiry_date` en `MedicalSupply`, tabla `SupplyThreshold` (umbral mínimo por clínica+producto, fila ausente = sin umbral configurado, nunca se asume cero) y el endpoint `PATCH /inventory/products/{id}/stock` (siempre 400, le da al principio ya existente del CONTEXT — "el stock nunca se modifica directamente" — un punto de aplicación concreto en vez de un 404 genérico). La brecha de categorías (`VALID_SUPPLY_CATEGORIES` vs las del CONTEXT) sigue sin resolver a propósito — cambio de modelo de datos más grande, no bloqueaba el resto (ver techContext.md).

`stock_threshold_triggered` usa una lectura de stock nueva, `get_current_stock_for_clinic` — la `get_current_stock` que ya usaba `/inventory/products` suma todas las clínicas juntas para un `supply_id`, y cambiar su significado habría roto esa pantalla en silencio. `supply_expiry_flagged` no tiene un disparador de usuario natural (el CONTEXT lo describe como job diario); este proyecto no tiene scheduler/cron, así que corre una vez al arrancar la API (`main.py::flag_expiring_supplies`) como stand-in. `login_succeeded`/`login_failed` se emiten desde `routes/auth.py`, no desde el frontend — `login_failed` necesita el hash HMAC-SHA256 con el secreto de la app, que no puede vivir en el navegador; no distingue `invalid_credentials` de `account_not_found` a propósito, mismo principio anti-enumeración que `/auth/forgot-password`. `inbound_order_created`/`outbound_order_created` sí se emiten desde el frontend (páginas de `/inventory/orders/{inbound,outbound}`), leyendo `product_category`/`country` de la lista de productos ya cargada en memoria, sin petición extra — mismo patrón que ya usaba el formulario de salida para el chequeo de stock en tiempo real.

Frontend: `uis/backoffice/services/telemetry.ts` (`TelemetryService`) es el único módulo que hace su propio `fetch` fuera de `http.ts` — necesita `NEXT_PUBLIC_TELEMETRY_ENDPOINT` (variable propia, ver `.env.local.example`) y `navigator.sendBeacon`, y sus fallos nunca deben lanzar ni redirigir a login como sí hace `requestJson`. `track()` es fire-and-forget (no devuelve promesa): un fallo de red en telemetría no puede bloquear una acción de negocio real. Cola en memoria, batch cada 10s o 20 eventos, reintento con backoff exponencial (hasta 3 intentos, luego se descarta el lote). Base técnica: `frontend_error_captured` vía `window.onerror`/`unhandledrejection` (`components/ErrorTracking.tsx`) más un Error Boundary de App Router (`app/error.tsx`) para errores del árbol de React; `page_viewed` vía `usePathname` (`components/PageViewTracker.tsx`, no necesita `<Suspense>` — a diferencia de `useSearchParams`, `usePathname` no lo exige salvo con `cacheComponents` activado, que este proyecto no usa); `web_vital_recorded` vía `useReportWebVitals` de `next/web-vitals` (`components/WebVitals.tsx`).

Almacenamiento real (rama `feat/telemetry-storage` sobre `main`, 2026-08-17, Fase 3 del brief). `POST /telemetry/events` deja de ser el stub: valida cada evento del lote por separado (`TelemetryEvent.model_validate` dentro de un `try/except` — nunca `list[TelemetryEvent]` como tipo del body, para que un evento mal formado no tumbe el lote con un 422) y persiste los válidos en Supabase (tabla `telemetry_events`, `telemetry_models.py`, registrada en el mismo `SQLModel.metadata` que inventario — mismo engine, sin variable de entorno nueva) en una sola transacción (`telemetry_repository.bulk_insert`). El modelo `TelemetryEvent` no se tocó. `tags` guarda `properties` más los campos de correlación del envelope (`eventId`/`sessionId`/`userId`/`requestId`/`schemaVersion` — decisión explícita para trazabilidad futura, ver `telemetry-plan.md` sección 9); `level` se deriva del `event_type` por palabras clave (`derive_level`, `telemetry_service.py`). Decisión de alcance: los eventos que el propio backend emite sin pasar por este endpoint (`login_failed`, `api_error_response`, `stock_threshold_triggered`, etc.) siguen sin persistirse — solo se loguean, igual que antes; conectarlos habría hecho que la mayoría de los tests de login/inventario intentaran hablar con Supabase real, porque ese código no pasa por el `Depends(get_inventory_db)` que la fixture de tests intercepta.

Bug real encontrado en la verificación manual contra Supabase real (los tests con timestamps limpios no lo detectaban): `TelemetryEvent.timestamp` es un `str` sin validar formato — un valor no parseable como fecha pasaba la validación de Pydantic y solo reventaba al construir el registro (`ValueError` sin capturar → 500 de todo el lote). Se envolvió también ese paso en manejo de errores por evento, no solo la validación de esquema. Gotcha de Supabase (entorno, no del proyecto): la conexión directa (`db.<ref>.supabase.co:5432`) solo resuelve por IPv6 desde 2024 sin el add-on de IPv4 — usar siempre la cadena del connection pooler (`aws-0-<region>.pooler.supabase.com:6543`, usuario `postgres.<project-ref>`).

Reporte técnico y pipeline de análisis (rama `feat/telemetry-report` sobre `feat/telemetry-storage`, 2026-08-21, Fase 4 del brief — no confundir con el Hito de Data Pipelines, que cubre métricas de negocio). `services/telemetry/analysis.py` (deliberadamente fuera de `services/api/`, a diferencia de todos los demás módulos de telemetría que son archivos planos — la ruta exacta la exige la rúbrica de la clase; importa `database`/`telemetry_models` de `services/api/` confiando en que esa carpeta ya está en `sys.path` tanto en dev/prod como en tests) tiene 4 funciones de métrica puras (`events_per_day`, `error_rate_by_day`, `web_vital_latency_by_day`, `auth_failure_rate`), cada una cargando en SQL solo el rango de fechas (y el `event_type` cuando aplica), refinando con Pandas, convirtiendo `timestamp` con `pd.to_datetime(..., utc=True)` antes de cualquier `groupby`, y devolviendo `.to_dict(orient="records")`. `GET /telemetry/report` (`routes/telemetry.py`) resuelve el período una sola vez (`start_date`/`end_date` opcionales, últimos 7 días por defecto) y cachea el resultado 60s en el mismo `cache.py` (`TTLCache`) que ya usan `/inventory/products` y `/api/incidents/summary` — sin caché nueva.

Decisión de alcance tomada en esta clase (cambia lo que decía la entrada de arriba): `login_succeeded`/`login_failed` ahora SÍ se persisten en `telemetry_events` — antes solo se logueaban, junto con el resto de eventos backend-originados. Fue deliberado para poder implementar `auth_failure_rate` (actividad adicional) con datos reales. El resto de eventos backend-originados (`api_error_response`, `stock_threshold_triggered`, etc.) sigue sin persistirse — la razón documentada sigue aplicando para ellos. Para login sí funciona sin romper el aislamiento de tests porque `routes/auth.py` ahora depende de `get_inventory_db_optional` (`database.py`, nuevo) vía `Depends()`, que `tests/conftest.py` intercepta con el mismo override que ya usaban `/inventory` y `/telemetry/events` — a diferencia de `emit_api_error` (llamado desde el exception handler global, fuera del ciclo normal de dependencias). `get_inventory_db_optional` existe porque el login no puede depender de `get_inventory_db` a secas: esa dependencia lanza `RuntimeError` si `DATABASE_URL` falta, lo que tumbaría el login entero por un problema de Supabase — la variante opcional degrada a `None` en vez de propagar el error, y `telemetry_service.emit_backend_event` además envuelve el propio insert en `try/except` (un Supabase caído no debe romper un login que sí tiene los datos que necesita en TinyDB).

Bug real encontrado y corregido al generar datos reales para el reporte (PR aparte: `fix/telemetry-frontend-endpoint-resolution` sobre `feat/telemetry-storage`, mismo día): `uis/backoffice/services/telemetry.ts` resolvía `ENDPOINT` vía `globalThis.process?.env?.NEXT_PUBLIC_TELEMETRY_ENDPOINT`, un patrón que el inlining de variables `NEXT_PUBLIC_*` de Next.js nunca sustituye (solo reconoce el literal `process.env.X`) — en el navegador `globalThis.process` no existe, así que `ENDPOINT` era siempre `""` y `sendBatch` lo trataba como éxito silencioso. Consecuencia real: desde que se instrumentó en `feat/telemetry-capture`, **ningún** evento originado en el frontend (`page_viewed`, `web_vital_recorded`, `frontend_error_captured`, `inbound_order_created`, `outbound_order_created`) llegó jamás a Supabase, sin ningún error visible — solo los eventos que emite el propio backend (login) llegaban. `services/http.ts` tiene el mismo patrón para `NEXT_PUBLIC_API_URL`, pero ahí queda enmascarado porque el fallback (`"/api-proxy"`) ya es el valor correcto para dev nativo — no corregido a propósito, deliberadamente fuera de alcance de ese PR.

Dashboard técnico en el backoffice (rama `feat/telemetry-dashboard` sobre `main`, 2026-09-12, actividad adicional "Dashboard visual simple" del mismo brief del reporte — el resto del brief ya estaba entregado desde `feat/telemetry-report`). Pantalla `/telemetry` (`app/telemetry/page.tsx`, enlace "Telemetría" en el menú) que consume `GET /telemetry/report`: un panel por métrica con la pregunta operacional que responde, barras hechas solo con CSS y tokens del tema (sin librería de gráficos: decisión explícita del usuario para no tocar `package-lock.json`; desde la Parte 3 del pipeline de negocio viven en `components/BarList.tsx`, compartidas con `/reporting`) y tabla de detalle plegable; muestra el `period` **que devuelve el servidor**, no el que se pidió. Selector de fechas con estado borrador/aplicado (no lanza una petición por tecla) sobre `useAsyncData`/`AsyncSection`. Contrato y utilidades puras en `types/telemetryReport.ts` (separado de `types/telemetry.ts`, que describe los eventos que se envían); la lectura va por `requestJson` en `services/telemetryReportApi.ts`, no por `TelemetryService`, porque aquí un fallo sí debe propagarse para mostrar "Reintentar". Reglas que conviene no romper:

- **Fin exclusivo**: los `<input type="date">` son días completos con ambos extremos incluidos y la API espera inicio inclusivo/fin exclusivo en UTC. `toReportQuery` convierte "del 6 al 12" en `[6 00:00Z, 13 00:00Z)`; mandar el 12 tal cual perdería el último día sin ningún error. Todo en UTC (también el rango por defecto de `getDefaultDateRange`), porque el backend agrupa por fecha UTC. El contrato está fijado en ambos lados: `__tests__/telemetryReport.test.ts` y `test_report_accepts_backoffice_date_range_with_exclusive_end` (formato `.000Z` de `toISOString()`, verificado también contra el Python 3.9 del venv).
- **Gotcha real: `noValidate` en el formulario.** Los `min`/`max` de los inputs de fecha activan la validación nativa, que bloquea el `submit` con un globo del navegador en su idioma, y `validateDateRange` nunca llega a mostrar su mensaje en español. Lo detectó el test de la pantalla, no el navegador.
- Residual conocido, no resuelto: `_parse_query_datetime` (`routes/telemetry.py`) no captura fechas mal formadas — `?start_date=hola` da un 500 genérico en vez de un 400. La pantalla nunca envía eso, pero un cliente externo sí podría.
- **Bug corregido en esta misma rama: la caché no funcionaba sin parámetros.** La clave de `get_report` se construía con el período ya resuelto, que sin fechas termina en `datetime.now()` con microsegundos, así que cada `GET /telemetry/report` a secas era una clave nueva y recalculaba (~370 ms cada vez contra Supabase real). Ahora `_report_cache_key` construye la clave con **lo que pidió el cliente**: fechas explícitas normalizadas (`...Z` y `....000Z` comparten entrada) y `default` para las ausentes. Medido tras el arreglo: ~460 ms la primera, ~4 ms las siguientes. El test original solo probaba con fechas fijas y no lo detectaba; `test_report_default_window_is_cached_within_ttl` falla con el código antiguo (comprobado con `git stash`) y hay dos tests más que fijan las otras dos caras: instantes equivalentes comparten entrada y rangos distintos nunca la comparten.

### Pipeline de desempeño de negocio — `data/pipelines/PIPELINE_DESIGN.md`

Hito de Data Pipelines. **Parte 1 (diseño)** en `feat/business-pipeline-design`, apilada sobre `feat/telemetry-dashboard`. **Parte 2 (implementación resiliente con Prefect)** en `feat/resilient-business-pipeline`, apilada sobre la anterior. Ambas del 2026-09-13; la §9 del documento mapea cada requisito del ticket a su código.

Pipeline `monthly_clinic_supply_performance`: el "Reporte Mensual de Desempeño de Insumos por Clínica" para la CEO y la CCO. Calcula 4 KPIs (`total_supply_cost`, `supply_consumption_count`, `critical_stockout_count`, `expiry_risk_count`) por `clinic_id` × `month_start` (UTC) y los escribe en `reporting.monthly_clinic_supply_performance`, creada en Supabase con el DDL **literal** del CONTEXT. Lee `telemetry_events` **en solo lectura** y **no** toca `services/telemetry/analysis.py` ni `GET /telemetry/report`.

Mapa del código:
- **`data/pipelines/pipeline.py`**: flow `monthly_clinic_supply_performance_flow` + 8 tasks + CLI.
- **`data/pipelines/monthly_clinic_supply_performance/`**: `models.py` (3 tablas `reporting.*` y `ensure_reporting_schema`), `storage.py` (extracción y carga idempotente), `run_log.py` (log de corridas y lock), `queries.py` y `trigger.py` (lo que usa la API).
- **`data/process/supply_performance_transforms.py`**: Pandas puro.
- **`services/reporting/`**: `router.py` + `schemas.py`, con `GET /reporting/monthly-clinic-supply-performance` (KPIs), `GET /reporting/pipeline-runs/latest` (estado) y `POST /reporting/pipeline-runs` (solo admin, 202, 409 si el mes está bloqueado). `services/reporting/` importa de `data/pipelines/`, nunca al revés.

Requisitos previos de captura, resueltos en la Parte 2:

- **P1 coste:** `unit_cost` opcional en `inbound_order_created`, con campo "Coste unitario (USD/GBP)" en `/inventory/orders/inbound` (`parseUnitCost` en `types/inventory.ts`). Vacío = coste desconocido, nunca 0. Solo viaja en el evento: la API de inventario no guarda coste.
- **P2:** `_check_stock_threshold` persiste (`db_session`) y **solo emite al cruzar el umbral** (stock `> threshold` antes de la orden y `<= threshold` después). Antes re-disparaba tras cada orden mientras la clínica siguiera bajo mínimo.
- **P3:** `services/api/inventory_alerts.py` emite y persiste `supply_expiry_flagged` **una vez por lote (clínica, producto, `expiry_date`)**, con `eventId` determinista `uuid5` y comprobación previa en JSONB. Sigue corriendo en el startup de la API, ahora de forma idempotente.
- **P5/P6 siguen abiertos:** `clinic_id` es el id real en texto (`"7"`), sin catálogo inventado. `country` de los eventos es el del **producto**, así que una clínica puede salir con la moneda de un producto de otro país (pasó con datos reales: la clínica 1 aparece `UK/GBP` en agosto). Si una partición mezcla dos países, se rechaza en vez de mezclar USD y GBP.

Decisiones que conviene no romper:

- **KPIs con la definición literal del CONTEXT** ("conteo de `stock_threshold_triggered`/`supply_expiry_flagged` del mes"). El brief de la Parte 2 prohíbe reinterpretarlos. Que ese conteo signifique "veces que cayó" y "lotes marcados" lo garantiza la **captura** (P2, P3), no la transformación. En la Parte 1 se había decidido "insumos distintos"; se descartó por eso.
- **`POST /telemetry/events` no deduplica** (`eventId` va en `tags` sin índice único). La transformación deduplica por `eventId` y después por `delivery_id`/`consumption_id`.
- **Idempotencia:** recalcular siempre la ventana completa y sustituir, nunca sumar deltas. Upsert `ON CONFLICT (clinic_id, month_start)` en una sola transacción con `reporting.pipeline_run_partitions` (valores antes/después). No se calcula el mes en curso (`resolve_month_start` lanza `ValueError`; el `POST` responde 400).
- **Caché de la transformación: clave = huella del contenido** (mes + `TRANSFORM_VERSION` + `(id, timestamp)` de cada evento) y `cache_expiration` de 1 hora. Nunca una clave solo por mes: un evento tardío tiene que invalidarla. **Si cambias una regla de `supply_performance_transforms.py`, sube `TRANSFORM_VERSION`**, o durante una hora saldrá el resultado viejo de caché. Extracción y carga llevan `cache_policy=NONE`.
- **Tolerancia a fallos parciales:** `extract_domain_activity` y `export_eval_snapshot` se llaman con `return_state=True`; si fallan, la corrida termina `completed_with_warnings`. Las críticas propagan: el `try/except` del flow registra `failed` en `pipeline_runs` y relanza. `start_pipeline_run` usa `retry_condition_fn` para no reintentar un `WindowLockedError`.
- **`supply_deliveries`/`supply_consumptions` solo auditan la captura** (`capture_ratio`, aviso por debajo de 0.95). Cero eventos con filas de dominio en la ventana → `CaptureGapError`, `failed` y nada publicado.
- **Disparo manual por Celery** desde el Ticket #DEV-55 (antes `BackgroundTasks` dentro del proceso de la API): la API solo comprueba el lock y reserva el `run_id` (`trigger.reserve_monthly_run`); el worker crea la fila y ejecuta el flow. Sin servidor ni worker **de Prefect** (su API efímera corre dentro del worker de Celery). Si el worker muere a mitad, la corrida queda `running` hasta que el heartbeat (30 min) la marca `crashed` y libera el lock del mes. **Sin `TTLCache` en `/reporting`**: quien escribe no puede invalidar la caché del proceso de la API.
- **Blocks de Prefect no implementados**: sin servidor persistente no hay dónde registrarlos. `DATABASE_URL` sale de `services/api/.env` a través de `database.get_inventory_engine()`.

Gotchas reales encontrados:

- **Prefect 3 y `from __future__ import annotations` en Python 3.9 no conviven en el módulo del flow**: Prefect genera el esquema de parámetros con Pydantic y falla con `CheckParameter is not fully defined`. `pipeline.py` no lleva ese import y usa `Optional[...]`. El resto de módulos sí puede llevarlo.
- **Timestamps mezclados en `telemetry_events`**: `isoformat()` omite los microsegundos cuando valen 0, y Pandas 2 infiere el formato de la primera fila y revienta con las demás. Siempre `pd.to_datetime(..., utc=True, format="ISO8601")`. Lo encontró la primera ejecución real, no los tests. Tiene test de regresión que falla sin el arreglo.
- **Columnas de enteros con `None` en Pandas**: con la inferencia por defecto pasan a `float64` (`clinic_id=1` llega como `1.0`). `events_to_frame` construye el DataFrame con `dtype=object`.
- **`data/__init__.py` obligatorio** (verificado): sin él, Python fusiona `data/` de la raíz con `services/api/data/` (TinyDB) como un único namespace package. `data/pipelines/__init__.py` añade `services/api` a `sys.path` para que `python data/pipelines/pipeline.py` pueda importar `database`/`telemetry_models`.
- **Tests:** SQLite no tiene esquemas con nombre, así que la fixture `inventory_engine` hace `ATTACH DATABASE ':memory:' AS reporting` en el evento `connect`. `test_business_pipeline.py` usa `prefect_test_harness` a nivel de módulo: sin él, Prefect apaga su servidor efímero en el `atexit` del intérprete y su log revienta contra la salida ya cerrada de pytest ("I/O operation on closed file"). `main.py` sube `httpx` a WARNING porque cada corrida de Prefect genera cientos de líneas `HTTP Request`. `pipeline.use_engine(engine)` sustituye Supabase en tests.
- **Postgres necesita el esquema antes que las tablas**: `init_inventory_schema` en `main.py` llama a `ensure_reporting_schema` antes del `create_all` genérico, porque las tablas `reporting.*` quedan registradas en `SQLModel.metadata` al importar el router.

**Parte 3 (subflows, tests unitarios y dashboard)**, rama `feat/pipeline-subflows-dashboard` apilada sobre `feat/resilient-business-pipeline`, 2026-09-13. Detalle en la §10 de `PIPELINE_DESIGN.md`.

- **Topología:** `monthly_clinic_supply_performance_flow` ya no contiene lógica de ETL. Coordina 4 subflows con entradas y salidas explícitas: `extract_clinic_supply_activity` → `compute_monthly_clinic_supply_kpis` → `load_monthly_clinic_supply_performance` → `export_supply_performance_eval_snapshot` (opcional, `return_state=True`). Dentro del de KPIs hay **una task por KPI** con el nombre del campo del CONTEXT (`compute_total_supply_cost`, `compute_supply_consumption_count`, `compute_critical_stockout_count`, `compute_expiry_risk_count`), más `prepare_clinic_supply_events` (la de la caché de 1 h), `assemble_monthly_clinic_supply_performance` y `validate_monthly_aggregates`. La carga se llama `upsert_monthly_clinic_supply_performance_rows`, porque el nombre `load_monthly_clinic_supply_performance` es ahora el del subflow y en Python no pueden coincidir.
- **Funciones puras por KPI** en `data/process/supply_performance_transforms.py`, defensivas por sí mismas (ignoran `clinic_id` inválido, tratan coste inválido como desconocido). `transform_supply_events`/`aggregate_monthly_clinic_metrics` siguen existiendo como composición de esas mismas funciones.
- **Tests unitarios en `tests/pipelines/test_pipeline.py`** (raíz del repo, con su propio `conftest.py` que añade la raíz a `sys.path`): llaman a `task.fn(...)` sin Prefect ni base de datos. Incluyen un KPI calculado a mano (86,00 USD) y casos defensivos. Los tests de integración del flow siguen en `services/api/tests/test_business_pipeline.py` (24, con `prefect_test_harness`), incluidos dos que ejecutan subflows sueltos.
- **Gotcha: `get_run_logger()` lanza `MissingContextError` fuera de un flow o task run.** Una task que loguea así no se puede llamar con `.fn` en un test. `pipeline.py` usa `_logger()`, que cae a un logger estándar sin contexto.
- **Gotcha de privacidad con subflows:** los parámetros de cada flow run se guardan en la base de Prefect. Como los eventos pasan a ser parámetro del subflow de KPIs, `storage.fetch_supply_events` recorta `tags` a `EXTRACTED_TAG_KEYS` (sin `userId` ni `requestId`) **antes** de devolverlos.
- **Dashboard `/reporting`** (`app/reporting/page.tsx`, "Informe mensual" en el menú), de solo lectura por decisión del usuario, sin botón de recalcular. Un panel por KPI con el nombre exacto del CONTEXT, período del mes, costo por país y moneda (nunca USD + GBP) y avisos en lenguaje de negocio (`describeReportNotices`: informe atrasado por `is_stale`, captura incompleta por `low_capture_ratio`/`coverage_unavailable`). Esa es la actividad adicional de las preguntas de diseño 4 y 6. Tipos y ayudas puras en `types/businessReport.ts`, lecturas en `services/reportingApi.ts`.
- **`http.ts` lanza `ApiRequestError` (con `status`)** en respuestas no satisfactorias en lugar de un `Error` genérico. El mensaje es el mismo, así que las pantallas existentes no cambian. `reportingApi.ts` lo usa para convertir un 404 en `null` ("ese mes no tiene informe"), en vez de mostrarlo como error.
- **`components/BarList.tsx`**: barras CSS extraídas de `/telemetry`, compartidas con `/reporting`.
- **Coste desconocido ≠ 0 también en la UI:** un `total_supply_cost` de `0` puede ser "compras sin `unit_cost`" (todo agosto de 2026, anterior al campo del formulario). `GET /reporting/pipeline-runs/latest` expone `clinics_with_unrecorded_cost` y el dashboard marca esas filas como "Incompleto" y añade un aviso. Lo detectó el recorrido en Chrome con datos reales, no los tests. Solo aplica si la última corrida es del mes mostrado.
- **Gotcha de React:** el efecto que rellena el `<input type="month">` con el mes cargado depende solo de `data`. Si dependiera también del valor del campo, borrarlo lo volvería a rellenar al instante. Lo detectó el test de la pantalla.

### Job nocturno de telemetría — `docs/nightly-export.md`

Ticket #DEV-53 (Procesos en Segundo Plano), rama `feat/nightly-telemetry-export` apilada sobre `feat/pipeline-subflows-dashboard`, 2026-09-13.

Mapa del código:
- **`scripts/nightly_export.py`**: script CLI. Resuelve `target_date` (`TARGET_DATE` o ayer en UTC), exporta el CSV, lanza `data/pipelines/pipeline.py` sin argumentos (último mes cerrado) y hace las transiciones de estado.
- **`services/job_runner/`**: lógica de estado. `models.py` (tabla `job_runs`), `repository.py` (`create_run`, `mark_processing`, `finish_run`, `cancel_run`, `has_processing_lock`, `has_completed_for_date`, `recover_stale_runs`) y `migrations/001_create_job_runs.sql`, que el script ejecuta en Postgres al arrancar.
- **Disparador:** servicio `scheduler` en `docker-compose.yml` (`services/scheduler/Dockerfile` + `crontab`), con supercronic y `15 2 * * *` en UTC. No lleva `depends_on: api`.
- **Tests:** `tests/jobs/` en la raíz y no en `services/api/tests`, porque aquel `conftest` importa FastAPI y el job debe ser independiente de la API. Un test lo comprueba explícitamente (`fastapi`/`prefect` no aparecen en `sys.modules` al importar el script).

Decisiones que conviene no romper:

- **El lock es el estado `processing`**, pero hecho atómico con un índice único parcial (`uq_job_runs_single_processing`). Una consulta previa sola no basta: con dos procesos reales ambos vieron "libre" en el mismo segundo. Lo demostraron el test de dos procesos y la prueba contra Supabase.
- **El estado final empieza en `failed`** y solo la última línea del `try` lo cambia a `completed`. El `finally` lo escribe con una sesión nueva (`_finish_safely`). SIGTERM se convierte en `JobTerminatedError`, así que `docker stop` también termina en `failed`.
- **Fila perdedora de la carrera → `failed` con prefijo `Cancelada (sin trabajo):`** (`CANCELLED_PREFIX`), no borrada: queda rastro auditable y se puede filtrar de las alarmas. Si ya hay un `processing` antes de crear fila, no se crea ninguna.
- **Idempotencia por `(job_name, target_date)`**, comprobada otra vez ya con el lock tomado. `TARGET_DATE` debe ser un día cerrado: marcar `completed` un día a medias bloquearía para siempre su export completo.
- **Zombis:** `recover_stale_runs` pasa a `failed` las filas `pending`/`processing` de más de 3 h (`STALE_AFTER`, muy por encima del timeout de 30 min del subproceso). No es un segundo lock.
- **`job_runs` ≠ `reporting.pipeline_runs`:** cada una con su lock (por job y por mes). Si el pipeline encuentra su mes bloqueado, sale con 1 y el job queda `failed`.
- **El CSV es backup, no input.** Se escribe en `.tmp` y se renombra al final; `data/raw/telemetry_*.csv` está en `.gitignore` (lleva `userId` seudonimizados).

Gotchas reales:

- **El `.env` de la raíz (el que usa `docker compose`) apuntaba al proyecto antiguo de Supabase `eu-central-1`**, no a la base buena de `services/api/.env`. Como `load_dotenv` no sobreescribe variables ya presentes, los contenedores usaban la base equivocada. Corregido el 2026-09-13 con autorización del usuario. Si se rehace algún `.env`, comprobar que los dos apuntan al mismo proyecto (`eu-west-1`).
- **Build con contexto en la raíz del repo:** `services/scheduler/Dockerfile.dockerignore` (BuildKit lo prioriza sobre un `.dockerignore` de la raíz) limita el contexto a `requirements.txt` y al crontab. Sin él, Docker enviaría `node_modules`, `.venv` y `.git`.
- **Test de dos procesos:** las tablas se crean antes de lanzarlos, porque `create(checkfirst=True)` no es atómico y uno moría con "table already exists". Era un fallo del test, no del script.

### Cola de tareas asíncronas — `docs/async-tasks.md`

Ticket #DEV-55, rama `feat/async-task-queue` apilada sobre `feat/nightly-telemetry-export`, 2026-09-13. Endpoint convertido, elegido por el usuario: `POST /reporting/pipeline-runs` (recálculo del informe mensual, 3-11 s contra Supabase), que antes corría con `BackgroundTasks` dentro de FastAPI.

Mapa del código:
- **`services/celery_app.py`**: instancia compartida por API y worker (`REDIS_URL` como broker y backend, solo JSON, `acks_late`, `track_started`, `prefetch=1`, límites 15/20 min, `visibility_timeout` 2 h, eventos para Flower). Añade la raíz y `services/api` a `sys.path` y carga `services/api/.env`.
- **`services/tasks/`**: `pipeline_tasks.py` (`ObservableTask`, la tarea `reporting.run_monthly_clinic_supply_performance` y `enqueue_monthly_pipeline_run`), `dead_letters.py` (tabla `task_dead_letters`), `status.py` + `router.py` + `schemas.py` (`GET /tasks/{task_id}`) y `redaction.py`.
- **Docker:** `redis` (7.4, `noeviction` con `maxmemory 256mb`, `appendonly`, puertos solo en `127.0.0.1`), `worker` y `flower` (misma imagen `services/worker/Dockerfile`, contexto en la raíz). Ninguno lleva `restart:`. **`api` monta ahora `./services` y `./data` completos**: antes solo montaba `services/api`, así que `/reporting` ni se podía importar dentro del contenedor (roto desde la Parte 2 del pipeline sin que nadie lo notara, porque tests y dev nativo ven el repo entero).
- **Tests:** `services/api/tests/test_async_tasks.py` (tarea con `apply()`, el modo síncrono de Celery que sí ejecuta los reintentos, con el flow sustituido) y los del disparo en `test_business_pipeline.py`.

Decisiones que conviene no romper:
- **`max_retries=3` literal** (decisión del usuario): 4 ejecuciones antes de la DLQ, `attempt=4`. Backoff `retry_backoff=10` → 10/20/40 s con **`retry_jitter=False`**. El jitter por defecto de Celery sortea entre 0 y el tope y podía reintentar al instante.
- **La API comprueba, el worker bloquea** (decisión del usuario tras medir). Crear el lock en la petición costaba ~520 ms y publicar en Redis 2-4 ms. `reserve_monthly_run` hace una sola lectura (`run_log.find_blocking_run`, que ignora corridas caducadas con `run_log.is_stale`) con `AUTOCOMMIT` (psycopg2 manda `BEGIN` como viaje aparte: ~170 → ~113 ms). Después reserva un `uuid4` y encola. `start_pipeline_run` crea la fila con ese id si no existe. Resultado: 202 en ~120 ms en caliente. El índice único parcial sigue siendo el lock, así que si dos peticiones pasan a la vez la segunda tarea termina `cancelled`.
- **Cada intento tiene su propia fila en `pipeline_runs`:** el primero usa el `run_id` reservado y los reintentos pasan `run_id=None`. La fila fallida ya soltó el lock y otra corrida pudo tomarlo durante la espera. `_release_run_if_still_active` marca `failed` una fila que quedó `queued`/`running` si el flow murió antes de registrar su fallo.
- **Gotcha real de Prefect 3: `return Cancelled(...)` en un flow NO se devuelve, se lanza como `CancelledRun`** al llamar al flow directamente. El `if not isinstance(result, dict)` del CLI de `pipeline.py` nunca se ejecuta. La tarea captura `CancelledRun` y devuelve `success` con `{"status": "cancelled"}`. Sin eso, autoretry reintentaba 3 veces "mes ocupado" y lo mandaba a la DLQ. Lo detectó `test_task_is_cancelled_when_the_month_was_taken_after_the_api_check`.
- **`SoftTimeLimitExceeded` no se reintenta** (`dont_autoretry_for`) y va directo a la DLQ con `attempt=1`. El límite duro no llama a `on_failure`, así que no llegaría a la DLQ; el blando salta antes.
- **Mapeo de estados** (`status.py`): `RECEIVED`→`pending`; `RETRY`→`started` con el error del intento anterior en `error`; `REVOKED`/`REJECTED`/`IGNORED`→`failure`; desconocido→`pending`. Un `task_id` inexistente o con el resultado caducado (24 h) también da `pending`.
- **Gotcha real de privacidad: el logger interno `celery.app.trace` repite el error crudo** en "Retry in 20s: RuntimeError('… ana@x.com')" y en el traceback. `redaction.RedactingFilter` enmascara el mensaje y precalcula `record.exc_text` con el traceback ya limpio (`logging.Formatter` lo reutiliza). Hay test que lo fija.
- **`pipeline_tasks.py` no importa Prefect a nivel de módulo** (lo importa la API para encolar). `celery_app.py` sube `httpx` a WARNING, como `main.py`: sin eso, ~40 líneas `HTTP Request` por corrida en el log del worker.
- **Gotcha de verificación:** la TinyDB real no tiene ningún admin. Se verificó con un admin en una TinyDB temporal (`SUPPLIERS_DB_PATH`) y un token generado con `create_access_token`, sin login, para no escribir `login_succeeded` en Supabase. Para reproducir la DLQ sin tocar datos ni código: worker con `PREFECT_API_URL=http://127.0.0.1:9/api PREFECT_CLIENT_MAX_RETRIES=0`.
- **No verificado:** el build de `services/worker/Dockerfile` y `worker`/`flower` dentro de Docker (quedaban 2,8 GB de disco). `docker compose config` sí resuelve. Todo lo demás se verificó contra Supabase real: API/worker/Flower nativos + Redis en Docker, ciclo `started→success`, 409, API apagada con mensaje en cola, DLQ tras 4 intentos y capturas de Flower en `docs/async-tasks/`.

### Rendimiento frontend — `AUDIT.md` + `REPORT.md` + `audit/`

Auditoría de rendimiento de los dos frontends (rama `feat/frontend-performance-audit` sobre `main`, 2026-09-07, ciclo medir → analizar → corregir → volver a medir con Lighthouse 13.4.1). `AUDIT.md` tiene metodología, puntuaciones iniciales, causa raíz de cada problema y el análisis de duplicación; `REPORT.md` tiene las correcciones y su impacto medido; `audit/before/` y `audit/after/` los informes HTML y las capturas.

Decisiones y gotchas reales que conviene no volver a descubrir:

- **CLS de 0.974 en `/application` (el único KPI que estaba fuera de umbral): styled-jsx dentro de un Client Component.** En el App Router, el CSS de `<style jsx>` en un Client Component lo inyecta JavaScript **después de la hidratación** — el HTML del servidor llegaba sin una sola regla para `.application-card` y la sección de 1286 px se recolocaba entera. Movido a `app/application/application.css`, importado por la página: Next.js lo extrae en build y lo enlaza en el `<head>`. CLS 0.974 → 0, Performance 76 → 99. **Regla para el proyecto: el CSS crítico va en un `.css` importado por la ruta, nunca en `<style jsx>` de un Client Component.** Se usa archivo propio de la ruta y no `globals.css` para no enviar los estilos del formulario a quien solo visita la portada.
- **Next.js 16: `priority` de `next/image` está deprecado** (los docs locales recomiendan `loading="eager"` o `fetchPriority="high"` antes que `preload`). Trampa: `next/image` es `loading="lazy"` por defecto, así que `fetchPriority="high"` a solas **no** basta para el elemento LCP — hace falta también `loading="eager"`. Con ambos, el "element render delay" del hero cayó de 792 ms a 22 ms.
- **El optimizador de imágenes integrado está desactivado a propósito en `uis/website`** (`images.loader: "custom"` + `image-loader.js`). Las fotos vienen de `images.unsplash.com`, que ya es un CDN de imágenes; pasar por `/_next/image` añadía el rodeo navegador → nuestro servidor → Unsplash → nuestro servidor y subió la descarga del LCP de 106 ms a 241 ms pese a bajar los bytes. Con el loader propio: 101 KB y 89 ms. Con loader propio `remotePatterns` deja de aplicarse — retirado para no dejar config muerta.
- **`next/font` con `preload: false` se probó y se revirtió**: el LCP no mejoró y empeoraron FCP (0.8→1.1 s), Speed Index (0.8→1.1 s) y CLS (0→0.015). La medición está en un comentario de `uis/website/app/layout.tsx` para que nadie repita la prueba.
- **Bug latente corregido**: `uis/website/app/globals.css` pedía `font-family: var(--font-manrope)` pero nadie definía esa variable — la web llevaba desde su migración a React cayendo al sans-serif del sistema. Resuelto con `next/font/google` en `layout.tsx`.
- **`app/icon.svg` en ambas apps**: sin él, cada carga daba un 404 de `/favicon.ico` que hacía fallar `errors-in-console` y mantenía Best Practices en 96. 370 bytes de SVG lo subieron a 100 en las cinco mediciones.
- **Metodología de medición** (si se vuelve a medir, hacerlo igual o los números no son comparables): siempre sobre `next build` + `next start`, **nunca** `next dev`; ejecuciones de una en una (en paralelo el Speed Index varía hasta 1.2 s); precalentar las URLs de Unsplash antes de medir (una variante de ancho nueva tarda ~650 ms la primera vez y ~50-90 ms cacheada). Todas las cifras publicadas se confirmaron con 3 pasadas idénticas.
- **Las vistas autenticadas del backoffice no son auditables con Lighthouse**: la sesión es un JWT en `localStorage` y `AuthGuard` redirige a `/login`, así que un Chrome limpio acaba midiendo el login. Se audita `/login`, que sí ejercita todo el JS compartido del layout (`ErrorTracking`, `WebVitals`, `PageViewTracker`, `AuthGuard`) montado en todas las rutas.

Refactorización extraída en el mismo hito (`uis/backoffice`):

- **`hooks/useAsyncData.ts`** es ahora el único sitio donde vive el ciclo cargando/éxito/error. Lo usan `/incidents`, `/incidents/summary`, `/inventory/products` y `/inventory/orders`, que antes copiaban el patrón carácter por carácter. Su `fetcher` **debe** venir envuelto en `useCallback` por quien llama: es dependencia del efecto y una función nueva por render provoca un bucle de peticiones. Lleva guarda de condición de carrera (`requestIdRef`) que corrige un bug real que ninguna de las 4 copias tenía: en `/incidents`, cambiar dos filtros seguidos dejaba dos peticiones en vuelo y la lenta podía pisar a la reciente, mostrando resultados que no correspondían al filtro.
- **`components/AsyncSection.tsx`** renderiza los estados cargando / error+reintento / vacío, y solo pinta `children` cuando hay algo que mostrar. El botón "Reintentar" deja de ser opcional, lo que hace cumplir por construcción la regla de la auditoría de errores del proyecto.
- **Tests de hooks y componentes sin `@testing-library`**: `act` de `react` (React 19 lo exporta) + `createRoot` de `react-dom/client` en jsdom, con `globalThis.IS_REACT_ACT_ENVIRONMENT = true`. Ver `__tests__/useAsyncData.test.tsx` y `__tests__/asyncSection.test.tsx`. Se evitó añadir una dependencia nueva.

### Compartido y legado

- `packages/shared/incidents_validation/`: paquete Python compartido (antes en `shared/incidents_analysis/`). `csv_analysis.py` valida/analiza el CSV legacy (`clean_row`, `validate_row`, `analyze_rows`); `incident_rules.py` define los valores permitidos y transiciones del modelo Incident. Lo consumen la API, `scripts/analyze.py` y `scripts/seed_incidents.py` — cualquier regla nueva de incidencias va aquí, no duplicada.
- `packages/shared/`: también contiene el paquete TS `@repo/shared-types` (scaffold sin usar, ver nota abajo).
- `packages/shared/business-logic/`: utilidades TypeScript del hito 2 (validaciones, transformaciones, búsqueda) — antes vivían en `src/` en la raíz, movidas aquí el 2026-08-10 al dockerizar (una carpeta suelta en la raíz, alcanzada por rutas relativas `../../../src` desde `uis/backoffice`, no encajaba en la estructura). Sigue siendo un import por ruta relativa (`../../../packages/shared/business-logic/...` desde `uis/backoffice/lib/businessMetrics.ts` y sus tests), no un paquete npm instalado — ver mejora futura abajo. Ojo: hay artefactos compilados (`.js`, `.d.ts`, `.map`) versionados junto al fuente; editar siempre el `.ts`.
- **Mejora futura documentada, no implementada**: convertir `packages/shared/business-logic` (y el scaffold `@repo/shared-types`) en paquetes npm reales vía **npm workspaces** de raíz, con `uis/backoffice` dependiendo de `@repo/business-logic` y consumiéndolo vía `transpilePackages` en `next.config.ts` en vez de una ruta relativa. Eliminaría también el hack `experimental.externalDir`/`turbopack.root` de `uis/backoffice/next.config.ts`. Se descartó para esta clase porque implica consolidar los `package-lock.json` (hoy cada app tiene el suyo independiente) — cambio de mayor alcance y riesgo que lo que pedía el ticket del día.
- `index.html`, `application.html`, `validation.js`, `server.py` en la raíz: versión estática original del hito 1 (previa a la migración a `uis/website`); no es el frontend activo.
- `skills/` y `workflows/`: plantillas y documentación del template del curso.

## Convenciones

- **Gestión de errores (estrategia transversal, auditada en el hito de error handling)**:
  - Frontends: los mensajes al usuario se generan centralmente en `uis/backoffice/services/http.ts` y `apps/talent-pipeline-tracker/services/api.ts` — nunca mostrar códigos de estado, stack traces ni JSON crudo; fallos de red y de parseo JSON capturados ahí mismo. Errores por campo viajan como `ApiFieldError` (soporta el formato propio `detail.errors` y el 422 de FastAPI). Toda carga async con tres estados (cargando/éxito/error), `finally` para limpiar loading, y todo estado de error con CTA (reintentar/volver). Prohibido `console.log`/`console.error` con datos de usuarios.
  - Backend: try/except acotados por operación, errores como `HTTPException` con JSON limpio, handler global de excepciones en `main.py` (500 genérico). Nada sensible en respuestas. **Tampoco en logs**: nunca emails ni otros datos de usuario. Ojo con `logger.exception` sobre errores de proveedores externos: el traceback repite el mensaje crudo, que puede traer direcciones (p. ej. Resend: "You can only send testing emails to your own email address (…)"). Patrón en `email_service.py`: `logger.error` con el tipo de excepción y el motivo pasado por `_redact_emails`, que conserva el diagnóstico ("API key is invalid") y enmascara cualquier dirección como `<email>`. Fijado en `tests/test_email_service.py` (3 de sus 4 tests fallan con el log anterior, que incluía el destinatario).
  - Scripts Python: errores a `stderr` y `sys.exit(1)`/retorno ≠ 0 en fallo crítico; I/O y parseo CSV protegidos.
- Documentación y mensajes del proyecto en español; código e identificadores en inglés.
- Commits estilo `feat(auth): ...` / `chore(api): ...`, un commit por feature, con PR por rama `feature/*` o `hito-*`.
- TypeScript: evitar `any`; la deuda existente se está migrando a `unknown` + narrowing (prioridad activa en `memory-bank/progress.md`).
