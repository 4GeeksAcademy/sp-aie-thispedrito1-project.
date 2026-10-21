"""Tests de la generación y evaluación de respuestas a RFP (Hito 9, Parte 2).
Sin red ni Supabase.

    services/api/.venv/bin/python -m pytest tests/pipelines/test_rfp_response.py

El modelo se sustituye por `FakeLlm`: reconoce al agente por su prompt de
sistema, devuelve borradores guionizados por departamento e intento, y graba
qué recibió cada uno. Así se prueba qué VE cada agente (handoff recortado,
feedback en el reintento) y no solo qué devuelve.

El handoff reproduce el que la Parte 1 dejó para la RFP de Meridian (US) en
la verificación real del 2026-10-19 (docs/rfp-intake.md).
"""

from __future__ import annotations

import copy
import json
import re
import threading
import uuid
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from data.pipelines.rfp_intake import evaluators, repository
from data.pipelines.rfp_intake.agents import AgentError
from data.pipelines.rfp_intake.departments import DEPARTMENT_IDS, DEPARTMENTS
from data.pipelines.rfp_intake.evaluators import (
    RULE_BAA_US,
    RULE_COUNTRY_KNOWN,
    RULE_CURRENCY,
    RULE_DPA_UK,
    RULE_NO_INVENTED_FIGURES,
    RULE_NO_PHI,
    count_syllables_es,
    evaluate_compliance,
    evaluate_readability,
    evaluate_relevance,
)
from data.pipelines.rfp_intake.generators import GENERATORS, GeneratorInput
from data.pipelines.rfp_intake.models import (
    NEXT_STAGE_APPROVAL,
    SECTION_NEEDS_REVIEW,
    SECTION_PASSED,
    STATUS_COMPLETE,
    STATUS_DISCARDED,
    STATUS_ERROR,
    STATUS_EVALUATED,
    STATUS_EVALUATING,
    STATUS_NEEDS_REVIEW,
    RfpDepartmentSection,
    RfpMetadata,
    RfpTicket,
    utcnow,
)
from data.pipelines.rfp_intake.response_graph import (
    build_response_graph,
    build_section_graph,
    compile_response_graph,
)
from data.pipelines.rfp_intake.response_runner import HandoffError, run_response

# --------------------------------------------------------------------------
# Datos: handoff de la Parte 1 y borradores
# --------------------------------------------------------------------------

META_US = {
    "client_name": "Meridian Manufacturing, Inc.",
    "client_country": "US",
    "currency": "USD",
    "program_type": ["occupational_health", "corporate_wellness"],
    "covered_population": 800,
    "covered_population_unit": "employees",
    "deadline": "2026-08-23",
    "budget_range": None,
    "departments_needed": ["revenue", "clinical", "compliance"],
    "unmapped_departments": [],
    "open_questions": [],
}

WORKSTREAMS_US = [
    {
        "department_id": "revenue",
        "owner": "Tom Callahan",
        "key_aspects": ["Cotizar en USD (cliente US).", "Contrato de 12 meses con opción de renovación."],
        "open_questions": ["Confirmar el presupuesto de referencia del cliente."],
        "excerpt_indexes": [3],
    },
    {
        "department_id": "clinical",
        "owner": "Dr. Marcus Reid",
        "key_aspects": ["Clínica en planta 3 días por semana.", "Revisiones anuales de bienestar para 800 empleados."],
        "open_questions": [],
        "excerpt_indexes": [4],
    },
    {
        "department_id": "compliance",
        "owner": "Claire Whitfield",
        "key_aspects": ["Cliente US: la propuesta debe incluir una cláusula BAA (HIPAA)."],
        "open_questions": [],
        "excerpt_indexes": [5],
    },
]


def handoff_for(ticket_id: str = "t-1", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "handoff_version": 1,
        "ticket_id": ticket_id,
        "rfp_id": "RFP-20261021-ABC123",
        "next_stage": "generando_borrador",
        "markdown_path": "data/raw/rfp/t-1.md",
        "metadata": metadata or dict(META_US),
        "workstreams": copy.deepcopy(WORKSTREAMS_US),
        "summary": "Meridian pide salud ocupacional y bienestar en planta para 800 empleados durante 12 meses.",
        "who_to_ask": [],
        "conflicts": [],
    }


