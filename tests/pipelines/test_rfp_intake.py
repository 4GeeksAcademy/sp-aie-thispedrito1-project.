"""Tests del flujo de recepción de RFPs (Hito 9, Parte 1). Sin red ni Supabase.

    services/api/.venv/bin/python -m pytest tests/pipelines/test_rfp_intake.py

El modelo se sustituye por `FakeLlm`, que responde según el agente que llama
(lo reconoce por su prompt de sistema) y graba lo que recibió cada uno: así
se comprueba qué ve cada agente, no solo qué devuelve. Los textos de las RFP
reproducen los PDF de muestra del BRIEFING (rfp-requests/healthcore/).
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from data.pipelines.rfp_intake import agents, repository
from data.pipelines.rfp_intake.conversion import Section, clean_markdown, pdf_to_markdown, split_sections
from data.pipelines.rfp_intake.departments import required_departments, unmapped
from data.pipelines.rfp_intake.graph import (
    OUTCOME_COMPLETE,
    OUTCOME_NOT_RFP,
    OUTCOME_PHI,
    RfpGraphError,
    build_rfp_intake_graph,
    compile_rfp_intake_graph,
    validate_structure,
)
from data.pipelines.rfp_intake.models import (
    STATUS_ANALYZING,
    STATUS_COMPLETE,
    STATUS_DISCARDED,
    STATUS_ERROR,
    RfpDepartmentSection,
    RfpMetadata,
    RfpTicket,
    utcnow,
)
from data.pipelines.rfp_intake.phi import is_patient_phi, screen
from data.pipelines.rfp_intake.readability import compute_readability
from data.pipelines.rfp_intake.runner import run_intake
from data.pipelines.rfp_intake.sample_pdf import write_pdf

MERIDIAN = """REQUEST FOR PROPOSAL (RFP)

On-Site Occupational Health & Corporate Wellness Program

Issuing Organization: Meridian Manufacturing, Inc.

Proposal Due Date: August 23, 2026

Meridian Manufacturing operates an 800-employee manufacturing facility in Austin, Texas.

- On-site occupational health clinic staffed 3 days/week

- Annual wellness screenings for all 800 employees

- Chronic condition management support (diabetes, hypertension)

- Contract term: 12 months, with option to renew

- Vendor must be able to execute a Business Associate Agreement (BAA) in compliance with HIPAA

Proposals must be submitted electronically to procurement@meridianmfg.com no later than the due date."""

THAMES = """Thames Valley University — United Kingdom

Subject: Partnership enquiry — student health referral network

We're looking to set up a referral partnership so our students (around 3,000 on the main campus) can access specialist care.

Could someone put together a proposal for us? We'll need this to be fully compliant with UK GDPR given it involves student health data."""

MEDFLOW = """Subject: Modernize your EHR with MedFlow

Our unified EHR platform could replace your legacy systems. Would you be open to a quick call to discuss?"""

PHI_RFP = """Northwind Logistics requests a proposal for an employee health program covering 450 employees.

Attachment A - Clinical summary Patient: Daniel Fakename Date of birth: 04/12/1979 MRN: 00482913 Diagnosis: type 2 diabetes.

Contact: benefits@northwind-logistics.example"""


def sections(text: str) -> List[Section]:
    return split_sections(clean_markdown(text))


class FakeLlm:
    """Modelo falso. `responses[agente]` es un dict (se devuelve como JSON),
    un str (se devuelve tal cual) o un callable(user) -> dict."""

    def __init__(self, **responses: Any) -> None:
        self.responses = responses
        self.calls: List[Dict[str, str]] = []

    @staticmethod
    def agent_of(system: str) -> str:
        if "clasificador" in system:
            return "classifier"
        if "orquestador" in system:
            return "orchestrator"
        if "synthesizer" in system:
            return "synthesizer"
        for department in ("Revenue Cycle", "Clinical Operations", "Compliance and Data Governance"):
            if f"analista de {department}" in system:
                return {"Revenue Cycle": "revenue", "Clinical Operations": "clinical"}.get(department, "compliance")
        raise AssertionError(f"prompt desconocido: {system[:60]}")

    def __call__(self, system: str, user: str) -> str:
        agent = self.agent_of(system)
        self.calls.append({"agent": agent, "system": system, "user": user})
        response = self.responses.get(agent, self.responses.get("worker"))
        if callable(response):
            response = response(user)
        if isinstance(response, str):
            return response
        return json.dumps(response)

    def called(self, agent: str) -> List[Dict[str, str]]:
        return [call for call in self.calls if call["agent"] == agent]


