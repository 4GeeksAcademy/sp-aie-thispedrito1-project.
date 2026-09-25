# Servidor MCP de HealthCore con OAuth (MCP Auth)

Rama `feature/mcp-oauth-tools` (sobre `main`, 2026-09-25). Ticket: "RFP — Servidor MCP para herramientas de la compañía".

El Incidents Manager y el inventario pasan a estar expuestos como un **servidor MCP independiente** (`mcps/healthcore/`), protegido con **OAuth 2.1 vía MCP Auth** (`mcpauth`). El agente LangGraph deja de leer las incidencias en proceso y las consulta **como cliente MCP** con `langchain-mcp-adapters`.

```
Cliente MCP (agente, MCP Playground, otro equipo)
   │  Authorization: Bearer <access token de Logto>
   ▼
mcps/healthcore  (Streamable HTTP, puerto 8765)
   ├─ /.well-known/oauth-protected-resource/mcp   público (RFC 9728)
   └─ /mcp → MCP Auth (401 si el token no vale) → FastMCP → tool (comprueba su scope)
                                                            │ cuenta de servicio (rol user)
                                                            ▼
                                                 services/api  (FastAPI)
                                                 /api/incidents…  /inventory/products…
```

## 1. Decisiones de diseño

### Transporte: Streamable HTTP, no stdio

stdio sirve a **un** cliente que lanza el servidor como subproceso en su misma máquina. No tiene cabeceras HTTP, así que no hay dónde poner un access token: la seguridad sería "quien pueda ejecutar el proceso". El ticket pide lo contrario: un servicio reutilizable por **varios clientes remotos** (el agente de la API, MCP Playground, otros equipos o partners), cada uno autenticado por separado. Por eso se usa Streamable HTTP, donde cada petición trae `Authorization: Bearer …` y el servidor actúa como *resource server* OAuth.

Además, el servidor funciona **sin estado** (`stateless_http=True`, respuestas JSON). Cada petición se autentica y se atiende por sí sola, sin sesiones que recordar, así que se podrían poner varias réplicas detrás de un balanceador.

### SDK: `mcp.server.fastmcp` y no el paquete `fastmcp`

`langchain-mcp-adapters` exige `mcp<2`, y las versiones nuevas del paquete `fastmcp` usan `mcp` 2.x: juntos, `uv` solo resolvía un `fastmcp` 2.2.0 muy antiguo. El SDK oficial `mcp` ya trae su propio FastMCP, que es el que usan los ejemplos de MCP Auth, así que se usa ese (el README acepta "FastMCP u otro SDK MCP equivalente"). La auth integrada de FastMCP (`auth=`, `token_verifier=`) **no** se usa.

### MCP Auth 0.2.0b1 (beta) y no 0.1.1

La 0.1.1 (la última estable) solo tiene el modo *authorization server* (deprecado): no sirve Protected Resource Metadata ni envía `WWW-Authenticate` con `resource_metadata`. La 0.2.0b1 añade el modo *resource server* que pide el ticket, así que se fija esa versión en `requirements.txt`.

Ajuste sobre la librería: en modo `"jwt"`, MCP Auth crea un cliente JWKS nuevo **en cada petición** (comprobado en su código), así que descargaría las claves de Logto cada vez. `server.build_jwt_verifier` usa la misma función de MCP Auth (`create_verify_jwt`) con un `PyJWKClient` creado una sola vez y cacheado, y comprueba el emisor con la misma excepción de MCP Auth.

### Mínimo privilegio: tres scopes

| Scope | Tools | Quién lo tiene |
|---|---|---|
| `incidents:read` | `incidents_get`, `incidents_search` | agente de soporte, integraciones de consulta |
| `incidents:write` | `incidents_create`, `incidents_update_status` | quien gestione tickets (no el agente) |
| `inventory:read` | `inventory_query` | integraciones que consulten stock |

- Cada tool exige **un solo** scope, el de su operación. Escribir no exige además leer: un integrador que solo abre tickets no necesita consultar toda la base.
- La tabla vive en `mcps/healthcore/scopes.py::TOOL_SCOPES` y **falla cerrado**: una tool que no esté en ella no se puede invocar.
- El agente pide a Logto **solo** `incidents:read`. Aunque el modelo "quisiera" cerrar un ticket, su token no puede hacerlo.

### Inventario de solo lectura por diseño (tres capas)