# Borradores en español, frases de 12-20 palabras, solo cifras del handoff.
REVENUE_OK = (
    "Términos económicos\n"
    "HealthCore cotiza este programa en dólares estadounidenses, es decir, en USD, como corresponde a un cliente de Estados Unidos. "
    "La propuesta cubre a los 800 empleados de la planta de Meridian Manufacturing. "
    "El precio por empleado y por servicio queda [por confirmar] por Revenue Cycle antes del envío. "
    "Proponemos una cuota mensual fija que incluye la clínica en planta y las revisiones de bienestar. "
    "El contrato tendría una duración de 12 meses, con opción de renovación al terminar el periodo. "
    "La facturación sería mensual y el pago se haría por transferencia en el plazo que acuerden las partes. "
    "Los importes finales dependerán del calendario de atención que confirme el equipo clínico. "
    "Pendiente de confirmar: el presupuesto de referencia del cliente, que la solicitud no indica todavía."
)
CLINICAL_OK = (
    "Viabilidad clínica y capacidad\n"
    "HealthCore abrirá una clínica de salud laboral dentro de la planta durante 3 días por semana. "
    "El equipo atenderá consultas laborales, revisiones preventivas y seguimiento de bienestar de los trabajadores. "
    "Las revisiones anuales de bienestar cubrirán a los 800 empleados que indica la solicitud del cliente. "
    "La sede que prestará el servicio y el número de profesionales quedan [por confirmar] por Clinical Operations. "
    "El arranque se hará por fases, empezando por los turnos con más personal en la planta. "
    "El cliente debe facilitar un espacio privado y accesible para las consultas dentro de sus instalaciones. "
    "Cualquier cambio de turnos se acordará con antelación para no dejar a nadie sin cobertura. "
    "Pendiente de confirmar: la sede de referencia y la plantilla clínica asignada al contrato."
)
COMPLIANCE_OK = (
    "Protección de datos y cumplimiento normativo\n"
    "El programa se rige por la ley HIPAA, porque Meridian Manufacturing es un cliente de Estados Unidos. "
    "Antes de empezar, las dos partes firmarán un Business Associate Agreement, también llamado BAA. "
    "Ese acuerdo fija cómo HealthCore protege la información de salud de los trabajadores en todo momento. "
    "El cliente solo recibirá datos agregados de participación, nunca información clínica de una persona concreta. "
    "Los registros médicos se guardarán en los sistemas de HealthCore, con acceso limitado al personal autorizado. "
    "Cualquier incidente de seguridad se comunicará al cliente en los plazos que marca la normativa aplicable. "
    "La propuesta necesita la aprobación de Compliance and Data Governance antes de enviarse al cliente. "
    "Pendiente de confirmar: ninguna cuestión adicional de protección de datos por ahora."
)
# Mismo texto sin el BAA: promete cumplir HIPAA pero omite la cláusula que el BRIEFING exige.
COMPLIANCE_NO_BAA = COMPLIANCE_OK.replace(
    "Antes de empezar, las dos partes firmarán un Business Associate Agreement, también llamado BAA. ",
    "Antes de empezar, las dos partes revisarán juntas las medidas de seguridad de la información. ",
)
OK_DRAFTS = {"revenue": REVENUE_OK, "clinical": CLINICAL_OK, "compliance": COMPLIANCE_OK}

RUN_ON = "La propuesta incluye " + "servicios de salud laboral y " * 60 + "bienestar"


