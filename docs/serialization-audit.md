# Auditoría de serialización del backend — HealthCore API

**Fecha:** 2026-09-07
**Rama:** `feat/serialization-audit`
**Alcance:** `services/api` — la aplicación FastAPI completa, 37 endpoints.
**Consumidores considerados:** `uis/backoffice` (backoffice de operaciones), `uis/web` (UI estática servida por el propio backend), y los scripts de `scripts/`.

---

## 1. Metodología

La pregunta que guía cada decisión no es *"qué campos tiene el modelo"* sino *"qué necesita el consumidor de esta ruta"*. Para responderla se siguió el frontend, no la base de datos: por cada endpoint se buscó en `uis/backoffice` qué campos lee realmente el componente que lo consume.

Ejemplo del método: `GET /suppliers` devolvía las 12 columnas del modelo `Supplier`. Un `grep -o "supplier\.[a-z_]*"` sobre `components/ProviderDirectory.tsx` —el único consumidor— devuelve 8 nombres. Los otros 4 se enviaban en cada fila de cada petición sin que nadie los mirara nunca.

Los tres estados de clasificación son los del brief:

- ✅ **Ya serializado** — `response_model` explícito y esquema adecuado al consumidor.
- ⚠️ **Parcialmente serializado** — tiene `response_model`, pero expone campos innecesarios o no encaja con lo que el cliente necesita.
- ❌ **Sin serializar** — devuelve un dict sin tipar o un objeto sin contrato declarado.

### Un matiz sobre "todo endpoint debe tener response_model"

Dos endpoints devuelven archivos, no JSON: `GET /` sirve el HTML estático de `uis/web` y `GET /api/incidents/results/export` es una descarga CSV. Ponerles un `response_model` produciría documentación **falsa**, porque prometería un objeto JSON que nunca llega. Lo correcto ahí es declarar `response_class` y el `content-type` real en `responses`, y eso es lo que se hizo. Se documenta explícitamente para que no se lea como un olvido.

Igual con los dos `DELETE`: responden `204 No Content`, y un 204 no lleva cuerpo por definición. Su contrato es "sin cuerpo", y así queda declarado.

---

## 2. Estado inicial: clasificación de los 37 endpoints

### ❌ Sin serializar (6)

| Endpoint | Qué devolvía | Por qué es un problema |
|---|---|---|
| `GET /api/health` | `Dict[str, str]` literal | Lo consultan sondas de disponibilidad, que necesitan una forma estable. Sin esquema no aparece en `/docs` ni se valida. |
| `GET /` | `FileResponse` sin declarar | `/docs` no indicaba que la respuesta es HTML. |
| `POST /api/incidents/analyze` | `Dict[str, Any]` | 11 claves de agregados construidas a mano. El contrato público era, de hecho, el valor de retorno de `analyze_rows()`: cualquier cambio en esa función se filtraba a los clientes en silencio. |
| `GET /api/incidents/results/export` | `Response` con CSV | Content-type real no declarado. |
| `PATCH /inventory/products/{id}/stock` | `-> None`, siempre 400 | El cuerpo del error también es contrato y no estaba documentado. |
| `GET /telemetry/report` | `Dict[str, Any]` | El más grave de los seis: las filas de cada métrica salen de `.to_dict(orient="records")` de Pandas, así que **los nombres de columna de un DataFrame eran el contrato público de la API**. Renombrar una columna en `analysis.py` rompía a los clientes sin aviso. |

### ⚠️ Parcialmente serializado (7)

| Endpoint | Esquema original | Problema |
|---|---|---|
| `POST /users` (registro) | `UserWithProfile` | Flujo **no autenticado** que devolvía `email` (el que el cliente acababa de enviar) más el perfil completo. |
| `GET /users` | `list[UserPublic]` | Devolvía el `email` de **todas** las cuentas a **cualquier** usuario autenticado — solo exigía `get_current_user`, no admin. |
| `GET /suppliers` | `list[Supplier]` | 12 campos por fila, 8 usados. Sobraban `contact_email`, `notes` (hasta 2000 caracteres), `compliance_agreement` y `contract_renewal_date`. |
| `GET /api/incidents` | `list[IncidentRead]` | Reutilizaba el esquema del detalle. `updated_at` no se muestra en la tabla. |
| `GET /profiles/me` | `ProfileRead` | Exponía `id` (fila de perfil) y `user_id`, ambos detalle de almacenamiento. |
| `PUT /profiles/me` | `ProfileRead` | Ídem. |
| `GET /auth/me` | `AuthMeResponse` con `profile: ProfileRead` | El objeto anidado repetía `user_id`, que ya viene como `id` en el nivel superior. |

### ✅ Ya serializado (24)

El resto partía correcto. Merecen mención por lo que hacen bien:

