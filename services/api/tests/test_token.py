"""GET /auth/me — validación del token de sesión (la lógica que se rompió en la regresión)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from jose import jwt

import database

TEST_SECRET = os.environ["JWT_SECRET_KEY"]


def _forge_token(subject: str, *, expires_in_minutes: int, secret: str = TEST_SECRET) -> str:
    """Construye un JWT a medida para probar expiración y firmas."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)
    return jwt.encode({"sub": subject, "role": "user", "exp": expire}, secret, algorithm="HS256")


def test_me_happy_path_returns_current_user(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/auth/me", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "ana.perez@healthcore.com"
    assert body["role"] == "user"
    assert body["profile"]["name"] == "Ana Perez"


def test_expired_token_is_rejected(client: TestClient, registered_user: dict[str, str]) -> None:
    # La regresion del ticket AUTH-088: un token caducado JAMAS debe pasar.
    expired = _forge_token(registered_user["id"], expires_in_minutes=-5)

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401


def test_malformed_token_is_rejected(client: TestClient) -> None:
    response = client.get("/auth/me", headers={"Authorization": "Bearer esto-no-es-un-jwt"})

    assert response.status_code == 401


def test_token_signed_with_wrong_key_is_rejected(client: TestClient, registered_user: dict[str, str]) -> None:
    # Un token bien formado pero firmado con otra clave no puede validar.
    forged = _forge_token(registered_user["id"], expires_in_minutes=30, secret="otra-clave-distinta")

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401


def test_request_without_token_is_rejected(client: TestClient) -> None:
    response = client.get("/auth/me")

    assert response.status_code == 401


def test_valid_token_of_deleted_user_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    # Caso limite: el token sigue firmado y vigente, pero el usuario ya no existe.
    database.get_db().drop_tables()

    response = client.get("/auth/me", headers=auth_headers)

    assert response.status_code == 401
