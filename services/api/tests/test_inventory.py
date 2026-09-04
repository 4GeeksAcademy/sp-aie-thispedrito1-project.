from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

import inventory_repository as repo

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


def test_direct_stock_edit_is_always_rejected(client: TestClient, auth_headers: dict[str, str]):
    supply = _create_supply(client, auth_headers)

    response = client.patch(
        f"/inventory/products/{supply['id']}/stock",
        json={"clinic_id": 1, "attempted_value": 999},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "Direct stock edits are not allowed" in response.json()["detail"]

    # Confirms the rejection is real, not just a message — stock is untouched.
    product = client.get(f"/inventory/products/{supply['id']}", headers=auth_headers).json()
    assert product["current_stock"] == 0


def test_direct_stock_edit_emits_direct_stock_edit_rejected(
    client: TestClient, auth_headers: dict[str, str], caplog
):
    supply = _create_supply(client, auth_headers)

    with caplog.at_level(logging.INFO, logger="api.telemetry"):
        client.patch(
            f"/inventory/products/{supply['id']}/stock",
            json={"clinic_id": 1, "attempted_value": 999},
            headers=auth_headers,
        )
    assert "event_type=direct_stock_edit_rejected" in caplog.text


def test_stock_threshold_triggered_fires_when_clinic_stock_drops_to_minimum(
    client: TestClient, auth_headers: dict[str, str], inventory_engine, caplog
):
    supply = _create_supply(client, auth_headers)
    with Session(inventory_engine) as session:
        repo.set_threshold(session, supply["id"], clinic_id=1, minimum_quantity=50)

    client.post(
        "/inventory/orders/inbound",
        json={"supply_id": supply["id"], "quantity": 100, "vendor_name": "MedLine Industries", "clinic_id": 1},
        headers=auth_headers,
    )

    with caplog.at_level(logging.INFO, logger="api.telemetry"):
        response = client.post(
            "/inventory/orders/outbound",
            json={"supply_id": supply["id"], "quantity": 60, "consumption_type": "clinical_use", "clinic_id": 1},
            headers=auth_headers,
        )
    assert response.status_code == 201
    assert "event_type=stock_threshold_triggered" in caplog.text


def test_stock_threshold_does_not_trigger_above_minimum(
    client: TestClient, auth_headers: dict[str, str], inventory_engine, caplog
):
    supply = _create_supply(client, auth_headers)
    with Session(inventory_engine) as session:
        repo.set_threshold(session, supply["id"], clinic_id=1, minimum_quantity=10)

    client.post(
        "/inventory/orders/inbound",
        json={"supply_id": supply["id"], "quantity": 100, "vendor_name": "MedLine Industries", "clinic_id": 1},
        headers=auth_headers,
    )

    with caplog.at_level(logging.INFO, logger="api.telemetry"):
        client.post(
            "/inventory/orders/outbound",
            json={"supply_id": supply["id"], "quantity": 20, "consumption_type": "clinical_use", "clinic_id": 1},
            headers=auth_headers,
        )
    assert "event_type=stock_threshold_triggered" not in caplog.text


def test_outbound_rejection_emits_outbound_order_rejected(
    client: TestClient, auth_headers: dict[str, str], caplog
):
    supply = _create_supply(client, auth_headers)
    client.post(
        "/inventory/orders/inbound",
        json={"supply_id": supply["id"], "quantity": 10, "vendor_name": "MedLine Industries", "clinic_id": 1},
        headers=auth_headers,
    )

    with caplog.at_level(logging.INFO, logger="api.telemetry"):
        client.post(
            "/inventory/orders/outbound",
            json={"supply_id": supply["id"], "quantity": 999, "consumption_type": "clinical_use", "clinic_id": 1},
            headers=auth_headers,
        )
    assert "event_type=outbound_order_rejected" in caplog.text


def test_create_product_accepts_optional_expiry_date(client: TestClient, auth_headers: dict[str, str]):
    supply = _create_supply(client, auth_headers, sku="HCR-MED-999", expiry_date="2026-12-31")
    assert supply["expiry_date"] == "2026-12-31"


def test_list_supplies_expiring_within_finds_near_expiry_stock_only(
    client: TestClient, auth_headers: dict[str, str], inventory_engine
):
    near_expiry = _create_supply(
        client, auth_headers, sku="HCR-MED-NEAR", expiry_date=(date.today() + timedelta(days=10)).isoformat()
    )
    far_expiry = _create_supply(
        client, auth_headers, sku="HCR-MED-FAR", expiry_date=(date.today() + timedelta(days=200)).isoformat()
    )
    client.post(
        "/inventory/orders/inbound",
        json={"supply_id": near_expiry["id"], "quantity": 5, "vendor_name": "MedLine Industries", "clinic_id": 1},
        headers=auth_headers,
    )
    client.post(
        "/inventory/orders/inbound",
        json={"supply_id": far_expiry["id"], "quantity": 5, "vendor_name": "MedLine Industries", "clinic_id": 1},
        headers=auth_headers,
    )

    with Session(inventory_engine) as session:
        results = repo.list_supplies_expiring_within(session, days=30)

    expiring_skus = {supply.sku for supply, _stock, _days in results}
    assert expiring_skus == {"HCR-MED-NEAR"}


def test_list_supplies_expiring_within_skips_zero_stock(
    client: TestClient, auth_headers: dict[str, str], inventory_engine
):
    _create_supply(
        client, auth_headers, sku="HCR-MED-EMPTY", expiry_date=(date.today() + timedelta(days=5)).isoformat()
    )

    with Session(inventory_engine) as session:
        results = repo.list_supplies_expiring_within(session, days=30)

    assert results == []
