"""Configuración del servidor MCP de HealthCore, solo por variables de entorno.

Se cargan de `mcps/healthcore/.env` si existe (plantilla en `.env.example`).
Nada de secretos en el código: la contraseña de la cuenta de servicio y los
datos de Logto viven solo en ese `.env`, que está en `.gitignore`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent / ".env"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_RESOURCE_URL = f"http://localhost:{DEFAULT_PORT}/mcp"
DEFAULT_API_URL = "http://localhost:8000"


class McpConfigError(RuntimeError):
    """Falta una variable obligatoria: el servidor no arranca a medias."""


@dataclass(frozen=True)
class McpSettings:
    # OAuth (MCP Auth en modo resource server)
    issuer: str  # emisor OIDC de Logto, p. ej. https://<tenant>.logto.app/oidc
    resource_url: str  # URL pública del endpoint MCP; la anuncia la Protected Resource Metadata
    audience: str  # identificador de la API en Logto: el `aud` que deben llevar los tokens
    # Incidents Manager + inventario (la API FastAPI existente)
    api_base_url: str
    api_email: str
    api_password: str = field(repr=False)
    # Servidor HTTP
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    allowed_hosts: List[str] = field(default_factory=list)
    api_timeout_s: float = 5.0


def _required(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise McpConfigError(f"Falta la variable de entorno {name} (ver mcps/healthcore/.env.example)")
    return value


def _host_of(url: str) -> Optional[str]:
    from urllib.parse import urlparse

    return urlparse(url).netloc or None


def load_settings() -> McpSettings:
    load_dotenv(ENV_FILE)
    resource_url = (os.getenv("MCP_RESOURCE_URL") or DEFAULT_RESOURCE_URL).strip()
    port = int(os.getenv("MCP_PORT") or DEFAULT_PORT)
    # Protección DNS-rebinding del SDK: solo se aceptan peticiones cuyo Host
    # esté en esta lista. Siempre localhost; más el host de MCP_RESOURCE_URL
    # (la URL reenviada de Codespaces) y lo que se añada en MCP_ALLOWED_HOSTS.
    allowed = [f"localhost:{port}", f"127.0.0.1:{port}"]
    resource_host = _host_of(resource_url)
    if resource_host:
        allowed.append(resource_host)
    allowed.extend(h.strip() for h in (os.getenv("MCP_ALLOWED_HOSTS") or "").split(",") if h.strip())
    return McpSettings(
        issuer=_required("MCP_OAUTH_ISSUER"),
        resource_url=resource_url,
        audience=(os.getenv("MCP_AUDIENCE") or resource_url).strip(),
        api_base_url=(os.getenv("HEALTHCORE_API_URL") or DEFAULT_API_URL).rstrip("/"),
        api_email=_required("HEALTHCORE_API_EMAIL"),
        api_password=_required("HEALTHCORE_API_PASSWORD"),
        host=(os.getenv("MCP_HOST") or DEFAULT_HOST).strip(),
        port=port,
        allowed_hosts=sorted(set(allowed)),
    )
