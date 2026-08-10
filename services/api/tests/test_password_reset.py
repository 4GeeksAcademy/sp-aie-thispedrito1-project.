"""Flujo de contraseñas: /auth/forgot-password, /auth/reset-password y /auth/change-password."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

NEW_PASSWORD = "NuevaClaveSegura99"


@pytest.fixture()
def sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Sustituye el envío real por Resend por un doble que captura el token."""
    captured: list[dict[str, str]] = []

    def fake_send(to_email: str, token: str) -> bool:
        captured.append({"to": to_email, "token": token})
        return True

    monkeypatch.setattr("routes.auth.send_password_reset_email", fake_send)
    return captured


def test_full_reset_flow_happy_path(
    client: TestClient, registered_user: dict[str, str], sent_emails: list[dict[str, str]]
) -> None:
    forgot = client.post("/auth/forgot-password", json={"email": registered_user["email"]})
    assert forgot.status_code == 200
    assert len(sent_emails) == 1

    reset = client.post(
        "/auth/reset-password",
        json={"token": sent_emails[0]["token"], "new_password": NEW_PASSWORD},
    )
    assert reset.status_code == 200

    # La contrasena vieja deja de valer y la nueva funciona.
    old_login = client.post(
        "/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )
    new_login = client.post(
        "/auth/login",
        json={"email": registered_user["email"], "password": NEW_PASSWORD},
    )
    assert old_login.status_code == 401
    assert new_login.status_code == 200


def test_forgot_password_does_not_reveal_unknown_emails(
    client: TestClient, registered_user: dict[str, str], sent_emails: list[dict[str, str]]
) -> None:
    # Anti-enumeracion: misma respuesta exista o no el email, y sin enviar nada.
    known = client.post("/auth/forgot-password", json={"email": registered_user["email"]})
    unknown = client.post("/auth/forgot-password", json={"email": "nadie@healthcore.com"})

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert len(sent_emails) == 1  # solo el email del usuario real


def test_reset_token_is_single_use(
    client: TestClient, registered_user: dict[str, str], sent_emails: list[dict[str, str]]
) -> None:
    client.post("/auth/forgot-password", json={"email": registered_user["email"]})
    token = sent_emails[0]["token"]

    first = client.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD})
    second = client.post("/auth/reset-password", json={"token": token, "new_password": "OtraClaveMas88"})

    assert first.status_code == 200
    assert second.status_code == 400  # el mismo token no puede usarse dos veces


def test_reset_with_malformed_token_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/auth/reset-password",
        json={"token": "no-es-un-token", "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400


def test_access_token_cannot_be_used_as_reset_token(
    client: TestClient, registered_user: dict[str, str], auth_token: str
) -> None:
    # Un token de sesion valido NO es un token de reset (falta el claim type=reset).
    response = client.post(
        "/auth/reset-password",
        json={"token": auth_token, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 400


def test_change_password_happy_path(
    client: TestClient, registered_user: dict[str, str], auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/auth/change-password",
        json={"current_password": registered_user["password"], "new_password": NEW_PASSWORD},
        headers=auth_headers,
    )

    assert response.status_code == 200
    new_login = client.post(
        "/auth/login",
        json={"email": registered_user["email"], "password": NEW_PASSWORD},
    )
    assert new_login.status_code == 200


def test_change_password_with_wrong_current_password(
    client: TestClient, registered_user: dict[str, str], auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/auth/change-password",
        json={"current_password": "NoEsLaActual99", "new_password": NEW_PASSWORD},
        headers=auth_headers,
    )

    assert response.status_code == 400
    # La contrasena original debe seguir funcionando.
    login = client.post(
        "/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )
    assert login.status_code == 200


def test_change_password_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/auth/change-password",
        json={"current_password": "loquesea123", "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 401
