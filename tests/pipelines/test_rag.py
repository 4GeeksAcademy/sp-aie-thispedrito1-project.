"""Tests unitarios del pipeline RAG (Hito 7, Fase 5).

    services/api/.venv/bin/python -m pytest tests/pipelines/test_rag.py

Sin red: Qdrant se sustituye por un stub (puntuaciones elegidas a mano) o por
el modo en memoria del propio SDK (`QdrantClient(":memory:")`, sin servidor),
y los modelos por dobles que registran con qué se les llamó. Los documentos
fuente sí son los reales de docs/company-knowledge-base/.
"""

from __future__ import annotations

import hashlib
import math
import re
from types import SimpleNamespace
from typing import List

import pytest
from qdrant_client import QdrantClient

from data.pipelines import rag as pipeline
from data.process import rag as process
from data.process.rag import COLLECTION_NAME, RagConfigError

DIM = 64


def toy_embed(text: str) -> List[float]:
    """Embedding determinista de juguete: bolsa de palabras con hashing."""
    vector = [0.0] * DIM
    for word in re.findall(r"\w+", text.lower()):
        vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class StubQdrant:
    """Devuelve siempre los mismos puntos con las puntuaciones indicadas."""

    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def query_points(self, collection_name, query, limit, with_payload):
        self.calls.append({"collection_name": collection_name, "query": query, "limit": limit})
        points = [
            SimpleNamespace(
                id=f"id-{i}",
                score=score,
                payload={"source_document": "appointment-policy", "section": f"s{i}", "chunk_index": i, "text": f"t{i}"},
            )
            for i, score in enumerate(self.scores)
        ]
        return SimpleNamespace(points=points[:limit])


class FakeLLM:
    """Doble del cliente compatible con OpenAI: registra la llamada de chat."""

    def __init__(self, content="Respuesta redactada por el modelo."):
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._content = content

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))])


@pytest.fixture()
def models_env(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "embedding-model-x")
    monkeypatch.setenv("GENERATION_MODEL", "chat-model-y")
    monkeypatch.delenv("RAG_MIN_SCORE", raising=False)


# --- Chunking ---------------------------------------------------------------


def test_every_source_document_produces_at_least_three_semantic_chunks() -> None:
    chunks = process.load_chunks()
    per_document = {}
    for chunk in chunks:
        per_document.setdefault(chunk.source_document, []).append(chunk)
    assert set(per_document) == {"insurance-coverage", "appointment-policy", "referral-process", "new-patient-checklist"}
    assert all(len(items) >= 3 for items in per_document.values())
    assert len(chunks) == 14


def test_conditions_stay_with_their_rule() -> None:
    chunks = process.load_chunks()
    by_section = {(c.source_document, c.section): c.text for c in chunks}
    cancellation = by_section[("appointment-policy", "Política de cancelación")]
    # La excepción de Medicare/Medicaid viaja con el cargo por no-show al que matiza.
    assert "50 USD (o 40 GBP en Reino Unido)" in cancellation
    assert "Medicare o Medicaid: no se les cobra cargo por no-show" in cancellation
    # La frase de entrada se une a la lista que presenta.
    checklist = by_section[("new-patient-checklist", "Todo paciente nuevo debe completar antes de su primera cita")]
    assert checklist.startswith("Todo paciente nuevo debe completar")
    assert "4. Consentimiento informado" in checklist
    # Las líneas cortadas a mano se reunifican: ninguna viñeta queda partida.
    us = by_section[("insurance-coverage", "Estados Unidos (Texas, Florida, Georgia)")]
    assert "- Medicaid: aceptado solo en clínicas de Texas y Florida, no en Georgia actualmente." in us


def test_chunk_payload_has_the_context_fields() -> None:
    chunk = process.load_chunks()[0]
    assert chunk.payload() == {
        "company": "healthcore",
        "source_document": "insurance-coverage",
        "section": "Estados Unidos (Texas, Florida, Georgia)",
        "language": "es",
        "chunk_index": 0,
        "text": chunk.text,
    }


