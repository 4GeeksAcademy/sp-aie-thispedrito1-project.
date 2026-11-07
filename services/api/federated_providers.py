"""Proveedor de identidad Microsoft (real y simulado) y validación del id_token.

Flujo OIDC *authorization code* + PKCE (S256) + `nonce`:

1. `authorization_url(...)` → el navegador va a Microsoft.
2. Microsoft vuelve al `redirect_uri` de la lista blanca con `code` y `state`.
3. `exchange_code(...)` canjea el `code` (con `code_verifier`) por un
   `id_token`. **Solo se lee el id_token**: el access/refresh token de
   Microsoft nunca se guarda (BRIEFING §5: "guarda el vínculo, no la sesión").
4. `validate_id_token(...)` comprueba firma, emisor del tenant, audiencia,
   caducidad, `nonce` y `tid`, y devuelve la identidad externa (`oid`).

El simulador (`MockMicrosoftProvider`) emite id_tokens RS256 con una clave
generada en memoria al arrancar y pasa por la MISMA `validate_id_token`.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from federated_config import MODE_MOCK, FederatedSettings

SCOPES = "openid profile email"
CODE_TTL_SECONDS = 300
ID_TOKEN_LEEWAY_SECONDS = 60


class InvalidIdToken(ValueError):
    """El id_token no prueba una identidad válida de NUESTRO tenant."""


class ProviderError(RuntimeError):
    """El proveedor rechazó el canje del code o no respondió."""


@dataclass(frozen=True)
class ExternalIdentity:
    provider_user_id: str  # `oid`: estable dentro del tenant
    email: Optional[str]
    tenant_id: str


# --- PKCE ------------------------------------------------------------------


def new_code_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def code_challenge_s256(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# --- Validación del id_token -------------------------------------------------


def validate_id_token(id_token: str, *, key: Any, settings: FederatedSettings, nonce: str) -> ExternalIdentity:
    try:
        claims = jwt.decode(
            id_token,
            key=key,
            algorithms=["RS256"],
            audience=settings.client_id,
            issuer=settings.issuer,
            leeway=ID_TOKEN_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidIdToken(type(exc).__name__) from exc

    # Single-tenant: el emisor ya lo fija, pero `tid` es la afirmación
    # explícita de Microsoft sobre el tenant de la cuenta.
    if claims.get("tid") != settings.tenant_id:
        raise InvalidIdToken("tenant_mismatch")
    if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise InvalidIdToken("nonce_mismatch")
    oid = claims.get("oid") or claims.get("sub")
    if not isinstance(oid, str) or not oid.strip():
        raise InvalidIdToken("missing_oid")
    email = claims.get("preferred_username") or claims.get("email")
    return ExternalIdentity(
        provider_user_id=oid.strip(),
        email=str(email).strip().lower() if email else None,
        tenant_id=str(claims["tid"]),
    )


# --- Proveedores ---------------------------------------------------------------


class IdentityProvider(Protocol):
    settings: FederatedSettings

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str, redirect_uri: str) -> str: ...

    def exchange_and_validate(
        self, *, code: str, code_verifier: str, redirect_uri: str, nonce: str
    ) -> ExternalIdentity: ...


def _authorize_query(settings: FederatedSettings, *, state: str, nonce: str, code_challenge: str, redirect_uri: str) -> str:
    return urlencode(
        {
            "client_id": settings.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            # Siempre elegir cuenta: evita entrar sin querer con la sesión
            # Microsoft que ya tenga abierta el navegador.
            "prompt": "select_account",
        }
    )


class MicrosoftProvider:
    """Microsoft Entra ID real (endpoints v2.0 de un tenant concreto)."""

    def __init__(
        self,
        settings: FederatedSettings,
        *,
        http_client: Optional[httpx.Client] = None,
        key_resolver: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.settings = settings
        base = f"https://login.microsoftonline.com/{settings.tenant_id}"
        self.authorize_endpoint = f"{base}/oauth2/v2.0/authorize"
        self.token_endpoint = f"{base}/oauth2/v2.0/token"
        self.jwks_uri = f"{base}/discovery/v2.0/keys"
        self._http = http_client
        self._key_resolver = key_resolver

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str, redirect_uri: str) -> str:
        query = _authorize_query(
            self.settings, state=state, nonce=nonce, code_challenge=code_challenge, redirect_uri=redirect_uri
        )
        return f"{self.authorize_endpoint}?{query}"

    def _signing_key(self, id_token: str) -> Any:
        if self._key_resolver is not None:
            return self._key_resolver(id_token)
        return _jwks_client(self.jwks_uri).get_signing_key_from_jwt(id_token).key

    def exchange_and_validate(
        self, *, code: str, code_verifier: str, redirect_uri: str, nonce: str
    ) -> ExternalIdentity:
        data = {
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret or "",
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "scope": SCOPES,
        }
        client = self._http or httpx.Client(timeout=10.0)
        try:
            response = client.post(self.token_endpoint, data=data)
        except httpx.HTTPError as exc:
            raise ProviderError(type(exc).__name__) from exc
        finally:
            if self._http is None:
                client.close()
        if response.status_code != 200:
            # Microsoft responde 400 invalid_grant con un code caducado o
            # reutilizado. No se propaga su texto: puede repetir datos.
            raise ProviderError(f"token_endpoint_{response.status_code}")
        try:
            id_token = response.json()["id_token"]
        except (ValueError, KeyError) as exc:
            raise ProviderError("missing_id_token") from exc
        # El access_token y el refresh_token de la respuesta se descartan aquí.
        try:
            key = self._signing_key(id_token)
        except jwt.PyJWTError as exc:
            raise InvalidIdToken(type(exc).__name__) from exc
        return validate_id_token(id_token, key=key, settings=self.settings, nonce=nonce)


_JWKS_CLIENTS: dict[str, jwt.PyJWKClient] = {}
_JWKS_LOCK = threading.Lock()


def _jwks_client(uri: str) -> jwt.PyJWKClient:
    # Cacheado por proceso: PyJWKClient guarda las claves y solo vuelve a
    # descargar si aparece un `kid` nuevo (rotación de claves de Microsoft).
    with _JWKS_LOCK:
        if uri not in _JWKS_CLIENTS:
            _JWKS_CLIENTS[uri] = jwt.PyJWKClient(uri, cache_keys=True, lifespan=3600)
        return _JWKS_CLIENTS[uri]


# --- Simulador ---------------------------------------------------------------


@dataclass(frozen=True)
class MockIdentity:
    oid: str
    email: str
    name: str
    tenant: Optional[str] = None  # None = el tenant de HealthCore


# Identidades sintéticas (BRIEFING §6). Los oid coinciden con la semilla
# `scripts/seed_federated_auth.py`. Ninguna es una persona real.
MOCK_IDENTITIES: tuple[MockIdentity, ...] = (
    MockIdentity("ms-oid-marcus-001", "marcus.clinical@healthcore.example", "Dr. Marcus Reid"),
    MockIdentity("ms-oid-priya-002", "priya.access@healthcore.example", "Priya Nair"),
    MockIdentity("ms-oid-alex-003", "alex.frontdesk@healthcore.example", "Alex Morgan"),
    MockIdentity("ms-oid-unknown-999", "unknown.staff@healthcore.example", "Cuenta no vinculada"),
    MockIdentity("ms-oid-external-777", "someone@contoso.example", "Cuenta de otro tenant", tenant="contoso-tenant"),
)


@dataclass
class _IssuedCode:
    identity: MockIdentity
    client_id: str
    redirect_uri: str
    nonce: str
    code_challenge: str
    expires_at: float


class MockMicrosoftProvider:
    """Microsoft simulado: autoriza en `routes/mock_microsoft.py` y canjea en
    proceso (sin HTTP a sí mismo). Comprueba PKCE, `redirect_uri` y un solo
    uso del code, igual que Entra."""

    _private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _codes: dict[str, _IssuedCode] = {}
    _lock = threading.Lock()
    KID = "healthcore-mock-key"

    def __init__(self, settings: FederatedSettings) -> None:
        self.settings = settings

    @classmethod
    def public_key(cls) -> Any:
        return cls._private_key.public_key()

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str, redirect_uri: str) -> str:
        query = _authorize_query(
            self.settings, state=state, nonce=nonce, code_challenge=code_challenge, redirect_uri=redirect_uri
        )
        return f"{self.settings.mock_public_url}/dev/mock-microsoft/authorize?{query}"

    @classmethod
    def issue_code(
        cls, *, identity: MockIdentity, client_id: str, redirect_uri: str, nonce: str, code_challenge: str
    ) -> str:
        code = secrets.token_urlsafe(32)
        with cls._lock:
            now = time.time()
            for stale in [key for key, item in cls._codes.items() if item.expires_at < now]:
                del cls._codes[stale]
            cls._codes[code] = _IssuedCode(
                identity, client_id, redirect_uri, nonce, code_challenge, now + CODE_TTL_SECONDS
            )
        return code

    def sign_id_token(self, identity: MockIdentity, *, nonce: str, lifetime_s: int = 3600, **overrides: Any) -> str:
        now = int(time.time())
        claims = {
            "iss": self.settings.issuer,
            "aud": self.settings.client_id,
            "iat": now,
            "nbf": now,
            "exp": now + lifetime_s,
            "tid": identity.tenant or self.settings.tenant_id,
            "oid": identity.oid,
            "sub": f"pairwise-{identity.oid}",
            "preferred_username": identity.email,
            "name": identity.name,
            "nonce": nonce,
        }
        claims.update(overrides)
        return jwt.encode(claims, self._private_key, algorithm="RS256", headers={"kid": self.KID})

    def exchange_and_validate(
        self, *, code: str, code_verifier: str, redirect_uri: str, nonce: str
    ) -> ExternalIdentity:
        with self._lock:
            issued = self._codes.pop(code, None)  # un solo uso
        if issued is None or issued.expires_at < time.time():
            raise ProviderError("invalid_grant")
        if issued.redirect_uri != redirect_uri or issued.client_id != self.settings.client_id:
            raise ProviderError("invalid_grant")
        if not secrets.compare_digest(code_challenge_s256(code_verifier), issued.code_challenge):
            raise ProviderError("invalid_grant")
        id_token = self.sign_id_token(issued.identity, nonce=issued.nonce)
        return validate_id_token(id_token, key=self.public_key(), settings=self.settings, nonce=nonce)


def find_mock_identity(oid: str) -> Optional[MockIdentity]:
    return next((item for item in MOCK_IDENTITIES if item.oid == oid), None)


def build_provider(settings: FederatedSettings) -> IdentityProvider:
    if settings.mode == MODE_MOCK:
        return MockMicrosoftProvider(settings)
    return MicrosoftProvider(settings)
