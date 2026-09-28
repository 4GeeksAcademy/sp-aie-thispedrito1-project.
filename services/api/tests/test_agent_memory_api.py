"""Memoria del agente por HTTP (Ticket #MEM-092): ciclos completos.

El grafo real se ejecuta, con el planificador, la generación estructurada y el
clasificador de decisiones sustituidos por dobles deterministas; la memoria y
su auditoría viven en la SQLite en memoria de la fixture `client` (la misma
sustitución de Supabase que usan inventario y telemetría).

Cubre los dos ciclos que pide la evidencia del ticket (aprobado y reflejado en
una consulta posterior; rechazado y memoria sin cambios), el rechazo explícito
de PHI del CONTEXT de HealthCore, el descarte por ambigüedad y por cambio de
tema, la respuesta + pregunta en el mismo mensaje, y la gobernanza (auditoría
y retirada solo para admin)."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from data.pipelines import rag
from database import get_inventory_db_optional
from main import app
from services.agent import planner
from services.agent.memory import reply
from services.agent.memory.decision import DecisionClassification
from services.agent.memory.models import AgentMemory, AgentMemoryProposal
from services.agent.planner import Plan, PlannedCall
from services.agent.router import get_decision_classifier

MANCHESTER = (
    "En la clínica de Manchester el proceso de referidos internos ahora pasa primero por el "
    "coordinador antes que por el especialista."
)
PROPOSAL = {"kind": "clinic_operations", "clinic": "Manchester", "content": MANCHESTER, "reason": "Cambio de protocolo"}


class FakeModel:
    """Planificador + generación estructurada + clasificador, programables."""

    def __init__(self) -> None:
        self.replies: List[reply.AgentReply] = []
        self.decisions: List[DecisionClassification] = []
        self.generated_with: List[List[Dict[str, Any]]] = []
        self.classified: List[str] = []

    def generate(self, question: str, evidence: List[Dict[str, Any]]) -> reply.AgentReply:
        self.generated_with.append(list(evidence))
        return self.replies.pop(0)

    def classify(self, proposal_content: str, message: str) -> DecisionClassification:
        self.classified.append(message)
        return self.decisions.pop(0)


@pytest.fixture()
def model(client: TestClient, monkeypatch, tmp_path) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setenv("AGENT_TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(rag, "get_min_score", lambda: 0.38)
    monkeypatch.setattr(rag, "retrieve", lambda q, *, k, min_score: [])
    monkeypatch.setattr(planner, "plan_sources", lambda q, memory_topics=None: Plan(
        calls=[PlannedCall(source="agent_memory")], status="model"))
    monkeypatch.setattr(reply, "generate_reply", fake.generate)
    app.dependency_overrides[get_decision_classifier] = lambda: fake.classify
    return fake


def ask(client: TestClient, headers, question: str) -> Dict[str, Any]:
    response = client.post("/agent/query", json={"question": question}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def rows(engine, table):
    with Session(engine) as session:
        return list(session.exec(select(table)).all())


def decision(label: str, confidence: float = 0.95, **extra) -> DecisionClassification:
    return DecisionClassification(label=label, confidence=confidence, **extra)


def test_approved_cycle_is_saved_audited_and_used_in_a_later_conversation(
    client, auth_headers, admin_headers, model, inventory_engine
):
    # 1) El usuario informa de un cambio: el agente responde y PROPONE, sin escribir.
    model.replies.append(reply.AgentReply(answer="Gracias, tomo nota del cambio.", memory_proposal=reply.MemoryProposalDraft(**PROPOSAL)))
    first = ask(client, auth_headers, MANCHESTER)
    assert first["memory"]["offered"]["status"] == "proposed"
    assert "¿Quieres que recuerde esto para la próxima vez?" in first["answer"]
    assert rows(inventory_engine, AgentMemory) == []

    # 2) "Sí": se clasifica contra la propuesta pendiente y se guarda. Sin grafo.
    model.decisions.append(decision("approve"))
    second = ask(client, auth_headers, "Sí, guárdalo")
    assert second["memory"]["resolved"] == {"status": "saved", "proposal_id": first["memory"]["offered"]["proposal_id"], "content": MANCHESTER}
    assert second["trace_id"] is None and second["outcome"] == "memory_decision"
    assert len(model.generated_with) == 1

    # 3) Otra consulta posterior: el recuerdo llega al modelo como nota del staff.
    model.replies.append(reply.AgentReply(answer="Primero pasan por el coordinador."))
    ask(client, auth_headers, "¿Cómo funcionan los referidos internos en Manchester?")
    assert [c["text"] for c in model.generated_with[-1]] == [MANCHESTER]
    assert client.get("/agent/memory", headers=auth_headers).json()[0]["content"] == MANCHESTER

    # 4) Auditoría: qué se propuso, qué decidió, cuándo; sin el texto del mensaje.
    audit = client.get("/agent/memory/audit", headers=admin_headers).json()
    assert len(audit) == 1
    entry = audit[0]
    assert entry["status"] == "approved" and entry["decision_label"] == "approve"
    assert entry["proposed_content"] == MANCHESTER and entry["memory_id"]
    assert entry["created_at"] and entry["resolved_at"]
    assert len(entry["origin_message_sha256"]) == 64 and len(entry["decision_message_sha256"]) == 64
    assert "Sí, guárdalo" not in json.dumps(audit, ensure_ascii=False)


def test_rejected_cycle_leaves_memory_unchanged_but_audited(client, auth_headers, admin_headers, model, inventory_engine):
    model.replies.append(reply.AgentReply(answer="Tomo nota.", memory_proposal=reply.MemoryProposalDraft(**PROPOSAL)))
    ask(client, auth_headers, MANCHESTER)
    model.decisions.append(decision("reject"))

    body = ask(client, auth_headers, "No, mejor no lo recuerdes")

    assert body["memory"]["resolved"]["status"] == "rejected"
    assert body["answer"] == "De acuerdo, no lo guardo."
    assert rows(inventory_engine, AgentMemory) == []
    assert client.get("/agent/memory", headers=auth_headers).json() == []
    [entry] = client.get("/agent/memory/audit", headers=admin_headers).json()
    assert entry["status"] == "rejected" and entry["memory_id"] is None and entry["resolved_at"]


def test_patient_data_is_refused_explicitly_and_logged_without_the_text(client, auth_headers, admin_headers, model, inventory_engine):
    """CONTEXT: "El paciente Johnson canceló su cita de mañana, apúntalo." debe
    rechazarse explícitamente, explicando por qué, no ignorarse en silencio."""
    johnson = "El paciente Johnson canceló su cita de mañana, apúntalo."
    model.replies.append(reply.AgentReply(
        answer="Entendido.",
        memory_proposal=reply.MemoryProposalDraft(kind="clinic_operations", content="Johnson canceló su cita de mañana"),
        user_requested_memory=True,
    ))

    body = ask(client, auth_headers, johnson)

    assert body["memory"]["offered"] == {"status": "blocked_phi", "proposal_id": None, "content": None}
    assert "No puedo guardar esto en mi memoria" in body["answer"] and "HIPAA" in body["answer"]
    [entry] = client.get("/agent/memory/audit", headers=admin_headers).json()
    assert entry["status"] == "blocked_phi" and entry["proposed_content"] is None
    assert "patient_name" in entry["phi_categories"]
    assert "Johnson" not in json.dumps(entry)
    # Nada pendiente: el siguiente mensaje no se clasifica contra nada.
    model.replies.append(reply.AgentReply(answer="ok"))
    ask(client, auth_headers, "Gracias")
    assert model.classified == []


def test_request_to_remember_phi_without_a_proposal_is_still_refused(client, auth_headers, model):
    model.replies.append(reply.AgentReply(answer="Lo siento.", user_requested_memory=True))
    body = ask(client, auth_headers, "Recuerda que la Sra. García tiene diabetes")
    assert body["memory"]["offered"]["status"] == "blocked_phi"
    assert "información clínica" in body["answer"]


def test_request_to_remember_something_not_memorable_is_explained(client, auth_headers, model):
    model.replies.append(reply.AgentReply(answer="La tasa está en el dashboard.", user_requested_memory=True))
    body = ask(client, auth_headers, "Apunta la tasa de no-show de esta semana")
    assert body["memory"]["offered"]["status"] == "not_memorable"


@pytest.mark.parametrize(
    "message",
    ["¿Cuál es la tasa de no-show de esta semana?", "Gracias, con eso resuelvo mi reporte."],
)
def test_most_interactions_propose_nothing(client, auth_headers, model, inventory_engine, message):
    model.replies.append(reply.AgentReply(answer="Respuesta."))
    body = ask(client, auth_headers, message)
    assert body["memory"] == {"resolved": None, "offered": None}
    assert "recuerde" not in body["answer"]
    assert rows(inventory_engine, AgentMemoryProposal) == []


def test_ambiguous_answer_discards_by_default(client, auth_headers, model, inventory_engine):
    model.replies.append(reply.AgentReply(answer="Tomo nota.", memory_proposal=reply.MemoryProposalDraft(**PROPOSAL)))
    ask(client, auth_headers, MANCHESTER)
    model.decisions.append(decision("unclear", confidence=0.4))

    body = ask(client, auth_headers, "mmm, ya veremos")

    assert body["memory"]["resolved"]["status"] == "discarded"
    assert rows(inventory_engine, AgentMemory) == []
    [proposal] = rows(inventory_engine, AgentMemoryProposal)
    assert proposal.status == "discarded" and proposal.decision_confidence == 0.4


def test_changing_topic_discards_and_answers_the_new_question(client, auth_headers, model, inventory_engine):
    model.replies.append(reply.AgentReply(answer="Tomo nota.", memory_proposal=reply.MemoryProposalDraft(**PROPOSAL)))
    ask(client, auth_headers, MANCHESTER)
    model.decisions.append(decision("unrelated"))
    model.replies.append(reply.AgentReply(answer="El stock de guantes es 40."))

    body = ask(client, auth_headers, "¿Cuántos guantes quedan?")

    assert body["memory"]["resolved"]["status"] == "discarded"
    assert body["answer"].endswith("El stock de guantes es 40.")
    assert body["trace_id"]
    assert rows(inventory_engine, AgentMemory) == []


def test_approval_and_a_new_question_in_the_same_message(client, auth_headers, model, inventory_engine):
    model.replies.append(reply.AgentReply(answer="Tomo nota.", memory_proposal=reply.MemoryProposalDraft(**PROPOSAL)))
    ask(client, auth_headers, MANCHESTER)
    model.decisions.append(decision("approve", remaining_message="¿Y en Londres cómo es?"))
    model.replies.append(reply.AgentReply(answer="No tengo ese dato de Londres."))

    body = ask(client, auth_headers, "Sí. ¿Y en Londres cómo es?")

    assert body["memory"]["resolved"]["status"] == "saved"
    assert body["answer"].startswith("Hecho: lo he guardado") and body["answer"].endswith("No tengo ese dato de Londres.")
    # La pregunta nueva ya ve el recuerdo recién guardado.
    assert [c["text"] for c in model.generated_with[-1]] == [MANCHESTER]
    assert len(rows(inventory_engine, AgentMemory)) == 1


def test_user_edit_is_saved_instead_of_the_proposal(client, auth_headers, model, inventory_engine):
    edited = "En Manchester los referidos internos pasan primero por el coordinador de la sede."
    model.replies.append(reply.AgentReply(answer="Tomo nota.", memory_proposal=reply.MemoryProposalDraft(**PROPOSAL)))
    ask(client, auth_headers, MANCHESTER)
    model.decisions.append(decision("edit", edited_content=edited))

    body = ask(client, auth_headers, "Sí, pero di 'coordinador de la sede'")

    assert body["memory"]["resolved"] == {"status": "saved", "proposal_id": body["memory"]["resolved"]["proposal_id"], "content": edited}
    [proposal] = rows(inventory_engine, AgentMemoryProposal)
    assert proposal.status == "approved_edited" and proposal.final_content == edited


def test_an_edit_that_adds_patient_data_is_blocked(client, auth_headers, model, inventory_engine):
    model.replies.append(reply.AgentReply(answer="Tomo nota.", memory_proposal=reply.MemoryProposalDraft(**PROPOSAL)))
    ask(client, auth_headers, MANCHESTER)
    model.decisions.append(decision("edit", edited_content="Manchester: el paciente Smith va primero al coordinador"))

    body = ask(client, auth_headers, "Sí, y añade que el paciente Smith va primero")

    assert body["memory"]["resolved"]["status"] == "blocked_phi"
    assert rows(inventory_engine, AgentMemory) == []
    [proposal] = rows(inventory_engine, AgentMemoryProposal)
    assert proposal.status == "blocked_phi" and proposal.final_content is None


def test_no_second_proposal_while_one_is_pending(client, auth_headers, model, inventory_engine):
    model.replies.append(reply.AgentReply(answer="Tomo nota.", memory_proposal=reply.MemoryProposalDraft(**PROPOSAL)))
    ask(client, auth_headers, MANCHESTER)
    # Cambia de tema y el modelo propone otra cosa: la primera se descarta antes.
    other = dict(PROPOSAL, clinic="Austin", content="En Austin el no-show alto de esa semana fue por un cierre de carretera.")
    model.decisions.append(decision("unrelated"))
    model.replies.append(reply.AgentReply(answer="Entendido.", memory_proposal=reply.MemoryProposalDraft(**other)))

    body = ask(client, auth_headers, other["content"])

    assert body["memory"]["resolved"]["status"] == "discarded"
    assert body["memory"]["offered"]["status"] == "proposed"
    statuses = sorted(p.status for p in rows(inventory_engine, AgentMemoryProposal))
    assert statuses == ["discarded", "pending"]


def test_agent_answers_without_memory_when_supabase_is_unavailable(client, auth_headers, model):
    def no_database():
        yield None

    app.dependency_overrides[get_inventory_db_optional] = no_database
    model.replies.append(reply.AgentReply(answer="Tomo nota.", memory_proposal=reply.MemoryProposalDraft(**PROPOSAL)))

    body = ask(client, auth_headers, MANCHESTER)

    assert body["answer"] == "Tomo nota."  # sin pregunta de memoria: no hay dónde registrarla
    assert body["memory"] == {"resolved": None, "offered": None}


def test_audit_and_revoke_are_admin_only(client, auth_headers, admin_headers, model):
    model.replies.append(reply.AgentReply(answer="Tomo nota.", memory_proposal=reply.MemoryProposalDraft(**PROPOSAL)))
    ask(client, auth_headers, MANCHESTER)
    model.decisions.append(decision("approve"))
    ask(client, auth_headers, "Sí")
    [memory] = client.get("/agent/memory", headers=auth_headers).json()

    assert client.get("/agent/memory/audit", headers=auth_headers).status_code == 403
    assert client.delete(f"/agent/memory/{memory['id']}", headers=auth_headers).status_code == 403
    assert client.delete(f"/agent/memory/{memory['id']}", headers=admin_headers).status_code == 204
    assert client.get("/agent/memory", headers=auth_headers).json() == []
    assert client.delete(f"/agent/memory/{memory['id']}", headers=admin_headers).status_code == 404


def test_patient_data_is_refused_even_when_the_model_proposes_nothing(client, auth_headers, admin_headers, model):
    """Regresión de la prueba real: con el ejemplo del CONTEXT, el modelo no
    propuso ni marcó `user_requested_memory` (y dijo "anoto la cancelación").
    La validación del mensaje no puede depender de ese campo."""
    model.replies.append(reply.AgentReply(answer="Recibido."))

    body = ask(client, auth_headers, "El paciente Johnson canceló su cita de mañana, apúntalo.")

    assert body["memory"]["offered"]["status"] == "blocked_phi"
    assert "no guardo en mi memoria nada de este mensaje" in body["answer"]
    [entry] = client.get("/agent/memory/audit", headers=admin_headers).json()
    assert entry["status"] == "blocked_phi" and entry["proposed_content"] is None
    assert entry["decision_note"].startswith("Mensaje con PHI excluido")
    assert "Johnson" not in json.dumps(entry)
