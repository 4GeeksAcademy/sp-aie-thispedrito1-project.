from __future__ import annotations

from fastapi.testclient import TestClient

SUPPLY_PAYLOAD = {
    "name": "Guantes de nitrilo (caja de 100)",
    "sku": "HCR-PPE-001",
    "category": "ppe",
    "unit": "box",
    "country": "US",
}


def _create_supply(client: TestClient, auth_headers: dict[str, str], **overrides) -> dict:
    payload = {**SUPPLY_PAYLOAD, **overrides}
    response = client.post("/inventory/products", json=payload, headers=auth_headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_product_starts_at_zero_stock(client: TestClient, auth_headers: dict[str, str]):
    supply = _create_supply(client, auth_headers)
    assert supply["current_stock"] == 0
    assert supply["country"] == "US"


def test_inbound_order_increases_stock(client: TestClient, auth_headers: dict[str, str]):
    supply = _create_supply(client, auth_headers)

    response = client.post(
        "/inventory/orders/inbound",
        json={"supply_id": supply["id"], "quantity": 100, "vendor_name": "MedLine Industries", "clinic_id": 1},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["user_uuid"]

    product = client.get(f"/inventory/products/{supply['id']}", headers=auth_headers).json()
    assert product["current_stock"] == 100


def test_outbound_order_decreases_stock(client: TestClient, auth_headers: dict[str, str]):
    supply = _create_supply(client, auth_headers)
    client.post(
        "/inventory/orders/inbound",
        json={"supply_id": supply["id"], "quantity": 100, "vendor_name": "MedLine Industries", "clinic_id": 1},
        headers=auth_headers,
    )

    response = client.post(
        "/inventory/orders/outbound",
        json={"supply_id": supply["id"], "quantity": 30, "consumption_type": "clinical_use", "clinic_id": 1},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text

    product = client.get(f"/inventory/products/{supply['id']}", headers=auth_headers).json()
    assert product["current_stock"] == 70


def test_outbound_order_exceeding_stock_is_rejected(client: TestClient, auth_headers: dict[str, str]):
    supply = _create_supply(client, auth_headers)
    client.post(
        "/inventory/orders/inbound",
        json={"supply_id": supply["id"], "quantity": 10, "vendor_name": "MedLine Industries", "clinic_id": 1},
        headers=auth_headers,
    )

    response = client.post(
        "/inventory/orders/outbound",
        json={"supply_id": supply["id"], "quantity": 999, "consumption_type": "clinical_use", "clinic_id": 1},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "Insufficient stock" in response.json()["detail"]

    product = client.get(f"/inventory/products/{supply['id']}", headers=auth_headers).json()
    assert product["current_stock"] == 10  # rejected order never got written


def test_outbound_order_invalid_consumption_type_is_rejected(client: TestClient, auth_headers: dict[str, str]):
    supply = _create_supply(client, auth_headers)

    response = client.post(
        "/inventory/orders/outbound",
        json={"supply_id": supply["id"], "quantity": 1, "consumption_type": "lost", "clinic_id": 1},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_list_orders_includes_supply_data(client: TestClient, auth_headers: dict[str, str]):
    supply = _create_supply(client, auth_headers)
    client.post(
        "/inventory/orders/inbound",
        json={"supply_id": supply["id"], "quantity": 50, "vendor_name": "Cardinal Health UK", "clinic_id": 10},
        headers=auth_headers,
    )
    client.post(
        "/inventory/orders/outbound",
        json={"supply_id": supply["id"], "quantity": 5, "consumption_type": "expiry_waste", "clinic_id": 10},
        headers=auth_headers,
    )

    response = client.get("/inventory/orders", headers=auth_headers)
    assert response.status_code == 200
    orders = response.json()
    assert len(orders) == 2
    assert {o["order_type"] for o in orders} == {"inbound", "outbound"}
    assert all(o["supply_sku"] == "HCR-PPE-001" for o in orders)


def test_inventory_requires_authentication(client: TestClient):
    response = client.get("/inventory/products")
    assert response.status_code == 401
