"""Gestión de usuarios (/users) — permisos self-or-admin y CRUD protegido."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_users_requires_authentication(client: TestClient) -> None:
    assert client.get("/users").status_code == 401


def test_list_users_requires_admin(
    client: TestClient, registered_user: dict[str, str], auth_headers: dict[str, str]
) -> None:
    """Antes lo servia cualquier autenticado. Un usuario normal ya no puede
    enumerar las cuentas de la organizacion."""
    assert client.get("/users", headers=auth_headers).status_code == 403


def test_list_users_happy_path_for_admin(
    client: TestClient, registered_user: dict[str, str], admin_headers: dict[str, str]
) -> None:
    response = client.get("/users", headers=admin_headers)

    assert response.status_code == 200
    ids = [user["id"] for user in response.json()]
    assert registered_user["id"] in ids


def test_user_list_never_exposes_emails(
    client: TestClient, registered_user: dict[str, str], admin_headers: dict[str, str]
) -> None:
    """Contrato fijado en la auditoria de serializacion: el listado es una
    vista de administracion (cuantas cuentas, con que rol, cuales activas).
    El correo de terceros no forma parte de esa pregunta, y devolverlo
    convertia el endpoint en un enumerador de emails de toda la red."""
    response = client.get("/users", headers=admin_headers)

    assert response.status_code == 200
    for user in response.json():
        assert set(user) == {"id", "role", "is_active", "created_at"}
        assert "email" not in user


def test_get_missing_user_returns_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/users/id-inexistente", headers=auth_headers)

    assert response.status_code == 404


def test_user_can_update_own_email(
    client: TestClient, registered_user: dict[str, str], auth_headers: dict[str, str]
) -> None:
    response = client.put(
        f"/users/{registered_user['id']}",
        json={"email": "ana.nueva@healthcore.com"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["email"] == "ana.nueva@healthcore.com"


def test_regular_user_cannot_change_roles(
    client: TestClient, registered_user: dict[str, str], auth_headers: dict[str, str]
) -> None:
    # Escalada de privilegios: un usuario normal no puede autoproclamarse admin.
    response = client.put(
        f"/users/{registered_user['id']}",
        json={"role": "admin"},
        headers=auth_headers,
    )

    assert response.status_code == 403


def test_user_cannot_update_another_users_account(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    other = client.post(
        "/users",
        json={"email": "otro.usuario@healthcore.com", "password": "SuperSecure123"},
    ).json()

    response = client.put(
        f"/users/{other['id']}",
        json={"email": "hackeado@healthcore.com"},
        headers=auth_headers,
    )

    assert response.status_code == 403


def test_regular_user_cannot_delete_accounts(
    client: TestClient, registered_user: dict[str, str], auth_headers: dict[str, str]
) -> None:
    # Borrar cuentas es exclusivo de admin, incluso la propia.
    response = client.delete(f"/users/{registered_user['id']}", headers=auth_headers)

    assert response.status_code == 403
