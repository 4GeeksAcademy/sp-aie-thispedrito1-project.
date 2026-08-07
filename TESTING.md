# TESTING.md — Plan y guía de pruebas

Batería de pruebas unitarias del monorepo HealthCore (ticket AUTH-088, más los
tickets de backlog API-042 y FE-019). Las pruebas afirman **lógica de negocio**
(qué decide cada endpoint o función), no serialización HTTP ni internos del
framework.

## Cómo ejecutar las pruebas

### Backend (pytest)

Desde `services/api`:

```bash
# Con el virtualenv local (macOS de desarrollo)
source .venv/bin/activate
python -m pytest                 # toda la batería
python -m pytest --cov           # con informe de cobertura
python -m pytest tests/test_login.py -k happy   # un archivo o un test concreto

# Con uv (Codespaces / entornos con uv instalado)
uv sync
uv run pytest
uv run pytest --cov
```

Las pruebas usan una **base de datos TinyDB temporal** (variable
`SUPPLIERS_DB_PATH` apuntando a un archivo desechable) y un **JWT_SECRET_KEY de
test** — nunca tocan `data/suppliers.db.json` ni requieren `.env`. El envío de
email de recuperación se sustituye por un doble (stub) que captura el token.

### Frontend (Jest)

Desde `uis/backoffice`:

```bash
npm install          # primera vez
npm test             # toda la suite
npx jest --coverage  # con informe de cobertura
```

## Plan de pruebas

Plan definido **antes** de escribir los tests. Cada endpoint cubre los tres
niveles exigidos: camino feliz, caso límite y modo de fallo.

### AUTH-088 — API de autenticación (`services/api`)

**`POST /users` (registro) — `tests/test_register.py`**

| Caso | Tipo | Resultado esperado |
| --- | --- | --- |
| Registro con email y contraseña válidos | Feliz | 201, usuario con rol `user`, activo, con perfil creado |
| Email duplicado (mismo email dos veces) | Límite | 409, el segundo registro no crea usuario |
| Email con mayúsculas/espacios se normaliza | Límite | El login posterior con el email normalizado funciona |
| Contraseña de 7 caracteres (mínimo es 8) | Fallo | 422, no se crea el usuario |
| Email con formato inválido | Fallo | 422 |
| La respuesta nunca incluye la contraseña ni el hash | Transversal | Campos sensibles ausentes del JSON |

**`POST /auth/login` y `POST /auth/token` — `tests/test_login.py`**

| Caso | Tipo | Resultado esperado |
| --- | --- | --- |
| Credenciales correctas | Feliz | 200, `access_token` no vacío, `token_type: bearer` |
| `/auth/token` (flujo OAuth2 de Swagger) con credenciales correctas | Feliz | 200 con token |
| Email registrado con contraseña incorrecta | Fallo | 401, mismo mensaje que usuario inexistente |
| Email no registrado | Fallo | 401 |
| El mensaje de error no revela si el email existe (anti-enumeración) | Límite | Mensajes idénticos en ambos fallos |

**`GET /auth/me` (validación de token) — `tests/test_token.py`**

| Caso | Tipo | Resultado esperado |
| --- | --- | --- |
| Token recién emitido | Feliz | 200 con id, email, rol y perfil del usuario |
| **Token expirado** (la regresión del ticket) | Fallo | 401, nunca 200 |
| Token malformado (no es un JWT) | Fallo | 401 |
| Token firmado con otra clave | Fallo | 401 |
| Sin cabecera Authorization | Fallo | 401 |
| Token válido de un usuario borrado | Límite | 401 (el token solo vale si el usuario sigue existiendo y activo) |

**`POST /auth/forgot-password`, `/auth/reset-password`, `/auth/change-password` — `tests/test_password_reset.py`**

| Caso | Tipo | Resultado esperado |
| --- | --- | --- |
| Flujo completo: forgot → email capturado → reset → login con la nueva | Feliz | 200 en cada paso; la contraseña vieja deja de funcionar |
| Forgot con email no registrado | Límite | 200 con el MISMO mensaje (anti-enumeración) y sin enviar email |
| Reusar un token de reset ya consumido | Fallo | 400 (single-use) |
| Token de reset malformado | Fallo | 400 |
| Un access token normal no sirve como token de reset | Fallo | 400 (valida el claim `type=reset`) |
| Change-password con contraseña actual correcta | Feliz | 200 y la nueva contraseña funciona |
| Change-password con contraseña actual incorrecta | Fallo | 400 y la contraseña no cambia |
| Change-password sin autenticación | Fallo | 401 |

### API-042 (extra) — Endpoints del backoffice

**Suppliers — `tests/test_suppliers.py`**

| Caso | Tipo | Resultado esperado |
| --- | --- | --- |
| Crear proveedor válido (USA + USD) | Feliz | 201 con id y `updated_at` |
| Regla de negocio país/moneda: USA con GBP | Límite | 422, no se crea |
| Categoría fuera del catálogo | Fallo | 422 |
| Listar con filtro por país | Feliz | Solo proveedores de ese país |
| Detalle de id inexistente | Fallo | 404 |
| Actualizar tarifa y borrar | Feliz | 200 con nueva tarifa / 204 y desaparece |
| Cualquier endpoint sin token | Fallo | 401 |