class FakeLlm:
    """Generadores: borradores guionizados por departamento (la lista avanza
    con cada llamada; el último se repite). Pertinencia: todo cubierto salvo
    que `relevance` diga otra cosa."""

    def __init__(
        self,
        drafts: Optional[Dict[str, List[str]]] = None,
        relevance: Optional[Callable[[str], Dict[str, Any]]] = None,
        on_relevance: Optional[Callable[[], None]] = None,
    ) -> None:
        self.drafts = {d: [OK_DRAFTS[d]] for d in DEPARTMENT_IDS}
        self.drafts.update(drafts or {})
        self.relevance = relevance
        self.on_relevance = on_relevance
        self.calls: List[Dict[str, str]] = []
        self._count: Dict[str, int] = {}
        self._lock = threading.Lock()

    def __call__(self, system: str, user: str) -> str:
        with self._lock:
            for department_id, department in DEPARTMENTS.items():
                if system.startswith(f"Eres el redactor de {department.name}"):
                    n = self._count.get(department_id, 0)
                    self._count[department_id] = n + 1
                    self.calls.append({"agent": f"generator:{department_id}", "user": user})
                    script = self.drafts[department_id]
                    return json.dumps({"content": script[min(n, len(script) - 1)]})
            if system.startswith("Eres el revisor de pertinencia"):
                self.calls.append({"agent": "relevance", "user": user})
        if self.on_relevance:
            self.on_relevance()
        if self.relevance:
            return json.dumps(self.relevance(user))
        aspects = re.findall(r"^\[(\d+)\]", user, re.MULTILINE)
        return json.dumps({"covered": [int(n) for n in aspects], "missing": []})

    def agent_calls(self, agent: str) -> List[str]:
        return [c["user"] for c in self.calls if c["agent"] == agent]


def generator_input(department_id: str = "compliance", metadata: Optional[Dict[str, Any]] = None) -> GeneratorInput:
    workstream = next(w for w in WORKSTREAMS_US if w["department_id"] == department_id)
    return GeneratorInput(
        department_id=department_id,
        metadata=metadata or META_US,
        key_aspects=workstream["key_aspects"],
        open_questions=workstream["open_questions"],
        summary=handoff_for()["summary"],
    )


def us_figures(department_id: str) -> set:
    workstream = next(w for w in WORKSTREAMS_US if w["department_id"] == department_id)
    return evaluators.allowed_figures(META_US, workstream, handoff_for()["summary"])


# --------------------------------------------------------------------------
# Generadores
# --------------------------------------------------------------------------


def test_each_department_has_its_own_generator():
    assert set(GENERATORS) == set(DEPARTMENT_IDS)
    prompts = {d: g.system_prompt for d, g in GENERATORS.items()}
    assert len(set(prompts.values())) == 3
    for department_id, prompt in prompts.items():
        assert DEPARTMENTS[department_id].owner in prompt
        assert GENERATORS[department_id].section_title in prompt


def test_generator_receives_only_its_slice_of_the_handoff():
    llm = FakeLlm()
    GENERATORS["clinical"].generate(generator_input("clinical"), llm=llm)
    user = llm.agent_calls("generator:clinical")[0]
    assert "Clínica en planta 3 días por semana." in user
    # Ni los aspectos de otros departamentos ni el PDF / Markdown de la Parte 1.
    assert "Cotizar en USD" not in user and "la propuesta debe incluir una cláusula BAA" not in user
    assert "markdown_path" not in user and ".pdf" not in user


def test_generator_is_given_the_fixed_facts_of_the_client_country():
    llm = FakeLlm()
    GENERATORS["compliance"].generate(generator_input("compliance"), llm=llm)
    assert "cláusula BAA y referencia HIPAA" in llm.agent_calls("generator:compliance")[0]

    uk = dict(META_US, client_country="UK", currency="GBP")
    GENERATORS["revenue"].generate(generator_input("revenue", uk), llm=llm)
    assert "se cotiza en GBP" in llm.agent_calls("generator:revenue")[0]


def test_generator_retry_carries_previous_draft_and_feedback():
    llm = FakeLlm()
    GENERATORS["compliance"].generate(
        generator_input("compliance"), previous_draft=COMPLIANCE_NO_BAA, feedback="1. [baa-us] Incluye una cláusula BAA.", llm=llm
    )
    user = llm.agent_calls("generator:compliance")[0]
    assert "NO PASÓ LA EVALUACIÓN" in user and "[baa-us]" in user and COMPLIANCE_NO_BAA in user


