"""POST /users — alta de cuentas.

Desde Roles y Permisos (2026-11-07) ya no es un registro público: solo un
admin crea cuentas, y elige rol y departamento por separado. Se conservan
las garantías de antes (respuesta mínima, sin email ni hash, duplicados,
normalización, validación) y se añade el control de acceso.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from access_models import department_id_for, role_id_for

VALID_PAYLOAD = {
    "email": "nuevo.empleado@healthcore.com",
    "password": "SuperSecure123",
    "name": "Nuevo Empleado",
    "role_id": role_id_for("employee"),
    "department_id": department_id_for("compliance"),
}


def test_anonymous_cannot_create_accounts(client: TestClient) -> None:
    assert client.post("/users", json=VALID_PAYLOAD).status_code == 401


def test_non_admin_cannot_create_accounts(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/users", json=VALID_PAYLOAD, headers=auth_headers)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "insufficient_role"


def test_register_response_is_minimal_and_omits_email(client: TestClient, admin_headers: dict[str, str]) -> None:
    """Contrato de la auditoria de serializacion: la respuesta solo confirma
    que la cuenta existe. No reenvia el email, ni el perfil, ni el rol."""
    response = client.post("/users", json=VALID_PAYLOAD, headers=admin_headers)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "created_at"}
    assert "email" not in body
    assert "profile" not in body


def test_admin_creates_active_user_with_chosen_role_and_department(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    assert client.post("/users", json=VALID_PAYLOAD, headers=admin_headers).status_code == 201

    login = client.post(
        "/auth/login",
        json={"email": VALID_PAYLOAD["email"], "password": VALID_PAYLOAD["password"]},
    )
    assert login.status_code == 200

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert me.status_code == 200
    assert me.json()["role"] == "employee"
    assert me.json()["department"] == "compliance"
    assert me.json()["profile"]["name"] == "Nuevo Empleado"


def test_role_and_department_are_required(client: TestClient, admin_headers: dict[str, str]) -> None:
    payload = {key: value for key, value in VALID_PAYLOAD.items() if key not in ("role_id", "department_id")}
    assert client.post("/users", json=payload, headers=admin_headers).status_code == 422


def test_unknown_role_or_department_is_rejected_per_field(client: TestClient, admin_headers: dict[str, str]) -> None:
    payload = {**VALID_PAYLOAD, "role_id": "no-existe", "department_id": "tampoco"}
    response = client.post("/users", json=payload, headers=admin_headers)

    assert response.status_code == 400
    assert {error["field"] for error in response.json()["detail"]["errors"]} == {"role_id", "department_id"}


def test_register_never_returns_password_or_hash(client: TestClient, admin_headers: dict[str, str]) -> None:
    body = client.post("/users", json=VALID_PAYLOAD, headers=admin_headers).json()
    assert "password" not in body
    assert "hashed_password" not in body


def test_register_duplicate_email_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    first = client.post("/users", json=VALID_PAYLOAD, headers=admin_headers)
    second = client.post("/users", json=VALID_PAYLOAD, headers=admin_headers)

    assert first.status_code == 201
    assert second.status_code == 409


def test_register_email_is_normalized_to_lowercase(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.post(
        "/users", json={**VALID_PAYLOAD, "email": "Nuevo.Empleado@healthcore.com"}, headers=admin_headers
    )
    assert response.status_code == 201

    login = client.post(
        "/auth/login",
        json={"email": "nuevo.empleado@healthcore.com", "password": "SuperSecure123"},
    )
    assert login.status_code == 200


def test_register_rejects_short_password(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.post("/users", json={**VALID_PAYLOAD, "password": "Solo7ch"}, headers=admin_headers)

    assert response.status_code == 422
    login = client.post("/auth/login", json={"email": VALID_PAYLOAD["email"], "password": "Solo7ch"})
    assert login.status_code == 401


def test_register_rejects_invalid_email_format(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.post("/users", json={**VALID_PAYLOAD, "email": "esto-no-es-un-email"}, headers=admin_headers)
    assert response.status_code == 422
