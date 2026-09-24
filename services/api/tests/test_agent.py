"""POST /agent/query (Agente de Soporte, Partes 1 y 2).

Recuperación, generación y planificador se sustituyen con monkeypatch
(data/pipelines/rag.py y services/agent/planner.py), así que el grafo real
(el que compila el router al importarse) se ejecuta de verdad sin tocar
Qdrant ni el modelo. La tool de incidencias NO se sustituye: lee de la TinyDB
temporal de los tests con el IncidentRepository real. Fijan el contrato HTTP:
respuesta + trace_id, autenticación y errores limpios."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from data.pipelines import rag
from data.process.rag import RagConfigError
from services.agent import planner
from services.agent.planner import Plan, PlannedCall

CHUNK = {
    "source_document": "appointment-policy",
    "section": "Política de cancelación",
    "chunk_index": 1,
    "text": "Cancelar con más de 24 horas de anticipación: sin cargo.",
    "score": 0.8,
}


@pytest.fixture(autouse=True)
def trace_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(rag, "get_min_score", lambda: 0.38)
    use_plan(monkeypatch, PlannedCall(source="knowledge_base"))
    return tmp_path


def use_plan(monkeypatch, *calls: PlannedCall) -> None:
    monkeypatch.setattr(planner, "plan_sources", lambda question: Plan(calls=list(calls), status="model"))


def test_agent_answers_through_the_graph_and_leaves_a_trace(client: TestClient, auth_headers, monkeypatch, trace_dir) -> None:
    calls = []
    monkeypatch.setattr(rag, "retrieve", lambda q, *, k, min_score: calls.append(("retrieve", q)) or [CHUNK])
    monkeypatch.setattr(rag, "generate_answer", lambda q, c: calls.append(("generate", len(c))) or "Sin cargo.")

    response = client.post("/agent/query", json={"question": " ¿Cobran por cancelar con 30 horas? "}, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "trace_id", "outcome"}
    assert body["answer"] == "Sin cargo."
    assert body["outcome"] == "answered"
    assert calls == [("retrieve", "¿Cobran por cancelar con 30 horas?"), ("generate", 1)]
    # Nada de lo recuperado llega al cliente.
    assert "score" not in response.text and CHUNK["text"] not in response.text

    trace = json.loads((trace_dir / f"{body['trace_id']}.json").read_text(encoding="utf-8"))
    assert trace["node_sequence"] == ["receive_question", "plan_sources", "retrieve", "generate"]
    assert trace["sources_used"] == ["knowledge_base"]
    assert "30 horas" not in json.dumps(trace, ensure_ascii=False)


def test_agent_answers_honestly_without_context(client: TestClient, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(rag, "retrieve", lambda q, *, k, min_score: [])
    monkeypatch.setattr(rag, "generate_answer", lambda q, c: pytest.fail("no debe generar sin contexto"))

    response = client.post("/agent/query", json={"question": "¿Receta de paella?"}, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["outcome"] == "no_information"
    assert "No tengo información suficiente" in response.json()["answer"]


def test_agent_requires_authentication(client: TestClient) -> None:
    assert client.post("/agent/query", json={"question": "hola"}).status_code == 401


def test_agent_rejects_blank_question(client: TestClient, auth_headers) -> None:
    assert client.post("/agent/query", json={"question": "   "}, headers=auth_headers).status_code == 422


def test_agent_failure_is_a_clean_503(client: TestClient, auth_headers, monkeypatch) -> None:
    def down(q, *, k, min_score):
        raise ConnectionError("qdrant at 10.0.0.5:6333 refused for ana@example.com")

    monkeypatch.setattr(rag, "retrieve", down)
    response = client.post("/agent/query", json={"question": "¿Horario?"}, headers=auth_headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "Support agent is temporarily unavailable."}
    assert "10.0.0.5" not in response.text and "Traceback" not in response.text


def test_agent_misconfiguration_is_a_clean_503(client: TestClient, auth_headers, monkeypatch) -> None:
    def missing(q, *, k, min_score):
        raise RagConfigError("Falta la variable de entorno EMBEDDING_MODEL")

    monkeypatch.setattr(rag, "retrieve", missing)
    response = client.post("/agent/query", json={"question": "¿Horario?"}, headers=auth_headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "Support agent is not configured."}


def test_agent_reads_the_live_incident_manager(client: TestClient, auth_headers, monkeypatch, trace_dir) -> None:
    created = client.post(
        "/api/incidents",
        json={
            "title": "Cobro duplicado",
            "description": "Texto libre que nunca debe llegar al modelo",
            "category": "billing_error",
            "status": "open",
            "origin": "customer",
            "branch": "central",
        },
        headers=auth_headers,
    ).json()
    client.patch(f"/api/incidents/{created['id']}/status", json={"status": "in_progress"}, headers=auth_headers)

    seen = {}
    monkeypatch.setattr(rag, "retrieve", lambda *a, **k: pytest.fail("una pregunta de ticket no debe ir al RAG"))
    monkeypatch.setattr(rag, "generate_answer", lambda q, c: seen.setdefault("context", c) and "En curso.")
    use_plan(monkeypatch, PlannedCall(source="incidents", args={"ticket_id": created["id"]}))

    response = client.post("/agent/query", json={"question": f"¿Estado del ticket {created['id']}?"}, headers=auth_headers)

    assert response.status_code == 200
    evidence = seen["context"][0]["text"]
    # El estado actual (tras el PATCH), no el de creación: dato en vivo.
    assert "estado in_progress (en curso)" in evidence
    assert "Texto libre" not in evidence and "Cobro duplicado" not in evidence
    trace = json.loads((trace_dir / f"{response.json()['trace_id']}.json").read_text(encoding="utf-8"))
    assert trace["sources_used"] == ["incidents"]


def test_agent_unknown_ticket_is_an_honest_answer_not_an_error(client: TestClient, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(rag, "generate_answer", lambda q, c: pytest.fail("no debe generar sin el dato"))
    use_plan(monkeypatch, PlannedCall(source="incidents", args={"ticket_id": 99999}))

    response = client.post("/agent/query", json={"question": "¿Estado del ticket 99999?"}, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["outcome"] == "tool_fallback"
    assert "No encuentro la incidencia #99999" in response.json()["answer"]
