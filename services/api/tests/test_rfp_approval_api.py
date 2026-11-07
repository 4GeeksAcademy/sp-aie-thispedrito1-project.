"""Endpoints de la Parte 3 del flujo de RFPs (aprobación humana y cierre).

Contrato HTTP: la API valida la respuesta humana contra la petición del
grafo (400 por campo, 409 si no hay nada esperando), la guarda y encola el
avance (202). El worker se sustituye por `advance` en el propio test, con
un checkpointer en memoria; el grafo tiene sus tests en
tests/pipelines/test_rfp_approval.py.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlmodel import Session

from data.pipelines.rfp_intake.approval_runner import advance
from services.rfp_intake import router as rfp_router

# Fixtures compartidas con los tests del grafo y el script E2E (no es un paquete).
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests" / "pipelines"))
from rfp_fixtures import ScriptedLlm, seed_ticket  # noqa: E402


@pytest.fixture()
def enqueued(monkeypatch):
    calls = []
    monkeypatch.setattr(rfp_router, "enqueue_rfp_approval", lambda ticket_id: calls.append(ticket_id) or "task-x")
    return calls


@pytest.fixture()
def flow(inventory_engine):
    """Ticket recién salido de la Parte 2 + un `advance` como el del worker."""
    saver = InMemorySaver()
    with Session(inventory_engine) as session:
        ticket_id = seed_ticket(session)

    def step() -> str:
        with Session(inventory_engine) as session:
            return advance(session, ticket_id, checkpointer=saver, llm=ScriptedLlm())

    return ticket_id, step


def decide(client, headers, ticket_id, department_id, **body):
    return client.post(f"/rfp-tickets/{ticket_id}/sections/{department_id}/decision", headers=headers, json=body)


def test_decision_is_validated_saved_and_queued(client, auth_headers, flow, enqueued):
    ticket_id, step = flow
    assert step() == "esperando_aprobación"

    response = decide(client, auth_headers, ticket_id, "compliance", decision="approve")
    assert response.status_code == 202
    assert response.json()["approval_status"] == "decision_registrada"
    assert enqueued == [ticket_id]

    detail = client.get(f"/rfp-tickets/{ticket_id}", headers=auth_headers).json()
    statuses = {s["department_id"]: s["approval_status"] for s in detail["sections"]}
    assert statuses == {"revenue": "pendiente", "clinical": "pendiente", "compliance": "decision_registrada"}
    # Aplicar (worker) → Compliance aprobada mientras las otras dos esperan.
    step()
    detail = client.get(f"/rfp-tickets/{ticket_id}", headers=auth_headers).json()
    compliance = next(s for s in detail["sections"] if s["department_id"] == "compliance")
    assert compliance["approval_status"] == "aprobada" and compliance["approver"] == "Claire Whitfield"
    assert compliance["approval_history"][0]["decided_by"] is not None


def test_invalid_decisions_get_field_errors_and_are_not_queued(client, auth_headers, flow, enqueued):
    ticket_id, step = flow
    step()
    no_reason = decide(client, auth_headers, ticket_id, "revenue", decision="request_changes")
    assert no_reason.status_code == 400
    assert no_reason.json()["detail"]["errors"][0]["field"] == "comments"

    phi = decide(client, auth_headers, ticket_id, "revenue", decision="reject",
                 comments="Patient: John Fakename, DOB: 01/02/1980, no cubrimos su caso.")
    assert phi.status_code == 400 and "Fakename" not in phi.text

    no_capacity = decide(client, auth_headers, ticket_id, "clinical", decision="approve")
    assert no_capacity.json()["detail"]["errors"][0]["field"] == "confirmed_capacity"
    assert enqueued == []


def test_decisions_are_409_when_nothing_waits_and_404_for_unknown_departments(client, auth_headers, flow, enqueued):
    ticket_id, step = flow
    assert decide(client, auth_headers, ticket_id, "revenue", decision="approve").status_code == 409  # sin arrancar
    step()
    assert decide(client, auth_headers, ticket_id, "marketing", decision="approve").status_code == 404
    assert client.post(f"/rfp-tickets/{ticket_id}/arbitration", headers=auth_headers,
                       json={"option": "add_sites", "note": "Sumar la sede de Round Rock."}).status_code == 409
    assert decide(client, auth_headers, ticket_id, "revenue", decision="approve").status_code == 202
    # Ya decidida: una segunda decisión sobre la misma petición no entra.
    assert decide(client, auth_headers, ticket_id, "revenue", decision="reject",
                  comments="Cambio de opinión sobre el precio.").status_code == 409


def test_decisions_require_authentication(client, flow):
    ticket_id, _ = flow
    assert decide(client, {}, ticket_id, "revenue", decision="approve").status_code == 401


def test_final_document_and_trace_after_every_department_approves(client, auth_headers, flow, enqueued):
    ticket_id, step = flow
    step()
    assert client.get(f"/rfp-tickets/{ticket_id}/final-document", headers=auth_headers).status_code == 404
    for department_id, body in (("compliance", {}), ("revenue", {}), ("clinical", {"confirmed_capacity": 800})):
        assert decide(client, auth_headers, ticket_id, department_id, decision="approve", **body).status_code == 202
        step()

    detail = client.get(f"/rfp-tickets/{ticket_id}", headers=auth_headers).json()
    assert detail["status"] == "terminado" and detail["has_final_document"] is True
    document = client.get(f"/rfp-tickets/{ticket_id}/final-document", headers=auth_headers).json()
    assert document["currency"] == "USD" and document["rfp_id"] == detail["rfp_id"]
    assert document["content_markdown"].startswith("# Propuesta económica para Meridian Manufacturing, Inc.")
    assert [a["approver"] for a in document["approvals"]] == ["Tom Callahan", "Dr. Marcus Reid", "Claire Whitfield"]

    trace = client.get(f"/rfp-tickets/{ticket_id}/trace", headers=auth_headers).json()
    assert trace[0]["node"] == "open_approval" and trace[-1]["agent"] == "ultimate_document_synthesizer"
    assert all({"agent", "input", "output", "timestamp"} <= set(event) for event in trace)

    # Sin saltos entre partes: los contratos de las Partes 1 y 2 siguen respondiendo.
    assert client.get(f"/rfp-tickets/{ticket_id}/handoff", headers=auth_headers).status_code == 200
    handoff = client.get(f"/rfp-tickets/{ticket_id}/response-handoff", headers=auth_headers)
    assert handoff.status_code == 200 and handoff.json()["next_stage"] == "esperando_aprobación"


def test_unknown_ticket_is_404_for_part_3_endpoints(client, auth_headers):
    unknown = uuid.uuid4()
    assert client.get(f"/rfp-tickets/{unknown}/final-document", headers=auth_headers).status_code == 404
    assert client.get(f"/rfp-tickets/{unknown}/trace", headers=auth_headers).status_code == 404
    assert decide(client, auth_headers, unknown, "revenue", decision="approve").status_code == 404
