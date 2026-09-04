"""POST /auth/login y POST /auth/token — autenticación de credenciales."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient


def test_login_happy_path_returns_bearer_token(client: TestClient, registered_user: dict[str, str]) -> None:
    response = client.post(
        "/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and len(body["access_token"]) > 20


def test_oauth_token_flow_returns_bearer_token(client: TestClient, registered_user: dict[str, str]) -> None:
    # /auth/token usa formulario OAuth2 (el flujo del boton Authorize de Swagger).
    response = client.post(
        "/auth/token",
        data={"username": registered_user["email"], "password": registered_user["password"]},
    )

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"


def test_login_wrong_password_is_rejected(client: TestClient, registered_user: dict[str, str]) -> None:
    response = client.post(
        "/auth/login",
        json={"email": registered_user["email"], "password": "ContrasenaIncorrecta1"},
    )

    assert response.status_code == 401


def test_login_unknown_email_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/auth/login",
        json={"email": "nadie@healthcore.com", "password": "SuperSecure123"},
    )

    assert response.status_code == 401


def test_login_error_does_not_reveal_which_field_failed(
    client: TestClient, registered_user: dict[str, str]
) -> None:
    # Anti-enumeracion: contrasena mala y email inexistente deben producir
    # exactamente el mismo mensaje, para no confirmar que un email existe.
    wrong_password = client.post(
        "/auth/login",
        json={"email": registered_user["email"], "password": "ContrasenaIncorrecta1"},
    )
    unknown_email = client.post(
        "/auth/login",
        json={"email": "nadie@healthcore.com", "password": "SuperSecure123"},
    )

    assert wrong_password.json()["detail"] == unknown_email.json()["detail"]


def test_login_success_emits_login_succeeded(
    client: TestClient, registered_user: dict[str, str], caplog
) -> None:
    with caplog.at_level(logging.INFO, logger="api.telemetry"):
        client.post(
            "/auth/login",
            json={"email": registered_user["email"], "password": registered_user["password"]},
        )
    assert "event_type=login_succeeded" in caplog.text


def test_login_failure_emits_login_failed(
    client: TestClient, registered_user: dict[str, str], caplog
) -> None:
    with caplog.at_level(logging.INFO, logger="api.telemetry"):
        client.post(
            "/auth/login",
            json={"email": registered_user["email"], "password": "ContrasenaIncorrecta1"},
        )
    assert "event_type=login_failed" in caplog.text
