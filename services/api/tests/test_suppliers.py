"""API-042 — Endpoints de proveedores (/suppliers)."""

from __future__ import annotations

from fastapi.testclient import TestClient

VALID_SUPPLIER = {
    "name": "Test Medical Corp",
    "country": "USA",
    "categories": ["medical_supplies"],
    "monthly_rate": 1200.0,
    "currency": "USD",
    "status": "active",
}


def test_create_supplier_happy_path(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/suppliers", json=VALID_SUPPLIER, headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == VALID_SUPPLIER["name"]
    assert isinstance(body["id"], int)
    assert body["updated_at"]  # el servidor asigna la marca de tiempo


def test_create_supplier_rejects_country_currency_mismatch(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # Regla de negocio: proveedores de USA facturan en USD, nunca en GBP.
    payload = dict(VALID_SUPPLIER, currency="GBP")

    response = client.post("/suppliers", json=payload, headers=auth_headers)

    assert response.status_code == 422


def test_create_supplier_rejects_unknown_category(client: TestClient, auth_headers: dict[str, str]) -> None:
    payload = dict(VALID_SUPPLIER, categories=["categoria-inventada"])

    response = client.post("/suppliers", json=payload, headers=auth_headers)

    assert response.status_code == 422


def test_list_suppliers_filters_by_country(client: TestClient, auth_headers: dict[str, str]) -> None:
    client.post("/suppliers", json=VALID_SUPPLIER, headers=auth_headers)
    uk_supplier = dict(VALID_SUPPLIER, name="UK Labs", country="UK", currency="GBP")
    client.post("/suppliers", json=uk_supplier, headers=auth_headers)

    response = client.get("/suppliers", params={"country": "UK"}, headers=auth_headers)

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["country"] == "UK"


def test_get_missing_supplier_returns_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/suppliers/999999", headers=auth_headers)

    assert response.status_code == 404


def test_update_rate_and_delete_supplier(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post("/suppliers", json=VALID_SUPPLIER, headers=auth_headers).json()

    updated = client.patch(
        f"/suppliers/{created['id']}/rate", json={"monthly_rate": 999.5}, headers=auth_headers
    )
    assert updated.status_code == 200
    assert updated.json()["monthly_rate"] == 999.5

    deleted = client.delete(f"/suppliers/{created['id']}", headers=auth_headers)
    assert deleted.status_code == 204
    assert client.get(f"/suppliers/{created['id']}", headers=auth_headers).status_code == 404


def test_suppliers_require_authentication(client: TestClient) -> None:
    assert client.get("/suppliers").status_code == 401
    assert client.post("/suppliers", json=VALID_SUPPLIER).status_code == 401
