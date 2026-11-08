# mcps/healthcore — servidor MCP con OAuth

Expone incidencias e inventario a clientes MCP remotos (el agente, MCP Playground). Detalle, configuración de Logto y guía de Codespaces en `docs/mcp/mcp-server.md`.

## Comandos (desde la raíz)

```bash
cp mcps/healthcore/.env.example mcps/healthcore/.env
services/api/.venv/bin/python scripts/create_mcp_service_account.py   # una vez
services/api/.venv/bin/python -m mcps.healthcore                      # puerto 8765, necesita la API en 8000
(cd services/api && .venv/bin/python -m pytest tests/test_mcp_server.py -o addopts="")
services/api/.venv/bin/python -m pytest tests/pipelines/test_agent_mcp_client.py
```

El agente lo consume con `MCP_SERVER_URL`, `MCP_OAUTH_TOKEN_ENDPOINT`, `MCP_AUDIENCE`, `MCP_AGENT_CLIENT_ID/SECRET` en `services/api/.env`.

## Mapa

`config.py` (variables, `.env` propio), `server.py` (Starlette: Protected Resource Metadata + MCP Auth + FastMCP), `tools.py` (5 tools: `incidents_get/search/create/update_status`, `inventory_query`; + log de auditoría), `scopes.py` (`TOOL_SCOPES`), `errors.py`, `api_client.py` (cliente HTTP con cuenta de servicio + `InventoryReader` solo GET). Arnés de tests: `services/api/tests/mcp_harness.py` (emisor OAuth falso con RSA local, cadena en memoria cliente → MCP → API con `httpx.ASGITransport`).

## Reglas que no hay que romper

- Streamable HTTP, sin estado y con respuestas JSON (un token por petición).
- SDK oficial `mcp` (su `FastMCP`), no el paquete `fastmcp` (usa `mcp` 2.x y `langchain-mcp-adapters` exige `mcp<2`). Nunca la auth integrada de FastMCP.
- `mcpauth==0.2.0b1` fijado en `requirements.txt` **y** `pyproject.toml` (la 0.1.1 no tiene modo resource server). `build_jwt_verifier` usa un `PyJWKClient` cacheado.
- Scopes: `incidents:read`, `incidents:write`, `inventory:read`, uno por tool. `TOOL_SCOPES` falla cerrado. No existe escritura de inventario: sin scope, `inventory_query` rechaza con `read_only_resource` e `InventoryReader` solo permite GET bajo `/inventory/`.
- El MCP llama a la API por HTTP con una cuenta de servicio, nunca a la base de datos. Estados siempre por `PATCH /api/incidents/{id}/status` (test espía). `incidents_update_status` exige `confirm` (`destructiveHint=True`).
- Sin `title`/`description` en salidas ni logs. Log `healthcore.mcp.audit`: una línea JSON con tool, `client_id`, `subject`, `outcome`.

## Gotchas

- FastMCP antepone `Error executing tool <tool>: ` al `ToolError`: `errors.parse_tool_error` lee desde la primera `{`.
- Protección DNS-rebinding: el host de `MCP_RESOURCE_URL` se añade a `allowed_hosts`. Clientes de navegador necesitan `MCP_ALLOWED_ORIGINS` (CORS externo + origen en la lista del SDK).
- MCP Playground envía `""` en los opcionales y llama desde sus servidores: `tools._blank_to_none`.
- Una app Starlette montada no ejecuta su lifespan: `session_manager.run()` va en el de la app exterior y solo arranca una vez por instancia (los tests crean una app por llamada).
- En tests el error del cliente llega envuelto en `ExceptionGroup`; `mcp_harness.agent_mcp_call` lo desempaqueta.
- `services/api/tests` no es un paquete: importar `mcp_harness` directamente, nunca `from tests import …`.
- `httpx.Request` no acepta `auth=`: `ClientCredentialsAuth` pone la cabecera Basic a mano.
- Logto: tenant `eb77o9`, API `http://localhost:8765/mcp`, roles M2M `mcp-agent-reader` y `mcp-full-tester`, apps `healthcore-agent` y `mcp-playground-tester`. Un rol creado como "User" no se convierte a M2M (borrar y recrear). Logto recorta los scopes al rol.
- Codespaces por `gh`: el puerto no existe sin `gh codespace ports forward`; `pkill -f <texto>` dentro de `gh codespace ssh -- '...'` mata la propia sesión si el texto está en el comando.