- **Todo el flujo de auth ya estaba limpio.** `login` y `token` devuelven solo `TokenResponse`; `forgot-password`, `reset-password` y `change-password` devuelven solo `MessageResponse`. Ningún esquema del proyecto ha contenido nunca `hashed_password`.
- **`forgot-password` responde siempre lo mismo** hay cuenta o no (anti-enumeración) — decisión previa que esta auditoría confirma.
- **Inventario ya separaba entrada de salida**: `MedicalSupplyCreate` ≠ `MedicalSupplyRead`, `SupplyDeliveryCreate` ≠ `SupplyDeliveryRead`. `current_stock` solo existe en el esquema de lectura, nunca en el de escritura, lo que impide por contrato editar el stock directamente.
- **`InventoryOrderRead` ya aplanaba una relación**: incorpora `supply_name` y `supply_sku` en lugar de anidar el objeto `MedicalSupply` completo, evitando además el N+1.

---

## 3. Cambios aplicados

### 3.1 Esquemas nuevos

| Esquema | Para | Decisión |
|---|---|---|
| `UserRegistered` | `POST /users` | Solo `id` y `created_at`. El registro es un flujo sin autenticar: no es el momento de emitir datos personales. No reenvía el email que el cliente acaba de mandar en el cuerpo. |
| `UserListItem` | `GET /users` | `id`, `role`, `is_active`, `created_at`. **Sin email.** |
| `ProfilePublic` | `GET/PUT /profiles/me`, anidado en `/auth/me` | Solo `name`, `phone`, `address`. |
| `SupplierListItem` | `GET /suppliers` | Los 8 campos que lee `ProviderDirectory.tsx`. |
| `IncidentListItem` | `GET /api/incidents` | Los 8 campos que lee `app/incidents/page.tsx`. |
| `HealthStatus` | `GET /api/health` | Contrato mínimo pero explícito. |
| `IncidentAnalysisSummary` + `IncidentAnalysisResponse` | `POST /api/incidents/analyze` | Ver nota sobre `dict[str, int]` más abajo. |
| `DirectStockEditRejection` | `PATCH .../stock` | Documenta el cuerpo del 400. |
| `TelemetryReport` + 6 esquemas de fila | `GET /telemetry/report` | Convierte las columnas de Pandas en un contrato explícito. |

### 3.2 Decisiones de relaciones (lo que el brief pide documentar explícitamente)

| Relación | Decisión | Motivo |
|---|---|---|
| `AuthMeResponse.profile` | **Objeto anidado, aplanado** a `ProfilePublic` | El backoffice pinta nombre/teléfono/dirección en la misma pantalla que el email, así que una petición extra sería peor. Pero se quitan los ids del objeto anidado: `user_id` es exactamente `body["id"]`, repetirlo es ruido. |
| `POST /users` → perfil | **No se devuelve** | El perfil se crea igual, pero el cliente lo consulta después por `/auth/me`, ya autenticado. |
| `InventoryOrderRead` → producto | **Proyección plana** (`supply_name`, `supply_sku`) | Ya era así. La tabla muestra nombre y SKU, no el objeto `MedicalSupply` entero. Evita además el N+1. |
| `InventoryOrderRead.user_uuid` | **Se mantiene el ID en bruto** | Decisión consciente, contra la recomendación genérica del brief sobre claves foráneas. Es el rastro de auditoría que HealthCore necesita por HIPAA/UK GDPR, la tabla de `/inventory/orders` lo muestra como columna, y **no existe un objeto usuario anidado** que ofrecer en su lugar: el usuario vive en TinyDB y las órdenes en Supabase, son dos bases distintas. Sustituirlo por un objeto anidado exigiría una lectura cruzada por fila (N+1) para mostrar un dato que solo se usa en auditoría. |
| `SupplierListItem` → detalle | **Listado recortado, detalle completo** | El recorte es de la *vista*, no del recurso. `GET /suppliers/{id}` sigue devolviendo los 12 campos. Hay un test que lo fija, para que "optimizar el payload" no derive en pérdida de funcionalidad. |

### 3.3 Nota sobre los `dict[str, int]` de `IncidentAnalysisSummary`

`invalid_reasons`, `category_counts`, `status_counts`, `country_counts` y `score_counts` se tipan como `dict[str, int]` y no como campos fijos. Es deliberado: sus claves son los valores válidos definidos en `packages/shared/incidents_validation`, que es la fuente de verdad de esas reglas de negocio. Fijarlas aquí duplicaría el catálogo en dos sitios y obligaría a editar `models.py` cada vez que cambiara una regla. El contrato garantiza la *forma* (un mapa de nombre a conteo); el catálogo de claves lo garantiza el paquete compartido.

### 3.4 Cambio de autorización (fuera del alcance estricto de serialización)

