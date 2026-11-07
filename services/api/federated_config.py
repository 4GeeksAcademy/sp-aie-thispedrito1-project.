"""Configuración de la autenticación federada (BRIEFING de Autenticación Federada §5).

Un solo proveedor permitido: Microsoft identity platform, en modo
single-tenant. Tres modos, elegidos con `MICROSOFT_IDP_MODE`:

- `microsoft`: Microsoft Entra real. Exige `MICROSOFT_CLIENT_ID`,
  `MICROSOFT_CLIENT_SECRET` y `MICROSOFT_TENANT_ID` (un tenant concreto:
  `common`/`organizations`/`consumers` se rechazan, porque abrirían el login
  a "cualquier cuenta Microsoft").
- `mock`: simulador local de Microsoft (`routes/mock_microsoft.py`) para
  tests y para la demo sin registro en Azure. Mismo flujo, mismas
  validaciones del `id_token`; solo cambia quién lo firma.
- vacío / sin configurar: login federado desactivado (la API funciona igual).

Se lee en cada llamada (es barato) para que los tests cambien el modo con
`monkeypatch.setenv` sin reimportar la app.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

PROVIDER_MICROSOFT = "microsoft"
# BRIEFING §2: único proveedor permitido. Google/Apple/LinkedIn → 404.
SUPPORTED_PROVIDERS = (PROVIDER_MICROSOFT,)

MODE_MICROSOFT = "microsoft"
MODE_MOCK = "mock"

DEFAULT_REDIRECT_URI = "http://localhost:3001/auth/callback/microsoft"
DEFAULT_STATE_TTL_SECONDS = 600
MULTI_TENANT_ALIASES = frozenset({"common", "organizations", "consumers"})

MOCK_CLIENT_ID = "healthcore-mock-client"
MOCK_TENANT_ID = "healthcore-mock-tenant"
DEFAULT_MOCK_PUBLIC_URL = "http://localhost:8000"


class FederatedConfigError(RuntimeError):
    """Configuración incompleta o insegura: el proveedor queda desactivado."""


@dataclass(frozen=True)
class FederatedSettings:
    mode: str
    client_id: str
    client_secret: Optional[str]
    tenant_id: str
    allowed_redirect_uris: tuple[str, ...]
    state_ttl_seconds: int
    mock_public_url: str

    @property
    def issuer(self) -> str:
        # Mismo formato de emisor en real y en simulado: la validación del
        # id_token es idéntica en los dos modos.
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    def is_allowed_redirect(self, redirect_uri: Optional[str]) -> bool:
        # Comparación exacta de cadena, sin normalizar: lo mismo que hace
        # Microsoft con las URIs registradas en la app.
        return bool(redirect_uri) and redirect_uri in self.allowed_redirect_uris


def _allowed_redirects() -> tuple[str, ...]:
    raw = os.getenv("OAUTH_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    uris = tuple(item.strip() for item in raw.split(",") if item.strip())
    for uri in uris:
        if "*" in uri or not uri.startswith(("http://", "https://")):
            raise FederatedConfigError("OAUTH_REDIRECT_URI must list exact http(s) URLs")
    return uris


def _state_ttl() -> int:
    raw = os.getenv("OAUTH_STATE_TTL_SECONDS", str(DEFAULT_STATE_TTL_SECONDS))
    try:
        value = int(raw)
    except ValueError as exc:
        raise FederatedConfigError("OAUTH_STATE_TTL_SECONDS must be an integer") from exc
    if value <= 0:
        raise FederatedConfigError("OAUTH_STATE_TTL_SECONDS must be positive")
    return value


def configured_mode() -> Optional[str]:
    """Modo pedido en el entorno, sin validar el resto (None = desactivado)."""
    mode = os.getenv("MICROSOFT_IDP_MODE", "").strip().lower()
    if not mode:
        # Compatibilidad con el .env literal del BRIEFING: con las tres
        # variables de Microsoft puestas, el modo real se activa solo.
        if os.getenv("MICROSOFT_CLIENT_ID") and os.getenv("MICROSOFT_TENANT_ID"):
            return MODE_MICROSOFT
        return None
    if mode not in (MODE_MICROSOFT, MODE_MOCK):
        raise FederatedConfigError("MICROSOFT_IDP_MODE must be 'microsoft' or 'mock'")
    return mode


def load_settings() -> FederatedSettings:
    """Configuración válida o `FederatedConfigError` (nunca a medias)."""
    mode = configured_mode()
    if mode is None:
        raise FederatedConfigError("Federated login with Microsoft is not configured")

    if mode == MODE_MOCK:
        return FederatedSettings(
            mode=mode,
            client_id=os.getenv("MICROSOFT_CLIENT_ID") or MOCK_CLIENT_ID,
            client_secret=None,
            tenant_id=os.getenv("MICROSOFT_TENANT_ID") or MOCK_TENANT_ID,
            allowed_redirect_uris=_allowed_redirects(),
            state_ttl_seconds=_state_ttl(),
            mock_public_url=(os.getenv("MOCK_MICROSOFT_PUBLIC_URL") or DEFAULT_MOCK_PUBLIC_URL).rstrip("/"),
        )

    client_id = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
    client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()
    tenant_id = os.getenv("MICROSOFT_TENANT_ID", "").strip()
    if not (client_id and client_secret and tenant_id):
        raise FederatedConfigError(
            "MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET and MICROSOFT_TENANT_ID are required"
        )
    if tenant_id.lower() in MULTI_TENANT_ALIASES:
        raise FederatedConfigError("MICROSOFT_TENANT_ID must be HealthCore's tenant, not a multi-tenant alias")
    return FederatedSettings(
        mode=mode,
        client_id=client_id,
        client_secret=client_secret,
        tenant_id=tenant_id,
        allowed_redirect_uris=_allowed_redirects(),
        state_ttl_seconds=_state_ttl(),
        mock_public_url=DEFAULT_MOCK_PUBLIC_URL,
    )
