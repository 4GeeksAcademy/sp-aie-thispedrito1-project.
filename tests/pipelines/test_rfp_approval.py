"""Tests de la aprobación humana y el cierre (Hito 9, Parte 3). Sin red ni Supabase.

    services/api/.venv/bin/python -m pytest tests/pipelines/test_rfp_approval.py

El checkpointer es `InMemorySaver` (en producción, Postgres en Supabase:
data/pipelines/rfp_intake/checkpointer.py). Lo que se prueba es la
semántica de LangGraph que importa (pausar, reanudar, ramas independientes),
idéntica con cualquier checkpointer.

Dos niveles:
- grafo: `compile_approval_graph` + `Command(resume=...)` directamente;
- runner: `advance` con la base SQLite en memoria, como lo haría el worker.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlmodel import Session, create_engine
from sqlmodel.pool import StaticPool

from data.pipelines.rfp_intake import repository
from data.pipelines.rfp_intake.approval_graph import (
    BRANCH_APPROVED,
    BRANCH_BLOCKED,
    DEFAULT_MAX_REVISION_ROUNDS,
    RESULT_ARBITRATION_LIMIT,
    RESULT_FINISHED,
    build_approval_graph,
    build_branch_graph,
    compile_approval_graph,
    route_after_decision,
)
from data.pipelines.rfp_intake.approval_runner import (
    ApprovalStateError,
    advance,
    build_approval_input,
    read_view,
    submit_arbitration_decision,
    submit_section_decision,
)
from data.pipelines.rfp_intake.arbitration import (
    TRIGGER_BAA_DPA,
    TRIGGER_CAPACITY,
    TRIGGER_PHI,
    capacity_shortfall,
    detect_conflicts,
)
from data.pipelines.rfp_intake.checkpointer import thread_config, thread_id_for
from data.pipelines.rfp_intake.decisions import DecisionError, validate_section_decision
from data.pipelines.rfp_intake.final_document import FinalDocumentError, build_final_document
from data.pipelines.rfp_intake.graph import validate_structure
from data.pipelines.rfp_intake.models import (
    APPROVAL_APPROVED,
    APPROVAL_BLOCKED,
    APPROVAL_CANCELLED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    DISCARD_ARBITRATION_LIMIT,
    DISCARD_REJECTED,
    STATUS_AWAITING_APPROVAL,
    STATUS_DISCARDED,
    STATUS_FINISHED,
)
from rfp_fixtures import (
    CLINICAL_500,
    CLINICAL_OK,
    CLINICAL_REVISED,
    CLINICAL_WITH_PHI,
    COMPLIANCE_NO_BAA,
    OK_DRAFTS,
    REVENUE_500,
    ScriptedLlm,
    create_rfp_tables,
    run_parallel_scenario,
    seed_ticket,
)

# --------------------------------------------------------------------------
# Ayudas
# --------------------------------------------------------------------------


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_rfp_tables(engine)
    with Session(engine) as db:
        yield db


class Recorder:
    """`on_trace` que guarda todo lo que el grafo emite, en orden."""

    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []

    def __call__(self, item: Dict[str, Any]) -> None:
        self.items.append(item)

    def nodes(self, department_id: str = None) -> List[str]:
        return [i["node"] for i in self.items if department_id is None or i["department_id"] == department_id]


class Flow:
    """Grafo de aprobación sobre un ticket sembrado, sin base de datos."""

    def __init__(self, session: Session, drafts: Dict[str, str] = None, llm: ScriptedLlm = None, **graph_kwargs: Any) -> None:
        self.ticket_id = seed_ticket(session, drafts)
        self.ticket = repository.get_ticket(session, self.ticket_id)
        self.llm = llm or ScriptedLlm()
        self.trace = Recorder()
        self.saver = InMemorySaver()
        self.graph = compile_approval_graph(checkpointer=self.saver, llm=self.llm, on_trace=self.trace)
        self.config = thread_config(self.ticket_id)
        self.payload = build_approval_input(self.ticket, **graph_kwargs)

    def start(self) -> None:
        self.graph.invoke(self.payload, self.config)

    def view(self):
        return read_view(self.graph, self.config)

    def resume(self, decisions: Dict[str, Dict[str, Any]]) -> None:
        pending = self.view().pending
        now = datetime.now(timezone.utc).isoformat()
        self.graph.invoke(Command(resume={
            pending[d][0]: {"decided_by": f"user-{d}", "decided_at": now, **value} for d, value in decisions.items()
        }), self.config)

    def arbitrate(self, value: Dict[str, Any]) -> None:
        interrupt_id, _ = self.view().arbitration
        self.graph.invoke(Command(resume={interrupt_id: {"decided_by": "user-tom", **value}}), self.config)

    def values(self) -> Dict[str, Any]:
        return self.view().values


APPROVE = {"decision": "approve"}
APPROVE_CLINICAL = {"decision": "approve", "confirmed_capacity": 800}

# --------------------------------------------------------------------------
# Estructura
# --------------------------------------------------------------------------


def test_graph_structures_are_valid():
    validate_structure(build_approval_graph())
    validate_structure(build_branch_graph())


def test_thread_id_is_namespaced_by_ticket():
    ticket_id = uuid.uuid4()
    assert thread_id_for(ticket_id) == f"rfp-{ticket_id}"
    assert thread_config(ticket_id) == {"configurable": {"thread_id": f"rfp-{ticket_id}"}}


# --------------------------------------------------------------------------
# Interrupción y reanudación
# --------------------------------------------------------------------------


def test_each_department_pauses_before_its_approval_and_state_is_checkpointed(session):
    flow = Flow(session)
    flow.start()
    view = flow.view()
    assert sorted(view.pending) == ["clinical", "compliance", "revenue"]
    assert not view.finished
    # El checkpoint existe en el saver, con el hilo del ticket.
    assert flow.saver.get_tuple(flow.config) is not None
    request = view.pending["clinical"][1]
    assert request["approver"] == "Dr. Marcus Reid"
    assert request["asks_capacity"] is True and request["covered_population"] == 800
    assert request["can_request_changes"] is True
    assert view.pending["compliance"][1]["fixed_facts"] == {
        "client_country": "US", "currency": "USD", "data_agreement": "BAA", "regulation": "HIPAA",
    }


def test_resume_continues_from_the_interrupt_without_restarting_the_flow(session):
    flow = Flow(session)
    flow.start()
    before = list(flow.trace.items)
    flow.resume({"revenue": APPROVE, "clinical": APPROVE_CLINICAL, "compliance": APPROVE})

    assert flow.view().finished and flow.values()["result"] == RESULT_FINISHED
    # Ningún nodo anterior a la pausa se volvió a ejecutar ni se llamó al modelo.
    assert flow.trace.nodes().count("open_approval") == 1
    assert flow.trace.nodes().count("screen_section") == 3
    assert flow.llm.calls == []
    new_nodes = [i["node"] for i in flow.trace.items[len(before):]]
    assert new_nodes.count("request_approval") == 3 and new_nodes[-1] == "synthesize_final_document"


def test_department_b_is_approved_while_department_a_stays_interrupted(session):
    """Ramas paralelas reales: aprobar Compliance la lleva hasta su final (su
    resultado ya está en el estado) mientras Revenue y Clinical siguen
    pausadas, y el documento final no se genera hasta que lo hacen."""
    flow = Flow(session)
    flow.start()
    flow.resume({"compliance": APPROVE})

    view = flow.view()
    assert sorted(view.pending) == ["clinical", "revenue"]
    assert view.values["outcomes"]["compliance"]["status"] == BRANCH_APPROVED
    assert "final_document" not in view.values
    # Trace: Compliance registró su decisión; Revenue y Clinical, todavía no.
    decided = [i["department_id"] for i in flow.trace.items if i["node"] == "request_approval"]
    assert decided == ["compliance"]

    flow.resume({"revenue": APPROVE})
    assert sorted(flow.view().pending) == ["clinical"]
    flow.resume({"clinical": APPROVE_CLINICAL})
    assert flow.values()["result"] == RESULT_FINISHED
    order = [i["department_id"] for i in flow.trace.items if i["node"] == "request_approval"]
    assert order == ["compliance", "revenue", "clinical"]


def test_request_changes_goes_back_to_the_part_2_generator_and_pauses_again(session):
    llm = ScriptedLlm(drafts={"clinical": [CLINICAL_REVISED]})
    flow = Flow(session, llm=llm)
    flow.start()
    flow.resume({"clinical": {"decision": "request_changes", "comments": "Concretad el turno de arranque."}})

    request = flow.view().pending["clinical"][1]
    assert request["draft"] == CLINICAL_REVISED
    assert request["revision_round"] == 1 and request["can_request_changes"] is True
    prompt = llm.agent_calls("generator:clinical")[0]
    assert "Revisión de Dr. Marcus Reid (Clinical Operations): Concretad el turno de arranque." in prompt
    assert "BORRADOR ANTERIOR" in prompt  # corrige sobre su versión, no empieza de cero
    assert llm.agent_calls("relevance")  # la versión nueva vuelve a pasar los evaluadores
    # Las otras dos ramas no se enteraron.
    assert sorted(flow.view().pending) == ["clinical", "compliance", "revenue"]
    assert llm.agent_calls("generator:revenue") == []


# --------------------------------------------------------------------------
# Límites de iteraciones
# --------------------------------------------------------------------------


def test_revision_round_limit_is_enforced_in_code(session):
    flow = Flow(session, llm=ScriptedLlm(drafts={"clinical": [CLINICAL_REVISED, CLINICAL_OK_V2, CLINICAL_OK_V3]}))
    flow.start()
    for _ in range(DEFAULT_MAX_REVISION_ROUNDS):
        flow.resume({"clinical": {"decision": "request_changes", "comments": "Otra vuelta, por favor, más concreto."}})

    request = flow.view().pending["clinical"][1]
    assert request["revision_round"] == DEFAULT_MAX_REVISION_ROUNDS
    assert request["can_request_changes"] is False
    with pytest.raises(DecisionError) as error:
        validate_section_decision({"decision": "request_changes", "comments": "Una más, por favor."}, request)
    assert error.value.errors[0]["field"] == "decision"
    # Red de seguridad del grafo, aunque alguien saltara la validación.
    with pytest.raises(DecisionError):
        route_after_decision({"decision": {"decision": "request_changes"}, "revision_round": 2, "max_revision_rounds": 2})


CLINICAL_OK_V2 = CLINICAL_REVISED.replace("turno de mañana", "turno de mañana del lunes")
CLINICAL_OK_V3 = CLINICAL_REVISED.replace("turno de mañana", "primer turno de mañana")


def test_arbitration_round_limit_discards_the_proposal(session):
    """Clinical sigue sin capacidad tras el arbitraje de Tom: al agotar las
    rondas, la propuesta se cierra sin documento (decisión del usuario)."""
    flow = Flow(session, llm=ScriptedLlm(drafts={"clinical": [CLINICAL_OK]}), max_arbitration_rounds=1)
    flow.start()
    flow.resume({"revenue": APPROVE, "compliance": APPROVE, "clinical": {"decision": "approve", "confirmed_capacity": 500}})
    flow.arbitrate({"option": "add_sites", "note": "Sumar la sede de Round Rock a la cobertura."})
    flow.resume({"clinical": {"decision": "approve", "confirmed_capacity": 600}})  # sigue sin llegar a 800

    values = flow.values()
    assert flow.view().finished and values["result"] == RESULT_ARBITRATION_LIMIT
    assert "final_document" not in values
    assert values["arbitration"]["resolution"] == "limit"


# --------------------------------------------------------------------------
# Arbitraje (BRIEFING §7)
# --------------------------------------------------------------------------


def test_phi_is_blocked_before_the_human_and_compliance_forces_changes(session):
    llm = ScriptedLlm(drafts={"clinical": [CLINICAL_OK]})
    flow = Flow(session, drafts={**OK_DRAFTS, "clinical": CLINICAL_WITH_PHI}, llm=llm)
    flow.start()
    # La rama con PHI no llega a pedir aprobación: el humano no ve el paciente.
    assert sorted(flow.view().pending) == ["compliance", "revenue"]
    flow.resume({"revenue": APPROVE, "compliance": APPROVE})

    values = flow.values()
    assert values["arbitration"]["conflicts"][0]["trigger_id"] == TRIGGER_PHI
    assert values["arbitration"]["conflicts"][0]["arbiter"] == "Claire Whitfield"
    assert values["arbitration"]["resolution"] == "rule"
    assert "final_document" not in values  # el synthesizer no corrió
    prompt = llm.agent_calls("generator:clinical")[0]
    assert "[phi-detected] Claire Whitfield (Compliance)" in prompt
    # El feedback nombra categorías, nunca el dato.
    feedback = values["arbitration"]["forced"]["clinical"]
    assert "Fakename" not in feedback and "diabetes" not in feedback

    assert sorted(flow.view().pending) == ["clinical"]
    flow.resume({"clinical": APPROVE_CLINICAL})
    assert flow.values()["result"] == RESULT_FINISHED


def test_baa_mismatch_forces_request_changes_on_compliance(session):
    llm = ScriptedLlm(drafts={"compliance": [OK_DRAFTS["compliance"]]})
    flow = Flow(session, drafts={**OK_DRAFTS, "compliance": COMPLIANCE_NO_BAA}, llm=llm)
    flow.start()
    assert "compliance" not in flow.view().pending
    flow.resume({"revenue": APPROVE, "clinical": APPROVE_CLINICAL})

    arbitration = flow.values()["arbitration"]
    assert [c["trigger_id"] for c in arbitration["conflicts"]] == [TRIGGER_BAA_DPA]
    assert arbitration["forced_departments"] == ["compliance"]
    prompt = llm.agent_calls("generator:compliance")[0]
    assert "[baa-dpa-mismatch] Claire Whitfield (Compliance)" in prompt and "cláusula BAA (HIPAA)" in prompt
    flow.resume({"compliance": APPROVE})
    assert flow.values()["result"] == RESULT_FINISHED


def test_capacity_conflict_is_decided_by_the_named_human_arbiter(session):
    llm = ScriptedLlm(drafts={"revenue": [REVENUE_500], "clinical": [CLINICAL_500]})
    flow = Flow(session, llm=llm)
    flow.start()
    flow.resume({"revenue": APPROVE, "compliance": APPROVE, "clinical": {"decision": "approve", "confirmed_capacity": 500}})

    _, request = flow.view().arbitration
    assert request["trigger_id"] == TRIGGER_CAPACITY
    assert request["arbiter"] == "Tom Callahan" and request["compliance_cleared"] is True
    assert request["covered_population"] == 800 and request["confirmed_capacity"] == 500
    assert llm.calls == []  # ningún modelo "vota": se espera a Tom

    flow.arbitrate({"option": "reduce_population", "new_population": 500})
    assert sorted(flow.view().pending) == ["clinical", "revenue"]  # Compliance conserva su aprobación
    assert "fija la población cubierta en 500" in llm.agent_calls("generator:revenue")[0]
    flow.resume({"revenue": APPROVE, "clinical": {"decision": "approve", "confirmed_capacity": 500}})

    document = flow.values()["final_document"]
    assert "500 empleados" in document["content_markdown"]
    assert "**Población cubierta:** 500 empleados" in document["content_markdown"]


@pytest.mark.parametrize("outcomes,context,expected", [
    ({"clinical": {"status": "approved", "confirmed_capacity": 500}, "compliance": {"status": "approved"}}, {"covered_population": 800}, True),
    ({"clinical": {"status": "approved", "confirmed_capacity": 800}, "compliance": {"status": "approved"}}, {"covered_population": 800}, False),
    ({"clinical": {"status": "approved", "confirmed_capacity": 900}, "compliance": {"status": "approved"}}, {"covered_population": 800}, False),
    # Sin población en la RFP no hay con qué comparar (nunca se inventa).
    ({"clinical": {"status": "approved", "confirmed_capacity": 500}, "compliance": {"status": "approved"}}, {"covered_population": None}, False),
    # Sin Clinical en la propuesta, o sin capacidad confirmada, no hay trigger.
    ({"compliance": {"status": "approved"}}, {"covered_population": 800}, False),
    ({"clinical": {"status": "blocked", "confirmed_capacity": None}, "compliance": {"status": "approved"}}, {"covered_population": 800}, False),
    # BRIEFING: Tom arbitra "tras confirmar Compliance que no hay PHI".
    ({"clinical": {"status": "approved", "confirmed_capacity": 500}, "compliance": {"status": "blocked"}}, {"covered_population": 800}, False),
])
def test_capacity_shortfall_rule(outcomes, context, expected):
    assert capacity_shortfall(outcomes, context) is expected


def test_regulatory_triggers_take_priority_over_capacity():
    outcomes = {
        "revenue": {"status": "approved", "draft": OK_DRAFTS["revenue"], "signals": []},
        "clinical": {"status": "approved", "draft": OK_DRAFTS["clinical"], "signals": [], "confirmed_capacity": 100},
        "compliance": {"status": "blocked", "draft": COMPLIANCE_NO_BAA,
                       "signals": [{"trigger_id": TRIGGER_BAA_DPA, "rule_ids": ["baa-us"], "messages": ["Falta el BAA."]}]},
    }
    conflicts = detect_conflicts(outcomes, {"client_country": "US", "covered_population": 800})
    assert [c.trigger_id for c in conflicts] == [TRIGGER_BAA_DPA]


# --------------------------------------------------------------------------
# Validación de la respuesta humana
# --------------------------------------------------------------------------


REQUEST = {"can_request_changes": True, "max_revision_rounds": 2, "asks_capacity": False}


@pytest.mark.parametrize("raw,field", [
    ({"decision": "aprobar"}, "decision"),
    ({"decision": "request_changes"}, "comments"),
    ({"decision": "reject", "comments": "No"}, "comments"),
    ({"decision": "approve", "confirmed_capacity": 300}, "confirmed_capacity"),
    ({"decision": "request_changes", "comments": "Patient: John Fakename, DOB: 01/02/1980, revisar."}, "comments"),
])
def test_invalid_human_decisions_never_reach_the_graph(raw, field):
    with pytest.raises(DecisionError) as error:
        validate_section_decision(raw, REQUEST)
    assert field in [e["field"] for e in error.value.errors]


def test_clinical_must_confirm_capacity_to_approve():
    request = {**REQUEST, "asks_capacity": True}
    with pytest.raises(DecisionError):
        validate_section_decision({"decision": "approve"}, request)
    assert validate_section_decision({"decision": "approve", "confirmed_capacity": 800}, request)["confirmed_capacity"] == 800


def test_phi_in_comments_is_refused_without_repeating_it():
    with pytest.raises(DecisionError) as error:
        validate_section_decision({"decision": "reject", "comments": "Patient: John Fakename tiene diabetes."}, REQUEST)
    assert "Fakename" not in str(error.value.errors)


# --------------------------------------------------------------------------
# Documento final
# --------------------------------------------------------------------------


def _approved(drafts=OK_DRAFTS) -> Dict[str, Dict[str, Any]]:
    return {d: {"status": "approved", "draft": text, "decided_at": "2026-10-21T10:00:00+00:00"} for d, text in drafts.items()}


CONTEXT = {"rfp_id": "RFP-X", "client_name": "Meridian", "client_country": "US", "covered_population": 800,
           "covered_population_unit": "employees", "program_type": ["occupational_health"]}


def test_final_document_consolidates_approved_sections_in_brief_format():
    document = build_final_document(CONTEXT, _approved(), generated_at=datetime(2026, 10, 21, tzinfo=timezone.utc))
    assert document["currency"] == "USD" and document["client_country"] == "US"
    assert [s["department_id"] for s in document["sections"]] == ["revenue", "clinical", "compliance"]
    markdown = document["content_markdown"]
    assert "## 1. Términos económicos" in markdown and "## 3. Protección de datos y cumplimiento normativo" in markdown
    assert "HIPAA — cláusula BAA" in markdown and "Claire Whitfield" in markdown
    # El título no se repite dentro del cuerpo.
    assert markdown.count("Términos económicos") == 1


def test_final_document_requires_compliance_and_every_approval():
    without_compliance = {d: o for d, o in _approved().items() if d != "compliance"}
    with pytest.raises(FinalDocumentError):
        build_final_document(CONTEXT, without_compliance, generated_at=datetime.now(timezone.utc))
    pending = _approved()
    pending["revenue"]["status"] = BRANCH_BLOCKED
    with pytest.raises(FinalDocumentError):
        build_final_document(CONTEXT, pending, generated_at=datetime.now(timezone.utc))


def test_final_document_refuses_phi():
    with pytest.raises(FinalDocumentError, match="phi-detected"):
        build_final_document(CONTEXT, _approved({**OK_DRAFTS, "clinical": CLINICAL_WITH_PHI}),
                             generated_at=datetime.now(timezone.utc))


# --------------------------------------------------------------------------
# Trazabilidad
# --------------------------------------------------------------------------


def test_every_node_execution_records_agent_input_output_and_timestamp(session):
    flow = Flow(session, llm=ScriptedLlm(drafts={"clinical": [CLINICAL_REVISED]}))
    flow.start()
    flow.resume({"clinical": {"decision": "request_changes", "comments": "Concretad el turno de arranque."}})
    flow.resume({"revenue": APPROVE, "compliance": APPROVE, "clinical": APPROVE_CLINICAL})

    trace = flow.values()["trace"]  # en el ESTADO del grafo, como pide el README
    assert {i["node"] for i in trace} >= {
        "open_approval", "screen_section", "request_approval", "revise_section", "arbitrate", "synthesize_final_document",
    }
    for item in trace:
        assert item["agent"] and isinstance(item["input"], dict) and isinstance(item["output"], dict)
        datetime.fromisoformat(item["timestamp"])
    # Sin texto libre: ni borradores ni comentarios.
    flat = str(trace)
    assert "Concretad el turno" not in flat and "HealthCore abrirá" not in flat
    revise = next(i for i in trace if i["node"] == "revise_section")
    assert revise["agent"] == "generator:clinical+evaluators" and revise["input"]["feedback_source"] == "human"
    assert len({i["event_id"] for i in trace}) == len(trace)


# --------------------------------------------------------------------------
# Runner: base de datos, concurrencia y ciclo completo
# --------------------------------------------------------------------------


def runner(session: Session, ticket_id: uuid.UUID, saver: InMemorySaver, llm: ScriptedLlm):
    return lambda: advance(session, ticket_id, checkpointer=saver, llm=llm)


def test_runner_puts_the_ticket_in_esperando_aprobacion_with_pending_sections(session):
    ticket_id = seed_ticket(session)
    status = advance(session, ticket_id, checkpointer=InMemorySaver(), llm=ScriptedLlm())

    assert status == STATUS_AWAITING_APPROVAL
    ticket = repository.get_ticket(session, ticket_id)
    assert ticket.approval_thread_id == f"rfp-{ticket_id}"
    # Lo de las Partes 1 y 2 sigue en la misma fila.
    assert ticket.handoff_payload and ticket.response_handoff and ticket.rfp_id
    sections = repository.sections_by_department(session, ticket_id)
    assert {s.approval_status for s in sections.values()} == {APPROVAL_PENDING}
    assert sections["clinical"].approval_request["approver"] == "Dr. Marcus Reid"
    waiting = [e for e in repository.list_trace_events(session, ticket_id) if e.node == "interrupt"]
    assert sorted(e.department_id for e in waiting) == ["clinical", "compliance", "revenue"]


def test_runner_parallel_scenario_reaches_terminado_with_final_document(session):
    ticket_id = seed_ticket(session)
    llm = ScriptedLlm(drafts={"clinical": [CLINICAL_REVISED, CLINICAL_500], "revenue": [REVENUE_500]})
    steps = run_parallel_scenario(session, ticket_id, runner(session, ticket_id, InMemorySaver(), llm))

    assert [s["status"] for s in steps[:-1]] == [STATUS_AWAITING_APPROVAL] * (len(steps) - 1)
    assert steps[1]["sections"] == {"clinical": APPROVAL_PENDING, "compliance": APPROVAL_APPROVED, "revenue": APPROVAL_PENDING}
    assert steps[4]["arbitration_pending"] is True
    assert steps[-1]["status"] == STATUS_FINISHED
    assert set(steps[-1]["sections"].values()) == {APPROVAL_APPROVED}

    document = repository.get_final_document(session, ticket_id)
    assert document is not None and document.currency == "USD"
    assert [a["approver"] for a in document.approvals] == ["Tom Callahan", "Dr. Marcus Reid", "Claire Whitfield"]
    sections = repository.sections_by_department(session, ticket_id)
    assert sections["clinical"].confirmed_capacity == 500 and sections["clinical"].approver == "Dr. Marcus Reid"
    assert sections["clinical"].approval_comments == "Concretad por qué turno empieza el arranque por fases."
    assert [h["decision"] for h in sections["clinical"].approval_history] == ["request_changes", "approve", "approve"]
    nodes = [e.node for e in repository.list_trace_events(session, ticket_id)]
    assert nodes[0] == "open_approval" and nodes[-1] == "synthesize_final_document"


def test_reject_cancels_the_waiting_branches_and_discards_the_proposal(session):
    ticket_id = seed_ticket(session)
    go = runner(session, ticket_id, InMemorySaver(), ScriptedLlm())
    go()
    submit_section_decision(session, ticket_id, "revenue", {"decision": "reject", "comments": "El cliente pide un precio inasumible."},
                            decided_by="sim-tom")
    assert go() == STATUS_DISCARDED

    ticket = repository.get_ticket(session, ticket_id)
    assert ticket.discard_reason == DISCARD_REJECTED
    sections = repository.sections_by_department(session, ticket_id)
    assert sections["revenue"].approval_status == APPROVAL_REJECTED
    assert {sections["clinical"].approval_status, sections["compliance"].approval_status} == {APPROVAL_CANCELLED}
    assert repository.get_final_document(session, ticket_id) is None


def test_arbitration_limit_marks_the_ticket_descartado(session):
    ticket_id = seed_ticket(session)
    go = lambda: advance(session, ticket_id, checkpointer=saver, llm=ScriptedLlm(), max_arbitration_rounds=1)  # noqa: E731
    saver = InMemorySaver()
    go()
    for department_id, raw in (("revenue", APPROVE), ("compliance", APPROVE), ("clinical", {"decision": "approve", "confirmed_capacity": 500})):
        submit_section_decision(session, ticket_id, department_id, raw, decided_by="sim")
    go()
    submit_arbitration_decision(session, ticket_id, {"option": "add_sites", "note": "Sumar la sede de Round Rock."}, decided_by="sim-tom")
    go()
    submit_section_decision(session, ticket_id, "clinical", {"decision": "approve", "confirmed_capacity": 600}, decided_by="sim")
    assert go() == STATUS_DISCARDED
    assert repository.get_ticket(session, ticket_id).discard_reason == DISCARD_ARBITRATION_LIMIT


def test_concurrent_tickets_never_share_a_checkpoint(session):
    saver = InMemorySaver()
    first, second = seed_ticket(session), seed_ticket(session)
    advance(session, first, checkpointer=saver, llm=ScriptedLlm())
    advance(session, second, checkpointer=saver, llm=ScriptedLlm())
    submit_section_decision(session, first, "compliance", APPROVE, decided_by="sim-claire")
    advance(session, first, checkpointer=saver, llm=ScriptedLlm())

    first_sections = repository.sections_by_department(session, first)
    second_sections = repository.sections_by_department(session, second)
    assert first_sections["compliance"].approval_status == APPROVAL_APPROVED
    assert second_sections["compliance"].approval_status == APPROVAL_PENDING
    threads = {checkpoint.config["configurable"]["thread_id"] for checkpoint in saver.list(None)}
    assert threads == {f"rfp-{first}", f"rfp-{second}"}


def test_stale_decision_for_a_changed_draft_is_not_applied(session):
    ticket_id = seed_ticket(session)
    go = runner(session, ticket_id, InMemorySaver(), ScriptedLlm())
    go()
    submit_section_decision(session, ticket_id, "revenue", APPROVE, decided_by="sim", draft_digest="0000000000000000")
    go()
    section = repository.sections_by_department(session, ticket_id)["revenue"]
    assert section.approval_status == APPROVAL_PENDING
    assert "El borrador cambió" in section.approval_request["last_error"]


def test_decisions_are_refused_when_nothing_is_waiting(session):
    ticket_id = seed_ticket(session)
    with pytest.raises(ApprovalStateError):  # la Parte 3 aún no arrancó
        submit_section_decision(session, ticket_id, "revenue", APPROVE, decided_by="sim")
    advance(session, ticket_id, checkpointer=InMemorySaver(), llm=ScriptedLlm())
    with pytest.raises(ApprovalStateError) as error:
        submit_section_decision(session, ticket_id, "marketing", APPROVE, decided_by="sim")
    assert error.value.not_found
    with pytest.raises(ApprovalStateError):
        submit_arbitration_decision(session, ticket_id, {"option": "add_sites", "note": "Sin conflicto"}, decided_by="sim")


def test_failed_revision_is_retried_without_losing_the_decision(session):
    """El proveedor cae durante la revisión: `advance` falla, la decisión ya
    está dentro del checkpoint y el siguiente `advance` reintenta solo esa
    tarea (no vuelve a pedir la decisión ni reinicia el flujo)."""
    ticket_id = seed_ticket(session)
    saver = InMemorySaver()
    llm = ScriptedLlm(drafts={"clinical": [CLINICAL_REVISED]}, fail={"generator:clinical": 1})
    advance(session, ticket_id, checkpointer=saver, llm=llm)
    submit_section_decision(session, ticket_id, "clinical", {"decision": "request_changes", "comments": "Concretad el turno de arranque."},
                            decided_by="sim-marcus")
    with pytest.raises(Exception):
        advance(session, ticket_id, checkpointer=saver, llm=llm)

    assert advance(session, ticket_id, checkpointer=saver, llm=llm) == STATUS_AWAITING_APPROVAL
    section = repository.sections_by_department(session, ticket_id)["clinical"]
    assert section.approval_status == APPROVAL_PENDING
    assert section.approval_request["revision_round"] == 1 and section.draft_content == CLINICAL_REVISED


def test_reprocessed_ticket_starts_a_fresh_thread(session):
    ticket_id = seed_ticket(session)
    saver = InMemorySaver()
    advance(session, ticket_id, checkpointer=saver, llm=ScriptedLlm())
    ticket = repository.get_ticket(session, ticket_id)
    # Parte 2 relanzada a mano: borra el hilo de aprobación del ticket.
    ticket.approval_thread_id = None
    ticket.status = "evaluacion_completa"
    ticket.next_stage = "esperando_aprobación"
    session.add(ticket)
    session.commit()
    assert advance(session, ticket_id, checkpointer=saver, llm=ScriptedLlm()) == STATUS_AWAITING_APPROVAL
    assert len(list(saver.list(thread_config(ticket_id)))) > 0
    assert repository.get_ticket(session, ticket_id).approval_thread_id == f"rfp-{ticket_id}"
