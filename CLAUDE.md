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

### Tests (ver TESTING.md en la raíz para el plan completo)

```bash
# Backend (74 tests): desde services/api, con el venv activado
python -m pytest            # o: uv run pytest (en Codespaces)
python -m pytest --cov      # cobertura: auth ≥70%, backoffice ≥60%, total ~77% (bajó de ~81% al sumar telemetría: rutas de startup con Supabase real, dificiles de cubrir sin conexión — no hay --cov-fail-under que lo bloquee)

# Frontend (19 tests): desde uis/backoffice
npm test                    # o: npx jest --coverage
```

Los tests de pytest usan una TinyDB temporal (`SUPPLIERS_DB_PATH`) y un secreto JWT de test definidos en `tests/conftest.py` **antes** de importar la app — nunca tocan `data/suppliers.db.json` ni requieren `.env`. El email de reset se sustituye con `monkeypatch`. Los tests de inventario (`tests/test_inventory.py`) siguen el mismo principio pero con Supabase: la fixture `client` de `conftest.py` sobreescribe `get_inventory_db` con una SQLite en memoria (`StaticPool`) creada y destruida por test — nunca tocan la base de Supabase real ni requieren `DATABASE_URL`. Los tests de Jest priorizan `.ts` sobre los artefactos `.js` compilados (`moduleFileExtensions` en `jest.config.js`). Toda feature nueva debe añadir sus casos (feliz/límite/fallo) a la batería y mantener los umbrales de cobertura.

### Docker (desarrollo contenedorizado)

```bash
cp .env.example .env   # o copia tus valores reales de services/api/.env + añade BACKEND_API_URL
docker compose up --build
```

Levanta dos servicios en la red `healthcore-net`: `api` (FastAPI, puerto 8000) y `uis` (un solo contenedor Node que arranca `website` en 3000 y `backoffice` en 3001 vía `uis/start.sh`). `uis/backoffice` habla con `api` por nombre de servicio Docker (`BACKEND_API_URL=http://api:8000`, consumido por el rewrite proxy de `next.config.ts`), nunca por `localhost`. Los `Dockerfile` viven en `uis/` y `services/api/` (no en `services/` — ahí solo hay un CSV); `docker-compose.yml` va en la raíz.

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

### Frontends

- `uis/website`: web corporativa pública (hito 1 migrado a React); contenido en `data/content.ts`, componentes de sección en `components/`.
- `uis/backoffice`: backoffice de proveedores + auth completa (login, registro, forgot/reset password, perfil, change password) contra `services/api`. Capa de servicios en `services/` (`http.ts`, `authApi.ts`, `suppliersApi.ts`, `incidentsApi.ts`, `inventoryApi.ts`, `session.ts`) y tipos en `types/`. Incluye el gestor de incidencias (`/incidents` listado con filtros y cambio de estado con revert, `/incidents/new` formulario con aviso obligatorio de no introducir datos de pacientes, `/incidents/summary` métricas). Interfaz íntegramente en español (traducida el 2026-08-09 por decisión explícita del usuario; el CONTEXT original de ese hito pedía inglés para Incidents — tenerlo en cuenta si se vuelve a evaluar contra esa rúbrica). Los mensajes de error que llegan literalmente de la API (p. ej. `"Insufficient stock for supply..."`) no se traducen: son el contrato de texto exacto que exige el CONTEXT del backend. `http.ts` lanza `ApiFieldError` cuando la API responde 400 con errores por campo.
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
  - Backend: try/except acotados por operación, errores como `HTTPException` con JSON limpio, handler global de excepciones en `main.py` (500 genérico). Nada sensible en respuestas.
  - Scripts Python: errores a `stderr` y `sys.exit(1)`/retorno ≠ 0 en fallo crítico; I/O y parseo CSV protegidos.
- Documentación y mensajes del proyecto en español; código e identificadores en inglés.
- Commits estilo `feat(auth): ...` / `chore(api): ...`, un commit por feature, con PR por rama `feature/*` o `hito-*`.
- TypeScript: evitar `any`; la deuda existente se está migrando a `unknown` + narrowing (prioridad activa en `memory-bank/progress.md`).