def test_unlabelled_paragraph_uses_document_title_as_section() -> None:
    chunks = process.chunk_document("# Título\n\nUna regla suelta sin etiqueta.\n\nOtra: con etiqueta en línea.", "x")
    assert [(c.section, c.chunk_index) for c in chunks] == [("Título", 0), ("Otra", 1)]


def test_missing_document_is_an_error(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        process.load_chunks(tmp_path)


# --- setup() ----------------------------------------------------------------


def test_setup_is_idempotent_and_keeps_source_metadata() -> None:
    client = QdrantClient(":memory:")
    first = process.setup(client=client, embed_fn=toy_embed)
    ids_first = sorted(str(p.id) for p in client.scroll(COLLECTION_NAME, limit=100)[0])
    second = process.setup(client=client, embed_fn=toy_embed)
    points = client.scroll(COLLECTION_NAME, limit=100, with_payload=True)[0]

    assert first == second
    assert first["chunks"] == 14 and first["vector_size"] == DIM
    assert client.count(COLLECTION_NAME).count == 14
    assert sorted(str(p.id) for p in points) == ids_first
    assert all({"source_document", "section", "company", "language", "chunk_index", "text"} <= set(p.payload) for p in points)


def test_setup_does_not_wipe_the_collection_when_embedding_fails() -> None:
    client = QdrantClient(":memory:")
    process.setup(client=client, embed_fn=toy_embed)

    def broken_embed(text):
        raise ConnectionError("proveedor de embeddings caído")

    with pytest.raises(ConnectionError):
        process.setup(client=client, embed_fn=broken_embed)
    assert client.count(COLLECTION_NAME).count == 14


# --- embed() ----------------------------------------------------------------


def test_embed_uses_the_dedicated_embedding_model(models_env, monkeypatch) -> None:
    calls = []
    fake = SimpleNamespace(
        embeddings=SimpleNamespace(
            create=lambda **kw: calls.append(kw) or SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2])])
        )
    )
    monkeypatch.setattr(process, "get_llm_client", lambda: fake)
    assert process.embed("  ¿Qué   seguros\naceptan? ") == [0.1, 0.2]
    assert calls == [{"model": "embedding-model-x", "input": "¿Qué seguros aceptan?"}]