def test_generator_invalid_output_is_an_error():
    with pytest.raises(AgentError):
        GENERATORS["revenue"].generate(generator_input("revenue"), llm=lambda s, u: "no es json")


# --------------------------------------------------------------------------
# Evaluador de cumplimiento (reglas del BRIEFING §5)
# --------------------------------------------------------------------------


def test_compliance_passes_a_us_section_with_baa_and_hipaa():
    result = evaluate_compliance(
        COMPLIANCE_OK, department_id="compliance", client_country="US", figures_allowed=us_figures("compliance")
    )
    assert result.passed, result.violations
    assert set(result.rule_ids) >= {RULE_NO_PHI, RULE_BAA_US, RULE_CURRENCY, RULE_NO_INVENTED_FIGURES}
    assert result.contains_phi is False


def test_compliance_fails_us_proposal_without_baa_clause():
    """Caso del README anclado al BRIEFING §5: "Toda propuesta para un cliente
    en Estados Unidos debe incluir una cláusula de Business Associate
    Agreement (BAA)"."""
    result = evaluate_compliance(
        COMPLIANCE_NO_BAA, department_id="compliance", client_country="US", figures_allowed=us_figures("compliance")
    )
    assert result.passed is False
    assert [v.rule_id for v in result.violations] == [RULE_BAA_US]


def test_compliance_fails_uk_section_quoted_in_usd_and_without_dpa():
    uk_figures = us_figures("revenue")
    revenue = evaluate_compliance(REVENUE_OK, department_id="revenue", client_country="UK", figures_allowed=uk_figures)
    assert not revenue.passed
    assert {v.rule_id for v in revenue.violations} == {RULE_CURRENCY}

    compliance = evaluate_compliance(COMPLIANCE_OK, department_id="compliance", client_country="UK", figures_allowed=uk_figures)
    assert not compliance.passed
    messages = " ".join(v.message for v in compliance.violations)
    assert {v.rule_id for v in compliance.violations} == {RULE_DPA_UK}
    assert "DPA" in messages and "UK GDPR" in messages and "Quita BAA y HIPAA" in messages


def test_compliance_flags_patient_data_without_repeating_it():
    draft = COMPLIANCE_OK + "\nPatient: Daniel Okafor. Diagnosis: type 2 diabetes."
    result = evaluate_compliance(draft, department_id="compliance", client_country="US", figures_allowed=us_figures("compliance") | {"2"})
    assert result.contains_phi is True
    phi = [v for v in result.violations if v.rule_id == RULE_NO_PHI]
    assert phi and "Okafor" not in phi[0].message and "diabetes" not in phi[0].message


def test_compliance_rejects_figures_that_are_not_in_the_rfp():
    draft = REVENUE_OK.replace("800 empleados", "950 empleados")
    result = evaluate_compliance(draft, department_id="revenue", client_country="US", figures_allowed=us_figures("revenue"))
    violation = next(v for v in result.violations if v.rule_id == RULE_NO_INVENTED_FIGURES)
    assert "950" in violation.message


@pytest.mark.parametrize("text,expected", [
    ("una población de ochocientos empleados", {"800"}),
    ("novecientos cincuenta empleados durante doce meses", {"950", "12"}),
    ("treinta y cinco por ciento y dos mil quinientas personas", {"35", "2500"}),
    ("un contrato con una clínica y un equipo", set()),
    ("las dos partes firmarán en tres pasos", set()),
])
def test_spoken_spanish_numbers_are_read_as_figures(text, expected):
    assert evaluators.spoken_numbers_es(text) == expected


