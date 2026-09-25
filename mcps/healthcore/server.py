"""Aplicación del servidor MCP de HealthCore: Streamable HTTP + MCP Auth.

Transporte: Streamable HTTP (no stdio). stdio solo sirve a UN cliente que
lanza el servidor como subproceso en su misma máquina, y no tiene cabeceras
HTTP donde viajar un access token. Aquí hay varios clientes remotos (el
agente de la API, MCP Playground, futuros equipos o partners), así que el
servidor escucha por HTTP y cada petición trae su `Authorization: Bearer`.
Modo sin estado (`stateless_http`) y respuestas JSON: cada petición se
autentica y se atiende por sí sola, sin sesiones que recordar entre réplicas.

Capas, de fuera a dentro:
    /.well-known/oauth-protected-resource/mcp   pública (RFC 9728, MCP Auth)
    /mcp → BearerAuth (MCP Auth: firma, emisor, audiencia; 401 si no)
         → AuthInfoToScope (deja la identidad en el scope ASGI)
         → FastMCP (discovery + tools, cada tool comprueba su scope)

Auth: MCP Auth (`mcpauth`) en modo resource server. NO se usa la capa de
auth integrada de FastMCP (`auth=` / `token_verifier=`), a propósito.
"""

from __future__ import annotations

import contextlib
import logging
from typing import AsyncIterator, Callable, Optional, Union

from jwt import PyJWK, PyJWKClient
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcpauth import MCPAuth
from mcpauth.config import AuthServerConfig, AuthServerType
from mcpauth.exceptions import BearerAuthExceptionCode, MCPAuthBearerAuthException, MCPAuthBearerAuthExceptionDetails
from mcpauth.types import AuthInfo, ResourceServerConfig, ResourceServerMetadata
from mcpauth.utils import create_verify_jwt, fetch_server_config
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from mcps.healthcore.api_client import HealthCoreApi
from mcps.healthcore.config import McpSettings
from mcps.healthcore.scopes import ALL_SCOPES
from mcps.healthcore.tools import register_tools

logger = logging.getLogger("healthcore.mcp")

SERVER_NAME = "healthcore-company-tools"
SERVER_INSTRUCTIONS = (
    "Servidor MCP de HealthCore (red de clínicas en EE. UU. y Reino Unido). Expone el Incidents Manager "
    "(consultar, buscar, crear y cambiar el estado de incidencias) y una consulta de SOLO LECTURA del "
    "inventario de insumos médicos. Autenticación OAuth 2.1: cada petición necesita un access token "
    "Bearer del emisor anunciado en /.well-known/oauth-protected-resource/mcp, emitido para esta API. "
    "Scopes: incidents:read (incidents_get, incidents_search), incidents:write (incidents_create, "
    "incidents_update_status) e inventory:read (inventory_query). No existe permiso de escritura de "
    "inventario. Los errores de tool son JSON {\"error\": {\"code\", \"message\", \"details\"}} con code en: "
    "insufficient_scope, read_only_resource, validation_error, not_found, upstream_unavailable. "
    "Nunca envíes datos de pacientes en los textos de una incidencia."
)

VerifyFn = Callable[[str], AuthInfo]


def build_jwt_verifier(auth_server: AuthServerConfig, *, key: Optional[PyJWK] = None) -> VerifyFn:
    """Verificación JWT de MCP Auth (`create_verify_jwt`) con la JWKS cacheada.

    El modo `"jwt"` de `bearer_auth_middleware` crea un cliente JWKS nuevo en
    CADA petición (mcpauth 0.2.0b1, comprobado en su código): una descarga de
    las claves de Logto por llamada. Aquí se crea una vez y PyJWKClient la
    cachea. Como con un verificador propio MCP Auth ya no comprueba el
    emisor, se comprueba aquí con su misma excepción (→ 401 invalid_issuer).
    `key` permite a los tests firmar con una clave local, sin red."""
    expected_issuer = auth_server.metadata.issuer
    source: Union[PyJWK, PyJWKClient] = key or PyJWKClient(
        auth_server.metadata.jwks_uri or "", cache_keys=True, headers={"user-agent": "healthcore-mcp"}
    )
    verify = create_verify_jwt(source)

    def verify_token(token: str) -> AuthInfo:
        info = verify(token)
        if info.issuer != expected_issuer:
            raise MCPAuthBearerAuthException(
                BearerAuthExceptionCode.INVALID_ISSUER,
                cause=MCPAuthBearerAuthExceptionDetails(expected=expected_issuer, actual=info.issuer),
            )
        return info

    return verify_token


class AuthInfoToScope:
    """Copia la identidad validada por MCP Auth al scope ASGI de la petición.

    MCP Auth la deja en una ContextVar; con el transporte Streamable HTTP la
    tool corre en otra tarea asíncrona y no hay garantía de que vea esa
    variable. El scope ASGI, en cambio, es el mismo diccionario que FastMCP
    entrega a la tool como `ctx.request_context.request.scope`."""

    def __init__(self, app: ASGIApp, mcp_auth: MCPAuth) -> None:
        self.app = app
        self.mcp_auth = mcp_auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})["auth_info"] = self.mcp_auth.auth_info
        await self.app(scope, receive, send)


def build_mcp_server(api: HealthCoreApi, settings: McpSettings) -> FastMCP:
    mcp = FastMCP(
        SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=[f"http://{h}" for h in settings.allowed_hosts] + [f"https://{h}" for h in settings.allowed_hosts],
        ),
    )
    register_tools(mcp, api)
    return mcp


def create_app(
    settings: McpSettings,
    *,
    auth_server: Optional[AuthServerConfig] = None,
    verify: Optional[VerifyFn] = None,
    api: Optional[HealthCoreApi] = None,
) -> Starlette:
    """Construye la app. Sin argumentos opcionales, descubre Logto por su
    `/.well-known/openid-configuration` (red al arrancar: si Logto no
    responde, el servidor no arranca, en vez de arrancar sin auth)."""
    auth_server = auth_server or fetch_server_config(settings.issuer, AuthServerType.OIDC)
    mcp_auth = MCPAuth(
        protected_resources=ResourceServerConfig(
            metadata=ResourceServerMetadata(
                resource=settings.resource_url,
                authorization_servers=[auth_server],
                scopes_supported=list(ALL_SCOPES),
                bearer_methods_supported=["header"],
                resource_name="HealthCore company tools (MCP)",
            )
        )
    )
    bearer_auth = mcp_auth.bearer_auth_middleware(
        verify or build_jwt_verifier(auth_server),
        audience=settings.audience,
        resource=settings.resource_url,
    )

    api = api or HealthCoreApi(settings.api_base_url, settings.api_email, settings.api_password, timeout_s=settings.api_timeout_s)
    mcp = build_mcp_server(api, settings)
    mcp_http_app = mcp.streamable_http_app()  # crea también mcp.session_manager

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        # Una app montada no ejecuta su propio lifespan: el gestor de
        # sesiones de MCP se arranca aquí, en la app exterior.
        async with mcp.session_manager.run():
            logger.info("Servidor MCP listo en %s (emisor %s)", settings.resource_url, auth_server.metadata.issuer)
            yield

    app = Starlette(
        routes=[
            *mcp_auth.resource_metadata_router().routes,
            Mount("/", app=mcp_http_app, middleware=[Middleware(bearer_auth), Middleware(AuthInfoToScope, mcp_auth=mcp_auth)]),
        ],
        lifespan=lifespan,
    )
    app.state.mcp = mcp
    app.state.mcp_auth = mcp_auth
    return app

