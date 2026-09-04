"""POST /telemetry/events — Phase 1 ingestion stub."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

import telemetry_service

EVENT = {
    "eventId": "550e8400-e29b-41d4-a716-446655440000",
    "timestamp": "2026-06-15T10:30:00.000Z",
    "sessionId": "sess_abc123",
    "userId": "user_42",
    "event_type": "page_viewed",
    "schemaVersion": "1.0.0",
    "requestId": "req_abc123",
    "properties": {"route_template": "/inventory/products"},
}


def test_ingest_returns_received_count(client: TestClient) -> None:
    response = client.post("/telemetry/events", json={"events": [EVENT, EVENT]})
    assert response.status_code == 200
    assert response.json() == {"received": 2}


def test_ingest_empty_batch_returns_zero(client: TestClient) -> None:
    response = client.post("/telemetry/events", json={"events": []})
    assert response.status_code == 200
    assert response.json() == {"received": 0}


def test_ingest_does_not_require_authentication(client: TestClient) -> None:
    # Unlike /inventory and /incidents, the stub has no get_current_user
    # dependency — the frontend queue must be able to flush even around a
    # session_expired redirect.
    response = client.post("/telemetry/events", json={"events": [EVENT]})
    assert response.status_code == 200


def test_ingest_logs_event_type(client: TestClient, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="api.telemetry"):
        client.post("/telemetry/events", json={"events": [EVENT]})
    assert "event_type=page_viewed" in caplog.text


def test_ingest_rejects_malformed_event(client: TestClient) -> None:
    response = client.post("/telemetry/events", json={"events": [{"event_type": "page_viewed"}]})
    assert response.status_code == 422


def test_hash_identifier_never_returns_the_raw_value() -> None:
    hashed = telemetry_service.hash_identifier("ana.perez@healthcore.com")
    assert hashed != "ana.perez@healthcore.com"
    assert "healthcore.com" not in hashed


def test_hash_identifier_is_deterministic_for_the_same_value() -> None:
    first = telemetry_service.hash_identifier("ana.perez@healthcore.com")
    second = telemetry_service.hash_identifier("ana.perez@healthcore.com")
    assert first == second


def test_hash_identifier_differs_for_different_values() -> None:
    assert telemetry_service.hash_identifier("ana@healthcore.com") != telemetry_service.hash_identifier(
        "marcus@healthcore.com"
    )