def test_compliance_catches_invented_figures_written_in_words():
    """Regresión de la primera corrida real: deepseek escribió las cifras con letras."""
    ok = REVENUE_OK.replace("800 empleados", "ochocientos empleados")
    assert evaluate_compliance(ok, department_id="revenue", client_country="US", figures_allowed=us_figures("revenue")).passed
    bad = REVENUE_OK.replace("800 empleados", "novecientos cincuenta empleados")
    result = evaluate_compliance(bad, department_id="revenue", client_country="US", figures_allowed=us_figures("revenue"))
    assert [v.rule_id for v in result.violations] == [RULE_NO_INVENTED_FIGURES]
    assert "950" in result.violations[0].message


def test_spanish_thousands_separator_is_not_an_invented_figure():
    """Regresión de la corrida real con Thames Valley: "3.000 estudiantes"
    (formato español) se marcaba como inventado frente al "3,000" de la RFP."""
    figures = evaluators.allowed_figures(dict(META_US, covered_population=3000), {"key_aspects": []}, None)
    draft = REVENUE_OK.replace("800 empleados", "3.000 estudiantes")
    result = evaluate_compliance(draft, department_id="revenue", client_country="US", figures_allowed=figures | us_figures("revenue"))
    assert result.passed, result.violations


def test_list_numbering_is_not_an_invented_figure():
    draft = "1. " + REVENUE_OK + "\n2) Condiciones de pago a convenir."
    result = evaluate_compliance(draft, department_id="revenue", client_country="US", figures_allowed=us_figures("revenue"))
    assert result.passed, result.violations


def test_unknown_country_is_not_retryable():
    result = evaluate_compliance(REVENUE_OK, department_id="revenue", client_country=None, figures_allowed=us_figures("revenue"))
    assert [v.rule_id for v in result.violations] == [RULE_COUNTRY_KNOWN]
    combined = evaluators.combine(
        section_id="t:revenue", department_id="revenue", iteration=1,
        readability=evaluate_readability(REVENUE_OK),
        relevance=evaluators.RelevanceResult(passed=True), compliance=result,
    )
    assert combined.overall_pass is False and combined.retryable is False


# --------------------------------------------------------------------------
# Evaluadores de pertinencia y legibilidad
# --------------------------------------------------------------------------


def test_relevance_sees_only_the_section_and_its_aspects():
    llm = FakeLlm()
    aspects = WORKSTREAMS_US[0]["key_aspects"]
    assert evaluate_relevance(REVENUE_OK, aspects, llm=llm).passed
    user = llm.agent_calls("relevance")[0]
    assert "[1] Cotizar en USD" in user and REVENUE_OK in user
    assert "Meridian pide salud ocupacional" not in user  # ni el resumen ni los metadatos


def test_relevance_reports_missing_aspects_and_treats_silence_as_missing():
    aspects = WORKSTREAMS_US[0]["key_aspects"]
    llm = FakeLlm(relevance=lambda _u: {"covered": [], "missing": [{"index": 2, "why": "no menciona la renovación"}, {"index": 9, "why": "x"}]})
    result = evaluate_relevance(REVENUE_OK, aspects, llm=llm)
    assert result.passed is False
    assert result.missing_aspects == [aspects[0], f"{aspects[1]} — no menciona la renovación"]


def test_relevance_without_aspects_passes_without_calling_the_model():
    llm = FakeLlm()
    assert evaluate_relevance(REVENUE_OK, [], llm=llm).passed
    assert llm.calls == []


@pytest.mark.parametrize("word,syllables", [("casa", 2), ("día", 2), ("ciudad", 2), ("propuesta", 3), ("aéreo", 4), ("y", 1)])
def test_spanish_syllable_counter(word, syllables):
    assert count_syllables_es(word) == syllables


def test_readability_passes_clear_prose():
    result = evaluate_readability(REVENUE_OK)
    assert result.passed, result.details
    assert result.score is not None and result.details["formula"] == "fernandez_huerta"


def test_readability_fails_a_run_on_sentence_with_concrete_feedback():
    result = evaluate_readability(RUN_ON)
    assert result.passed is False
    assert result.details["problems"]


