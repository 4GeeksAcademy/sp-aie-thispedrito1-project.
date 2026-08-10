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
# Backend (56 tests): desde services/api, con el venv activado
python -m pytest            # o: uv run pytest (en Codespaces)
python -m pytest --cov      # cobertura: auth ≥70%, backoffice ≥60%, total ~81%

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

### Frontends

- `uis/website`: web corporativa pública (hito 1 migrado a React); contenido en `data/content.ts`, componentes de sección en `components/`.
- `uis/backoffice`: backoffice de proveedores + auth completa (login, registro, forgot/reset password, perfil, change password) contra `services/api`. Capa de servicios en `services/` (`http.ts`, `authApi.ts`, `suppliersApi.ts`, `incidentsApi.ts`, `inventoryApi.ts`, `session.ts`) y tipos en `types/`. Incluye el gestor de incidencias (`/incidents` listado con filtros y cambio de estado con revert, `/incidents/new` formulario con aviso obligatorio de no introducir datos de pacientes, `/incidents/summary` métricas). Interfaz íntegramente en español (traducida el 2026-08-09 por decisión explícita del usuario; el CONTEXT original de ese hito pedía inglés para Incidents — tenerlo en cuenta si se vuelve a evaluar contra esa rúbrica). Los mensajes de error que llegan literalmente de la API (p. ej. `"Insufficient stock for supply..."`) no se traducen: son el contrato de texto exacto que exige el CONTEXT del backend. `http.ts` lanza `ApiFieldError` cuando la API responde 400 con errores por campo.
- Tema visual "Supply Manifest console" (`app/globals.css`): oscuro y denso por defecto (acento ámbar `#FF8A3D`, LEDs cuadrados para nivel de stock, tipografía monoespaciada para SKUs/IDs/cantidades), con **toggle real a modo claro** (`components/ThemeToggle.tsx`, atributo `data-theme` en `<html>`, persistido en `localStorage`, sin flash gracias a un script inline en el `<head>` — por eso `<html>` lleva `suppressHydrationWarning`). Todo color vive como token CSS (`--bg`, `--panel`, `--text`, `--muted`, `--brand`, `--line`, `--critical`/`--warning`/`--ok`, `--success-*`/`--error-*`); nunca hardcodear un hex en un `.tsx` del backoffice.
- Interfaz de inventario (Hito 5 backoffice, `app/inventory/`): 4 pantallas sobre la API de `/inventory` — `products` (lista con `current_stock` y LED de color por nivel, umbrales en `types/inventory.ts::getStockLevel`), `orders/inbound` y `orders/outbound` (formularios; el de salida muestra el stock del producto seleccionado en tiempo real, tomándolo de la lista ya cargada — no hace una petición extra — y bloquea el envío si la cantidad supera el stock, antes de tocar la API) y `orders` (historial de solo lectura, entradas/salidas con badge). Sin variable de entorno nueva: reutiliza `NEXT_PUBLIC_API_URL`/proxy de `http.ts`, el mismo backend que proveedores e incidencias. Rutas sin prefijo `/backoffice` (la app ya es el backoffice), a diferencia de lo sugerido en el README del hito. `clinic_id` es un número simple (1-12): el CONTEXT no da un catálogo de nombres de clínica para este módulo.
- `apps/talent-pipeline-tracker`: tracker de candidatos (hito 3/4) contra API externa; integración centralizada en `services/api.ts`, contratos en `types/tracker.ts`, con normalización defensiva de payloads (campos heterogéneos tipo `stage`/`step`, manejo explícito de 422) y fallback de datos en el dashboard.
- Patrón común: App Router con componentes cliente para interactividad, `AuthGuard` para rutas protegidas, sesión en `services/session.ts`.
- `uis/web/index.html`: UI estática servida por el backend en `/`.

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