ACCEPT = {"is_rfp": True, "confidence": 0.93, "document_kind": "formal_rfp", "reason": "Empleador pide propuesta"}
MERIDIAN_PLAN = {
    "client_name": "Meridian Manufacturing, Inc.",
    "client_country": "US",
    "program_type": ["occupational_health", "corporate_wellness"],
    "covered_population": {"count": 800, "unit": "employees"},
    "deadline": "2026-08-23",
    "deadline_text": "August 23, 2026",
    "budget_range": None,
    "departments_needed": ["revenue", "clinical", "compliance"],
    "assignments": {"revenue": [0, 4, 8], "clinical": [5, 6, 7], "compliance": [10, 11]},
}
SYNTHESIS = {
    "summary": "Meridian pide salud ocupacional para su planta de Austin.",
    "asks": [{"department_id": "clinical", "request": "Confirmar personal para 3 días por semana."}],
    "conflicts": [],
}
WORKER = {"key_aspects": ["Revisar el alcance"], "open_questions": []}


def meridian_llm(**overrides: Any) -> FakeLlm:
    responses = {"classifier": ACCEPT, "orchestrator": MERIDIAN_PLAN, "synthesizer": SYNTHESIS, "worker": WORKER}
    responses.update(overrides)
    return FakeLlm(**responses)


# --------------------------------------------------------------------------
# Agente clasificador
# --------------------------------------------------------------------------


def test_classifier_accepts_formal_rfp():
    result = agents.classify(sections(MERIDIAN), llm=meridian_llm())
    assert result.accepted and result.document_kind == "formal_rfp"


def test_classifier_accepts_informal_email_rfp():
    llm = FakeLlm(classifier={"is_rfp": True, "confidence": 0.82, "document_kind": "informal_request", "reason": "Universidad pide propuesta"})
    assert agents.classify(sections(THAMES), llm=llm).accepted


def test_classifier_rejects_vendor_pitch():
    llm = FakeLlm(classifier={"is_rfp": False, "confidence": 0.97, "document_kind": "vendor_pitch", "reason": "Proveedor vende un EHR"})
    result = agents.classify(sections(MEDFLOW), llm=llm)
    assert not result.accepted and result.document_kind == "vendor_pitch"


def test_classifier_low_confidence_yes_is_not_accepted():
    llm = FakeLlm(classifier={"is_rfp": True, "confidence": 0.4, "document_kind": "other", "reason": "Dudoso"})
    assert not agents.classify(sections(THAMES), llm=llm).accepted


def test_classifier_sees_numbered_blocks_of_the_markdown():
    llm = meridian_llm()
    agents.classify(sections(MERIDIAN), llm=llm)
    assert "[0] REQUEST FOR PROPOSAL (RFP)" in llm.called("classifier")[0]["user"]


@pytest.mark.parametrize("raw", ["no es json", json.dumps({"is_rfp": "quizá"}), json.dumps({"confidence": 2})])
def test_classifier_invalid_output_is_an_error_not_a_rejection(raw):
    """Una salida inutilizable es un fallo técnico (estado `error`), nunca un
    `descartado`: decir "no es RFP" por un fallo del modelo sería mentir."""
    with pytest.raises(agents.AgentError):
        agents.classify(sections(MERIDIAN), llm=FakeLlm(classifier=raw))