def test_readability_fails_a_section_that_is_too_short():
    result = evaluate_readability("Cotizamos en USD. El precio queda por confirmar.")
    assert result.passed is False
    assert result.details["problems"]


def test_feedback_lists_compliance_first_and_is_actionable():
    compliance = evaluate_compliance(COMPLIANCE_NO_BAA, department_id="compliance", client_country="US", figures_allowed=us_figures("compliance"))
    relevance = evaluators.RelevanceResult(passed=False, missing_aspects=["Minimización de datos"])
    result = evaluators.combine(
        section_id="t:compliance", department_id="compliance", iteration=1,
        readability=evaluate_readability(COMPLIANCE_NO_BAA), relevance=relevance, compliance=compliance,
    )
    lines = result.feedback_for_generator.splitlines()
    assert lines[0].startswith("1. [baa-us] Incluye una cláusula BAA")
    assert lines[1] == "2. Aborda este aspecto de la RFP: Minimización de datos"
    record = result.to_record()
    assert record["compliance"]["pass"] is False and record["overall_pass"] is False  # claves del README


# --------------------------------------------------------------------------
# Grafo: ciclo generador-evaluador
# --------------------------------------------------------------------------


def run_graph(llm: FakeLlm, *, handoff: Optional[Dict[str, Any]] = None, max_iterations: int = 3) -> Dict[str, Dict[str, Any]]:
    graph = compile_response_graph(llm=llm, max_iterations=max_iterations)
    state = graph.invoke({"ticket_id": "t-1", "handoff": handoff or handoff_for(), "max_iterations": max_iterations, "sections": []})
    return {s["department_id"]: s for s in state["sections"]}


def test_graphs_have_a_valid_structure():
    compile_response_graph(llm=FakeLlm())
    build_section_graph(llm=FakeLlm()).compile()
    build_response_graph(llm=FakeLlm()).compile()


def test_all_sections_pass_on_the_first_iteration():
    llm = FakeLlm()
    sections = run_graph(llm)
    assert set(sections) == set(DEPARTMENT_IDS)
    for section in sections.values():
        assert section["status"] == SECTION_PASSED
        assert section["iterations"] == 1 and len(section["history"]) == 1
        assert section["evaluation"]["overall_pass"] is True
    assert len(llm.agent_calls("relevance")) == 3


def test_failing_section_goes_back_to_its_generator_with_the_feedback():
    llm = FakeLlm(drafts={"compliance": [COMPLIANCE_NO_BAA, COMPLIANCE_OK]})
    sections = run_graph(llm)
    compliance = sections["compliance"]
    assert compliance["status"] == SECTION_PASSED and compliance["iterations"] == 2
    assert compliance["history"][0]["compliance"]["pass"] is False
    retry = llm.agent_calls("generator:compliance")[1]
    assert "[baa-us]" in retry and COMPLIANCE_NO_BAA in retry
    # Los otros departamentos no repiten: su ciclo es independiente.
    assert sections["revenue"]["iterations"] == 1 and sections["clinical"]["iterations"] == 1


def test_iteration_limit_keeps_last_draft_and_marks_needs_human_review():
    last = COMPLIANCE_NO_BAA + " Versión final."
    llm = FakeLlm(drafts={"compliance": [COMPLIANCE_NO_BAA, COMPLIANCE_NO_BAA, last]})
    sections = run_graph(llm, max_iterations=3)
    compliance = sections["compliance"]
    assert compliance["status"] == SECTION_NEEDS_REVIEW
    assert compliance["iterations"] == 3 and len(llm.agent_calls("generator:compliance")) == 3
    assert compliance["draft_content"] == last
    assert compliance["evaluation"]["iteration"] == 3 and compliance["evaluation"]["overall_pass"] is False
    assert sections["revenue"]["status"] == SECTION_PASSED  # el ticket sigue con el resto


def test_unfixable_section_stops_without_retrying():
    llm = FakeLlm()
    sections = run_graph(llm, handoff=handoff_for(metadata=dict(META_US, client_country=None)))
    assert all(s["status"] == SECTION_NEEDS_REVIEW and s["iterations"] == 1 for s in sections.values())
    assert len(llm.agent_calls("generator:revenue")) == 1


