"""Autenticación federada con Microsoft (BRIEFING de Autenticación Federada).

Dos flujos que comparten proveedor pero NO lógica:

- Vinculación: `POST /{provider}/link/start` EXIGE sesión (contraseña) y el
  state queda atado al user_id de quien lo pidió. Es el único sitio donde
  nace un vínculo.
- Login: `POST /{provider}/login/start` es público y solo crea states de
  propósito "login", que en el callback NUNCA crean ni vinculan cuentas: si
  la identidad no está vinculada, 401 `not_linked` + evento de seguridad.

El callback (`POST /{provider}/callback`) es común: el propósito sale del
state guardado en el servidor, nunca de lo que diga el navegador.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

from access_models import CAP_USERS_MANAGE
from audit_log import audit_event
from auth_repository import AuthRepository
from federated_config import SUPPORTED_PROVIDERS, FederatedConfigError, FederatedSettings, configured_mode, load_settings
from federated_models import (
    FederatedAccountStatus,
    FederatedAuditEvent,
    FederatedAuditResponse,
    FederatedCallbackRequest,
    FederatedCallbackResponse,
    FederatedLinkPublic,
    FederatedProvider,
    FederatedProvidersResponse,
    FederatedStartRequest,
    FederatedStartResponse,
    FederatedUnlinkResponse,
)
from federated_providers import (
    InvalidIdToken,
    ProviderError,
    build_provider,
    code_challenge_s256,
    new_code_verifier,
)
from federated_repository import FederatedRepository, IdentityAlreadyLinked, ProviderAlreadyLinked
from federated_rules import (
    AUDIT_EVENTS,
    EVENT_LINK,
    EVENT_LINK_REJECTED,
    EVENT_LOGIN_REJECTED,
    EVENT_LOGIN_SUCCESS,
    EVENT_UNLINK,
    PURPOSE_LINK,
    PURPOSE_LOGIN,
    REASONS,
    has_password,
    link_block_reason,
    login_reject_reason,
    unlink_block_reason,
    unlink_warning,
)
from permissions import require_capability
from security import create_access_token, get_current_user, resolve_access, user_from_token

logger = logging.getLogger("healthcore.federated")

router = APIRouter(prefix="/auth/federated", tags=["auth-federated"])
auth_repo = AuthRepository()
optional_bearer = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)
require_audit_reader = require_capability(CAP_USERS_MANAGE)


# --- Utilidades -----------------------------------------------------------------


def _audit(
    event: str,
    *,
    provider: str,
    user_id: Optional[str],
    provider_user_id: Optional[str],
    reason: Optional[str] = None,
) -> None:
    """Traza consultable (tabla) + línea JSON en `healthcore.audit` (log)."""
    FederatedRepository().record_event(
        event, user_id=user_id, provider=provider, provider_user_id=provider_user_id, reason=reason
    )
    fields: dict[str, Any] = {"provider": provider}
    if reason:
        fields["reason"] = reason
    if provider_user_id:
        fields["external_identity"] = provider_user_id
    audit_event(event, actor_id=user_id, **fields)


def _fail(reason: str) -> NoReturn:
    status_code, message = REASONS[reason]
    raise HTTPException(status_code=status_code, detail={"code": reason, "message": message})


def _reject(
    reason: str,
    *,
    event: str,
    provider: str,
    user_id: Optional[str] = None,
    provider_user_id: Optional[str] = None,
) -> NoReturn:
    _audit(event, provider=provider, user_id=user_id, provider_user_id=provider_user_id, reason=reason)
    _fail(reason)


def _checked_provider(provider: str) -> str:
    if provider not in SUPPORTED_PROVIDERS:
        _fail("unsupported_provider")
    return provider


def _settings() -> FederatedSettings:
    try:
        return load_settings()
    except FederatedConfigError as exc:
        # El motivo exacto va al log de operación, nunca al cliente.
        logger.warning("federated login disabled: %s", exc)
        _fail("provider_disabled")


def _link_public(link: dict[str, Any]) -> FederatedLinkPublic:
    return FederatedLinkPublic(
        provider=link["provider"],
        provider_user_id=link["provider_user_id"],
        email_at_link_time=link.get("email_at_link_time"),
        linked_at=link["linked_at"],
    )


def _account_status(user: dict[str, Any]) -> FederatedAccountStatus:
    links = FederatedRepository().links_for_user(user["id"])
    password = has_password(user)
    # Aviso solo si hay algo que desvincular: el último vínculo que queda.
    warning = (
        unlink_warning(has_password=password, other_links=len(links) - 1) if links else None
    )
    return FederatedAccountStatus(
        links=[_link_public(link) for link in links], has_password=password, unlink_warning=warning
    )


def _start(settings: FederatedSettings, *, provider: str, purpose: str, user_id: Optional[str], redirect_uri: str) -> FederatedStartResponse:
    if not settings.is_allowed_redirect(redirect_uri):
        _fail("redirect_uri_not_allowed")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = new_code_verifier()
    FederatedRepository().save_state(
        state=state,
        purpose=purpose,
        provider=provider,
        user_id=user_id,
        code_verifier=code_verifier,
        nonce=nonce,
        redirect_uri=redirect_uri,
        ttl_seconds=settings.state_ttl_seconds,
    )
    url = build_provider(settings).authorization_url(
        state=state, nonce=nonce, code_challenge=code_challenge_s256(code_verifier), redirect_uri=redirect_uri
    )
    return FederatedStartResponse(authorization_url=url, state=state, expires_in=settings.state_ttl_seconds)


# --- Endpoints ------------------------------------------------------------------


@router.get("/providers", response_model=FederatedProvidersResponse)
def list_providers() -> FederatedProvidersResponse:
    """Público: la pantalla de login decide si muestra el botón."""
    try:
        mode = configured_mode()
        enabled = mode is not None and load_settings() is not None
    except FederatedConfigError:
        mode, enabled = None, False
    return FederatedProvidersResponse(
        providers=[FederatedProvider(provider=name, enabled=enabled, mode=mode if enabled else None) for name in SUPPORTED_PROVIDERS]
    )


@router.post("/{provider}/login/start", response_model=FederatedStartResponse)
def start_login(provider: str, payload: FederatedStartRequest) -> FederatedStartResponse:
    provider = _checked_provider(provider)
    return _start(_settings(), provider=provider, purpose=PURPOSE_LOGIN, user_id=None, redirect_uri=payload.redirect_uri)


@router.post("/{provider}/link/start", response_model=FederatedStartResponse)
def start_link(
    provider: str, payload: FederatedStartRequest, current_user: dict[str, Any] = Depends(get_current_user)
) -> FederatedStartResponse:
    provider = _checked_provider(provider)
    settings = _settings()
    reason = link_block_reason(current_user)
    if reason:
        _reject(reason, event=EVENT_LINK_REJECTED, provider=provider, user_id=current_user["id"])
    return _start(settings, provider=provider, purpose=PURPOSE_LINK, user_id=current_user["id"], redirect_uri=payload.redirect_uri)


@router.post("/{provider}/callback", response_model=FederatedCallbackResponse)
def callback(
    provider: str, payload: FederatedCallbackRequest, token: Optional[str] = Depends(optional_bearer)
) -> FederatedCallbackResponse:
    provider = _checked_provider(provider)
    settings = _settings()
    repo = FederatedRepository()

    record = repo.consume_state(payload.state)  # un solo uso, aunque falle después
    if record is None or record["provider"] != provider:
        # Propósito desconocido: se audita como login rechazado (es el caso
        # peligroso: un callback fabricado o repetido).
        _reject("invalid_state", event=EVENT_LOGIN_REJECTED, provider=provider)
    purpose = record["purpose"]
    rejected_event = EVENT_LINK_REJECTED if purpose == PURPOSE_LINK else EVENT_LOGIN_REJECTED

    # redirect_uri: en lista blanca Y el mismo con el que empezó el flujo.
    if not settings.is_allowed_redirect(payload.redirect_uri) or payload.redirect_uri != record["redirect_uri"]:
        _reject("redirect_uri_mismatch", event=rejected_event, provider=provider, user_id=record.get("user_id"))

    linking_user: Optional[dict[str, Any]] = None
    if purpose == PURPOSE_LINK:
        # La sesión que termina el flujo debe ser la que lo empezó: un state
        # de vinculación robado no sirve para atar una identidad a otra cuenta.
        linking_user = user_from_token(token)
        if linking_user is None:
            _reject("login_required", event=rejected_event, provider=provider, user_id=record.get("user_id"))
        if linking_user["id"] != record["user_id"]:
            _reject("link_session_mismatch", event=rejected_event, provider=provider, user_id=record["user_id"])

    try:
        identity = build_provider(settings).exchange_and_validate(
            code=payload.code,
            code_verifier=record["code_verifier"],
            redirect_uri=record["redirect_uri"],
            nonce=record["nonce"],
        )
    except InvalidIdToken as exc:
        logger.warning("federated id_token rejected: %s", exc)
        _reject("invalid_id_token", event=rejected_event, provider=provider, user_id=record.get("user_id"))
    except ProviderError as exc:
        logger.warning("federated code exchange failed: %s", exc)
        _reject("provider_error", event=rejected_event, provider=provider, user_id=record.get("user_id"))

    if purpose == PURPOSE_LINK:
        return _complete_link(repo, provider, linking_user, identity)
    return _complete_login(repo, provider, identity)


def _complete_login(repo: FederatedRepository, provider: str, identity) -> FederatedCallbackResponse:
    link = repo.find_link(provider, identity.provider_user_id)
    user = auth_repo.get_user_by_id(link["user_id"]) if link else None
    reason = login_reject_reason(link, user)
    if reason:
        # Sin crear fila de usuario (BRIEFING §7): aquí no hay ni un insert.
        _reject(
            reason,
            event=EVENT_LOGIN_REJECTED,
            provider=provider,
            user_id=user["id"] if user else None,
            provider_user_id=identity.provider_user_id,
        )
    resolved = resolve_access(user)
    _audit(EVENT_LOGIN_SUCCESS, provider=provider, user_id=user["id"], provider_user_id=identity.provider_user_id)
    # Misma sesión que /auth/login: mismo JWT, misma caducidad
    # (ACCESS_TOKEN_EXPIRE_MINUTES) y misma revocación (cuenta desactivada →
    # user_from_token la rechaza en la siguiente petición).
    return FederatedCallbackResponse(
        purpose=PURPOSE_LOGIN,
        message="Signed in with Microsoft.",
        access_token=create_access_token(subject=user["id"], role=resolved["role"]),
        token_type="bearer",
    )


def _complete_link(repo: FederatedRepository, provider: str, user: dict[str, Any], identity) -> FederatedCallbackResponse:
    reason = link_block_reason(user)
    if reason:
        _reject(reason, event=EVENT_LINK_REJECTED, provider=provider, user_id=user["id"], provider_user_id=identity.provider_user_id)
    try:
        link, created = repo.create_link(
            user_id=user["id"],
            provider=provider,
            provider_user_id=identity.provider_user_id,
            email_at_link_time=identity.email,
            provisioned_by_it=True,
        )
    except IdentityAlreadyLinked:
        _reject("identity_in_use", event=EVENT_LINK_REJECTED, provider=provider, user_id=user["id"], provider_user_id=identity.provider_user_id)
    except ProviderAlreadyLinked:
        _reject("provider_already_linked", event=EVENT_LINK_REJECTED, provider=provider, user_id=user["id"], provider_user_id=identity.provider_user_id)
    if created:
        _audit(EVENT_LINK, provider=provider, user_id=user["id"], provider_user_id=identity.provider_user_id)
    return FederatedCallbackResponse(
        purpose=PURPOSE_LINK,
        message="Microsoft account linked." if created else "This Microsoft account was already linked.",
        link=_link_public(link),
    )


@router.get("/links", response_model=FederatedAccountStatus)
def my_links(current_user: dict[str, Any] = Depends(get_current_user)) -> FederatedAccountStatus:
    return _account_status(current_user)


@router.delete("/{provider}", response_model=FederatedUnlinkResponse)
def unlink(provider: str, current_user: dict[str, Any] = Depends(get_current_user)) -> FederatedUnlinkResponse:
    provider = _checked_provider(provider)
    repo = FederatedRepository()
    links = repo.links_for_user(current_user["id"])
    target = next((link for link in links if link["provider"] == provider), None)
    if target is None:
        _fail("not_linked_to_you")
    reason = unlink_block_reason(has_password=has_password(current_user), other_links=len(links) - 1)
    if reason:
        _fail(reason)
    repo.delete_link(current_user["id"], provider)
    _audit(EVENT_UNLINK, provider=provider, user_id=current_user["id"], provider_user_id=target["provider_user_id"])
    return FederatedUnlinkResponse(
        message="Microsoft account unlinked.", provider=provider, account=_account_status(current_user)
    )


@router.get("/audit", response_model=FederatedAuditResponse)
def audit_trail(
    event: Optional[str] = None, limit: int = 200, _admin: dict[str, Any] = Depends(require_audit_reader)
) -> FederatedAuditResponse:
    if event is not None and event not in AUDIT_EVENTS:
        raise HTTPException(status_code=400, detail={"errors": [{"field": "event", "message": "Unknown event"}]})
    rows = FederatedRepository().list_events(event=event, limit=max(1, min(limit, 1000)))
    return FederatedAuditResponse(events=[FederatedAuditEvent(**row) for row in rows])
