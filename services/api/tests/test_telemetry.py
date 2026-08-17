"""POST /telemetry/events — Phase 3 real storage."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlmodel import Session, select

import telemetry_repository
import telemetry_service
from telemetry_models import TelemetryEventRecord

VALID_EVENT = {
    "eventId": "550e8400-e29b-41d4-a716-446655440000",
    "timestamp": "2026-06-15T10:30:00.000Z",
    "sessionId": "sess_abc123",
    "userId": "user_42",
    "event_type": "page_viewed",
    "schemaVersion": "1.0.0",
    "requestId": "req_abc123",
    "properties": {"route_template": "/inventory/products"},
}

# Missing every required envelope field — TelemetryEvent.model_validate must
# reject this one on its own, without touching the rest of the batch.
MALFORMED_EVENT = {"event_type": "page_viewed"}


def test_ingest_stores_valid_event_and_returns_counts(client: TestClient) -> None:
    response = client.post("/telemetry/events", json={"events": [VALID_EVENT]})
    assert response.status_code == 200
    assert response.json() == {"received": 1, "stored": 1, "rejected": 0}


def test_ingest_persists_the_row_in_supabase(client: TestClient, inventory_engine) -> None:
    client.post("/telemetry/events", json={"events": [VALID_EVENT]})
    with Session(inventory_engine) as session:
        rows = session.exec(select(TelemetryEventRecord)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.event_type == "page_viewed"
    assert row.service == "backoffice"
    assert row.timestamp.year == 2026
    assert row.level in {"info", "warn", "error"}


def test_ingest_tags_preserve_properties_and_correlation_fields(
    client: TestClient, inventory_engine
) -> None:
    client.post("/telemetry/events", json={"events": [VALID_EVENT]})
    with Session(inventory_engine) as session:
        row = session.exec(select(TelemetryEventRecord)).one()
    assert row.tags["route_template"] == "/inventory/products"
    assert row.tags["eventId"] == VALID_EVENT["eventId"]
    assert row.tags["sessionId"] == VALID_EVENT["sessionId"]
    assert row.tags["userId"] == VALID_EVENT["userId"]
    assert row.tags["requestId"] == VALID_EVENT["requestId"]
    assert row.tags["schemaVersion"] == VALID_EVENT["schemaVersion"]


def test_ingest_rejects_malformed_event_without_failing_the_batch(
    client: TestClient, inventory_engine
) -> None:
    response = client.post(
        "/telemetry/events", json={"events": [VALID_EVENT, MALFORMED_EVENT]}
    )
    assert response.status_code == 200
    assert response.json() == {"received": 2, "stored": 1, "rejected": 1}
    with Session(inventory_engine) as session:
        rows = session.exec(select(TelemetryEventRecord)).all()
    assert len(rows) == 1
    assert rows[0].event_type == "page_viewed"


def test_ingest_all_malformed_batch_stores_nothing(client: TestClient) -> None:
    response = client.post("/telemetry/events", json={"events": [MALFORMED_EVENT]})
    assert response.status_code == 200
    assert response.json() == {"received": 1, "stored": 0, "rejected": 1}


def test_ingest_rejects_event_with_unparseable_timestamp_without_500(
    client: TestClient, inventory_engine
) -> None:
    # Passes TelemetryEvent.model_validate (timestamp is typed as a plain
    # str, unmodified from the prior class) but blows up when
    # build_event_record tries to turn it into a real datetime. Found by
    # hand while doing the class's own end-to-end verification step against
    # real Supabase — the first version of this endpoint 500'd the whole
    # batch on this exact shape.
    bad_timestamp_event = {**VALID_EVENT, "eventId": "bad-ts", "timestamp": "not-a-real-timestamp"}
    response = client.post(
        "/telemetry/events", json={"events": [VALID_EVENT, bad_timestamp_event]}
    )
    assert response.status_code == 200
    assert response.json() == {"received": 2, "stored": 1, "rejected": 1}
    with Session(inventory_engine) as session:
        rows = session.exec(select(TelemetryEventRecord)).all()
    assert len(rows) == 1


def test_ingest_empty_batch_returns_all_zeros(client: TestClient) -> None:
    response = client.post("/telemetry/events", json={"events": []})
    assert response.status_code == 200
    assert response.json() == {"received": 0, "stored": 0, "rejected": 0}


def test_ingest_does_not_require_authentication(client: TestClient) -> None:
    # Unlike /inventory and /incidents, this endpoint has no get_current_user
    # dependency — the frontend queue must be able to flush even around a
    # session_expired redirect.
    response = client.post("/telemetry/events", json={"events": [VALID_EVENT]})
    assert response.status_code == 200


def test_ingest_logs_event_type(client: TestClient, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="api.telemetry"):
        client.post("/telemetry/events", json={"events": [VALID_EVENT]})
    assert "event_type=page_viewed" in caplog.text


def test_bulk_insert_uses_a_single_transaction_for_the_whole_batch() -> None:
    session = MagicMock()
    records = [
        TelemetryEventRecord(
            timestamp=telemetry_service._parse_timestamp(VALID_EVENT["timestamp"]),
            service="backoffice",
            event_type="page_viewed",
            level="info",
            tags={},
        )
        for _ in range(5)
    ]
    telemetry_repository.bulk_insert(session, records)
    session.add_all.assert_called_once_with(records)
    session.commit.assert_called_once()


def test_bulk_insert_skips_the_transaction_for_an_empty_list() -> None:
    session = MagicMock()
    telemetry_repository.bulk_insert(session, [])
    session.add_all.assert_not_called()
    session.commit.assert_not_called()


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