# --------------------------------------------------------------------------
# Runner + persistencia (SQLite en memoria)
# --------------------------------------------------------------------------

RFP_TABLES = [RfpTicket.__table__, RfpMetadata.__table__, RfpDepartmentSection.__table__]


@pytest.fixture()
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine, tables=RFP_TABLES)
    return engine


@pytest.fixture()
def session(engine):
    with Session(engine) as db:
        yield db


def completed_ticket(db: Session, *, status: str = STATUS_COMPLETE, metadata: Optional[Dict[str, Any]] = None) -> RfpTicket:
    """Ticket tal como lo deja la Parte 1: `analisis_completo` + secciones + handoff."""
    ticket_id = uuid.uuid4()
    ticket = repository.create_ticket(db, ticket_id=ticket_id, raw_pdf_path="data/raw/rfp/x.pdf", created_by="user-1")
    handoff = handoff_for(str(ticket_id), metadata)
    repository.save_result(
        db, ticket,
        ticket_fields={"status": status, "handoff_payload": handoff, "next_stage": "generando_borrador"},
        metadata_fields={"client_name": META_US["client_name"], "client_country": META_US["client_country"]},
        sections=[
            {k: w[k] for k in ("department_id", "owner", "key_aspects", "open_questions", "excerpt_indexes")}
            for w in handoff["workstreams"]
        ],
    )
    return ticket


def test_runner_persists_drafts_evaluations_and_handoff_to_part_3(session):
    ticket = completed_ticket(session)
    part1_handoff = copy.deepcopy(ticket.handoff_payload)

    status = run_response(session, ticket.ticket_id, llm=FakeLlm())

    assert status == STATUS_EVALUATED
    session.refresh(ticket)
    assert ticket.status == STATUS_EVALUATED and ticket.next_stage == NEXT_STAGE_APPROVAL
    assert ticket.handoff_payload == part1_handoff  # el contrato de la Parte 1 no se pisa
    _, sections = repository.get_details(session, ticket.ticket_id)
    assert {s.department_id: s.draft_status for s in sections} == {d: SECTION_PASSED for d in DEPARTMENT_IDS}
    for section in sections:
        assert section.draft_content == OK_DRAFTS[section.department_id]
        assert section.evaluation_results["overall_pass"] is True
        assert set(section.evaluation_results) >= {"readability", "relevance", "compliance", "feedback_for_generator"}
    handoff = ticket.response_handoff
    assert [s["department_id"] for s in handoff["sections"]] == list(DEPARTMENT_IDS)
    assert all(s["evaluation_result"] and s["draft_content"] and not s["provisional"] for s in handoff["sections"])


def test_exhausted_section_reaches_part_3_as_provisional_and_the_ticket_survives(session):
    ticket = completed_ticket(session)
    status = run_response(session, ticket.ticket_id, llm=FakeLlm(drafts={"compliance": [COMPLIANCE_NO_BAA]}))

    assert status == STATUS_NEEDS_REVIEW
    session.refresh(ticket)
    assert ticket.status == STATUS_NEEDS_REVIEW and ticket.next_stage == NEXT_STAGE_APPROVAL
    by_department = {s["department_id"]: s for s in ticket.response_handoff["sections"]}
    assert set(by_department) == set(DEPARTMENT_IDS)  # nada se descarta
    compliance = by_department["compliance"]
    assert compliance["provisional"] is True and compliance["draft_status"] == SECTION_NEEDS_REVIEW
    assert compliance["draft_content"] == COMPLIANCE_NO_BAA
    assert compliance["evaluation_result"]["compliance"]["violations"][0]["rule_id"] == RULE_BAA_US
    _, sections = repository.get_details(session, ticket.ticket_id)
    stored = next(s for s in sections if s.department_id == "compliance")
    assert stored.draft_iterations == 3 and len(stored.evaluation_history) == 3