def test_classifier_provider_failure_is_an_error():
    def broken(system, user):
        raise TimeoutError("proxy caído")

    with pytest.raises(agents.AgentError):
        agents.classify(sections(MERIDIAN), llm=broken)


def test_classifier_accepts_json_wrapped_in_markdown_fences():
    llm = FakeLlm(classifier="```json\n" + json.dumps(ACCEPT) + "\n```")
    assert agents.classify(sections(MERIDIAN), llm=llm).accepted


# --------------------------------------------------------------------------
# Orquestador
# --------------------------------------------------------------------------


def test_orchestrator_rejects_population_not_in_the_document():
    plan = dict(MERIDIAN_PLAN, covered_population={"count": 950, "unit": "employees"})
    result = agents.orchestrate(sections(MERIDIAN), llm=meridian_llm(orchestrator=plan))
    assert result["metadata"]["covered_population"] is None
    assert any("población cubierta" in q for q in result["open_questions"])


def test_orchestrator_keeps_population_written_with_thousands_separator():
    plan = {**MERIDIAN_PLAN, "client_name": "Thames Valley University", "client_country": "UK",
            "covered_population": {"count": 3000, "unit": "students"}, "assignments": {}}
    result = agents.orchestrate(sections(THAMES), llm=meridian_llm(orchestrator=plan))
    assert result["metadata"]["covered_population"] == 3000


def test_orchestrator_normalizes_country_and_ignores_unknown_blocks():
    plan = dict(MERIDIAN_PLAN, client_country="USA", assignments={"clinical": [5, 999], "marketing": [1]})
    result = agents.orchestrate(sections(MERIDIAN), llm=meridian_llm(orchestrator=plan))
    assert result["metadata"]["client_country"] == "US"
    assert [s.index for s in result["excerpts"]["clinical"]] == [5]
    assert "marketing" not in result["excerpts"]


def test_orchestrator_missing_country_and_deadline_become_open_questions():
    plan = dict(MERIDIAN_PLAN, client_country=None, deadline=None)
    questions = agents.orchestrate(sections(MERIDIAN), llm=meridian_llm(orchestrator=plan))["open_questions"]
    assert any("país" in q for q in questions) and any("fecha límite" in q for q in questions)


def test_revenue_and_compliance_are_always_required_and_unknown_departments_are_reported():
    assert required_departments(["clinical", "marketing"]) == ["revenue", "clinical", "compliance"]
    assert required_departments([]) == ["revenue", "compliance"]
    assert unmapped(["revenue", "marketing", "legal"]) == ["legal", "marketing"]


# --------------------------------------------------------------------------
# Worker (al menos uno, exigido por el README)
# --------------------------------------------------------------------------


def _meridian_metadata() -> Dict[str, Any]:
    return agents.orchestrate(sections(MERIDIAN), llm=meridian_llm())["metadata"]


def test_worker_receives_only_its_department_excerpts():
    doc = sections(MERIDIAN)
    llm = meridian_llm()
    agents.run_worker("clinical", _meridian_metadata(), [doc[5]], llm=llm)
    user = llm.called("clinical")[0]["user"]
    assert "3 days/week" in user
    assert "procurement@meridianmfg.com" not in user  # bloque de otro departamento
    assert "Business Associate Agreement" not in user


def test_worker_drops_aspects_with_figures_not_in_its_excerpts():
    doc = sections(MERIDIAN)
    llm = meridian_llm(worker={
        "key_aspects": ["Clínica in situ 3 días por semana", "Abrir 5 clínicas nuevas en Texas"],
        "open_questions": ["¿Cuántos turnos? Estimamos 40 horas"],
    })
    result = agents.run_worker("clinical", _meridian_metadata(), [doc[5]], llm=llm)
    assert result["key_aspects"] == ["Clínica in situ 3 días por semana"]
    assert result["unsupported_claims_removed"] == 1
    assert result["open_questions"] == []  # la pregunta con una cifra inventada tampoco pasa