def test_embedding_and_generation_models_must_differ(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL", "same-model")
    monkeypatch.setenv("GENERATION_MODEL", "same-model")
    with pytest.raises(RagConfigError):
        process.get_embedding_model()
    with pytest.raises(RagConfigError):
        pipeline.get_generation_model()


# --- retrieve() -------------------------------------------------------------


def test_retrieve_excludes_results_below_min_score_and_returns_fewer_than_k() -> None:
    stub = StubQdrant([0.91, 0.62, 0.49, 0.30])
    results = pipeline.retrieve("¿cargo por cancelar?", k=5, min_score=0.5, client=stub, embed_fn=toy_embed)

    assert [r["score"] for r in results] == [0.91, 0.62]
    assert len(results) < 5
    assert all(isinstance(r, dict) for r in results)
    assert results[0]["text"] == "t0" and results[0]["source_document"] == "appointment-policy"
    assert stub.calls[0]["collection_name"] == COLLECTION_NAME
    assert stub.calls[0]["limit"] == 5
    assert stub.calls[0]["query"] == toy_embed("¿cargo por cancelar?")


def test_retrieve_returns_nothing_when_no_chunk_clears_the_threshold() -> None:
    stub = StubQdrant([0.41, 0.2])
    assert pipeline.retrieve("¿aceptan Kaiser?", k=3, min_score=0.5, client=stub, embed_fn=toy_embed) == []


def test_retrieve_respects_k() -> None:
    stub = StubQdrant([0.9, 0.8, 0.7, 0.6])
    assert len(pipeline.retrieve("x", k=2, min_score=0.0, client=stub, embed_fn=toy_embed)) == 2


def test_retrieve_against_in_memory_qdrant_returns_plain_payloads() -> None:
    client = QdrantClient(":memory:")
    process.setup(client=client, embed_fn=toy_embed)
    results = pipeline.retrieve(
        "Pacientes con Medicare o Medicaid cargo por no-show", k=3, min_score=0.0, client=client, embed_fn=toy_embed
    )
    assert results[0]["section"] == "Política de cancelación"
    assert set(results[0]) == {"company", "source_document", "section", "language", "chunk_index", "text", "score"}


# --- generate_answer() y query() ---------------------------------------------


def test_generate_answer_uses_the_generation_model_with_the_retrieved_context(models_env) -> None:
    llm = FakeLLM()
    context = [{"source_document": "appointment-policy", "section": "Política de cancelación", "text": "Regla X."}]
    answer = pipeline.generate_answer("¿Cobran?", context, client=llm)

    assert answer == "Respuesta redactada por el modelo."
    request = llm.requests[0]
    assert request["model"] == "chat-model-y"
    system, user = request["messages"]
    assert system["role"] == "system" and "EXCLUSIVAMENTE" in system["content"]
    assert "Documento: appointment-policy · Sección: Política de cancelación\nRegla X." in user["content"]
    assert user["content"].endswith("¿Cobran?")


def test_generate_answer_without_context_tells_the_model_there_is_nothing(models_env) -> None:
    llm = FakeLLM("No tengo información suficiente en la base de conocimiento para responder a eso.")
    answer = pipeline.generate_answer("¿Aceptan Kaiser?", [], client=llm)
    user = llm.requests[0]["messages"][1]["content"]
    assert pipeline.NO_CONTEXT_MARKER in user
    # Aun sin contexto, la respuesta la redacta el modelo.
    assert answer == "No tengo información suficiente en la base de conocimiento para responder a eso."


def test_empty_model_output_is_an_error_not_an_empty_answer(models_env) -> None:
    with pytest.raises(RuntimeError):
        pipeline.generate_answer("¿?", [], client=FakeLLM("   "))


def test_query_returns_the_model_output_not_the_raw_chunks(models_env, monkeypatch) -> None:
    chunk = {"source_document": "new-patient-checklist", "section": "Documentos", "text": "- Documento de identidad válido."}
    seen = {}

    def fake_retrieve(question, *, k, min_score):
        seen["retrieve"] = (question, k, min_score)
        return [chunk]

    def fake_generate(question, context):
        seen["generate"] = (question, context)
        return "Trae tu documento de identidad y la tarjeta del seguro."

    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    monkeypatch.setattr(pipeline, "generate_answer", fake_generate)

    answer = pipeline.query("¿Qué debo traer a mi primera cita?")

    assert answer == "Trae tu documento de identidad y la tarjeta del seguro."
    assert answer != chunk["text"]
    assert seen["retrieve"] == ("¿Qué debo traer a mi primera cita?", pipeline.DEFAULT_K, pipeline.DEFAULT_MIN_SCORE)
    assert seen["generate"] == ("¿Qué debo traer a mi primera cita?", [chunk])


def test_query_uses_min_score_from_environment(models_env, monkeypatch) -> None:
    monkeypatch.setenv("RAG_MIN_SCORE", "0.7")
    captured = {}
    monkeypatch.setattr(pipeline, "retrieve", lambda q, *, k, min_score: captured.setdefault("min", min_score) and [])
    monkeypatch.setattr(pipeline, "generate_answer", lambda q, c: "ok")
    pipeline.query("¿x?")
    assert captured["min"] == 0.7


def test_invalid_min_score_is_a_configuration_error(monkeypatch) -> None:
    monkeypatch.setenv("RAG_MIN_SCORE", "alto")
    with pytest.raises(RagConfigError):
        pipeline.get_min_score()


def test_blank_question_is_rejected() -> None:
    with pytest.raises(ValueError):
        pipeline.query("   ")


def test_system_prompt_carries_the_context_business_rules() -> None:
    system = pipeline.build_messages("¿x?", [])[0]["content"]
    assert "mejor vendedor de servicios de la clínica" in system
    assert "equipo de facturación" in system  # seguro no listado → verificar
    assert "Estados Unidos o Reino Unido" in system  # distinguir país
    assert "Medicare y Medicaid" in system and "no-show" in system
    assert "TODO" not in system
