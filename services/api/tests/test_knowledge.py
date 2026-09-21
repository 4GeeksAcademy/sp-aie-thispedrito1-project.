"""POST /knowledge/query (Hito 7, Fase 3).

El pipeline se sustituye con monkeypatch: estos tests nunca hablan con
Qdrant ni con el proveedor de modelos. Fijan el contrato HTTP: solo
`{"answer": ...}`, autenticación obligatoria, validación y 503 limpios."""

from __future__ import annotations

from fastapi.testclient import TestClient

from data.pipelines import rag
from data.process.rag import RagConfigError

RAW_CHUNK_TEXT = "Cancelar con más de 24 horas de anticipación: sin cargo."


def _fake_chunk() -> dict:
    return {
        "company": "healthcore",
        "source_document": "appointment-policy",
        "section": "Política de cancelación",
        "language": "es",
        "chunk_index": 1,
        "text": RAW_CHUNK_TEXT,
        "score": 0.87,
    }


def test_query_returns_only_the_generated_answer(client: TestClient, auth_headers, monkeypatch) -> None:
    seen: dict = {}

    def fake_retrieve(question, *, k, min_score, **_):
        seen["retrieve"] = (question, k, min_score)
        return [_fake_chunk()]

    def fake_generate(question, context, **_):
        seen["generate"] = (question, list(context))
        return "No, con más de 24 horas de antelación la cancelación no tiene cargo."

    monkeypatch.setattr(rag, "retrieve", fake_retrieve)
    monkeypatch.setattr(rag, "generate_answer", fake_generate)

    response = client.post(
        "/knowledge/query",
        json={"question": "  ¿Cobran por cancelar con 30 horas?  "},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"answer": "No, con más de 24 horas de antelación la cancelación no tiene cargo."}
    # El router reutiliza query() de data/pipelines: retrieve → generate_answer.
    assert seen["retrieve"][0] == "¿Cobran por cancelar con 30 horas?"
    assert seen["generate"][1] == [_fake_chunk()]
    # Nada de lo recuperado llega al cliente.
    assert RAW_CHUNK_TEXT not in response.text
    assert "score" not in response.text
    assert "source_document" not in response.text


def test_query_requires_authentication(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(rag, "query", lambda question: "no debería llamarse")
    response = client.post("/knowledge/query", json={"question": "¿Qué seguros aceptan?"})
    assert response.status_code == 401


def test_blank_question_is_rejected_before_calling_the_pipeline(client: TestClient, auth_headers, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(rag, "query", lambda question: calls.append(question) or "x")
    for body in ({"question": "   "}, {"question": ""}, {}, {"question": "a" * 1001}):
        response = client.post("/knowledge/query", json=body, headers=auth_headers)
        assert response.status_code == 422, body
    assert calls == []


def test_missing_configuration_returns_clean_503(client: TestClient, auth_headers, monkeypatch) -> None:
    def fail(question):
        raise RagConfigError("Falta la variable de entorno LLM_API_KEY")

    monkeypatch.setattr(rag, "query", fail)
    response = client.post("/knowledge/query", json={"question": "¿Qué traigo?"}, headers=auth_headers)
    assert response.status_code == 503
    assert response.json() == {"detail": "Knowledge assistant is not configured."}
    assert "LLM_API_KEY" not in response.text


def test_provider_failure_returns_clean_503(client: TestClient, auth_headers, monkeypatch) -> None:
    def fail(question):
        raise ConnectionError("qdrant:6333 refused connection")

    monkeypatch.setattr(rag, "query", fail)
    response = client.post("/knowledge/query", json={"question": "¿Qué traigo?"}, headers=auth_headers)
    assert response.status_code == 503
    assert response.json() == {"detail": "Knowledge assistant is temporarily unavailable."}
    assert "qdrant" not in response.text