def test_block_labels_do_not_count_as_document_figures():
    """El "[7]" del formato no es una cifra de la RFP: "7 clínicas" no pasa."""
    doc = sections(MERIDIAN)
    llm = meridian_llm(worker={"key_aspects": ["Hacen falta 7 clínicas"], "open_questions": []})
    result = agents.run_worker("clinical", _meridian_metadata(), [doc[7]], llm=llm)
    assert "Hacen falta 7 clínicas" not in result["key_aspects"]


def test_compliance_worker_always_carries_baa_for_us_clients():
    result = agents.run_worker("compliance", _meridian_metadata(), [], llm=meridian_llm(worker={"key_aspects": [], "open_questions": []}))
    assert any("BAA" in a and "HIPAA" in a for a in result["key_aspects"])


def test_compliance_worker_carries_dpa_and_revenue_quotes_gbp_for_uk_clients():
    metadata = dict(_meridian_metadata(), client_country="UK")
    llm = meridian_llm(worker={"key_aspects": [], "open_questions": []})
    compliance = agents.run_worker("compliance", metadata, [], llm=llm)
    revenue = agents.run_worker("revenue", metadata, [], llm=llm)
    assert any("DPA" in a and "UK GDPR" in a for a in compliance["key_aspects"])
    assert revenue["key_aspects"] == ["Cotizar en GBP (cliente UK)."]


def test_worker_is_told_the_fixed_facts_it_must_not_question():
    llm = meridian_llm()
    agents.run_worker("revenue", _meridian_metadata(), [], llm=llm)
    assert "HECHOS FIJADOS:\n- Cotizar en USD (cliente US)." in llm.called("revenue")[0]["user"]


def test_graph_runs_revenue_even_when_the_orchestrator_omits_it():
    """Regresión de la prueba real con Thames Valley: el modelo pidió solo
    clinical + compliance."""
    plan = dict(MERIDIAN_PLAN, departments_needed=["clinical"])
    state = run_graph(MERIDIAN, meridian_llm(orchestrator=plan))
    assert sorted(r["department_id"] for r in state["worker_results"]) == ["clinical", "compliance", "revenue"]


def test_worker_without_country_asks_instead_of_assuming():
    metadata = dict(_meridian_metadata(), client_country=None)
    result = agents.run_worker("compliance", metadata, [], llm=meridian_llm(worker={"key_aspects": [], "open_questions": []}))
    assert not any("BAA" in a or "DPA" in a for a in result["key_aspects"])
    assert any("País del cliente" in q for q in result["open_questions"])


# --------------------------------------------------------------------------
# Synthesizer
# --------------------------------------------------------------------------


def test_synthesizer_contacts_come_from_the_catalog_not_the_model():
    llm = meridian_llm(synthesizer={
        "summary": "Resumen",
        "asks": [{"department_id": "clinical", "request": "Plan de personal"}, {"department_id": "legal", "request": "x"}],
        "conflicts": [{"department_ids": ["revenue", "clinical"], "description": "Duración distinta"}],
    })
    results = [{"department_id": d, "key_aspects": [], "open_questions": []} for d in ("compliance", "clinical")]
    synthesis = agents.synthesize({}, results, llm=llm)
    assert [(a["department_id"], a["owner"]) for a in synthesis["who_to_ask"]] == [
        ("clinical", "Dr. Marcus Reid"),
        ("compliance", "Claire Whitfield"),
    ]
    assert synthesis["who_to_ask"][0]["request"] == "Plan de personal"
    assert synthesis["conflicts"] == [{"department_ids": ["revenue", "clinical"], "description": "Duración distinta"}]


# --------------------------------------------------------------------------
# PHI
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", [MERIDIAN, THAMES, MEDFLOW])
def test_sample_rfps_are_not_flagged_as_phi(text):
    assert not screen(clean_markdown(text)).contains_phi


@pytest.mark.parametrize(
    "categories,expected",
    [
        (frozenset({"email"}), False),  # contacto de empresa
        (frozenset({"clinical_content"}), False),  # describe un servicio
        (frozenset({"patient_name"}), True),
        (frozenset({"medical_record_number", "email"}), True),
        (frozenset({"clinical_content", "phone"}), True),  # conservador
        (frozenset(), False),
    ],
)
def test_is_patient_phi_rule(categories, expected):
    assert is_patient_phi(categories) is expected


