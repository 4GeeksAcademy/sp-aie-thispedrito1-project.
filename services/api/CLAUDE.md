# services/api — backend FastAPI

Monolito FastAPI. Dos bases: **TinyDB** (`data/suppliers.db.json`, ruta en `SUPPLIERS_DB_PATH`; usuarios, perfiles, proveedores, incidencias, roles, vínculos federados) y **Supabase** vía SQLModel (inventario, telemetría y todo lo de los módulos de `services/*`). Detalle de seguridad en `docs/security/`; serialización en `docs/serialization-audit.md`.

## Comandos

```bash
cd services/api && source .venv/bin/activate
cp .env.example .env              # JWT_SECRET_KEY real + DATABASE_URL (pooler de Supabase)
python seed.py && python seed_inventory.py
uvicorn main:app --reload --port 8000          # Swagger en /docs
python -m pytest -o addopts=""                 # pyproject pone -q y oculta el resumen
python -m pytest --cov                         # auth ≥70 %, total ~77 % (sin fail-under)
```

Semillas de demo por módulo (siempre con `SUPPLIERS_DB_PATH` apuntando a su TinyDB): `scripts/seed_roles_permissions.py` → `data/roles_permissions/` (contraseña aleatoria o `ROLES_SEED_PASSWORD`; la imprime), `scripts/seed_federated_auth.py` → `data/federated_auth/` (arrancar con `MICROSOFT_IDP_MODE=mock`), `scripts/seed_audit_log.py` → `data/audit_log/`.

## Mapa

- `main.py`: app, CORS, routers, handler global (500 genérico, nunca stack traces), `timing_middleware`, startup no fatal (si falta `DATABASE_URL` o Supabase no responde, solo avisa por stderr). Sube `httpx` a WARNING.
- `routes/`: un router por dominio. `security.py`: JWT HS256 (python-jose), bcrypt (passlib), `get_current_user`/`user_from_token`. `auth_repository.py`/`repository.py`: repositorios sobre TinyDB. `database.py`: `get_inventory_db`, `get_inventory_db_optional`, `LockedJSONStorage`.
- Incidencias: `routes/incidents.py` + `incident_repository.py`; validación con `packages/shared/incidents_validation` (no Pydantic) → 400 `{"errors":[{field,message}]}`. `GET /summary` va declarado antes de `GET /{id}`.
- Inventario: `routes/inventory.py` + `inventory_repository.py` + `inventory_models.py`.
- Telemetría: `routes/telemetry.py`, `telemetry_service.py`, `telemetry_repository.py`, `telemetry_models.py`; análisis en `services/telemetry/analysis.py`.
- Roles y permisos: `access_models.py`, `access_repository.py`, `permissions.py`, `routes/access.py`, `routes/internal_reports.py`.
- Federada: `federated_config.py`, `federated_providers.py`, `federated_repository.py`, `federated_rules.py`, `routes/federated.py`, `routes/mock_microsoft.py`.
- Transversal: `cache.py` (`TTLCache`), `rate_limit.py`, `audit_log.py` (logger `healthcore.audit`), `email_service.py` (Resend).

## Tests

`tests/conftest.py` fija una TinyDB temporal y un secreto JWT **antes** de importar la app; nunca toca `data/suppliers.db.json` ni necesita `.env`. La fixture `client` sustituye `get_inventory_db` por SQLite en memoria (`StaticPool`). `clean_db` (autouse) hace `cache.clear()`: la caché es un singleton de proceso y los tests que resetean la base sin pasar por los endpoints recibirían datos de otro test. Fixtures útiles: `admin_headers`, `registered_user` (Ana Pérez, supervisor de `patient_experience`, aprovisionada por repositorio con `provision_user`/`login_headers`), `service_account`, `offline_agent_harness`. `EmailStr` rechaza el dominio `.test`. Tests rápidos: un hash bcrypt por módulo y tokens con `create_access_token` (sin login). Toda feature nueva añade casos feliz/límite/fallo.

## Reglas que no hay que romper

**Datos y contratos**
- `User`/`Profile` solo en TinyDB; `user_id` se reutiliza como `user_uuid` en otros módulos. `user_uuid` se mantiene en inventario a propósito (rastro de auditoría; los usuarios no están en Supabase).
- `current_stock` siempre calculado (`SUM(entregas) - SUM(consumos)`), nunca una columna. Un consumo que dejaría stock negativo → 400 antes de escribir. `get_current_stock` suma toda la red; por clínica es `get_current_stock_for_clinic` (no cambiar el significado de la primera). `PATCH /inventory/products/{id}/stock` responde siempre 400 (el stock no se edita).
- `country` de inventario es `US/UK` (`SupplyCountry`); el de proveedores es `USA/UK` (`Country`). No reutilizar enums sin mirar el CONTEXT.
- `VALID_SUPPLY_CATEGORIES` no coincide con el CONTEXT: brecha conocida, no resuelta a propósito.
- Los errores literales de la API (`"Insufficient stock for supply..."`) no se traducen: son contrato y el frontend hace `includes("stock")`.

**Serialización**
- Un modelo, varios contratos: detalle / listado / escritura no se reutilizan (`UserPublic`/`UserListItem`/`UserRegistered`, `Supplier`/`SupplierListItem`, `IncidentRead`/`IncidentListItem`). El listado lo define el consumidor (qué lee la pantalla).
- Respuestas no JSON (`GET /` HTML, export CSV) llevan `response_class` + `responses`, no `response_model`. Los DELETE responden 204.
- Los flujos de auth no autenticados no devuelven email; `/auth/me` sí.
- `tests/test_serialization.py` recorre el OpenAPI completo: cualquier ruta nueva que filtre `hashed_password` o devuelva JSON sin esquema falla sola.

