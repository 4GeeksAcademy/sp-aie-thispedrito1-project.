"""Memoria del agente (Ticket #MEM-092): piezas sin FastAPI ni red.

    services/api/.venv/bin/python -m pytest tests/pipelines/test_agent_memory.py

Validador de PHI con los ejemplos literales del CONTEXT de HealthCore,
lectura de la salida estructurada del modelo, política de decisión,
consolidación (deduplicación, sustitución, tope, caducidad, re-verificación
de PHI), la regla de una sola propuesta pendiente y el paso de la memoria por
el grafo. Los ciclos completos por HTTP están en
services/api/tests/test_agent_memory_api.py.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from services.agent.graph import GENERATE, RECALL_MEMORY, AgentNodes, compile_agent_graph, build_agent_graph
from services.agent.memory import consolidation, phi_guard
from services.agent.memory.decision import DecisionClassification, Resolution, resolve_decision
from services.agent.memory.models import (
    KIND_CLINIC_OPERATIONS,
    KIND_STAFF_PREFERENCE,
    MEMORY_ACTIVE,
    MEMORY_EVICTED,
    MEMORY_EXPIRED,
    MEMORY_QUARANTINED,
    MEMORY_SUPERSEDED,
    PROPOSAL_EXPIRED,
    AgentMemory,
    AgentMemoryProposal,
)
from services.agent.memory.reply import AgentReply, MemoryProposalDraft, build_reply_messages, parse_reply
from services.agent.memory.store import PENDING_TTL, MemoryStore, PendingProposalExists, as_evidence
from services.agent.planner import Plan, PlannedCall, _system_prompt
from services.agent.tracing import run_agent

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)

# --- Ejemplos literales del CONTEXT -------------------------------------------

MEMORABLE = [
    "En la clínica de Manchester el proceso de referidos internos ahora pasa primero por el "
    "coordinador antes que por el especialista — cambió el trimestre pasado.",
    "Esa alerta de no-show elevado en la clínica de Austin fue porque hubo un cierre de carretera "
    "esa semana, no un problema real del programa de recordatorios.",
    "El reporte semanal para Diane Foster debe incluir vacantes por rol, no solo por clínica — eso "
    "lo pidió hace dos semanas.",
    "El sistema de referidos falla los lunes en la mañana por el batch nocturno.",
    "Los pacientes Medicare no pagan el cargo por no-show en Texas.",
]

WITH_PHI = [
    ("El paciente Johnson canceló su cita de mañana, apúntalo.", "patient_name"),
    ("El paciente Smith tuvo un referido fallido.", "patient_name"),
    ("La Sra. García tiene diabetes", "patient_name"),
    ("MRN 448812 tiene la cita duplicada", "medical_record_number"),
    ("Su NHS number es 943 476 5919", "nhs_number"),
    ("SSN 123-45-6789 en la ficha", "ssn"),
    ("Póliza número AB-99812 rechazada", "insurance_number"),
    ("Fecha de nacimiento 12/03/1980", "date_of_birth"),
    ("Tiene 54 años y viene los martes", "date_of_birth"),
    ("Los resultados de laboratorio salieron altos", "clinical_content"),
    ("Le subieron la metformina a 850 mg", "clinical_content"),
    ("Su diagnóstico es hipertensión", "clinical_content"),
    ("Escríbele a juan.perez@gmail.com", "email"),
    ("Llama al +44 7700 900123", "phone"),
    ("Vive en M1 1AE", "uk_postcode"),
]


@pytest.mark.parametrize("text", MEMORABLE)
def test_phi_guard_lets_operational_facts_from_the_context_through(text):
    assert phi_guard.scan(text).categories == ()


@pytest.mark.parametrize("text,category", WITH_PHI)
def test_phi_guard_blocks_patient_identifiers_and_clinical_content(text, category):
    verdict = phi_guard.scan(text)
    assert verdict.contains_phi
    assert category in verdict.categories


def test_phi_refusal_explains_why_under_both_frameworks():
    message = phi_guard.refusal_message(phi_guard.scan("El paciente Johnson canceló su cita"))
    assert "HIPAA" in message and "UK GDPR" in message
    assert "nombre de un paciente" in message
    assert "Johnson" not in message  # la explicación no repite el dato


# --- Salida estructurada del modelo -------------------------------------------


def test_reply_parses_answer_and_proposal_from_one_json_object():
    raw = json.dumps(
        {
            "answer": "Entendido.",
            "memory_proposal": {"kind": "clinic_operations", "clinic": "Manchester", "content": MEMORABLE[0], "reason": "cambio"},
            "user_requested_memory": False,
        }
    )
    reply = parse_reply(raw)
    assert reply.answer == "Entendido."
    assert reply.memory_proposal.kind == KIND_CLINIC_OPERATIONS
    assert reply.memory_proposal.clinic == "Manchester"


def test_reply_that_is_not_json_is_an_answer_without_proposal():
    reply = parse_reply("Texto plano del modelo")
    assert reply == AgentReply(answer="Texto plano del modelo")


def test_reply_with_an_unknown_memory_kind_drops_only_the_proposal():
    raw = json.dumps({"answer": "Vale.", "memory_proposal": {"kind": "patient_note", "content": "algo que recordar"}})
    reply = parse_reply(raw)
    assert reply.answer == "Vale." and reply.memory_proposal is None


def test_reply_prompt_reuses_the_rag_prompt_and_adds_the_memory_criteria():
    from data.pipelines import rag

    system = build_reply_messages("¿Horario?", [])[0]["content"]
    assert system.startswith(rag.ASSISTANT_ROLE)
    assert rag.GROUNDING_RULES in system and rag.BUSINESS_RULES in system
    assert "NO propongas memoria" in system and "memory_proposal" in system


# --- Política de decisión (reglas no negociables del ticket) -----------------


def classification(label, confidence=0.95, edited=None, remaining=None):
    return DecisionClassification(label=label, confidence=confidence, edited_content=edited, remaining_message=remaining)


def test_clear_approval_saves_and_clear_rejection_rejects():
    assert resolve_decision(classification("approve")) is Resolution.SAVE
    assert resolve_decision(classification("reject")) is Resolution.REJECT


def test_edit_with_new_text_saves_the_edited_version():
    assert resolve_decision(classification("edit", edited="Manchester: referidos por el coordinador")) is Resolution.SAVE_EDITED


@pytest.mark.parametrize(
    "case",
    [
        classification("unclear"),
        classification("unrelated"),
        classification("approve", confidence=0.3),
        classification("edit", confidence=0.3, edited="texto editado largo"),
        classification("edit", edited=None),
        classification("edit", edited="   "),
    ],
    ids=["unclear", "topic-change", "low-confidence-approve", "low-confidence-edit", "edit-without-text", "edit-blank"],
)
def test_ambiguity_never_counts_as_approval(case):
    assert resolve_decision(case) not in (Resolution.SAVE, Resolution.SAVE_EDITED)


# --- Almacén y consolidación sobre SQLite ------------------------------------


@pytest.fixture()
def session():
    # Solo las tablas de memoria: si otro test registró reporting.*, SQLite
    # fallaría con "unknown database reporting" (gotcha de la Parte 2 del agente).
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    tables = [AgentMemory.__table__, AgentMemoryProposal.__table__]
    SQLModel.metadata.create_all(engine, tables=tables)
    with Session(engine) as db:
        yield db
    SQLModel.metadata.drop_all(engine, tables=tables)


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def propose(store, user="u1", content=MEMORABLE[0], kind=KIND_CLINIC_OPERATIONS, clinic="Manchester"):
    return store.propose(
        user_id=user, kind=kind, clinic=clinic, content=content, reason="r",
        origin_message_sha256="0" * 64, origin_trace_id="t",
    )


def approve(store, proposal, content=None, user="u1"):
    result = store.commit(proposal, content or proposal.proposed_content, approved_by=user)
    store.resolve(proposal, status="approved", memory_id=result.memory.id, final_content=result.memory.content)
    return result


def test_only_one_pending_proposal_per_user_is_enforced_by_the_database(session):
    store = MemoryStore(session, now_fn=Clock())
    propose(store, user="u1")
    with pytest.raises(PendingProposalExists):
        propose(store, user="u1", content=MEMORABLE[1])
    propose(store, user="u2")  # otro usuario sí puede tener la suya


def test_unanswered_proposal_expires_and_is_never_approved(session):
    clock = Clock()
    store = MemoryStore(session, now_fn=clock)
    proposal = propose(store)
    clock.now = NOW + PENDING_TTL + timedelta(minutes=1)

    assert store.get_pending("u1") is None
    session.refresh(proposal)
    assert proposal.status == PROPOSAL_EXPIRED and proposal.memory_id is None
    assert session.exec(select(AgentMemory)).all() == []


def test_memory_is_only_written_from_a_pending_proposal_of_the_same_user(session):
    store = MemoryStore(session, now_fn=Clock())
    proposal = propose(store, user="u1")
    with pytest.raises(PermissionError):
        store.commit(proposal, proposal.proposed_content, approved_by="u2")
    approve(store, proposal)
    with pytest.raises(PermissionError):  # ya no está pendiente
        store.commit(proposal, proposal.proposed_content, approved_by="u1")


def test_duplicate_fact_refreshes_the_existing_memory_instead_of_growing(session):
    clock = Clock()
    store = MemoryStore(session, now_fn=clock)
    first = approve(store, propose(store))
    clock.now = NOW + timedelta(days=10)
    second = approve(store, propose(store))

    assert second.action == "duplicate" and second.memory.id == first.memory.id
    memories = session.exec(select(AgentMemory)).all()
    assert len(memories) == 1
    assert consolidation.as_utc(memories[0].last_confirmed_at) == NOW + timedelta(days=10)


def test_a_correction_supersedes_the_previous_fact_of_the_same_clinic(session):
    store = MemoryStore(session, now_fn=Clock())
    old = approve(store, propose(store, content="En Manchester los referidos internos pasan primero por el especialista."))
    new = approve(store, propose(store, content="En Manchester los referidos internos pasan primero por el coordinador."))

    assert new.action == "superseded"
    session.refresh(old.memory)
    assert old.memory.status == MEMORY_SUPERSEDED and old.memory.superseded_by == new.memory.id
    assert [m.content for m in store.recall("u1")] == [new.memory.content]


def test_same_fact_about_another_clinic_is_not_a_correction(session):
    store = MemoryStore(session, now_fn=Clock())
    approve(store, propose(store, clinic="Manchester"))
    result = approve(store, propose(store, clinic="Londres", content=MEMORABLE[0].replace("Manchester", "Londres")))
    assert result.action == "created"
    assert len(store.recall("u1")) == 2


def test_consolidation_rechecks_phi_and_writes_nothing(session):
    store = MemoryStore(session, now_fn=Clock())
    proposal = propose(store)
    with pytest.raises(consolidation.PhiInMemoryError):
        store.commit(proposal, "El paciente Johnson prefiere los lunes", approved_by="u1")
    session.rollback()
    assert session.exec(select(AgentMemory)).all() == []


def test_sweep_quarantines_stored_memories_that_now_look_like_phi(session):
    store = MemoryStore(session, now_fn=Clock())
    result = approve(store, propose(store))
    result.memory.content = "Llamar al paciente Johnson los lunes"  # p. ej. una regla del validador mejoró
    session.add(result.memory)
    session.commit()

    assert consolidation.sweep(session, now=NOW)["quarantined"] == 1
    session.commit()  # sweep no confirma: lo hace quien llama, en su transacción
    session.refresh(result.memory)
    assert result.memory.status == MEMORY_QUARANTINED
    assert store.recall("u1") == []


def test_unconfirmed_memories_expire_after_the_ttl(session):
    clock = Clock()
    store = MemoryStore(session, now_fn=clock)
    result = approve(store, propose(store))
    clock.now = NOW + consolidation.MEMORY_TTL + timedelta(days=1)

    assert store.recall("u1") == []
    session.refresh(result.memory)
    assert result.memory.status == MEMORY_EXPIRED


def test_preferences_are_capped_per_user_and_evict_the_least_recently_confirmed(session, monkeypatch):
    monkeypatch.setattr(consolidation, "MAX_PREFERENCES_PER_USER", 2)
    clock = Clock()
    store = MemoryStore(session, now_fn=clock)
    contents = [
        "Diane Foster quiere el reporte semanal con vacantes por rol.",
        "Prefiero las cifras de no-show en porcentaje con un decimal.",
        "Muéstrame siempre primero las sedes del Reino Unido en los resúmenes.",
    ]
    results = []
    for day, content in enumerate(contents):
        clock.now = NOW + timedelta(days=day)
        results.append(approve(store, propose(store, kind=KIND_STAFF_PREFERENCE, clinic=None, content=content)))

    session.refresh(results[0].memory)
    assert results[0].memory.status == MEMORY_EVICTED
    assert {m.content for m in store.recall("u1")} == set(contents[1:])


def test_preferences_are_private_and_operational_facts_are_shared(session):
    store = MemoryStore(session, now_fn=Clock())
    approve(store, propose(store, user="u1"))
    approve(store, propose(store, user="u1", kind=KIND_STAFF_PREFERENCE, clinic=None, content=MEMORABLE[2]))

    assert {m.kind for m in store.recall("u1")} == {KIND_CLINIC_OPERATIONS, KIND_STAFF_PREFERENCE}
    assert {m.kind for m in store.recall("u2")} == {KIND_CLINIC_OPERATIONS}


def test_memory_package_never_touches_the_rag_knowledge_collection():
    """El RAG es de solo lectura: ningún módulo de memoria importa Qdrant ni
    nombra la colección *_knowledge (requisito explícito del README)."""
    for path in (REPO_ROOT / "services" / "agent" / "memory").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(body[0].value)
            for body in [tree.body] + [n.body for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                assert "_knowledge" not in node.value, path.name
            if isinstance(node, ast.Name):
                assert node.id != "COLLECTION_NAME", path.name
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [getattr(node, "module", None) or ""] + [alias.name for alias in node.names]
                assert not any("qdrant" in name for name in names), path.name
                assert not (isinstance(node, ast.ImportFrom) and node.module == "data.process.rag"
                            and any(a.name in {"get_qdrant_client", "setup"} for a in node.names)), path.name


# --- La memoria en el grafo ---------------------------------------------------


class GraphDoubles:
    def __init__(self, plan_sources, reply):
        self.plan_sources = plan_sources
        self.reply = reply
        self.planner_calls = []
        self.generated_with = None

    def planner(self, question, topics=None):
        self.planner_calls.append(topics)
        return Plan(calls=[PlannedCall(source=s) for s in self.plan_sources], status="model")

    def generate(self, question, evidence):
        self.generated_with = evidence
        return self.reply

    def compiled(self):
        nodes = AgentNodes(
            retrieve_fn=lambda q, *, k, min_score: [],
            generate_fn=self.generate,
            min_score_fn=lambda: 0.38,
            k=5,
            planner_fn=self.planner,
        )
        return compile_agent_graph(build_agent_graph(nodes))


def stored_memory(session):
    store = MemoryStore(session, now_fn=Clock())
    approve(store, propose(store))
    return as_evidence(store.recall("u1"))


def test_a_statement_reaches_generate_through_memory_even_with_nothing_stored(tmp_path):
    proposal = MemoryProposalDraft(kind=KIND_CLINIC_OPERATIONS, clinic="Manchester", content=MEMORABLE[0])
    doubles = GraphDoubles(["agent_memory"], AgentReply(answer="Entendido.", memory_proposal=proposal))

    result = run_agent(doubles.compiled(), MEMORABLE[0], trace_dir=tmp_path, memories=[])

    assert result.trace["node_sequence"] == ["receive_question", "plan_sources", RECALL_MEMORY, GENERATE]
    assert result.trace["sources_used"] == ["agent_memory"]
    assert result.memory_proposal["content"] == MEMORABLE[0]
    # El trace no guarda el texto propuesto (aún no ha pasado el validador de PHI).
    assert "Manchester" not in json.dumps(result.trace, ensure_ascii=False)
    assert result.trace["memory"]["proposal"]["kind"] == KIND_CLINIC_OPERATIONS


def test_approved_memories_reach_generation_as_staff_notes(session, tmp_path):
    memories = stored_memory(session)
    doubles = GraphDoubles(["agent_memory"], AgentReply(answer="Pasan por el coordinador."))

    result = run_agent(doubles.compiled(), "¿Cómo van los referidos en Manchester?", trace_dir=tmp_path, memories=memories)

    assert doubles.planner_calls == [["clinic_operations (Manchester)"]]  # índice, no contenido
    assert [c["text"] for c in doubles.generated_with] == [MEMORABLE[0]]
    assert doubles.generated_with[0]["source_document"] == "agent-memory"
    assert result.trace["memory"]["available"] == [m["memory_id"] for m in memories]


def test_memories_are_not_used_when_the_plan_does_not_ask_for_them(session, tmp_path):
    doubles = GraphDoubles(["knowledge_base"], AgentReply(answer="x"))
    result = run_agent(doubles.compiled(), "¿Receta de paella?", trace_dir=tmp_path, memories=stored_memory(session))
    assert result.outcome == "no_information" and doubles.generated_with is None


def test_planner_sees_only_the_memory_index():
    assert "vacía" in _system_prompt(None)
    assert _system_prompt(["clinic_operations (Manchester)"]).endswith("clinic_operations (Manchester).")