def test_rfp_with_patient_summary_is_flagged_and_fully_redacted():
    verdict = screen(clean_markdown(PHI_RFP))
    assert verdict.contains_phi
    assert {"patient_name", "date_of_birth", "medical_record_number", "clinical_content"} <= set(verdict.categories)
    for leaked in ("Daniel", "Fakename", "1979", "00482913", "diabetes"):
        assert leaked not in verdict.redacted_markdown
    assert "450 employees" in verdict.redacted_markdown  # el resto de la RFP se conserva


# --------------------------------------------------------------------------
# Conversión y legibilidad
# --------------------------------------------------------------------------


def test_clean_markdown_turns_cid_glyphs_into_separate_bullets():
    raw = "2. Scope\n\n(cid:127) First item\nwrapped line\n\n(cid:127) Second item"
    assert split_sections(clean_markdown(raw))[1:] == [
        Section(1, "- First item wrapped line"),
        Section(2, "- Second item"),
    ]


def test_real_pdf_goes_through_markitdown(tmp_path):
    pdf = write_pdf(["REQUEST FOR PROPOSAL", "", "Covering 800 employees in Austin, Texas."], tmp_path / "rfp.pdf")
    markdown = pdf_to_markdown(pdf)
    assert "REQUEST FOR PROPOSAL" in markdown and "800 employees" in markdown


def test_short_documents_get_null_readability_with_a_reason():
    metrics = compute_readability(THAMES)
    assert metrics["flesch_kincaid_grade"] is None and "100 palabras" in metrics["readability_note"]
    assert metrics["word_count"] > 0 and metrics["estimated_tokens"] > 0


def test_long_documents_get_readability_scores():
    metrics = compute_readability(" ".join([MERIDIAN] * 2))
    assert metrics["flesch_kincaid_grade"] is not None and metrics["gunning_fog"] is not None


# --------------------------------------------------------------------------
# Grafo
# --------------------------------------------------------------------------


def run_graph(text: str, llm: FakeLlm) -> Dict[str, Any]:
    graph = compile_rfp_intake_graph(build_rfp_intake_graph(llm=llm, converter=lambda _path: clean_markdown(text)))
    return graph.invoke({"ticket_id": "t", "pdf_path": "unused.pdf", "worker_results": []})


def test_graph_structure_is_valid():
    validate_structure(build_rfp_intake_graph())


def test_graph_rejects_a_node_without_exit():
    builder = build_rfp_intake_graph()
    builder.add_node("orphan", lambda state: {})
    with pytest.raises(RfpGraphError):
        validate_structure(builder)


def test_graph_full_run_fans_out_one_worker_per_department():
    llm = meridian_llm()
    state = run_graph(MERIDIAN, llm)
    assert state["outcome"] == OUTCOME_COMPLETE
    assert sorted(r["department_id"] for r in state["worker_results"]) == ["clinical", "compliance", "revenue"]
    assert [c["agent"] for c in llm.calls][:2] == ["classifier", "orchestrator"]
    assert llm.calls[-1]["agent"] == "synthesizer"


def test_graph_not_an_rfp_stops_after_the_classifier():
    llm = FakeLlm(classifier={"is_rfp": False, "confidence": 0.95, "document_kind": "vendor_pitch", "reason": "Pitch"})
    state = run_graph(MEDFLOW, llm)
    assert state["outcome"] == OUTCOME_NOT_RFP
    assert [c["agent"] for c in llm.calls] == ["classifier"]


def test_graph_with_phi_never_calls_the_model():
    llm = meridian_llm()
    state = run_graph(PHI_RFP, llm)
    assert state["outcome"] == OUTCOME_PHI
    assert llm.calls == []
    assert "Fakename" not in state["markdown"]


