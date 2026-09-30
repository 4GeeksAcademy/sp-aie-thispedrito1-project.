"""El agente como CLIENTE del servidor MCP de HealthCore (mcps/healthcore).

Desde el ticket del servidor MCP, el agente ya no lee el Incidents Manager en
proceso: carga las tools del servidor MCP con `langchain-mcp-adapters` y las
invoca por Streamable HTTP, con un access token OAuth como cualquier otro
cliente. Es el único camino del agente hacia las incidencias.

Mínimo privilegio: el agente pide a Logto SOLO `incidents:read` (flujo
`client_credentials`, máquina a máquina). Aunque el modelo "quisiera" crear o
cerrar un ticket, su token no puede: el servidor respondería
`insufficient_scope`.

Variables (services/api/.env, plantilla en .env.example):
    MCP_SERVER_URL          p. ej. http://localhost:8765/mcp
    MCP_OAUTH_TOKEN_ENDPOINT https://<tenant>.logto.app/oidc/token
    MCP_AGENT_CLIENT_ID / MCP_AGENT_CLIENT_SECRET   app M2M del agente en Logto
    MCP_AUDIENCE            identificador de la API en Logto (parámetro `resource`)
    MCP_AGENT_SCOPES        por defecto "incidents:read"
"""

from __future__ import annotations

import asyncio
import base64
import os
import threading
import time
from typing import Any, Callable, Dict, Generator, Optional
from urllib.parse import quote

import httpx

SERVER_NAME = "healthcore"
DEFAULT_AGENT_SCOPES = "incidents:read"
TOKEN_REFRESH_MARGIN_S = 60


class McpClientConfigError(RuntimeError):
    """Falta configuración del cliente MCP del agente."""


class McpToolCallError(RuntimeError):
    """La tool respondió con error (`isError`). `code` es el código estable
    del servidor (not_found, insufficient_scope, ...), o None si no lo trae."""

    def __init__(self, tool: str, code: Optional[str], message: str) -> None:
        super().__init__(f"{tool}: {code or 'error'}")
        self.tool = tool
        self.code = code
        self.message = message


def _env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise McpClientConfigError(f"Falta la variable de entorno {name} (cliente MCP del agente)")
    return value


class ClientCredentialsAuth(httpx.Auth):
    """`httpx.Auth` que obtiene y renueva el access token de Logto.

    Va como `auth` de la conexión de langchain-mcp-adapters: cada petición al
    servidor MCP sale con `Authorization: Bearer ...` y, si el token está a
    un minuto de caducar, antes se pide otro. Es un flujo de httpx (generador),
    así que funciona igual con clientes síncronos y asíncronos. Si el servidor
    responde 401 (token revocado o caducado antes de tiempo), se renueva una
    vez y se reintenta."""

    requires_response_body = True

    def __init__(self, token_endpoint: str, client_id: str, client_secret: str, *, resource: str, scopes: str) -> None:
        self._token_endpoint = token_endpoint
        self._client_id = client_id
        self._client_secret = client_secret
        self._resource = resource
        self._scopes = scopes
        self._token: Optional[str] = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _token_request(self) -> httpx.Request:
        # client_secret_basic a mano: `httpx.Request` no acepta `auth=` (solo
        # el cliente), y aquí la petición la emite el propio flujo de auth.
        # Lo detectó la prueba contra Logto real, no los tests con token fijo.
        basic = base64.b64encode(f"{quote(self._client_id, safe='')}:{quote(self._client_secret, safe='')}".encode()).decode()
        return httpx.Request(
            "POST",
            self._token_endpoint,
            data={"grant_type": "client_credentials", "resource": self._resource, "scope": self._scopes},
            headers={"Authorization": f"Basic {basic}"},
        )

    def _store(self, response: httpx.Response) -> None:
        if response.status_code != 200:
            # Sin repetir el cuerpo: podría traer detalles de la app en Logto.
            raise McpClientConfigError(f"Logto rechazó las credenciales del agente (HTTP {response.status_code})")
        payload = response.json()
        with self._lock:
            self._token = payload["access_token"]
            self._expires_at = time.time() + float(payload.get("expires_in", 300))

    def _current(self) -> Optional[str]:
        with self._lock:
            if self._token and time.time() < self._expires_at - TOKEN_REFRESH_MARGIN_S:
                return self._token
            return None

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        token = self._current()
        if token is None:
            self._store((yield self._token_request()))
            token = self._current()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code == 401:
            self._store((yield self._token_request()))
            request.headers["Authorization"] = f"Bearer {self._current()}"
            yield request


_auth: Optional[ClientCredentialsAuth] = None
_auth_lock = threading.Lock()


def default_auth() -> ClientCredentialsAuth:
    """Una sola instancia por proceso, para reutilizar el token entre consultas."""
    global _auth
    with _auth_lock:
        if _auth is None:
            _auth = ClientCredentialsAuth(
                _env("MCP_OAUTH_TOKEN_ENDPOINT"),
                _env("MCP_AGENT_CLIENT_ID"),
                _env("MCP_AGENT_CLIENT_SECRET"),
                resource=_env("MCP_AUDIENCE"),
                scopes=(os.getenv("MCP_AGENT_SCOPES") or DEFAULT_AGENT_SCOPES).strip(),
            )
        return _auth


def connection(
    *,
    url: Optional[str] = None,
    auth: Optional[httpx.Auth] = None,
    httpx_client_factory: Optional[Callable[..., httpx.AsyncClient]] = None,
    timeout_s: float = 10.0,
) -> Dict[str, Any]:
    """Conexión Streamable HTTP para `MultiServerMCPClient`. Los tests pasan
    una `httpx_client_factory` que apunta a la app MCP en memoria."""
    conn: Dict[str, Any] = {
        "transport": "streamable_http",
        "url": url or _env("MCP_SERVER_URL"),
        "auth": auth or default_auth(),
        "timeout": timeout_s,
    }
    if httpx_client_factory is not None:
        conn["httpx_client_factory"] = httpx_client_factory
    return conn


async def acall_tool(name: str, args: Dict[str, Any], *, conn: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Descubre las tools del servidor (tools/list), invoca `name` y devuelve
    su salida estructurada (`structuredContent`). Error de tool → McpToolCallError."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    from mcps.healthcore.errors import parse_tool_error

    client = MultiServerMCPClient({SERVER_NAME: conn or connection()})
    tools = {tool.name: tool for tool in await client.get_tools(server_name=SERVER_NAME)}
    if name not in tools:
        raise McpToolCallError(name, None, "El servidor MCP no expone esta tool")
    message = await tools[name].ainvoke({"type": "tool_call", "name": name, "args": args, "id": f"agent-{name}"})
    if getattr(message, "status", "success") == "error":
        text = message.content if isinstance(message.content, str) else " ".join(
            block.get("text", "") for block in message.content if isinstance(block, dict)
        )
        error = parse_tool_error(text)
        raise McpToolCallError(name, error["code"], error["message"])
    artifact = getattr(message, "artifact", None) or {}
    structured = artifact.get("structured_content") if isinstance(artifact, dict) else None
    if not isinstance(structured, dict):
        raise McpToolCallError(name, None, "La tool no devolvió salida estructurada")
    return structured


def call_tool(name: str, args: Dict[str, Any], *, conn: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Versión síncrona para los nodos del grafo. Se ejecuta en el hilo de
    `run_tool` (tools/base.py), que no tiene bucle de eventos propio."""
    return asyncio.run(acall_tool(name, args, conn=conn))