**Incidents — `tests/test_incidents.py`**

| Caso | Tipo | Resultado esperado |
| --- | --- | --- |
| Crear incidencia válida | Feliz | 201 con `created_at`/`updated_at` y estado inicial |
| Campos obligatorios ausentes | Fallo | 400 con `errors[{field, message}]` señalando cada campo |
| Categoría/sede fuera de catálogo | Fallo | 400 señalando el campo |
| Filtro de listado con valor inválido | Fallo | 400 |
| Transición válida open → in_progress → resolved | Feliz | 200 en cada paso |
| Transición inválida (open → resolved directo) | Fallo | 400 |
| Estado final no se puede cambiar (resolved → open) | Fallo | 400 con mensaje de estado final |
| Summary con base de datos vacía | Límite | 200 con todos los conteos a 0 (no rompe) |
| Detalle de id inexistente | Fallo | 404 |

### FE-019 (extra) + utilidades de auth — Frontend (Jest, `uis/backoffice/__tests__/`)

| Función | Feliz | Fallo |
| --- | --- | --- |
| `isTokenValid` (session.ts) | JWT con `exp` futuro → `true` | JWT expirado → `false`; cadena malformada → `false`; `null` → `false` |
| `getAuthToken`/`setAuthToken`/`clearAuthToken` (session.ts) | Guarda y recupera el token en localStorage | Tras `clearAuthToken` no queda sesión válida |
| `validateClaim` (src/utils/validations.ts) | Claim correcto → `valid: true` | Monto ≤ 0, sede desconocida y denegada sin motivo → errores concretos |
| `calculateDenialRate` (src/utils/transformations.ts) | 1 denegada de 4 → 25.0 | Lista vacía → lanza error (documentado) |

## Resultados de cobertura

### Backend — `python -m pytest --cov` (49 tests, todos en verde)

| Módulo | Cobertura | Umbral |
| --- | --- | --- |
| `routes/auth.py` | 95% | ≥70% (AUTH-088) ✅ |
| `security.py` | 91% | ≥70% (AUTH-088) ✅ |
| `models.py` | 97% | ≥70% (AUTH-088) ✅ |
| `routes/users.py` | 83% | ≥70% (AUTH-088) ✅ |
| `auth_repository.py` | 75% | ≥70% (AUTH-088) ✅ |
| `routes/incidents.py` | 91% | ≥60% (API-042) ✅ |
| `routes/suppliers.py` | 82% | ≥60% (API-042) ✅ |
| `repository.py` (suppliers) | 77% | ≥60% (API-042) ✅ |
| `incident_repository.py` | 76% | ≥60% (API-042) ✅ |
| **TOTAL del servicio** | **81%** | — |

Módulos con cobertura baja y por qué se aceptan: `email_service.py` (47%) es
integración con Resend — se sustituye por un doble en los tests, y probar el
render del HTML no afirma lógica de negocio; `main.py` (56%) contiene el
endpoint legacy del analizador de CSV, fuera del alcance de estos tickets.

### Frontend — `npx jest --coverage` (13 tests, todos en verde)

| Suite | Qué cubre | Resultado |
| --- | --- | --- |
| `__tests__/session.test.ts` | `services/session.ts` (validación JWT + almacenamiento) | 96.7% de líneas |
| `__tests__/businessLogic.test.ts` | `validateClaim` y `calculateDenialRate` de `src/utils` | 5 tests en verde |

## Hallazgos y flujo asistido por IA

- **Bug real encontrado al montar la infraestructura**: `services/api/pyproject.toml`
  no declaraba la dependencia `resend` (la API la importa desde el flujo AUTH-03).
  En un entorno limpio con uv, `uv sync && uv run pytest` habría fallado al
  importar la app. Corregido añadiendo `resend==2.34.0` a `dependencies`.
- **Casos límite identificados con ayuda del agente de IA** (no estaban en el
  plan inicial obvio):
  - Un access token de sesión válido **no** debe servir como token de reset
    (verifica el claim `type=reset`) — `test_access_token_cannot_be_used_as_reset_token`.
  - Un token firmado y vigente de un **usuario borrado** debe rechazarse —
    `test_valid_token_of_deleted_user_is_rejected`.
  - Los dos fallos de login (contraseña mala / email inexistente) deben devolver
    el **mismo mensaje** para no permitir enumerar usuarios —
    `test_login_error_does_not_reveal_which_field_failed`.
  - Un usuario normal no puede **autoproclamarse admin** vía `PUT /users/{id}` —
    `test_regular_user_cannot_change_roles`.
- **Comportamientos documentados por la batería** (no son bugs, pero ahora están
  fijados por tests): `isTokenValid` acepta tokens sin claim `exp`, y
  `calculateDenialRate` lanza excepción con lista vacía (preferible a devolver
  un 0% engañoso) — cualquier cambio futuro que los altere hará saltar un test.
- Los 62 tests pasan contra el código actual: la batería no destapó regresiones,
  lo cual valida además el trabajo de manejo de errores del hito anterior.