def test_ticket_shows_progress_in_real_time(session, engine):
    ticket = completed_ticket(session)
    seen: List[tuple] = []

    def snapshot() -> None:
        # Durante la evaluación, otra conexión ya ve el estado intermedio.
        with Session(engine) as other:
            current = other.get(RfpTicket, ticket.ticket_id)
            _, sections = repository.get_details(other, ticket.ticket_id)
            seen.append((current.status, sorted({s.draft_status for s in sections if s.draft_content})))

    run_response(session, ticket.ticket_id, llm=FakeLlm(on_relevance=snapshot))
    assert seen and all(status == STATUS_EVALUATING for status, _ in seen)
    assert all("en_evaluación" in statuses for _, statuses in seen)


def test_phi_in_a_draft_flags_the_ticket_for_compliance_review(session):
    ticket = completed_ticket(session)
    with_phi = CLINICAL_OK + "\nPatient: Daniel Okafor."
    run_response(session, ticket.ticket_id, llm=FakeLlm(drafts={"clinical": [with_phi]}))
    session.refresh(ticket)
    assert ticket.requires_compliance_review is True
    assert ticket.status == STATUS_NEEDS_REVIEW


@pytest.mark.parametrize("status", [STATUS_DISCARDED, STATUS_ERROR, STATUS_EVALUATED])
def test_runner_skips_tickets_that_are_not_ready(session, status):
    ticket = completed_ticket(session, status=status)
    llm = FakeLlm()
    assert run_response(session, ticket.ticket_id, llm=llm) == status
    assert llm.calls == []


def test_runner_rejects_an_unknown_handoff_version(session):
    ticket = completed_ticket(session)
    ticket.handoff_payload = {**ticket.handoff_payload, "handoff_version": 99}
    session.add(ticket)
    session.commit()
    with pytest.raises(HandoffError):
        run_response(session, ticket.ticket_id, llm=FakeLlm())


def test_stale_drafting_ticket_becomes_error(session):
    ticket = completed_ticket(session, status=STATUS_EVALUATING)
    ticket.updated_at = utcnow() - repository.STALE_AFTER - timedelta(minutes=1)
    session.add(ticket)
    session.commit()
    assert repository.recover_stale(session, ticket) is True
    assert ticket.status == STATUS_ERROR and "generación de borradores" in ticket.error_message


# --------------------------------------------------------------------------
# Celery: la Parte 1 encadena la Parte 2
# --------------------------------------------------------------------------


@pytest.mark.parametrize("final_status,chained", [(STATUS_COMPLETE, True), (STATUS_DISCARDED, False)])
def test_part_1_task_enqueues_part_2_only_when_the_analysis_is_complete(monkeypatch, engine, final_status, chained):
    from data.pipelines.rfp_intake import runner
    from services.tasks import rfp_tasks

    enqueued: List[uuid.UUID] = []
    monkeypatch.setattr(rfp_tasks, "_engine", lambda: engine)
    monkeypatch.setattr(runner, "run_intake", lambda _session, _ticket_id: final_status)
    monkeypatch.setattr(rfp_tasks, "enqueue_rfp_response", enqueued.append)
    ticket_id = uuid.uuid4()

    rfp_tasks.process_rfp_ticket.run(ticket_id=str(ticket_id))
    assert enqueued == ([ticket_id] if chained else [])


def test_redis_failure_when_chaining_does_not_fail_part_1(monkeypatch, engine):
    from data.pipelines.rfp_intake import runner
    from services.tasks import rfp_tasks

    def broken(_ticket_id):
        raise ConnectionError("redis down")

    monkeypatch.setattr(rfp_tasks, "_engine", lambda: engine)
    monkeypatch.setattr(runner, "run_intake", lambda _session, _ticket_id: STATUS_COMPLETE)
    monkeypatch.setattr(rfp_tasks, "enqueue_rfp_response", broken)
    assert rfp_tasks.process_rfp_ticket.run(ticket_id=str(uuid.uuid4()))["status"] == STATUS_COMPLETE