1. **No existe ningún scope de escritura de inventario.** Un token con un `inventory:write` inventado no consigue nada; hay test.
2. **La tool reconoce las escrituras y las rechaza de forma explícita.** `inventory_query` acepta `action`. `list_supplies` y `get_supply` funcionan; cualquier acción de escritura (`register_inbound`, `adjust_stock`, `delete_supply`, … o cualquier verbo de escritura: `create*`, `update*`, `set*`…) devuelve `read_only_resource` con las acciones permitidas. No es un "no implementado": el servidor dice que no, y por qué.
3. **El cliente HTTP no sabe escribir.** La tool solo recibe un `InventoryReader`, que permite `GET` y únicamente bajo `/inventory/`. Cualquier otro método lanza `ReadOnlyViolation`, así que un bug en la tool tampoco podría escribir.

### Contra el Incidents Manager real, no una copia

El servidor MCP no toca ninguna base de datos: llama a la API con una **cuenta de servicio** (rol `user`, nunca admin) por los mismos endpoints que el backoffice. Los cambios de estado pasan por `PATCH /api/incidents/{id}/status`, con su validación de transiciones; hay un test que lo espía y comprueba que nunca se usa un `PATCH` genérico. Los valores de dominio del discovery (categorías, sedes, estados, orígenes) salen de `packages/shared/incidents_validation`, la misma fuente que usa la API.

### Datos sensibles

- Las respuestas de incidencias **no** incluyen `title` ni `description` (texto libre con posibles datos de pacientes), el mismo criterio que el agente y la telemetría. `incidents_create` los recibe, pero no los devuelve ni los escribe en el log.
- El log de auditoría guarda solo campos de catálogo e ids.

## 2. Tools (lo que muestra el discovery)

Cada tool publica título, una descripción que incluye el scope que necesita, `inputSchema` con los valores permitidos (`enum`), `outputSchema` y anotaciones (`readOnlyHint`, `idempotentHint`, `destructiveHint`). Las `instructions` del servidor resumen scopes y códigos de error. Hay un test que comprueba todo esto leyendo el discovery, sin mirar el código.

| Tool | Qué hace | API que llama |
|---|---|---|
| `incidents_get` | estado y datos operativos de un ticket + `allowed_next_statuses` | `GET /api/incidents/{id}` |
| `incidents_search` | filtra por estado/categoría/sede/origen; `total` + hasta `limit` | `GET /api/incidents` |
| `incidents_create` | abre un ticket (siempre `open`) | `POST /api/incidents` |
| `incidents_update_status` | ciclo de vida open → in_progress → resolved/discarded | `PATCH /api/incidents/{id}/status` |
| `inventory_query` | `list_supplies` / `get_supply` con stock calculado; rechaza escrituras | `GET /inventory/products[/{id}]` |

## 3. Códigos de error

**Nivel HTTP (antes de MCP), los pone MCP Auth.** Van con `WWW-Authenticate: Bearer error="…", resource_metadata="…/.well-known/oauth-protected-resource/mcp"`.

| HTTP | `error` | Cuándo |
|---|---|---|
| 401 | `missing_auth_header` | sin cabecera `Authorization` (tampoco se puede listar tools) |
| 401 | `invalid_auth_header_format` | cabecera que no es `Bearer <token>` |
| 401 | `invalid_token` | firma inválida, caducado o no es un JWT |
| 401 | `invalid_issuer` | token de otro emisor |
| 401 | `invalid_audience` | token emitido para otra API |
| 500 | `server_error` | fallo con la configuración del servidor de autorización |

**Nivel tool (dentro de MCP).** `CallToolResult` con `isError: true` y el texto `Error executing tool <tool>: {"error": {"code", "message", "details"}}`. El prefijo lo añade siempre el FastMCP del SDK; el JSON empieza en la primera `{` (`errors.parse_tool_error`).

| `code` | Tipo | Ejemplo |
|---|---|---|
| `insufficient_scope` | autorización | token con solo `incidents:read` llama a `incidents_create`; `details.missing_scopes` |
| `read_only_resource` | escritura prohibida | `inventory_query` con `action: "adjust_stock"`; `details.allowed_actions` |
| `validation_error` | validación | categoría inexistente, transición `open → resolved`; `details.fields` viene de la API |
| `not_found` | recurso | ticket o insumo que no existe |
| `upstream_unavailable` | disponibilidad | la API no responde o la cuenta de servicio no puede entrar |

Si los tipos del JSON no encajan con el esquema (p. ej. `incident_id: "abc"`), el propio SDK responde con un texto `… validation error …`, que `parse_tool_error` clasifica como `validation_error`.