`GET /users` pasa a exigir `require_admin`. La serialización por sí sola arregla *qué* se devuelve, pero no *a quién*: sin este cambio, cualquier usuario autenticado seguiría pudiendo enumerar todas las cuentas de la organización, aunque ya no viera sus correos. En una red sanitaria con obligaciones HIPAA y UK GDPR, las dos mitades del problema merecían arreglarse juntas. Se señala aquí porque es un cambio de comportamiento, no solo de forma.

---

## 4. Estado final: los 37 endpoints

Todos ✅. Generado desde el esquema OpenAPI real de la aplicación, no escrito a mano.

| Endpoint | Esquema de respuesta |
|---|---|
| `GET /` | `text/html` (UI estática) |
| `GET /api/health` | `HealthStatus` |
| `POST /api/incidents` | `IncidentRead` |
| `GET /api/incidents` | `list[IncidentListItem]` |
| `POST /api/incidents/analyze` | `IncidentAnalysisResponse` |
| `GET /api/incidents/results/export` | `text/csv` (descarga) |
| `GET /api/incidents/summary` | `IncidentSummary` |
| `GET /api/incidents/{incident_id}` | `IncidentRead` |
| `PATCH /api/incidents/{incident_id}/status` | `IncidentRead` |
| `POST /auth/change-password` | `MessageResponse` |
| `POST /auth/forgot-password` | `MessageResponse` |
| `POST /auth/login` | `TokenResponse` |
| `GET /auth/me` | `AuthMeResponse` |
| `POST /auth/reset-password` | `MessageResponse` |
| `POST /auth/token` | `TokenResponse` |
| `GET /inventory/orders` | `list[InventoryOrderRead]` |
| `POST /inventory/orders/inbound` | `SupplyDeliveryRead` |
| `POST /inventory/orders/outbound` | `SupplyConsumptionRead` |
| `GET /inventory/products` | `list[MedicalSupplyRead]` |
| `POST /inventory/products` | `MedicalSupplyRead` |
| `GET /inventory/products/{supply_id}` | `MedicalSupplyRead` |
| `PATCH /inventory/products/{supply_id}/stock` | `DirectStockEditRejection` |
| `GET /profiles/me` | `ProfilePublic` |
| `PUT /profiles/me` | `ProfilePublic` |
| `POST /suppliers` | `Supplier` |
| `GET /suppliers` | `list[SupplierListItem]` |
| `GET /suppliers/{supplier_id}` | `Supplier` |
| `DELETE /suppliers/{supplier_id}` | `204` sin cuerpo |
| `PATCH /suppliers/{supplier_id}/rate` | `Supplier` |
| `PATCH /suppliers/{supplier_id}/status` | `Supplier` |
| `POST /telemetry/events` | `TelemetryIngestResponse` |
| `GET /telemetry/report` | `TelemetryReport` |
| `GET /users` | `list[UserListItem]` |
| `POST /users` | `UserRegistered` |
| `GET /users/{user_id}` | `UserPublic` |
| `PUT /users/{user_id}` | `UserPublic` |
| `DELETE /users/{user_id}` | `204` sin cuerpo |

### Esquemas de entrada, separados de los de salida

Ninguno se reutiliza como respuesta:

| Endpoint | Entrada | Salida |
|---|---|---|
| `POST /users` | `UserCreate` | `UserRegistered` |
| `PUT /users/{id}` | `UserUpdate` (solo `email` y `role`) | `UserPublic` |
| `PUT /profiles/me` | `ProfileUpdate` | `ProfilePublic` |
| `POST /suppliers` | `SupplierCreate` | `Supplier` |
| `PATCH /suppliers/{id}/rate` | `SupplierRateUpdate` (solo `monthly_rate`) | `Supplier` |
| `PATCH /suppliers/{id}/status` | `SupplierStatusUpdate` (solo `status`) | `Supplier` |
| `POST /inventory/products` | `MedicalSupplyCreate` (sin `current_stock`) | `MedicalSupplyRead` (con `current_stock`) |
| `POST /inventory/orders/inbound` | `SupplyDeliveryCreate` | `SupplyDeliveryRead` |
| `POST /inventory/orders/outbound` | `SupplyConsumptionCreate` | `SupplyConsumptionRead` |
| `POST /api/incidents` | validación propia (`validate_incident_payload`) | `IncidentRead` |

`MedicalSupplyCreate` merece destacarse: no acepta `current_stock`, así que la regla de negocio *"el stock nunca se edita directamente"* queda garantizada **por el contrato**, no por una comprobación que alguien pueda olvidar.

---

## 5. Verificación

### Batería automática

101 tests de pytest en verde (89 previos + 12 nuevos). Los nuevos viven en `services/api/tests/test_serialization.py` y no comprueban lógica de negocio: comprueban la **forma** de las respuestas.