# --------------------------------------------------------------------------
# Runner + persistencia (SQLite en memoria)
# --------------------------------------------------------------------------

# Todo el esquema RFP (las Partes 2 y 3 limpian también documento final y trace al reprocesar).
from rfp_fixtures import RFP_TABLES  # noqa: E402


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # Solo estas tablas: el metadata entero incluye reporting.* (sin esquema en SQLite).
    SQLModel.metadata.create_all(engine, tables=RFP_TABLES)
    with Session(engine) as db:
        yield db


def new_ticket(db: Session, tmp_path: Path):
    ticket_id = uuid.uuid4()
    pdf = tmp_path / f"{ticket_id}.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    return repository.create_ticket(db, ticket_id=ticket_id, raw_pdf_path=str(pdf), created_by="user-1")


def graph_for(text: str, llm: FakeLlm):
    return compile_rfp_intake_graph(build_rfp_intake_graph(llm=llm, converter=lambda _path: clean_markdown(text)))


def test_runner_persists_complete_analysis_and_handoff(session, tmp_path):
    ticket = new_ticket(session, tmp_path)
    status = run_intake(session, ticket.ticket_id, graph=graph_for(MERIDIAN, meridian_llm()))
    assert status == STATUS_COMPLETE

    session.refresh(ticket)
    metadata, sections_ = repository.get_details(session, ticket.ticket_id)
    assert metadata.client_country == "US" and metadata.currency == "USD"
    assert metadata.covered_population == 800 and str(metadata.deadline) == "2026-08-23"
    assert {s.department_id for s in sections_} == {"revenue", "clinical", "compliance"}
    assert ticket.next_stage == "generando_borrador"
    handoff = ticket.handoff_payload
    assert handoff["ticket_id"] == str(ticket.ticket_id)
    assert [w["department_id"] for w in handoff["workstreams"]] == ["revenue", "clinical", "compliance"]
    assert all(w["key_aspects"] for w in handoff["workstreams"])
    assert Path(tmp_path / f"{ticket.ticket_id}.md").exists()


def test_runner_phi_ticket_is_discarded_for_compliance_without_metadata(session, tmp_path):
    ticket = new_ticket(session, tmp_path)
    run_intake(session, ticket.ticket_id, graph=graph_for(PHI_RFP, meridian_llm()))
    session.refresh(ticket)
    assert ticket.status == STATUS_DISCARDED and ticket.discard_reason == "phi_detected"
    assert ticket.requires_compliance_review is True
    assert "patient_name" in ticket.phi_categories
    assert session.get(RfpMetadata, ticket.ticket_id) is None
    stored = (tmp_path / f"{ticket.ticket_id}.md").read_text()
    assert "Fakename" not in stored  # el Markdown guardado es el redactado


def test_runner_not_rfp_keeps_the_classifier_reason(session, tmp_path):
    ticket = new_ticket(session, tmp_path)
    llm = FakeLlm(classifier={"is_rfp": False, "confidence": 0.9, "document_kind": "vendor_pitch", "reason": "Pitch de EHR"})
    run_intake(session, ticket.ticket_id, graph=graph_for(MEDFLOW, llm))
    session.refresh(ticket)
    assert (ticket.status, ticket.discard_reason, ticket.classifier_reason) == (STATUS_DISCARDED, "not_rfp", "Pitch de EHR")
    assert session.exec(select(RfpDepartmentSection)).all() == []


def test_runner_is_idempotent_for_finished_tickets(session, tmp_path):
    ticket = new_ticket(session, tmp_path)
    run_intake(session, ticket.ticket_id, graph=graph_for(MERIDIAN, meridian_llm()))
    second = meridian_llm()
    assert run_intake(session, ticket.ticket_id, graph=graph_for(MERIDIAN, second)) == STATUS_COMPLETE
    assert second.calls == []  # mensaje de Celery repetido: no se rehace el trabajo