**Auth y sesiones**
- Tokens de sesión con `type: "access"`; `user_from_token` rechaza otros tipos (sin `type` = sesión antigua, aceptada). Reset: JWT `type=reset` + `jti` de un solo uso en `password_resets`. `/auth/forgot-password` siempre 200 (anti-enumeración).
- Rol y departamento se releen de la base en cada petición; el `role` del JWT es informativo.
- Cuentas sin contraseña (`hashed_password` null): `authenticate_user` → None; `/auth/change-password` → 400.
- `GET /users` y `POST /users` solo admin; `GET /users/{id}` con `ensure_self_or_admin`; `GET /telemetry/report` exige sesión.

**Roles y permisos** (`docs/roles-permissions/roles-permissions.md`)
- Dos ejes independientes, en este orden: rol (`require_capability`, 403 `insufficient_role`) y departamento (`ensure_in_scope`/`department_scope`, 403 `department_out_of_scope`/`department_required`). Solo en `permissions.py`.
- Ninguna ruta compara rol/departamento a mano (test con `ast`); cada endpoint sensible lleva su `require_*` (test que recorre `app.routes`).
- `users.role_id` y `users.department_id` separados; `role` antiguo en texto se traduce al leer (`user`→`employee`, `manager`→`supervisor`; alias en el enum para los scripts viejos).
- Incidencias por categoría: `patient_experience`→`patient_experience`, `billing_error`→`revenue_cycle`, `other`→`clinical_operations`. El resumen cachea por alcance (`incidents_summary:<depto|all>`), escrituras con `invalidate_prefix`.
- `PUT /users/{id}` es `extra="forbid"`. Bajar al último admin → 409. Rango de los 3 roles base inmutable → 409. Informes internos pasan por `phi_guard.scan`.
- Cuenta de servicio MCP = supervisor de `patient_experience` (residual: el agente no ve incidencias de facturación ni "otros").

**Federada con Microsoft** (`docs/federated-auth/federated-auth.md`)
- El login federado **nunca crea cuentas** ni busca por email: solo entra a cuentas que su dueño vinculó desde el perfil con sesión de contraseña.
- El propósito (`login`/`link`) viaja con el `state` en el servidor (huella SHA-256, un uso, 10 min), nunca desde el navegador.
- `provisioned_by_it` ausente = no. Sin tokens de Microsoft en disco (test busca `eyJ` en la TinyDB). Desvincular el último método → 409 `last_access_method`.
- Errores como `{"detail": {"code","message"}}`; el texto en español lo pone el backoffice. `routes/mock_microsoft.py` da 404 fuera de modo `mock` y valida `redirect_uri` como Entra.

**Seguridad transversal**
- CORS por lista (`CORS_ALLOWED_ORIGINS`); `parse_cors_origins` impide arrancar con `*`.
- Rate limit de LLM (`rate_limit.LLM_LIMITER`, 10/60 s por usuario, en memoria; `LLM_RATE_LIMIT_MAX_REQUESTS`/`LLM_RATE_LIMIT_WINDOW_S`): un cupo compartido por `/agent/query`, `/knowledge/query` y el chat WS. Cuentan las bloqueadas por guardarraíl; no las rechazadas por el propio límite. Tests: `monkeypatch` de `rate_limit.LLM_LIMITER`.
- Cerrar una incidencia (`resolved`/`discarded`) exige `"confirm": true`, validado después de la transición.
- `audit_log.audit_event`: campos en lista cerrada (`ALLOWED_FIELDS`), actor en seudónimo SHA-256.
- Nada sensible en logs: nunca emails. Errores de proveedores externos con `logger.error` + `_redact_emails` (patrón de `email_service.py`), nunca `logger.exception` con el mensaje crudo.

**Caché** (`CACHING_REPORT.md`)
- `GET /inventory/products` (30 s) y `/api/incidents/summary` (60 s); cada escritura invalida su clave, nunca solo TTL. La validación de stock no pasa por caché.

**Telemetría** (`docs/telemetry/`)
- `POST /telemetry/events` valida cada evento por separado (`model_validate` en `try/except`, también el parseo de `timestamp`): un evento malo no tumba el lote. Body como `dict`, nunca `list[TelemetryEvent]`.
- `tags` = `properties` + `eventId`/`sessionId`/`userId`/`requestId`/`schemaVersion`. No deduplica: los consumidores deduplican por `tags->>'eventId'`.
- Se persisten `login_*` (vía `get_inventory_db_optional`, que degrada a None), `stock_threshold_triggered` (solo al cruzar el umbral) y `supply_expiry_flagged` (`inventory_alerts.py`, una vez por lote con `uuid5`). `api_error_response` y el resto de eventos backend solo se loguean.
- `login_failed` lleva el identificador como HMAC-SHA256 y no distingue cuenta inexistente de contraseña mala.
- `incident_created` no lleva `title`/`description` (PHI en texto libre).
- `GET /telemetry/report`: la clave de caché es lo que pidió el cliente (`_report_cache_key`, `default` si no hay fecha), no el período resuelto con microsegundos. Residual: `?start_date=hola` da 500.

## Gotchas

- **TinyDB entre hilos**: sin `LockedJSONStorage`, peticiones paralelas corrompían la lectura (`JSONDecodeError`). Con varios workers de uvicorn seguiría habiendo carreras entre procesos.
- Scripts sueltos que necesiten `DATABASE_URL` cargan `.env` vía `database.py`.
- Verificar a mano con tokens sin login: cargar `services/api/.env` antes (si no, se firma con otro secreto → 401) y usar como `subject` el UUID del usuario, no la clave "1" de TinyDB. El hash se crea con `hash_password` (no existe `get_password_hash`).
- La TinyDB versionada no tiene admin ni incidencias: verificar con una TinyDB temporal.
