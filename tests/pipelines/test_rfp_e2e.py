"""Ruta E2E reproducible del Hito 9: Parte 1 → Parte 2 → Parte 3. Sin red.

    services/api/.venv/bin/python -m pytest tests/pipelines/test_rfp_e2e.py

Mismo recorrido que scripts/run_rfp_e2e.py --offline: la RFP de Meridian
entra como ticket `analizando`, pasa por los runners reales de las tres
partes (los que llama el worker de Celery) con un modelo guionizado, y la
Parte 3 recibe aprobaciones humanas simuladas por el mismo camino que la API
(`submit_section_decision` / `submit_arbitration_decision` + `advance`).

Comprueba lo que pide la revisión de extremo a extremo del README: que los
estados se encadenan sin saltos, que el mismo ticket conserva lo de cada
parte y que el trace cuenta el proceso entero en orden.
"""

from __future__ import annotations

import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlmodel import Session, create_engine
from sqlmodel.pool import StaticPool

from data.pipelines.rfp_intake import repository
from data.pipelines.rfp_intake.approval_runner import advance
from data.pipelines.rfp_intake.conversion import clean_markdown
from data.pipelines.rfp_intake.graph import build_rfp_intake_graph, compile_rfp_intake_graph
from data.pipelines.rfp_intake.models import (
    NEXT_STAGE_APPROVAL,
    STATUS_ANALYZING,
    STATUS_AWAITING_APPROVAL,
    STATUS_COMPLETE,
    STATUS_EVALUATED,
    STATUS_FINISHED,
)
from data.pipelines.rfp_intake.response_runner import run_response
from data.pipelines.rfp_intake.runner import run_intake
from rfp_fixtures import (
    CLINICAL_500,
    CLINICAL_OK,
    CLINICAL_REVISED,
    MERIDIAN,
    REVENUE_500,
    REVENUE_OK,
    ScriptedLlm,
    create_rfp_tables,
    run_parallel_scenario,
)


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_rfp_tables(engine)
    with Session(engine) as db:
        yield db


def test_rfp_goes_through_the_three_parts_end_to_end(session, tmp_path):
    # Los generadores de la Parte 2 escriben primero; las revisiones de la
    # Parte 3 (cambio pedido por Clinical, arbitraje de Tom) vienen después.
    llm = ScriptedLlm(drafts={"revenue": [REVENUE_OK, REVENUE_500], "clinical": [CLINICAL_OK, CLINICAL_REVISED, CLINICAL_500]})
    ticket_id = uuid.uuid4()
    pdf = tmp_path / f"{ticket_id}.pdf"
    pdf.write_bytes(b"%PDF-1.4 fixture")
    ticket = repository.create_ticket(session, ticket_id=ticket_id, raw_pdf_path=str(pdf), created_by="user-1")
    statuses = [ticket.status]

    # Parte 1: recepción (lo que hace la tarea rfp_intake.process_ticket)
    intake = compile_rfp_intake_graph(build_rfp_intake_graph(llm=llm, converter=lambda _p: clean_markdown(MERIDIAN)))
    statuses.append(run_intake(session, ticket_id, graph=intake))
    # Parte 2: borradores + evaluación (rfp_response.generate_ticket)
    statuses.append(run_response(session, ticket_id, llm=llm))
    ticket = repository.get_ticket(session, ticket_id)
    assert ticket.next_stage == NEXT_STAGE_APPROVAL and ticket.response_handoff["next_stage"] == NEXT_STAGE_APPROVAL
    # Parte 3: aprobación humana simulada y cierre (rfp_approval.advance_ticket)
    saver = InMemorySaver()
    steps = run_parallel_scenario(session, ticket_id, lambda: advance(session, ticket_id, checkpointer=saver, llm=llm))
    statuses += [step["status"] for step in steps]

    # Estados encadenados sin saltos ni estados huérfanos.
    assert statuses[:3] == [STATUS_ANALYZING, STATUS_COMPLETE, STATUS_EVALUATED]
    assert set(statuses[3:-1]) == {STATUS_AWAITING_APPROVAL}
    assert statuses[-1] == STATUS_FINISHED

    # Misma fila de principio a fin: nada de las Partes 1 y 2 se perdió.
    session.expire_all()
    ticket = repository.get_ticket(session, ticket_id)
    metadata, sections = repository.get_details(session, ticket_id)
    assert ticket.handoff_payload["metadata"]["client_name"] == "Meridian Manufacturing, Inc."
    assert ticket.response_handoff["ticket_status"] == STATUS_EVALUATED
    assert metadata.currency == "USD" and metadata.covered_population == 800
    assert all(s.key_aspects for s in sections) and all(s.evaluation_history for s in sections)
    assert ticket.next_stage is None and ticket.completed_at is not None

    document = repository.get_final_document(session, ticket_id)
    assert document.currency == "USD" and len(document.sections) == 3
    assert "Business Associate Agreement" in document.content_markdown
    assert "**Población cubierta:** 500 empleados" in document.content_markdown

    # Trace: las tres partes, en orden, con agente y sin texto libre.
    events = repository.list_trace_events(session, ticket_id)
    parts = [e.part for e in events]
    assert parts == sorted(parts), "el trace debe contar las partes en orden"
    assert {1, 2, 3} <= set(parts)
    agents = [e.agent for e in events]
    assert agents[:4] == ["markitdown", "phi_screen", "classifier", "orchestrator"]
    assert "ultimate_document_synthesizer" == agents[-1]
    assert any(a.startswith("arbiter:human:Tom Callahan") for a in agents)
    assert "HealthCore abrirá" not in str([(e.input, e.output) for e in events])