## 4. Log por invocación

Logger `healthcore.mcp.audit`: una línea JSON por llamada, con éxito o con error.

```json
{"event": "mcp_tool_call", "tool": "incidents_create", "client_id": "agent-healthcore", "subject": "agent-healthcore",
 "outcome": "ok", "duration_ms": 359.1, "args": {"category": "clinical_equipment", "origin": "branch", "branch": "london_city"}}
```

`outcome` es `ok` o el código de error. `client_id` y `subject` salen del token validado por MCP Auth.

## 5. Migración del agente

- `services/agent/tools/incidents.py`: la lectura en proceso (`IncidentRepository`) se **eliminó**. `lookup_incident` llama a `incidents_get` / `incidents_search` por MCP (`services/agent/mcp_client.py`, con `MultiServerMCPClient` y `tool.ainvoke`). No quedan dos caminos: un test recorre `services/agent/` con `ast` y falla si algún módulo importa el repositorio o la ruta de incidencias.
- `mcp_client.ClientCredentialsAuth` es un `httpx.Auth` que pide el token a Logto (`client_credentials`, `resource` = audiencia, `scope=incidents:read`), lo cachea y lo renueva un minuto antes de caducar o tras un 401.
- **El enrutamiento no cambia.** Se mantienen el nombre del nodo (`lookup_incident`), el contrato de entrada que valida el planificador y el de salida que lee `evidence.py`. Los 86 evals sobre traces grabados siguen pasando. El trace muestra ahora `"via": "mcp"` en el resultado de la tool.
- El timeout de la tool sube de 3 s a 5 s, porque ahora hay red: token cacheado, `tools/list` + `tools/call` y la llamada del MCP a la API.
- Si el servidor MCP no está, rechaza el token o falta el scope, la tool devuelve `unavailable` y el grafo responde con el fallback honesto de siempre.

## 6. Configurar Logto Cloud (una vez)

1. Crea un tenant gratuito en <https://cloud.logto.io>. El **emisor** es `https://<tenant>.logto.app/oidc`.
2. **API resources → Create**. Nombre `HealthCore MCP`, identificador `http://localhost:8765/mcp`. En *Permissions* añade `incidents:read`, `incidents:write` e `inventory:read`.
3. **Roles → Create** (tipo *Machine-to-machine*):
   - `mcp-agent-reader` con solo `incidents:read`;
   - `mcp-full-tester` con los tres permisos.
4. **Applications → Create → Machine-to-machine**:
   - `healthcore-agent` con el rol `mcp-agent-reader`: su App ID y App secret van a `services/api/.env`;
   - `mcp-playground-tester` con el rol `mcp-full-tester`: sirve para las pruebas manuales y Playground.
5. Rellena `mcps/healthcore/.env` a partir de `.env.example`: `MCP_OAUTH_ISSUER`, `MCP_AUDIENCE` y la cuenta de servicio.

Pedir un token a mano (p. ej. para Playground):

```bash
curl -s -u "$CLIENT_ID:$CLIENT_SECRET" https://<tenant>.logto.app/oidc/token \
  -d grant_type=client_credentials -d resource=http://localhost:8765/mcp \
  -d "scope=incidents:read incidents:write inventory:read"
```

## 7. Ejecutar en local

```bash
# 1. API (terminal 1)
cd services/api && .venv/bin/uvicorn main:app --port 8000
# 2. Cuenta de servicio, una vez (rol user), desde la RAÍZ
services/api/.venv/bin/python scripts/create_mcp_service_account.py
# 3. Servidor MCP (terminal 2), desde la RAÍZ
services/api/.venv/bin/python -m mcps.healthcore
```

## 8. MCP Playground desde GitHub Codespaces

1. Abre el repo en un Codespace e instala las dependencias: `cd services/api && uv venv --python 3.12 .venv && uv pip install -r requirements.txt`.
2. Arranca la API (puerto 8000) y el MCP con `MCP_HOST=0.0.0.0`.
3. En la pestaña **Ports**, puerto 8765 → *Port Visibility → Public*. Copia la URL, del tipo `https://<codespace>-8765.app.github.dev`.
4. En `mcps/healthcore/.env` pon `MCP_RESOURCE_URL=https://<codespace>-8765.app.github.dev/mcp` (su host se añade solo a los hosts permitidos) y reinicia el MCP. `MCP_AUDIENCE` no cambia.
5. En <https://www.mcpplayground.tech/playground>: transporte *Streamable HTTP*, URL `https://<codespace>-8765.app.github.dev/mcp` y cabecera `Authorization: Bearer <token>` (curl de la sección 6).
6. Flujos a ejecutar: `incidents_create` → `incidents_get` → `incidents_update_status` (in_progress) → `incidents_search`; `inventory_query` con `list_supplies`; e **intento de escritura**: `inventory_query` con `{"action": "adjust_stock", "supply_id": 1}` → `read_only_resource`. Prueba también sin cabecera → 401.

