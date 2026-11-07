"""Simulador de la pantalla de Microsoft (solo con MICROSOFT_IDP_MODE=mock).

Sustituye a login.microsoftonline.com en desarrollo y en la demo sin
registro en Azure: muestra las identidades sintéticas de
`federated_providers.MOCK_IDENTITIES` y vuelve al `redirect_uri` con un `code`.
Valida lo mismo que Entra antes de redirigir (client_id, redirect_uri
registrado, PKCE S256): un `redirect_uri` fuera de la lista blanca NUNCA
recibe una redirección, solo una página de error.

Fuera de modo simulado responde 404. No aparece en el esquema OpenAPI.
"""

from __future__ import annotations

from html import escape
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from federated_config import MODE_MOCK, FederatedConfigError, FederatedSettings, load_settings
from federated_providers import MOCK_IDENTITIES, MockMicrosoftProvider, find_mock_identity

router = APIRouter(prefix="/dev/mock-microsoft", include_in_schema=False)

PAGE_STYLE = (
    "body{font-family:system-ui,sans-serif;background:#f3f2f1;color:#1b1b1b;margin:0;padding:40px 16px}"
    "main{max-width:440px;margin:0 auto;background:#fff;padding:32px;box-shadow:0 2px 6px rgba(0,0,0,.2)}"
    "h1{font-size:1.4rem;margin:0 0 4px}p.note{color:#605e5c;font-size:.85rem}"
    "a.id{display:block;padding:12px;margin:8px 0;border:1px solid #c8c6c4;text-decoration:none;color:inherit}"
    "a.id:hover{background:#f3f2f1}small{color:#605e5c}a.cancel{color:#0067b8}"
)


def _mock_settings() -> FederatedSettings:
    try:
        settings = load_settings()
    except FederatedConfigError:
        settings = None
    if settings is None or settings.mode != MODE_MOCK:
        raise HTTPException(status_code=404, detail="Not Found")
    return settings


def _error_page(message: str) -> HTMLResponse:
    body = f"<main><h1>Microsoft (simulado)</h1><p>{escape(message)}</p></main>"
    return HTMLResponse(f"<!doctype html><style>{PAGE_STYLE}</style>{body}", status_code=400)


def _validate(settings: FederatedSettings, client_id: str, redirect_uri: str, response_type: str, code_challenge_method: str) -> Optional[str]:
    if client_id != settings.client_id:
        return "client_id desconocido."
    if not settings.is_allowed_redirect(redirect_uri):
        return "redirect_uri no registrado para esta aplicación."
    if response_type != "code" or code_challenge_method != "S256":
        return "Solo se admite response_type=code con PKCE S256."
    return None


@router.get("/authorize")
def authorize(
    client_id: str = "",
    redirect_uri: str = "",
    response_type: str = "",
    state: str = "",
    nonce: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "",
    scope: str = "",
) -> HTMLResponse:
    settings = _mock_settings()
    error = _validate(settings, client_id, redirect_uri, response_type, code_challenge_method)
    if error or not (state and nonce and code_challenge):
        return _error_page(error or "Faltan state, nonce o code_challenge.")

    carry = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
    }
    rows = []
    for identity in MOCK_IDENTITIES:
        href = "select?" + urlencode({**carry, "oid": identity.oid})
        tenant = "otro tenant" if identity.tenant else "tenant HealthCore"
        rows.append(
            f'<a class="id" href="{escape(href)}"><strong>{escape(identity.name)}</strong><br>'
            f"<small>{escape(identity.email)} · oid {escape(identity.oid)} · {tenant}</small></a>"
        )
    cancel = escape(f"{redirect_uri}?" + urlencode({"error": "access_denied", "state": state}))
    body = (
        "<main><h1>Microsoft</h1><p class=note>Simulador local de HealthCore: identidades sintéticas, "
        "ninguna cuenta real.</p><p>Elige una cuenta</p>"
        + "".join(rows)
        + f'<p><a class="cancel" href="{cancel}">Cancelar</a></p></main>'
    )
    return HTMLResponse(f"<!doctype html><title>Microsoft (simulado)</title><style>{PAGE_STYLE}</style>{body}")


@router.get("/select")
def select_identity(
    oid: str = Query(...),
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    nonce: str = "",
    code_challenge: str = "",
):
    settings = _mock_settings()
    error = _validate(settings, client_id, redirect_uri, "code", "S256")
    identity = find_mock_identity(oid)
    if error or identity is None or not (state and nonce and code_challenge):
        return _error_page(error or "Identidad o parámetros no válidos.")
    code = MockMicrosoftProvider.issue_code(
        identity=identity, client_id=client_id, redirect_uri=redirect_uri, nonce=nonce, code_challenge=code_challenge
    )
    return RedirectResponse(f"{redirect_uri}?" + urlencode({"code": code, "state": state}), status_code=302)
