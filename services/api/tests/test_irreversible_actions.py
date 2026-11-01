"""Acciones irreversibles con confirmación humana y auditoría (clase NIST).

BRIEFING de ciberseguridad §5: "cualquier acción que modifique el estado [...]
sin registro de auditoría" requiere confirmación humana. En este fork la
acción equivalente es cerrar una incidencia (`resolved`/`discarded`): son
estados finales, sin transición de vuelta.

Fijan: sin `"confirm": true` exacto no se cierra (por la API y por el MCP);
las transiciones no finales no lo necesitan; el error de transición imposible
tiene prioridad; y cada cambio deja una línea de auditoría sin título,
descripción, id ni email del actor.
"""

from __future__ import annotations

import json
import logging

import mcp_harness as h
import pytest
from fastapi.testclient import TestClient

from audit_log import ALLOWED_FIELDS, actor_pseudonym, audit_event
from main import app as api_app
from packages.shared.incidents_validation import TERMINAL_STATUSES, validate_status_confirmation

PAYLOAD = {
    "title": "Fallo en la impresora de recepción",
    "description": "Texto libre que nunca debe aparecer en la auditoría.",
    "category": "it_system",
    "status": "open",
    "origin": "internal",
    "branch": "central",
}


def _create(client: TestClient, headers) -> dict:
    response = client.post("/api/incidents", json=PAYLOAD, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _patch(client: TestClient, headers, incident_id: int, body: dict):
    return client.patch(f"/api/incidents/{incident_id}/status", json=body, headers=headers)


def _audit_lines(caplog) -> list:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "healthcore.audit"]


# --- Regla compartida --------------------------------------------------------------


def test_terminal_statuses_are_derived_from_the_lifecycle() -> None:
    assert set(TERMINAL_STATUSES) == {"resolved", "discarded"}


@pytest.mark.parametrize("confirm", [None, False, "true", 1, "yes"])
def test_only_a_literal_true_confirms(confirm) -> None:
    assert validate_status_confirmation("resolved", confirm) is not None


def test_non_terminal_status_needs_no_confirmation() -> None:
    assert validate_status_confirmation("in_progress", None) is None


# --- API ---------------------------------------------------------------------------


def test_closing_without_confirmation_is_refused_and_nothing_changes(client, auth_headers) -> None:
    incident = _create(client, auth_headers)

    response = _patch(client, auth_headers, incident["id"], {"status": "discarded"})

    assert response.status_code == 400
    error = response.json()["detail"]["errors"][0]
    assert error["field"] == "confirm"
    assert "cannot be undone" in error["message"]
    assert client.get(f"/api/incidents/{incident['id']}", headers=auth_headers).json()["status"] == "open"


def test_closing_with_a_truthy_but_not_true_value_is_refused(client, auth_headers) -> None:
    incident = _create(client, auth_headers)
    assert _patch(client, auth_headers, incident["id"], {"status": "discarded", "confirm": "true"}).status_code == 400


def test_closing_with_explicit_confirmation_works(client, auth_headers) -> None:
    incident = _create(client, auth_headers)
    _patch(client, auth_headers, incident["id"], {"status": "in_progress"})

    response = _patch(client, auth_headers, incident["id"], {"status": "resolved", "confirm": True})

    assert response.status_code == 200
    assert response.json()["status"] == "resolved"


def test_intermediate_transition_needs_no_confirmation(client, auth_headers) -> None:
    incident = _create(client, auth_headers)
    assert _patch(client, auth_headers, incident["id"], {"status": "in_progress"}).status_code == 200


def test_impossible_transition_reports_the_lifecycle_error_first(client, auth_headers) -> None:
    """open → resolved directo: el error es de transición, aunque falte confirm."""
    incident = _create(client, auth_headers)
    error = _patch(client, auth_headers, incident["id"], {"status": "resolved"}).json()["detail"]["errors"][0]
    assert error["field"] == "status"


def test_every_status_change_is_audited_without_content(client, auth_headers, registered_user, caplog) -> None:
    incident = _create(client, auth_headers)
    with caplog.at_level(logging.INFO, logger="healthcore.audit"):
        _patch(client, auth_headers, incident["id"], {"status": "in_progress"})
        _patch(client, auth_headers, incident["id"], {"status": "discarded", "confirm": True})

    lines = _audit_lines(caplog)
    actor = actor_pseudonym(registered_user["id"])
    assert [(l["event"], l["from_status"], l["to_status"], l["confirmed"]) for l in lines] == [
        ("incident_status_changed", "open", "in_progress", False),
        ("incident_status_changed", "in_progress", "discarded", True),
    ]
    assert all(l["actor"] == actor and l["incident_id"] == incident["id"] and l["role"] == "user" for l in lines)
    assert all(l["at"] for l in lines)
    # Nada de contenido ni de identidad en claro (BRIEFING §1).
    text = caplog.text
    for forbidden in (PAYLOAD["title"], PAYLOAD["description"], str(registered_user["id"]), registered_user["email"]):
        assert forbidden not in text


def test_refused_closure_leaves_no_audit_line(client, auth_headers, caplog) -> None:
    incident = _create(client, auth_headers)
    with caplog.at_level(logging.INFO, logger="healthcore.audit"):
        _patch(client, auth_headers, incident["id"], {"status": "discarded"})
    assert _audit_lines(caplog) == []


def test_audit_refuses_free_text_fields() -> None:
    """Un campo fuera de la lista es un error de programación, no un dato que se cuela."""
    assert "title" not in ALLOWED_FIELDS and "description" not in ALLOWED_FIELDS
    with pytest.raises(ValueError):
        audit_event("incident_status_changed", actor_id="u", title="no")


# --- MCP ---------------------------------------------------------------------------


def test_mcp_close_without_confirm_is_a_validation_error(client, auth_headers, service_account) -> None:
    token = h.make_token()
    ticket = h.call(api_app, token, "incidents_create", PAYLOAD).structuredContent

    error = h.error_of(h.call(api_app, token, "incidents_update_status", {"incident_id": ticket["id"], "status": "discarded"}))

    assert error["code"] == "validation_error"
    assert error["details"]["fields"][0]["field"] == "confirm"
    assert client.get(f"/api/incidents/{ticket['id']}", headers=auth_headers).json()["status"] == "open"


def test_mcp_tool_is_announced_as_destructive(service_account) -> None:
    """Los clientes MCP usan `destructiveHint` para pedir confirmación a la persona."""
    async def list_tools(session):
        return (await session.list_tools()).tools

    tools = {tool.name: tool for tool in h.run_session(api_app, h.make_token(), list_tools)}
    tool = tools["incidents_update_status"]
    assert tool.annotations.destructiveHint is True
    assert "confirm" in tool.inputSchema["properties"]
    assert "IRREVERSIBLE" in tool.description
