"""Autenticación federada con Microsoft (BRIEFING de Autenticación Federada).

Recorre los flujos completos contra el simulador de Microsoft
(`MICROSOFT_IDP_MODE=mock`): start → pantalla de Microsoft → callback. El
simulador firma id_tokens RS256 que pasan por la MISMA validación que los de
Entra, así que estos tests ejercitan state, PKCE, nonce, tenant y
redirect_uri de verdad, sin red.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from tinydb import Query

import database
from auth_repository import AuthRepository
from conftest import provision_user
from federated_config import FederatedConfigError, FederatedSettings, load_settings
from federated_providers import (
    InvalidIdToken,
    MicrosoftProvider,
    MockMicrosoftProvider,
    code_challenge_s256,
    find_mock_identity,
    validate_id_token,
)
from federated_repository import FederatedRepository
from federated_rules import unlink_block_reason
from security import create_access_token

CALLBACK = "http://localhost:3001/auth/callback/microsoft"
OTHER_CALLBACK = "http://localhost:3002/auth/callback/microsoft"

MARCUS = "marcus.clinical@healthcore.example"
PRIYA = "priya.access@healthcore.example"
ALEX = "alex.frontdesk@healthcore.example"
JAMES = "james.admin@healthcore.example"


@pytest.fixture(autouse=True)
def mock_microsoft(monkeypatch):
    monkeypatch.setenv("MICROSOFT_IDP_MODE", "mock")
    monkeypatch.setenv("OAUTH_REDIRECT_URI", f"{CALLBACK},{OTHER_CALLBACK}")
    for name in ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_TENANT_ID", "OAUTH_STATE_TTL_SECONDS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def staff(client):
    """Semilla del BRIEFING §6: Marcus vinculado, Priya sin vincular, Alex
    vinculado y SIN contraseña, James admin. Todos aprovisionados por IT."""
    repo = AuthRepository()
    ids = {
        MARCUS: provision_user(MARCUS, role="supervisor", department="clinical_operations"),
        PRIYA: provision_user(PRIYA, role="employee", department="patient_experience"),
        ALEX: provision_user(ALEX, role="employee", department="patient_experience"),
        JAMES: provision_user(JAMES, role="admin", department="technology"),
    }
    for user_id in ids.values():
        repo.set_federation_profile(user_id, provisioned_by_it=True)
    repo.set_password(ids[ALEX], None)
    fed = FederatedRepository()
    for email, oid in ((MARCUS, "ms-oid-marcus-001"), (ALEX, "ms-oid-alex-003")):
        fed.create_link(user_id=ids[email], provider="microsoft", provider_user_id=oid, email_at_link_time=email, provisioned_by_it=True)
    return ids


def bearer(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=user_id, role='employee')}"}


def start(client, purpose: str, headers=None, redirect_uri: str = CALLBACK):
    return client.post(
        f"/auth/federated/microsoft/{purpose}/start", json={"redirect_uri": redirect_uri}, headers=headers or {}
    )


def pick_identity(client, authorization_url: str, oid: str) -> dict[str, str]:
    """Simula elegir la cuenta en la pantalla de Microsoft: devuelve los
    parámetros con los que Microsoft vuelve al callback (code + state)."""
    query = {key: values[0] for key, values in parse_qs(urlparse(authorization_url).query).items()}
    response = client.get(
        "/dev/mock-microsoft/select",
        params={**{k: query[k] for k in ("client_id", "redirect_uri", "state", "nonce", "code_challenge")}, "oid": oid},
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == query["redirect_uri"]
    return {key: values[0] for key, values in parse_qs(location.query).items()}


def federated_flow(client, *, purpose: str, oid: str, start_headers=None, callback_headers=None, callback_redirect: str = CALLBACK):
    started = start(client, purpose, start_headers)
    assert started.status_code == 200, started.text
    returned = pick_identity(client, started.json()["authorization_url"], oid)
    return client.post(
        "/auth/federated/microsoft/callback",
        json={"code": returned["code"], "state": returned["state"], "redirect_uri": callback_redirect},
        headers=callback_headers or {},
    )


def user_count() -> int:
    return len(database.get_db().table("users"))


def events(name: str) -> list[dict]:
    return FederatedRepository().list_events(event=name)


# --- Criterio 1: login con proveedor no vinculado → rechazado, sin cuenta nueva ---


def test_unlinked_microsoft_login_is_rejected_and_creates_no_account(client, staff) -> None:
    before = user_count()

    response = federated_flow(client, purpose="login", oid="ms-oid-unknown-999")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "not_linked"
    assert "access_token" not in response.text
    assert user_count() == before
    assert AuthRepository().get_user_by_email("unknown.staff@healthcore.example") is None
    [event] = events("federated_login_rejected")
    assert event["user_id"] is None
    assert event["provider"] == "microsoft"
    assert event["provider_user_id"] == "ms-oid-unknown-999"
    assert event["reason"] == "not_linked"


def test_matching_email_is_not_a_link(client, staff) -> None:
    """Priya existe con el mismo correo que su cuenta Microsoft, pero no la
    vinculó: el email NUNCA sustituye a la vinculación explícita."""
    response = federated_flow(client, purpose="login", oid="ms-oid-priya-002")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "not_linked"
    assert FederatedRepository().links_for_user(staff[PRIYA]) == []


def test_other_tenant_identity_is_rejected(client, staff) -> None:
    before = user_count()
    response = federated_flow(client, purpose="login", oid="ms-oid-external-777")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_id_token"
    assert user_count() == before
    assert events("federated_login_rejected")[0]["reason"] == "invalid_id_token"


# --- Login de una cuenta vinculada: misma sesión que la tradicional -------------


def test_linked_identity_signs_in_with_the_same_session_as_password(client, staff) -> None:
    response = federated_flow(client, purpose="login", oid="ms-oid-marcus-001")

    assert response.status_code == 200
    body = response.json()
    assert body["purpose"] == "login" and body["token_type"] == "bearer"
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == MARCUS

    password_token = client.post("/auth/login", json={"email": MARCUS, "password": "SuperSecure123"}).json()["access_token"]
    import jwt as pyjwt

    federated_claims = pyjwt.decode(body["access_token"], options={"verify_signature": False})
    password_claims = pyjwt.decode(password_token, options={"verify_signature": False})
    assert federated_claims.keys() == password_claims.keys()
    assert federated_claims["type"] == password_claims["type"] == "access"
    assert abs(federated_claims["exp"] - password_claims["exp"]) <= 5  # mismos 30 min
    [event] = events("federated_login_success")
    assert event["user_id"] == staff[MARCUS] and event["provider_user_id"] == "ms-oid-marcus-001"


def test_federated_session_is_revoked_like_a_password_session(client, staff) -> None:
    token = federated_flow(client, purpose="login", oid="ms-oid-marcus-001").json()["access_token"]
    database.get_db().table("users").update({"is_active": False}, Query().id == staff[MARCUS])

    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    again = federated_flow(client, purpose="login", oid="ms-oid-marcus-001")
    assert again.status_code == 401
    assert again.json()["detail"]["code"] == "account_inactive"


# --- Criterio 2: vincular solo desde una sesión ya autenticada --------------------


def test_link_start_requires_an_authenticated_session(client, staff) -> None:
    assert start(client, "link").status_code == 401


def test_link_flow_from_the_profile(client, staff) -> None:
    response = federated_flow(
        client, purpose="link", oid="ms-oid-priya-002", start_headers=bearer(staff[PRIYA]), callback_headers=bearer(staff[PRIYA])
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["purpose"] == "link"
    assert body["link"]["provider"] == "microsoft"
    assert body["link"]["provider_user_id"] == "ms-oid-priya-002"
    assert body["link"]["email_at_link_time"] == PRIYA
    status = client.get("/auth/federated/links", headers=bearer(staff[PRIYA])).json()
    assert [link["provider_user_id"] for link in status["links"]] == ["ms-oid-priya-002"]
    assert events("federated_link")[0]["user_id"] == staff[PRIYA]
    # Y a partir de ahora Microsoft sirve para entrar.
    assert federated_flow(client, purpose="login", oid="ms-oid-priya-002").status_code == 200


def test_a_login_state_can_never_link_even_with_a_session(client, staff) -> None:
    """El propósito sale del state del servidor: un flujo empezado en la
    pantalla de login no vincula aunque el callback llegue con sesión."""
    response = federated_flow(client, purpose="login", oid="ms-oid-priya-002", callback_headers=bearer(staff[PRIYA]))

    assert response.status_code == 401
    assert FederatedRepository().links_for_user(staff[PRIYA]) == []


def test_link_callback_without_session_is_rejected(client, staff) -> None:
    response = federated_flow(client, purpose="link", oid="ms-oid-priya-002", start_headers=bearer(staff[PRIYA]))

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "login_required"
    assert FederatedRepository().links_for_user(staff[PRIYA]) == []


def test_link_state_from_another_session_is_rejected(client, staff) -> None:
    response = federated_flow(
        client, purpose="link", oid="ms-oid-priya-002", start_headers=bearer(staff[PRIYA]), callback_headers=bearer(staff[JAMES])
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "link_session_mismatch"
    assert FederatedRepository().links_for_user(staff[JAMES]) == []
    assert events("federated_link_rejected")[0]["reason"] == "link_session_mismatch"


def test_account_not_provisioned_by_it_cannot_link(client, staff) -> None:
    AuthRepository().set_federation_profile(staff[PRIYA], provisioned_by_it=False)

    response = start(client, "link", bearer(staff[PRIYA]))

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "not_provisioned"
    assert events("federated_link_rejected")[0]["reason"] == "not_provisioned"


def test_legacy_account_without_the_flag_fails_closed(client) -> None:
    user_id = provision_user("legacy@healthcore.example", role="employee", department="compliance")
    assert start(client, "link", bearer(user_id)).json()["detail"]["code"] == "not_provisioned"


# --- Criterio 3: una identidad, una sola cuenta --------------------------------------


def test_identity_already_linked_to_another_account_cannot_be_linked_again(client, staff) -> None:
    response = federated_flow(
        client, purpose="link", oid="ms-oid-marcus-001", start_headers=bearer(staff[PRIYA]), callback_headers=bearer(staff[PRIYA])
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "identity_in_use"
    assert FederatedRepository().find_link("microsoft", "ms-oid-marcus-001")["user_id"] == staff[MARCUS]
    rejected = events("federated_link_rejected")[0]
    assert rejected["user_id"] == staff[PRIYA] and rejected["provider_user_id"] == "ms-oid-marcus-001"


def test_an_account_cannot_hold_two_microsoft_identities(client, staff) -> None:
    response = federated_flow(
        client, purpose="link", oid="ms-oid-priya-002", start_headers=bearer(staff[MARCUS]), callback_headers=bearer(staff[MARCUS])
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "provider_already_linked"


def test_relinking_the_same_identity_is_idempotent(client, staff) -> None:
    response = federated_flow(
        client, purpose="link", oid="ms-oid-marcus-001", start_headers=bearer(staff[MARCUS]), callback_headers=bearer(staff[MARCUS])
    )

    assert response.status_code == 200
    assert len(FederatedRepository().links_for_user(staff[MARCUS])) == 1
    assert events("federated_link") == []  # no se registra un vínculo que no ocurrió


# --- Criterio 4: desvincular y aviso de último método ---------------------------------


def test_unlink_with_a_password_left_works_and_is_audited(client, staff) -> None:
    response = client.delete("/auth/federated/microsoft", headers=bearer(staff[MARCUS]))

    assert response.status_code == 200
    assert response.json()["account"]["links"] == []
    assert FederatedRepository().find_link("microsoft", "ms-oid-marcus-001") is None
    [event] = events("federated_unlink")
    assert event["user_id"] == staff[MARCUS] and event["provider_user_id"] == "ms-oid-marcus-001"
    assert federated_flow(client, purpose="login", oid="ms-oid-marcus-001").status_code == 401


def test_unlinking_the_only_access_method_is_warned_and_refused(client, staff) -> None:
    status = client.get("/auth/federated/links", headers=bearer(staff[ALEX])).json()
    assert status["has_password"] is False
    assert status["unlink_warning"] == "last_access_method"

    response = client.delete("/auth/federated/microsoft", headers=bearer(staff[ALEX]))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "last_access_method"
    assert FederatedRepository().links_for_user(staff[ALEX]) != []
    assert events("federated_unlink") == []


def test_unlink_is_allowed_once_a_password_exists(client, staff) -> None:
    from security import hash_password

    AuthRepository().set_password(staff[ALEX], hash_password("NewSecure123"))
    assert client.get("/auth/federated/links", headers=bearer(staff[ALEX])).json()["unlink_warning"] is None
    assert client.delete("/auth/federated/microsoft", headers=bearer(staff[ALEX])).status_code == 200


def test_account_with_password_and_link_has_no_warning(client, staff) -> None:
    assert client.get("/auth/federated/links", headers=bearer(staff[MARCUS])).json()["unlink_warning"] is None


def test_unlink_without_a_link_is_a_404(client, staff) -> None:
    response = client.delete("/auth/federated/microsoft", headers=bearer(staff[PRIYA]))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_linked_to_you"


def test_passwordless_account_password_login_fails_cleanly(client, staff) -> None:
    response = client.post("/auth/login", json={"email": ALEX, "password": "anything123"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("has_password", "other_links", "expected"),
    [(True, 0, None), (True, 2, None), (False, 1, None), (False, 0, "last_access_method")],
)
def test_unlink_block_reason(has_password: bool, other_links: int, expected) -> None:
    assert unlink_block_reason(has_password=has_password, other_links=other_links) == expected


# --- Criterio 5: auditoría ----------------------------------------------------------------


def test_audit_trail_is_admin_only_and_lists_every_event(client, staff) -> None:
    federated_flow(client, purpose="login", oid="ms-oid-unknown-999")
    federated_flow(client, purpose="login", oid="ms-oid-marcus-001")
    federated_flow(client, purpose="link", oid="ms-oid-priya-002", start_headers=bearer(staff[PRIYA]), callback_headers=bearer(staff[PRIYA]))
    client.delete("/auth/federated/microsoft", headers=bearer(staff[MARCUS]))

    assert client.get("/auth/federated/audit", headers=bearer(staff[PRIYA])).status_code == 403
    response = client.get("/auth/federated/audit", headers=bearer(staff[JAMES]))
    assert response.status_code == 200
    names = {event["event"] for event in response.json()["events"]}
    assert {"federated_link", "federated_unlink", "federated_login_success", "federated_login_rejected"} <= names
    for event in response.json()["events"]:
        assert event["at"] and event["provider"] == "microsoft"
    only = client.get("/auth/federated/audit", params={"event": "federated_login_rejected"}, headers=bearer(staff[JAMES]))
    assert {event["event"] for event in only.json()["events"]} == {"federated_login_rejected"}


def test_audit_log_line_pseudonymizes_the_external_identity(client, staff, caplog) -> None:
    caplog.set_level("INFO", logger="healthcore.audit")
    federated_flow(client, purpose="login", oid="ms-oid-unknown-999")

    lines = [json.loads(record.getMessage()) for record in caplog.records if record.name == "healthcore.audit"]
    assert lines[-1]["event"] == "federated_login_rejected"
    assert lines[-1]["reason"] == "not_linked"
    assert "ms-oid-unknown-999" not in caplog.text


# --- Criterio 6: state y redirect_uri ------------------------------------------------------


def test_state_is_single_use(client, staff) -> None:
    started = start(client, "login").json()
    returned = pick_identity(client, started["authorization_url"], "ms-oid-marcus-001")
    body = {"code": returned["code"], "state": returned["state"], "redirect_uri": CALLBACK}

    assert client.post("/auth/federated/microsoft/callback", json=body).status_code == 200
    replay = client.post("/auth/federated/microsoft/callback", json=body)
    assert replay.status_code == 400
    assert replay.json()["detail"]["code"] == "invalid_state"


def test_forged_state_is_rejected_and_audited(client, staff) -> None:
    started = start(client, "login").json()
    returned = pick_identity(client, started["authorization_url"], "ms-oid-marcus-001")

    response = client.post(
        "/auth/federated/microsoft/callback",
        json={"code": returned["code"], "state": "forged-state", "redirect_uri": CALLBACK},
    )

    assert response.status_code == 400
    assert events("federated_login_rejected")[0]["reason"] == "invalid_state"


def test_expired_state_is_rejected(client, staff) -> None:
    started = start(client, "login").json()
    returned = pick_identity(client, started["authorization_url"], "ms-oid-marcus-001")
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    database.get_db().table("oauth_states").update({"expires_at": past})

    response = client.post(
        "/auth/federated/microsoft/callback",
        json={"code": returned["code"], "state": returned["state"], "redirect_uri": CALLBACK},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_state"


def test_state_is_stored_as_a_fingerprint(client, staff) -> None:
    state = start(client, "login").json()["state"]
    stored = database.get_db().table("oauth_states").all()
    assert len(stored) == 1
    assert state not in json.dumps(stored)


def test_redirect_uri_outside_the_whitelist_is_refused_at_start(client, staff) -> None:
    response = start(client, "login", redirect_uri="https://evil.example/auth/callback/microsoft")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "redirect_uri_not_allowed"
    assert database.get_db().table("oauth_states").all() == []


def test_callback_with_a_different_redirect_uri_is_rejected(client, staff) -> None:
    response = federated_flow(client, purpose="login", oid="ms-oid-marcus-001", callback_redirect=OTHER_CALLBACK)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "redirect_uri_mismatch"


def test_microsoft_simulator_never_redirects_to_an_unregistered_uri(client) -> None:
    response = client.get(
        "/dev/mock-microsoft/select",
        params={
            "oid": "ms-oid-marcus-001",
            "client_id": "healthcore-mock-client",
            "redirect_uri": "https://evil.example/callback",
            "state": "s",
            "nonce": "n",
            "code_challenge": "c",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "location" not in response.headers


def test_wrong_pkce_verifier_is_rejected(client, staff) -> None:
    started = start(client, "login").json()
    returned = pick_identity(client, started["authorization_url"], "ms-oid-marcus-001")
    database.get_db().table("oauth_states").update({"code_verifier": "x" * 64})

    response = client.post(
        "/auth/federated/microsoft/callback",
        json={"code": returned["code"], "state": returned["state"], "redirect_uri": CALLBACK},
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "provider_error"


def test_no_microsoft_tokens_are_persisted(client, staff) -> None:
    federated_flow(client, purpose="link", oid="ms-oid-priya-002", start_headers=bearer(staff[PRIYA]), callback_headers=bearer(staff[PRIYA]))
    raw = open(os.environ["SUPPLIERS_DB_PATH"], encoding="utf-8").read()
    assert "eyJ" not in raw  # ningún JWT (id/access/refresh token) en disco
    assert "code_verifier" not in raw  # el state se consumió y se borró


# --- Proveedor y configuración ----------------------------------------------------------------


def test_only_microsoft_is_supported(client, staff) -> None:
    response = client.post("/auth/federated/google/login/start", json={"redirect_uri": CALLBACK})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unsupported_provider"


def test_providers_endpoint_reports_the_mode(client) -> None:
    assert client.get("/auth/federated/providers").json() == {
        "providers": [{"provider": "microsoft", "enabled": True, "mode": "mock"}]
    }


def test_disabled_provider_answers_503(client, monkeypatch) -> None:
    monkeypatch.delenv("MICROSOFT_IDP_MODE")
    assert client.get("/auth/federated/providers").json()["providers"][0]["enabled"] is False
    assert start(client, "login").status_code == 503
    assert client.get("/dev/mock-microsoft/authorize").status_code == 404


def test_real_mode_refuses_multi_tenant_configuration(monkeypatch) -> None:
    monkeypatch.setenv("MICROSOFT_IDP_MODE", "microsoft")
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "client")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "common")
    with pytest.raises(FederatedConfigError):
        load_settings()


def real_settings() -> FederatedSettings:
    return FederatedSettings(
        mode="microsoft",
        client_id="hc-client",
        client_secret="hc-secret",
        tenant_id="hc-tenant",
        allowed_redirect_uris=(CALLBACK,),
        state_ttl_seconds=600,
        mock_public_url="",
    )


def test_real_provider_uses_the_tenant_authority_and_pkce() -> None:
    url = MicrosoftProvider(real_settings()).authorization_url(
        state="s", nonce="n", code_challenge="c", redirect_uri=CALLBACK
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "login.microsoftonline.com"
    assert parsed.path == "/hc-tenant/oauth2/v2.0/authorize"
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [CALLBACK]
    assert query["response_type"] == ["code"]


def test_real_provider_exchanges_the_code_and_keeps_only_the_identity() -> None:
    settings = real_settings()
    signer = MockMicrosoftProvider(settings)
    id_token = signer.sign_id_token(find_mock_identity("ms-oid-marcus-001"), nonce="nonce-1", tid="hc-tenant")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(200, json={"id_token": id_token, "access_token": "AT", "refresh_token": "RT"})

    provider = MicrosoftProvider(
        settings, http_client=httpx.Client(transport=httpx.MockTransport(handler)), key_resolver=lambda _t: MockMicrosoftProvider.public_key()
    )
    identity = provider.exchange_and_validate(code="abc", code_verifier="v" * 64, redirect_uri=CALLBACK, nonce="nonce-1")

    assert identity.provider_user_id == "ms-oid-marcus-001"
    assert seen["url"] == "https://login.microsoftonline.com/hc-tenant/oauth2/v2.0/token"
    assert seen["form"]["code_verifier"] == ["v" * 64]
    assert seen["form"]["client_secret"] == ["hc-secret"]
    assert not any(hasattr(identity, name) for name in ("access_token", "refresh_token"))


@pytest.mark.parametrize(
    ("overrides", "nonce"),
    [
        ({"aud": "someone-else"}, "n"),
        ({"iss": "https://login.microsoftonline.com/other/v2.0"}, "n"),
        ({"tid": "other-tenant"}, "n"),
        ({"exp": int(time.time()) - 3600}, "n"),
        ({}, "different-nonce"),
        ({"oid": "", "sub": ""}, "n"),
    ],
    ids=["audience", "issuer", "tenant", "expired", "nonce", "no-oid"],
)
def test_id_token_validation_rejects_tampered_tokens(overrides, nonce) -> None:
    settings = load_settings()
    signer = MockMicrosoftProvider(settings)
    token = signer.sign_id_token(find_mock_identity("ms-oid-marcus-001"), nonce="n", **overrides)
    with pytest.raises(InvalidIdToken):
        validate_id_token(token, key=MockMicrosoftProvider.public_key(), settings=settings, nonce=nonce)


def test_pkce_challenge_is_s256() -> None:
    # Vector de la RFC 7636, apéndice B.
    assert code_challenge_s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