Tres son barridos sobre toda la superficie de la API, no listas escritas a mano:

- `test_ninguna_respuesta_expone_credenciales` — recorre el esquema OpenAPI completo buscando `hashed_password`, `password`, `new_password` o `current_password` en cualquier respuesta 2xx. Un endpoint nuevo que filtre credenciales hace fallar este test sin que nadie lo actualice.
- `test_todo_endpoint_json_declara_su_esquema_de_respuesta` — el requisito mínimo del hito, comprobado de forma continua. Excluye los 204 y las respuestas de archivo, por lo explicado en la sección 1.
- `test_los_flujos_de_auth_no_autenticados_no_devuelven_email` — registro, login, token, forgot y reset.

`access_token` se excluye a propósito de la lista de campos prohibidos: es el propósito mismo de `/auth/login`.

Dos tests existentes se reescribieron porque afirmaban justo el comportamiento que la auditoría corrige. `test_register_happy_path_creates_active_user_with_profile` verificaba rol, estado activo y perfil **leyéndolos de la respuesta del registro**; ahora verifica lo mismo por la vía correcta: se registra, hace login (lo que demuestra que la cuenta quedó activa) y consulta `/auth/me`. Es una comprobación más fuerte, no más débil.

### Verificación manual contra la API en marcha

Ejecutada sobre una base TinyDB temporal (`SUPPLIERS_DB_PATH=/tmp/...`), nunca contra `data/suppliers.db.json`. La rúbrica pide 3 endpoints; se comprobaron 7:

| # | Comprobación | Resultado |
|---|---|---|
| 1 | `GET /api/health` | `{"status": "ok"}` — coincide con `HealthStatus` |
| 2 | `POST /users` | Claves devueltas: `["created_at", "id"]`. Sin `email`, sin `profile` |
| 3 | `GET /auth/me` | Devuelve el email propio (permitido) y `profile` sin `user_id` |
| 4 | `GET /profiles/me` | Solo `name`, `phone`, `address` |
| 5 | `GET /users` con usuario normal | **403** |
| 6 | `GET /suppliers` | 8 campos; sin `contact_email`, `notes`, `compliance_agreement` ni `contract_renewal_date` |
| 7 | `GET /suppliers/{id}` | Los 12 campos, `contact_email` y `notes` incluidos |

**Reducción de payload medida** en el listado de proveedores: 373 B → 202 B por fila (**45 % menos**), y eso con un `notes` corto de prueba. El campo admite hasta 2000 caracteres, así que en datos reales el ahorro es considerablemente mayor.

### Impacto en el frontend

Los recortes obligaron a corregir los tipos de TypeScript, que habían quedado mintiendo sobre el contrato real. `npx tsc --noEmit` detectó exactamente los dos puntos donde el código asumía la proyección completa (`app/incidents/page.tsx` y `components/ProviderDirectory.tsx`), que ahora tipan su estado con `IncidentListItem` y `SupplierListItem`. Build y los 21 tests de Jest en verde.

Ningún componente perdió datos que estuviera mostrando: los campos retirados se comprobaron uno a uno contra su consumidor antes de quitarlos.

---

## 6. Residuales y seguimiento

Puntos que esta auditoría deja identificados y **no** resueltos, por decisión de alcance:

1. **`GET /users/{user_id}` sigue devolviendo el email de cualquier cuenta a cualquier usuario autenticado.** El esquema (`UserPublic`) es correcto para un endpoint de detalle, y la rúbrica no prohíbe el email fuera de los flujos de auth, pero hay una asimetría con `GET /users`, que sí quedó restringido a admin. Lo coherente sería aplicarle `ensure_self_or_admin`, igual que ya tiene `PUT /users/{user_id}`. Se dejó fuera para no ampliar el cambio de autorización más allá de lo acordado.

2. **`PUT /users/{user_id}` devuelve `UserPublic` con email.** Aquí sí está justificado: `ensure_self_or_admin` garantiza que quien recibe el correo es el titular de la cuenta o un admin, y devolverlo confirma el resultado de la escritura.

3. **`Supplier.contact_email` sigue saliendo en el detalle y en las respuestas de creación y PATCH.** Es un correo comercial de una empresa proveedora, no una credencial ni un dato de paciente, y el detalle es su sitio natural. Se anota por transparencia.

4. **`TelemetryEvent.properties` sigue siendo un `dict` libre** en la entrada de `POST /telemetry/events`. Su allowlist es por `event_type` y la aplica el código emisor, no el esquema (ver `docs/telemetry/telemetry-plan.md`). Tiparlo requeriría una unión discriminada de 24 esquemas de evento: es el siguiente paso natural, pero excede este hito.
