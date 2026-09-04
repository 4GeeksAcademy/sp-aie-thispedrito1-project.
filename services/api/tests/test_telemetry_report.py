"""GET /telemetry/report — pipeline de análisis (services/telemetry/analysis.py) + caché."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from services.telemetry import analysis

REPORT_WINDOW = {"start_date": "2026-06-14T00:00:00Z", "end_date": "2026-06-16T00:00:00Z"}


def _event(event_type: str, timestamp: str, properties: dict, event_id: str) -> dict:
    return {
        "eventId": event_id,
        "timestamp": timestamp,
        "sessionId": "sess_report_test",
        "userId": "user_report_test",
        "event_type": event_type,
        "schemaVersion": "1.0.0",
        "requestId": f"req_{event_id}",
        "properties": properties,
    }


def test_report_returns_period_and_empty_metrics_when_no_events(client: TestClient) -> None:
    response = client.get("/telemetry/report")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"period", "metrics"}
    assert set(body["period"].keys()) == {"from", "to"}
    assert body["metrics"] == {
        "events_per_day": [],
        "error_rate_by_day": [],
        "web_vital_latency_by_day": [],
        "auth_failure_rate": [],
    }


def test_report_defaults_to_last_seven_days(client: TestClient) -> None:
    response = client.get("/telemetry/report")
    body = response.json()
    start = datetime.fromisoformat(body["period"]["from"])
    end = datetime.fromisoformat(body["period"]["to"])
    assert abs((end - start) - timedelta(days=7)) < timedelta(seconds=5)


def test_events_per_day_groups_by_date_and_event_type(client: TestClient) -> None:
    client.post(
        "/telemetry/events",
        json={
            "events": [
                _event("page_viewed", "2026-06-15T10:00:00.000Z", {"route_template": "/inventory"}, "evt-1"),
                _event("page_viewed", "2026-06-15T12:00:00.000Z", {"route_template": "/suppliers"}, "evt-2"),
                _event(
                    "frontend_error_captured",
                    "2026-06-15T13:00:00.000Z",
                    {"component_name": "X", "route_template": "/", "error_name": "TypeError", "error_message": "boom"},
                    "evt-3",
                ),
            ]
        },
    )
    response = client.get("/telemetry/report", params=REPORT_WINDOW)
    rows = {(r["date"], r["event_type"]): r["count"] for r in response.json()["metrics"]["events_per_day"]}
    assert rows[("2026-06-15", "page_viewed")] == 2
    assert rows[("2026-06-15", "frontend_error_captured")] == 1


def test_error_rate_by_day_uses_level_column(client: TestClient) -> None:
    client.post(
        "/telemetry/events",
        json={
            "events": [
                _event("page_viewed", "2026-06-15T09:00:00.000Z", {"route_template": "/"}, "err-1"),
                _event("page_viewed", "2026-06-15T09:05:00.000Z", {"route_template": "/"}, "err-2"),
                _event(
                    "frontend_error_captured",
                    "2026-06-15T09:10:00.000Z",
                    {"component_name": "X", "route_template": "/", "error_name": "TypeError", "error_message": "boom"},
                    "err-3",
                ),
            ]
        },
    )
    response = client.get("/telemetry/report", params=REPORT_WINDOW)
    rows = response.json()["metrics"]["error_rate_by_day"]
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-06-15"
    assert rows[0]["total_events"] == 3
    assert rows[0]["error_events"] == 1
    assert rows[0]["error_rate"] == 1 / 3


def test_web_vital_latency_groups_by_metric_name(client: TestClient) -> None:
    client.post(
        "/telemetry/events",
        json={
            "events": [
                _event(
                    "web_vital_recorded",
                    "2026-06-15T09:00:00.000Z",
                    {"route_template": "/", "metric_name": "LCP", "value": 2000, "rating": "good"},
                    "wv-1",
                ),
                _event(
                    "web_vital_recorded",
                    "2026-06-15T09:05:00.000Z",
                    {"route_template": "/", "metric_name": "LCP", "value": 3000, "rating": "needs-improvement"},
                    "wv-2",
                ),
            ]
        },
    )
    response = client.get("/telemetry/report", params=REPORT_WINDOW)
    rows = response.json()["metrics"]["web_vital_latency_by_day"]
    assert len(rows) == 1
    assert rows[0]["metric_name"] == "LCP"
    assert rows[0]["avg_value"] == 2500


def test_auth_failure_rate_reflects_persisted_login_events(
    client: TestClient, registered_user: dict[str, str]
) -> None:
    client.post("/auth/login", json={"email": registered_user["email"], "password": "wrong-password"})
    client.post(
        "/auth/login", json={"email": registered_user["email"], "password": registered_user["password"]}
    )

    response = client.get("/telemetry/report")
    rows = response.json()["metrics"]["auth_failure_rate"]
    assert len(rows) == 1
    assert rows[0]["total_attempts"] == 2
    assert rows[0]["failed_attempts"] == 1
    assert rows[0]["failure_rate"] == 0.5


def test_report_is_cached_within_ttl(client: TestClient, monkeypatch) -> None:
    calls = {"count": 0}
    original = analysis.events_per_day

    def counting_events_per_day(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr("routes.telemetry.events_per_day", counting_events_per_day)

    client.get("/telemetry/report", params=REPORT_WINDOW)
    client.get("/telemetry/report", params=REPORT_WINDOW)

    assert calls["count"] == 1
