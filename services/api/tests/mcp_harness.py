"""Utilidades de test del servidor MCP: un "Logto" local y la cadena en memoria.

- Emisor OAuth falso: una clave RSA generada al importar firma los access
  tokens; el servidor MCP la recibe como `PyJWK` y verifica con el mismo
  código de MCP Auth que usa contra Logto (sin red).
- Cadena real sin puertos: cliente MCP → app MCP (httpx.ASGITransport) →
  API FastAPI de los tests (otra ASGITransport, con la TinyDB temporal y la
  SQLite de inventario de conftest.py).

Cada llamada crea su propia app MCP: el gestor de sesiones del SDK solo se
puede arrancar una vez por instancia y debe vivir en el mismo bucle de
eventos que las peticiones.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Iterable, Optional

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWK
from jwt.algorithms import RSAAlgorithm
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcpauth.config import AuthorizationServerMetadata, AuthServerConfig, AuthServerType

from mcps.healthcore.api_client import HealthCoreApi
from mcps.healthcore.config import McpSettings
from mcps.healthcore.scopes import ALL_SCOPES
from mcps.healthcore.server import build_jwt_verifier, create_app

ISSUER = "https://healthcore-test.logto.app/oidc"
AUDIENCE = "http://localhost:8765/mcp"
MCP_URL = "http://localhost:8765/mcp"
SERVICE_EMAIL = "mcp-service@healthcore.com"
SERVICE_PASSWORD = "ServiceAccount123"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "test-key"


def _public_jwk() -> PyJWK:
    jwk = json.loads(RSAAlgorithm.to_jwk(_PRIVATE_KEY.public_key()))
    jwk.update({"kid": _KID, "alg": "RS256", "use": "sig"})
    return PyJWK.from_dict(jwk)


AUTH_SERVER = AuthServerConfig(
    type=AuthServerType.OIDC,
    metadata=AuthorizationServerMetadata(
        issuer=ISSUER,
        authorization_endpoint=f"{ISSUER}/auth",
        token_endpoint=f"{ISSUER}/token",
        jwks_uri=f"{ISSUER}/jwks",
        response_types_supported=["code"],
        grant_types_supported=["authorization_code", "refresh_token", "client_credentials"],
        code_challenge_methods_supported=["S256"],
    ),
)

SETTINGS = McpSettings(
    issuer=ISSUER,
    resource_url=MCP_URL,
    audience=AUDIENCE,
    api_base_url="http://healthcore-api",
    api_email=SERVICE_EMAIL,
    api_password=SERVICE_PASSWORD,
    allowed_hosts=["localhost:8765", "127.0.0.1:8765"],
)


def make_token(
    scopes: Iterable[str] = ALL_SCOPES,
    *,
    client_id: str = "test-client",
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    other_key: bool = False,
    expires_in: int = 600,
) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": client_id,
        "client_id": client_id,
        "scope": " ".join(scopes),
        "iat": now,
        "exp": now + expires_in,
    }
    key = _OTHER_KEY if other_key else _PRIVATE_KEY
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": _KID})


def build_app(api_app: Any, settings: Optional[McpSettings] = None) -> Any:
    """App MCP completa (MCP Auth incluido) hablando con la API de los tests."""
    settings = settings or SETTINGS
    api = HealthCoreApi(settings.api_base_url, SERVICE_EMAIL, SERVICE_PASSWORD, transport=httpx.ASGITransport(app=api_app))
    return create_app(settings, auth_server=AUTH_SERVER, verify=build_jwt_verifier(AUTH_SERVER, key=_public_jwk()), api=api)


def http_client(mcp_app: Any, token: Optional[str] = None, **kwargs: Any) -> httpx.AsyncClient:
    headers = dict(kwargs.pop("headers", None) or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url="http://localhost:8765", headers=headers, **kwargs)


async def with_lifespan(mcp_app: Any, coro_fn: Any) -> Any:
    async with mcp_app.router.lifespan_context(mcp_app):
        return await coro_fn()


def run_session(api_app: Any, token: str, fn: Any) -> Any:
    """Abre una sesión MCP autenticada y ejecuta `fn(session)`."""
    mcp_app = build_app(api_app)

    async def go() -> Any:
        async with http_client(mcp_app, token) as client:
            async with streamable_http_client(MCP_URL, http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await fn(session)

    return asyncio.run(with_lifespan(mcp_app, go))


def call(api_app: Any, token: str, tool: str, args: Dict[str, Any]) -> Any:
    async def fn(session: ClientSession) -> Any:
        return await session.call_tool(tool, args)

    return run_session(api_app, token, fn)


def error_of(result: Any) -> Dict[str, Any]:
    from mcps.healthcore.errors import parse_tool_error

    assert result.isError, result
    text = result.content[0].text
    assert text.startswith("Error executing tool "), text
    return parse_tool_error(text)


def agent_mcp_call(token: str, api_app: Any = None):
    """`mcp_call` del agente apuntando a la app MCP en memoria, con el mismo
    `acall_tool` (langchain-mcp-adapters) que usa en producción."""
    from services.agent import mcp_client

    if api_app is None:
        from main import app as api_app

    def call(name, args):
        mcp_app = build_app(api_app)

        def factory(headers=None, timeout=None, auth=None):
            return http_client(mcp_app, token, headers=headers, timeout=timeout)

        conn = {"transport": "streamable_http", "url": MCP_URL, "httpx_client_factory": factory}
        try:
            return asyncio.run(with_lifespan(mcp_app, lambda: mcp_client.acall_tool(name, args, conn=conn)))
        except BaseExceptionGroup as group:
            # Solo en tests: el servidor MCP vive en este mismo bucle y su
            # grupo de tareas envuelve el error del cliente. En producción el
            # servidor es otro proceso y el error llega tal cual.
            if len(group.exceptions) == 1:
                raise group.exceptions[0] from None
            raise

    return call
