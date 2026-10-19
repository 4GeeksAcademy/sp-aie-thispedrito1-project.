"""Endpoints de la recepción de RFPs (/rfp-tickets). Sin Redis ni modelo.

La tarea de Celery se sustituye por un espía (`enqueued`): aquí se prueba el
contrato HTTP (202 rápido, validación del PDF, estados, handoff), no el
grafo, que tiene sus propios tests en tests/pipelines/test_rfp_intake.py.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlmodel import Session

from data.pipelines.rfp_intake import repository
from data.pipelines.rfp_intake.models import RfpTicket
from services.rfp_intake import router as rfp_router

PDF = b"%PDF-1.4\n% RFP de prueba\n"


@pytest.fixture()
def raw_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def enqueued(monkeypatch):
    calls = []

    def fake_enqueue(ticket_id):
        calls.append(ticket_id)
        return f"task-{len(calls)}"

    monkeypatch.setattr(rfp_router, "enqueue_rfp_ticket", fake_enqueue)
    return calls


def upload(client, headers, content=PDF, filename="Resumen paciente Pérez.pdf"):
    return client.post("/rfp-tickets", headers=headers, files={"file": (filename, content, "application/pdf")})


def test_upload_creates_ticket_saves_pdf_and_enqueues(client, auth_headers, raw_dir, enqueued, inventory_engine):
    response = upload(client, auth_headers)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "analizando" and body["rfp_id"].startswith("RFP-")
    ticket_id = uuid.UUID(body["ticket_id"])
    assert enqueued == [ticket_id]
    # El PDF se guarda con el ticket_id como nombre, nunca el nombre original.
    assert (raw_dir / f"{ticket_id}.pdf").read_bytes() == PDF
    assert not any("Pérez" in p.name for p in raw_dir.iterdir())
    with Session(inventory_engine) as session:
        assert session.get(RfpTicket, ticket_id).task_id == "task-1"


def test_upload_requires_authentication(client, raw_dir, enqueued):
    assert upload(client, {}).status_code == 401
    assert enqueued == []


def test_upload_rejects_files_that_are_not_pdf(client, auth_headers, raw_dir, enqueued):
    response = upload(client, auth_headers, content=b"<html>no soy un pdf</html>", filename="rfp.pdf")
    assert response.status_code == 400
    assert enqueued == [] and list(raw_dir.iterdir()) == []


def test_upload_rejects_pdf_over_10_mb(client, auth_headers, raw_dir, enqueued):
    response = upload(client, auth_headers, content=PDF + b"0" * (10 * 1024 * 1024))
    assert response.status_code == 413
    assert enqueued == []


def test_queue_down_returns_503_and_ticket_is_error_not_stuck(client, auth_headers, raw_dir, monkeypatch):
    def broken(_ticket_id):
        raise RedisConnectionError("redis caído")

    monkeypatch.setattr(rfp_router, "enqueue_rfp_ticket", broken)
    assert upload(client, auth_headers).status_code == 503

    tickets = client.get("/rfp-tickets", headers=auth_headers).json()
    assert len(tickets) == 1 and tickets[0]["status"] == "error"


def test_list_and_detail_while_analyzing(client, auth_headers, raw_dir, enqueued):
    ticket_id = upload(client, auth_headers).json()["ticket_id"]

    listing = client.get("/rfp-tickets", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()[0]["ticket_id"] == ticket_id
    assert "raw_pdf_path" not in listing.json()[0] and "created_by" not in listing.json()[0]

    detail = client.get(f"/rfp-tickets/{ticket_id}", headers=auth_headers).json()
    assert detail["status"] == "analizando" and detail["metadata"] is None and detail["sections"] == []


def test_unknown_ticket_is_404(client, auth_headers):
    assert client.get(f"/rfp-tickets/{uuid.uuid4()}", headers=auth_headers).status_code == 404


def test_handoff_is_409_until_the_analysis_is_complete(client, auth_headers, raw_dir, enqueued):
    ticket_id = upload(client, auth_headers).json()["ticket_id"]
    assert client.get(f"/rfp-tickets/{ticket_id}/handoff", headers=auth_headers).status_code == 409


def test_completed_ticket_exposes_sections_contacts_and_handoff(client, auth_headers, raw_dir, enqueued, inventory_engine):
    ticket_id = uuid.UUID(upload(client, auth_headers).json()["ticket_id"])
    with Session(inventory_engine) as session:
        ticket = repository.get_ticket(session, ticket_id)
        repository.save_result(
            session,
            ticket,
            ticket_fields={
                "status": "analisis_completo",
                "next_stage": "generando_borrador",
                "handoff_payload": {"ticket_id": str(ticket_id), "workstreams": [{"department_id": "compliance"}]},
            },
            metadata_fields={"client_name": "Meridian Manufacturing, Inc.", "client_country": "US", "currency": "USD",
                             "deadline": "2026-08-23", "who_to_ask": [{"department_id": "compliance", "owner": "Claire Whitfield"}]},
            sections=[
                {"department_id": "compliance", "owner": "Claire Whitfield", "key_aspects": ["Cláusula BAA (HIPAA)"]},
                {"department_id": "revenue", "owner": "Tom Callahan", "key_aspects": ["Cotizar en USD"]},
            ],
        )

    detail = client.get(f"/rfp-tickets/{ticket_id}", headers=auth_headers).json()
    assert detail["client_name"] == "Meridian Manufacturing, Inc."
    assert detail["metadata"]["currency"] == "USD" and detail["metadata"]["deadline"] == "2026-08-23"
    # Orden del catálogo (revenue, clinical, compliance) y nombre del departamento.
    assert [(s["department_id"], s["department"]) for s in detail["sections"]] == [
        ("revenue", "Revenue Cycle"),
        ("compliance", "Compliance and Data Governance"),
    ]

    handoff = client.get(f"/rfp-tickets/{ticket_id}/handoff", headers=auth_headers)
    assert handoff.status_code == 200
    assert handoff.json()["next_stage"] == "generando_borrador"
    assert handoff.json()["payload"]["ticket_id"] == str(ticket_id)
