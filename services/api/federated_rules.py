"""Reglas de negocio de la autenticación federada (puras: sin BD ni HTTP).

Cada función devuelve `None` si la acción está permitida, o un CÓDIGO de
motivo. El router traduce el código a la respuesta HTTP y a la auditoría; así
el mismo motivo queda igual en la respuesta, en la traza y en los tests.
"""

from __future__ import annotations

from typing import Any, Optional

STAFF_AUDIENCE = "staff"

# Eventos del BRIEFING §7 (obligatorios) + uno propio para los intentos de
# vinculación rechazados, que también son eventos de seguridad.
EVENT_LINK = "federated_link"
EVENT_UNLINK = "federated_unlink"
EVENT_LOGIN_SUCCESS = "federated_login_success"
EVENT_LOGIN_REJECTED = "federated_login_rejected"
EVENT_LINK_REJECTED = "federated_link_rejected"
AUDIT_EVENTS = (EVENT_LINK, EVENT_UNLINK, EVENT_LOGIN_SUCCESS, EVENT_LOGIN_REJECTED, EVENT_LINK_REJECTED)

PURPOSE_LOGIN = "login"
PURPOSE_LINK = "link"

# Motivos (código → (estado HTTP, mensaje en inglés de la API)). El backoffice
# muestra su propio texto en español por código (types/federated.ts).
REASONS: dict[str, tuple[int, str]] = {
    "not_linked": (401, "This Microsoft account is not linked to any HealthCore account."),
    "account_inactive": (401, "This HealthCore account is disabled."),
    "invalid_id_token": (401, "Microsoft did not return a valid identity for HealthCore's tenant."),
    "invalid_state": (400, "The sign-in request is invalid, expired or was already used."),
    "redirect_uri_not_allowed": (400, "The redirect URI is not allowed."),
    "redirect_uri_mismatch": (400, "The redirect URI does not match the sign-in request."),
    "provider_error": (502, "Microsoft could not complete the sign-in. Try again."),
    "login_required": (401, "Linking requires an authenticated session."),
    "link_session_mismatch": (403, "This linking request belongs to another session."),
    "not_provisioned": (403, "Your account has not been provisioned by IT for Microsoft sign-in."),
    "not_staff": (403, "Microsoft sign-in is only available to HealthCore staff."),
    "identity_in_use": (409, "This Microsoft account is already linked to another HealthCore account."),
    "provider_already_linked": (409, "Your account already has a different Microsoft account linked."),
    "not_linked_to_you": (404, "Your account has no Microsoft account linked."),
    "last_access_method": (409, "Microsoft is your only sign-in method. Set a password before unlinking."),
    "unsupported_provider": (404, "Unsupported identity provider."),
    "provider_disabled": (503, "Microsoft sign-in is not configured."),
}


def has_password(user: dict[str, Any]) -> bool:
    return bool(user.get("hashed_password"))


def link_block_reason(user: dict[str, Any]) -> Optional[str]:
    """¿Puede ESTA cuenta vincular un proveedor? (BRIEFING §4)

    Falla cerrado: una cuenta sin `provisioned_by_it` explícito no vincula.
    `audience` ausente = staff, porque la tabla `users` de esta API solo
    contiene personal (el portal de pacientes tiene su propia identidad)."""
    if user.get("audience", STAFF_AUDIENCE) != STAFF_AUDIENCE:
        return "not_staff"
    if user.get("provisioned_by_it") is not True:
        return "not_provisioned"
    if not user.get("is_active", True):
        return "account_inactive"
    return None


def login_reject_reason(link: Optional[dict[str, Any]], user: Optional[dict[str, Any]]) -> Optional[str]:
    """¿Se rechaza un login federado? `link` = vínculo de la identidad.

    Nunca se busca la cuenta por email: que el correo de Microsoft coincida
    con el de una cuenta NO la vincula (ese es justo el atajo que el CTO veta).
    """
    if link is None or user is None:
        return "not_linked"
    if not user.get("is_active", True):
        return "account_inactive"
    if user.get("audience", STAFF_AUDIENCE) != STAFF_AUDIENCE:
        return "not_staff"
    return None


def unlink_block_reason(*, has_password: bool, other_links: int) -> Optional[str]:
    """¿Se puede quitar este vínculo sin dejar la cuenta sin acceso?

    `has_password`: la cuenta tiene contraseña (método tradicional).
    `other_links`: cuántos proveedores externos le quedarían DESPUÉS de quitar
    este (hoy siempre 0: solo existe Microsoft, BRIEFING §2).

    Devuelve None si se permite, o "last_access_method" si al desvincular la
    cuenta se quedaría sin ninguna forma de entrar.

    Learn by Doing delegado al asistente (2026-11-07). Pregunta única: tras
    desvincular, ¿le queda al menos una puerta? Una contraseña lo es; otro
    proveedor vinculado también. Falla cerrado: un `other_links` negativo
    (datos incoherentes) cuenta como "ninguna".
    """
    if has_password or other_links > 0:
        return None
    return "last_access_method"


def unlink_warning(*, has_password: bool, other_links: int) -> Optional[str]:
    """Código de aviso para la UI (mismo criterio que la API)."""
    return unlink_block_reason(has_password=has_password, other_links=other_links)