## 9. Verificación

**Tests (sin red):**
- `services/api/tests/test_mcp_server.py` (37) sobre la cadena real en memoria: cliente MCP → MCP Auth → FastMCP → API FastAPI con TinyDB temporal. Tokens firmados con una clave RSA local y verificados con el código de MCP Auth.
- `tests/pipelines/test_agent_mcp_client.py` (5): el flujo OAuth del agente (`ClientCredentialsAuth`) contra un Logto simulado.
- `test_agent.py` pasa por el MCP y `tests/pipelines/test_agent_tools.py` usa un doble del servidor MCP.
- Sin regresiones: API 217, raíz 247.

**Prueba real (2026-09-25)** con Logto Cloud (tenant `eb77o9`), la API y el MCP como procesos, una copia temporal de la TinyDB con las 94 incidencias del seed y el inventario de Supabase real:

| Comprobación | Resultado |
|---|---|
| MCP Auth valida la configuración OIDC de Logto | válida; solo el aviso `dynamic_registration_not_supported` (las apps se crean a mano) |
| Token del agente pidiendo los tres scopes | Logto solo le da `incidents:read` (su rol) |
| `POST /mcp` sin token / con token falso | 401 `missing_auth_header` (con `WWW-Authenticate` + `resource_metadata`) / 401 `invalid_token` |
| Discovery | 5 tools con descripción, esquemas y `readOnlyHint` |
| Ticket: crear → consultar → in_progress → open → resolved | ok · ok · ok · `validation_error` (detalle de la API) · ok |
| Búsqueda y ticket inexistente | total + lista · `not_found` |
| `inventory_query list_supplies` | 6 insumos de Supabase (guantes de nitrilo: 350) |
| `inventory_query adjust_stock` / `register_inbound` | `read_only_resource` |
| Token del agente: consultar / crear / inventario | ok · `insufficient_scope` · `insufficient_scope` |
| Agente real (`lookup_incident` → Logto → MCP → API) | ok `via=mcp` (1,34 s la primera, ~0,05 s después con tokens en caché); `not_found` para un ticket inexistente |
| Log de auditoría | 17 líneas, una por llamada, con `client_id` de cada app |

**Bug encontrado por la prueba real, no por los tests:** `ClientCredentialsAuth` construía la petición de token con `httpx.Request(auth=...)`, que `httpx.Request` no admite. Los tests inyectaban el token hecho y no pasaban por esa clase. Se corrigió poniendo la cabecera Basic a mano, y los 5 tests nuevos fallan con el código anterior (comprobado).

**Preguntas reales al agente** (`POST /agent/query`, modelo real, tras regenerar la `LLM_API_KEY` del proxy de 4Geeks, que había caducado):

| Pregunta | Resultado | Fuentes · vía |
|---|---|---|
| ¿En qué estado está el ticket 95? | "El ticket 95 está resuelto." (10,6 s, incluye tokens de Logto y login de la cuenta de servicio) | incidents · mcp |
| ¿Cuántas incidencias abiertas hay en la sede london_city? | "Hay 4 incidencias abiertas…" (5,6 s) | incidents · mcp |
| ¿Qué pasa con el ticket 99999? | fallback fijo "No encuentro la incidencia #99999…" (1,2 s, sin modelo) | incidents · mcp |
| Cierra el ticket 22, por favor | "No puedo cerrar el ticket 22 desde aquí…"; el ticket sigue `open` en la base y el log de auditoría solo muestra `incidents_get` | incidents · mcp |

El enrutamiento entre RAG y tools se mantiene: el planificador eligió `incidents` en las cuatro, igual que antes de la migración. Qdrant no se levantó (las preguntas de tickets no lo usan); el camino del RAG no cambió en este ticket y lo cubren los evals grabados.

**Pendiente:** la prueba de MCP Playground desde Codespaces (sección 8), que hace el usuario.
