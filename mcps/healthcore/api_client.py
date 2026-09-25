"""Cliente HTTP del servidor MCP hacia la API de HealthCore (services/api).

El servidor MCP no toca ninguna base de datos: habla con el Incidents Manager
y con el inventario por sus endpoints de siempre, igual que el backoffice.
Así toda escritura pasa por la validación de la API (p. ej. las transiciones
de estado de `PATCH /api/incidents/{id}/status`) y nada se duplica aquí.

Identidad hacia la API: una cuenta de servicio (rol `user`, nunca admin) con
login en `POST /auth/login`. El token de la API se reutiliza hasta un minuto
antes de caducar y se renueva una vez si la API responde 401.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import httpx

from mcps.healthcore.errors import NOT_FOUND, UPSTREAM_UNAVAILABLE, VALIDATION_ERROR, McpToolError

TOKEN_REFRESH_MARGIN_S = 60
DEFAULT_TOKEN_TTL_S = 25 * 60  # si el token no trae `exp` legible


class ReadOnlyViolation(PermissionError):
    """Se intentó un método de escritura a través del lector de inventario."""


def _token_expiry(token: str) -> float:
    """`exp` del JWT de la API, sin verificar la firma: solo sirve para saber
    cuándo renovarlo. Quien lo verifica de verdad es la propia API."""
    import jwt

    try:
        exp = jwt.decode(token, options={"verify_signature": False}).get("exp")
        return float(exp) if exp else time.time() + DEFAULT_TOKEN_TTL_S
    except jwt.PyJWTError:
        return time.time() + DEFAULT_TOKEN_TTL_S


class HealthCoreApi:
    def __init__(
        self,
        base_url: str,
        email: str,
        password: str,
        *,
        timeout_s: float = 5.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._timeout_s = timeout_s
        self._transport = transport  # los tests pasan la app FastAPI en memoria
        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self._lock = asyncio.Lock()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout_s, transport=self._transport)

    async def _login(self, client: httpx.AsyncClient) -> str:
        response = await client.post("/auth/login", json={"email": self._email, "password": self._password})
        if response.status_code != 200:
            # Credenciales de la cuenta de servicio mal configuradas: para el
            # cliente MCP es "el servicio no está disponible", no su culpa.
            raise McpToolError(UPSTREAM_UNAVAILABLE, "La cuenta de servicio del servidor MCP no pudo autenticarse en la API.")
        token = response.json()["access_token"]
        self._token, self._token_expires_at = token, _token_expiry(token)
        return token

    async def _valid_token(self, client: httpx.AsyncClient, *, force: bool = False) -> str:
        async with self._lock:
            if force or not self._token or time.time() >= self._token_expires_at - TOKEN_REFRESH_MARGIN_S:
                return await self._login(client)
            return self._token

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Llama a la API y devuelve el JSON, o lanza un McpToolError con código."""
        try:
            async with self._client() as client:
                token = await self._valid_token(client)
                response = await client.request(method, path, params=params, json=json, headers={"Authorization": f"Bearer {token}"})
                if response.status_code == 401:
                    token = await self._valid_token(client, force=True)
                    response = await client.request(method, path, params=params, json=json, headers={"Authorization": f"Bearer {token}"})
        except McpToolError:
            raise
        except httpx.HTTPError as exc:
            raise McpToolError(UPSTREAM_UNAVAILABLE, f"La API de HealthCore no respondió ({type(exc).__name__}).") from exc
        return _decode(response)


def _decode(response: httpx.Response) -> Any:
    if response.status_code in (200, 201):
        return response.json()
    if response.status_code == 404:
        raise McpToolError(NOT_FOUND, "El recurso solicitado no existe.")
    if response.status_code in (400, 422):
        detail = _json_or_none(response)
        fields = (detail or {}).get("detail", {}).get("errors") if isinstance((detail or {}).get("detail"), dict) else None
        raise McpToolError(VALIDATION_ERROR, "La API rechazó los datos enviados.", {"fields": fields or []})
    raise McpToolError(UPSTREAM_UNAVAILABLE, f"La API de HealthCore respondió con un estado inesperado ({response.status_code}).")


def _json_or_none(response: httpx.Response) -> Optional[Dict[str, Any]]:
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except ValueError:
        return None


class InventoryReader:
    """Única vía de la tool de inventario hacia la API: solo GET.

    Es la tercera capa del "solo lectura por diseño" (las otras dos son que no
    existe ningún scope de escritura de inventario y que la tool rechaza las
    acciones de escritura): aunque un bug en la tool intentara escribir, este
    objeto no sabe hacerlo y lo dice con una excepción propia."""

    ALLOWED_METHODS = frozenset({"GET"})

    def __init__(self, api: HealthCoreApi) -> None:
        self._api = api

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if method.upper() not in self.ALLOWED_METHODS:
            raise ReadOnlyViolation(f"El inventario es de solo lectura: método {method.upper()} no permitido")
        if not path.startswith("/inventory/"):
            raise ReadOnlyViolation("El lector de inventario solo accede a /inventory/")
        return await self._api.request("GET", path, **kwargs)

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)
