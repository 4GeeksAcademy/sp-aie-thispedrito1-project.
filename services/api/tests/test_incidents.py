"""API-042 — Gestor de incidencias (/api/incidents)."""

from __future__ import annotations

from fastapi.testclient import TestClient

VALID_INCIDENT = {
    "title": "EHR sync failure between London clinics",
    "description": "Referrals stopped syncing between London City and London West End.",
    "category": "it_system",
    "status": "open",
    "origin": "internal",
    "branch": "central",
}


def _create(client: TestClient, headers: dict[str, str], **overrides: str) -> dict:
    payload = dict(VALID_INCIDENT, **overrides)
    response = client.post("/api/incidents", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_incident_happy_path(client: TestClient, auth_headers: dict[str, str]) -> None:
    body = _create(client, auth_headers)

    assert body["status"] == "open"
    assert body["created_at"] == body["updated_at"]  # recien creada, sin modificaciones
    assert isinstance(body["id"], int)


def test_create_incident_reports_every_missing_field(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post("/api/incidents", json={"title": "Solo titulo"}, headers=auth_headers)

    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    missing_fields = {error["field"] for error in errors}
    # Todos los obligatorios ausentes deben venir señalados, no solo el primero.
    assert {"description", "category", "status", "origin", "branch"} <= missing_fields


def test_create_incident_rejects_unknown_branch(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/incidents", json=dict(VALID_INCIDENT, branch="sede-inventada"), headers=auth_headers
    )

    assert response.status_code == 400
    assert response.json()["detail"]["errors"][0]["field"] == "branch"


def test_list_incidents_rejects_invalid_filter(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/incidents", params={"status": "banana"}, headers=auth_headers)

    assert response.status_code == 400
    assert response.json()["detail"]["errors"][0]["field"] == "status"


def test_lifecycle_valid_transitions(client: TestClient, auth_headers: dict[str, str]) -> None:
    incident = _create(client, auth_headers)

    in_progress = client.patch(
        f"/api/incidents/{incident['id']}/status", json={"status": "in_progress"}, headers=auth_headers
    )
    resolved = client.patch(
        f"/api/incidents/{incident['id']}/status", json={"status": "resolved"}, headers=auth_headers
    )

    assert in_progress.status_code == 200
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"


def test_lifecycle_rejects_skipping_stages(client: TestClient, auth_headers: dict[str, str]) -> None:
    # open -> resolved directo esta prohibido: hay que pasar por in_progress.
    incident = _create(client, auth_headers)

    response = client.patch(
        f"/api/incidents/{incident['id']}/status", json={"status": "resolved"}, headers=auth_headers
    )

    assert response.status_code == 400


def test_lifecycle_final_states_are_immutable(client: TestClient, auth_headers: dict[str, str]) -> None:
    incident = _create(client, auth_headers)
    client.patch(f"/api/incidents/{incident['id']}/status", json={"status": "discarded"}, headers=auth_headers)

    response = client.patch(
        f"/api/incidents/{incident['id']}/status", json={"status": "open"}, headers=auth_headers
    )

    assert response.status_code == 400


def test_summary_with_empty_database_returns_zeros(client: TestClient, auth_headers: dict[str, str]) -> None:
    # Caso limite: sin incidencias el endpoint no rompe y devuelve todo a cero.
    response = client.get("/api/incidents/summary", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["by_status"]["open"] == 0
    assert sum(body["by_branch"].values()) == 0


def test_summary_counts_created_incidents(client: TestClient, auth_headers: dict[str, str]) -> None:
    _create(client, auth_headers)
    _create(client, auth_headers, origin="customer", branch="tampa_bay")

    body = client.get("/api/incidents/summary", headers=auth_headers).json()

    assert body["total"] == 2
    assert body["by_origin"]["internal"] == 1
    assert body["by_origin"]["customer"] == 1
    assert body["by_branch"]["tampa_bay"] == 1


def test_get_missing_incident_returns_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/api/incidents/999999", headers=auth_headers)

    assert response.status_code == 404
