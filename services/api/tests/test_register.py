"""POST /users — reglas de negocio del registro."""

from __future__ import annotations

from fastapi.testclient import TestClient

VALID_PAYLOAD = {"email": "ana.perez@healthcore.com", "password": "SuperSecure123", "name": "Ana Perez"}


def test_register_response_is_minimal_and_omits_email(client: TestClient) -> None:
    """Contrato fijado en la auditoria de serializacion: el registro es un
    flujo NO autenticado, asi que su respuesta se limita a confirmar que la
    cuenta existe. No reenvia el email que el cliente acaba de mandar en el
    cuerpo, ni el perfil, ni el rol."""
    response = client.post("/users", json=VALID_PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "created_at"}
    assert "email" not in body
    assert "profile" not in body


def test_register_creates_active_user_with_profile(client: TestClient) -> None:
    """El comportamiento sigue siendo el mismo aunque la respuesta ya no lo
    muestre: se verifica por donde corresponde, con la sesion ya iniciada."""
    assert client.post("/users", json=VALID_PAYLOAD).status_code == 201

    # Que el login funcione demuestra que la cuenta quedo activa.
    login = client.post(
        "/auth/login",
        json={"email": VALID_PAYLOAD["email"], "password": VALID_PAYLOAD["password"]},
    )
    assert login.status_code == 200

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert me.status_code == 200
    assert me.json()["role"] == "user"          # nadie se registra como admin
    assert me.json()["profile"]["name"] == "Ana Perez"


def test_register_never_returns_password_or_hash(client: TestClient) -> None:
    # Transversal: los campos sensibles no deben salir en la respuesta.
    response = client.post("/users", json=VALID_PAYLOAD)

    body = response.json()
    assert "password" not in body
    assert "hashed_password" not in body


def test_register_duplicate_email_is_rejected(client: TestClient) -> None:
    first = client.post("/users", json=VALID_PAYLOAD)
    second = client.post("/users", json=VALID_PAYLOAD)

    assert first.status_code == 201
    assert second.status_code == 409


def test_register_email_is_normalized_to_lowercase(client: TestClient) -> None:
    # Caso limite: registrar con mayusculas debe permitir login en minusculas.
    response = client.post(
        "/users",
        json={"email": "Ana.Perez@healthcore.com", "password": "SuperSecure123"},
    )
    assert response.status_code == 201

    login = client.post(
        "/auth/login",
        json={"email": "ana.perez@healthcore.com", "password": "SuperSecure123"},
    )
    assert login.status_code == 200


def test_register_rejects_short_password(client: TestClient) -> None:
    # El minimo del contrato es 8 caracteres; 7 debe fallar.
    response = client.post(
        "/users",
        json={"email": "ana.perez@healthcore.com", "password": "Solo7ch"},
    )

    assert response.status_code == 422
    # Y el usuario no debe existir: el login tiene que fallar.
    login = client.post(
        "/auth/login",
        json={"email": "ana.perez@healthcore.com", "password": "Solo7ch"},
    )
    assert login.status_code == 401


def test_register_rejects_invalid_email_format(client: TestClient) -> None:
    response = client.post(
        "/users",
        json={"email": "esto-no-es-un-email", "password": "SuperSecure123"},
    )

    assert response.status_code == 422