def test_failure_mid_pipeline_leaves_no_partial_result(session, tmp_path):
    ticket = new_ticket(session, tmp_path)
    with pytest.raises(agents.AgentError):
        run_intake(session, ticket.ticket_id, graph=graph_for(MERIDIAN, meridian_llm(synthesizer="roto")))
    session.refresh(ticket)
    assert ticket.status == STATUS_ANALYZING and ticket.attempts == 1
    assert session.exec(select(RfpDepartmentSection)).all() == []


def test_stale_analyzing_ticket_becomes_error(session, tmp_path):
    ticket = new_ticket(session, tmp_path)
    assert repository.recover_stale(session, ticket) is False
    ticket.updated_at = utcnow() - timedelta(hours=1)
    session.add(ticket)
    session.commit()
    assert repository.recover_stale(session, ticket) is True
    assert ticket.status == STATUS_ERROR and "Reprocesar" in ticket.error_message


# --------------------------------------------------------------------------
# Aviso en tiempo real `rfp_ticket_created` (Sistemas en Tiempo Real, Parte 1)
# `published_rfp_events` (conftest) graba lo que iría a Redis.
# --------------------------------------------------------------------------

import json  # noqa: E402

from data.pipelines.rfp_intake import notifications  # noqa: E402


def test_valid_rfp_publishes_one_ticket_created_event_while_still_analyzing(session, tmp_path, published_rfp_events):
    ticket = new_ticket(session, tmp_path)
    run_intake(session, ticket.ticket_id, graph=graph_for(MERIDIAN, meridian_llm()))

    assert len(published_rfp_events) == 1
    channel, raw = published_rfp_events[0]
    assert channel == notifications.RFP_EVENTS_CHANNEL
    message = json.loads(raw)
    assert message["event"] == "rfp_ticket_created"
    data = message["data"]
    # Campos del BRIEFING de tiempo real (§3), en su orden y ninguno más.
    assert list(data) == ["ticket_id", "rfp_id", "client_name", "client_country", "program_type", "status", "created_at"]
    assert data["ticket_id"] == str(ticket.ticket_id) and data["rfp_id"] == ticket.rfp_id
    assert data["client_name"] == MERIDIAN_PLAN["client_name"] and data["client_country"] == "US"
    # Se avisa al empezar el procesamiento, no al terminarlo.
    assert data["status"] == STATUS_ANALYZING
    assert data["created_at"].endswith("Z")


def test_not_an_rfp_publishes_nothing(session, tmp_path, published_rfp_events):
    ticket = new_ticket(session, tmp_path)
    llm = FakeLlm(classifier={"is_rfp": False, "confidence": 0.9, "document_kind": "vendor_pitch", "reason": "Pitch"})
    run_intake(session, ticket.ticket_id, graph=graph_for(MEDFLOW, llm))
    assert published_rfp_events == []


def test_rfp_with_phi_publishes_nothing(session, tmp_path, published_rfp_events):
    ticket = new_ticket(session, tmp_path)
    run_intake(session, ticket.ticket_id, graph=graph_for(PHI_RFP, meridian_llm()))
    assert published_rfp_events == []


def test_redis_down_does_not_break_the_analysis(session, tmp_path, monkeypatch):
    def broken_publisher():
        def publish(channel, message):
            raise ConnectionError("redis down")
        return publish

    monkeypatch.setattr(notifications, "_redis_publisher", broken_publisher)
    ticket = new_ticket(session, tmp_path)
    assert run_intake(session, ticket.ticket_id, graph=graph_for(MERIDIAN, meridian_llm())) == STATUS_COMPLETE


def test_ticket_created_payload_never_carries_fields_outside_the_allowlist():
    ticket = RfpTicket(rfp_id="RFP-TEST", raw_pdf_path="x.pdf", created_by="u")
    metadata = {"client_name": "Westbrook Manufacturing", "client_country": "US", "program_type": ["occupational_health"],
                "covered_population": 800, "summary": "texto libre del documento"}
    payload = notifications.ticket_created_payload(ticket, metadata)
    assert set(payload) == set(notifications.TICKET_CREATED_FIELDS)
