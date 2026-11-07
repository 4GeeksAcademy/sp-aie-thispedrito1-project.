"""Dudas: material oficial con cita o derivación explícita, nunca invención."""

from __future__ import annotations

import hashlib
import math
import re

import pytest

from conftest import new_hire
from data.pipelines import onboarding_docs
from services.onboarding import qa, repository, routing
from services.onboarding.catalog import RECIPIENT_COMPLIANCE, RECIPIENT_VP_PEOPLE
from services.onboarding.runner import profile_of

# --- Buscador real (Qdrant en memoria) con embeddings deterministas ------------------

DIM = 256


def fake_embed(text: str):
    vector = [0.0] * DIM
    for word in re.findall(r"[a-záéíóúñ0-9]+", text.lower()):
        if len(word) < 3:
            continue
        vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


@pytest.fixture(scope="module")
def qdrant():
    from qdrant_client import QdrantClient

    client = QdrantClient(":memory:")
    summary = onboarding_docs.setup(client=client, embed_fn=fake_embed)
    assert summary["per_language"] == {"en": 48, "es": 40}
    return client


def _retrieve(qdrant):
    def run(text, profile, *, min_score):
        return onboarding_docs.retrieve(text, profile, min_score=min_score, client=qdrant, embed_fn=fake_embed)

    return run


def test_uk_hire_is_never_cited_the_hipaa_policy(qdrant):
    london = onboarding_docs.HireProfile("en", "uk_gdpr", "revenue_cycle_analyst", "revenue_cycle")
    hits = onboarding_docs.retrieve("breach report ICO 72 hours incident notification", london, min_score=-1, k=10, client=qdrant, embed_fn=fake_embed)
    docs = {h["doc_id"] for h in hits}
    assert "compliance-uk-gdpr" in docs and "compliance-hipaa" not in docs
    assert "role-digital-engineer" not in docs  # tampoco el material de otro puesto
    assert {h["language"] for h in hits} == {"en"}


def test_spanish_hire_only_gets_spanish_material(qdrant):
    florida = onboarding_docs.HireProfile("es", "hipaa", "digital_engineer", "digital")
    hits = onboarding_docs.retrieve("plazo notificación brecha 60 días", florida, min_score=-1, k=10, client=qdrant, embed_fn=fake_embed)
    assert hits and {h["language"] for h in hits} == {"es"}


# --- Respuesta con cita ---------------------------------------------------------------------


def test_covered_question_is_answered_with_its_citation(session, driver, qdrant):
    hire = driver.to_stage(new_hire(session), 4)
    seen = []

    def llm(messages):
        seen.append(messages)
        return "Report it to Compliance the same day you notice it [1]."

    record = qa.answer_question(
        session, hire, "What do I do if I suspect PHI was sent to the wrong person? incident notification report compliance same day",
        retrieve_fn=_retrieve(qdrant), llm_call=llm, min_score=0.3,
    )
    assert record.status == "answered"
    assert record.citations and record.citations[0].startswith("compliance-hipaa §")
    assert "EXCERPTS" in seen[0][1]["content"] and hire.full_name not in seen[0][1]["content"]
    assert not repository.notifications_for_hire(session, hire.hire_id) or all(n.kind != "question_routed" for n in repository.notifications_for_hire(session, hire.hire_id))


def test_out_of_material_question_is_routed_without_calling_the_model(session, driver, qdrant, monkeypatch):
    monkeypatch.setattr(routing, "route_uncovered_question", lambda *a, **k: RECIPIENT_VP_PEOPLE)
    hire = driver.to_stage(new_hire(session), 2)

    def llm(messages):  # pragma: no cover - no debe llamarse
        raise AssertionError("sin material, el modelo no responde")

    record = qa.answer_question(session, hire, "Can I expense a standing desk for my home office?", retrieve_fn=_retrieve(qdrant), llm_call=llm)
    assert record.status == "routed" and record.reason == "not_in_official_material"
    assert record.routed_to == RECIPIENT_VP_PEOPLE and record.citations == []
    assert "not covered" in record.answer and "Diane Foster" in record.answer
    routed = [n for n in repository.notifications_for_hire(session, hire.hire_id) if n.kind == "question_routed"]
    assert [n.recipient_user_id for n in routed] == ["diane"]
    assert "standing desk" in routed[0].body


def test_model_saying_not_covered_is_routed_not_answered(session, driver, monkeypatch):
    monkeypatch.setattr(routing, "route_uncovered_question", lambda *a, **k: RECIPIENT_COMPLIANCE)
    hire = driver.to_stage(new_hire(session), 4)
    hits = [{"citation": "compliance-hipaa §Access logging", "text": "Every access is logged.", "stage": 4, "score": 0.9}]
    record = qa.answer_question(session, hire, "How long are access logs kept?", retrieve_fn=lambda *a, **k: hits, llm_call=lambda m: "NOT_COVERED")
    assert record.status == "routed" and record.reason == "model_found_material_insufficient"
    assert record.routed_to == RECIPIENT_COMPLIANCE and "Claire Whitfield" in record.answer


def test_search_outage_routes_instead_of_inventing(session, driver, monkeypatch):
    monkeypatch.setattr(routing, "route_uncovered_question", lambda *a, **k: RECIPIENT_VP_PEOPLE)
    hire = driver.to_stage(new_hire(session), 2)

    def broken(*args, **kwargs):
        raise ConnectionError("qdrant down")

    record = qa.answer_question(session, hire, "Where is the office?", retrieve_fn=broken, llm_call=lambda m: "invented")
    assert record.status == "routed" and record.reason == "assistant_unavailable"


def test_spanish_hire_gets_the_routing_message_in_spanish(session, driver, monkeypatch):
    monkeypatch.setattr(routing, "route_uncovered_question", lambda *a, **k: RECIPIENT_VP_PEOPLE)
    hire = driver.to_stage(new_hire(session, language="es"), 2)
    record = qa.answer_question(session, hire, "¿Puedo traer a mi perro a la oficina?", retrieve_fn=lambda *a, **k: [], llm_call=lambda m: "x")
    assert "no cubre esta duda" in record.answer


# --- A quién se deriva (implementación de routing.route_uncovered_question) ----------------


@pytest.mark.parametrize(
    "question,nearest,current",
    [
        ("Can I paste patient data into ChatGPT if I remove the names?", 6, 3),
        ("¿Cuánto tiempo se guardan los registros de accesos a la PHI?", 4, 2),
        ("Do we need a BAA for a free analytics tool?", 6, 6),
    ],
)
def test_uncovered_compliance_questions_go_to_claire(question, nearest, current):
    assert routing.route_uncovered_question(question, nearest_stage=nearest, current_stage=current) == RECIPIENT_COMPLIANCE


@pytest.mark.parametrize(
    "question,nearest,current",
    [
        ("Can I expense a standing desk for my home office?", 6, 2),
        ("¿Hay parking en la oficina de Austin?", 2, 3),
        ("When do I get my company laptop?", 6, 1),
    ],
)
def test_other_uncovered_questions_go_to_diane(question, nearest, current):
    assert routing.route_uncovered_question(question, nearest_stage=nearest, current_stage=current) == RECIPIENT_VP_PEOPLE


# --- Corpus ------------------------------------------------------------------------------------


def test_operations_material_uses_only_generic_patient_examples():
    """BRIEFING: la etapa 3 no puede contener datos de pacientes reales ni realistas."""
    for language in ("en", "es"):
        document = onboarding_docs.load_corpus()[("operations", language)]
        text = " ".join(s.body for s in document.sections)
        assert not __import__("services.onboarding.sensitive", fromlist=["x"]).contains_sensitive(text)
        assert re.search(r"(Patient|paciente) A\b", text)
        assert not re.search(r"\b(19|20)\d{2}-\d{2}-\d{2}\b", text)  # sin fechas de nacimiento ni de cita
        assert "ficticio" in text or "fictional" in text


def test_every_content_stage_has_material_and_a_valid_check_for_every_allowed_profile():
    from services.onboarding.checks import validate_bank

    profiles = [
        onboarding_docs.HireProfile(lang, fw, role, dept)
        for lang, fw in (("en", "hipaa"), ("en", "uk_gdpr"), ("es", "hipaa"))
        for role, dept in (("digital_engineer", "digital"), ("revenue_cycle_analyst", "revenue_cycle"))
    ]
    for profile in profiles:
        assert onboarding_docs.material_available(profile) == [], profile
        for stage in (2, 3, 4, 5, 6):
            assert validate_bank(stage, profile) == [], (profile, stage)


def test_spanish_uk_gdpr_material_does_not_exist_and_is_never_substituted():
    profile = onboarding_docs.HireProfile("es", "uk_gdpr", "digital_engineer", "digital")
    assert onboarding_docs.material_available(profile) == ["compliance-uk-gdpr"]
    with pytest.raises(onboarding_docs.MissingMaterialError):
        onboarding_docs.select_document(4, profile)


def test_content_is_adapted_to_the_department():
    digital = onboarding_docs.HireProfile("en", "hipaa", "digital_engineer", "digital")
    revenue = onboarding_docs.HireProfile("en", "uk_gdpr", "revenue_cycle_analyst", "revenue_cycle")
    digital_titles = [s["title"] for s in onboarding_docs.present(3, digital)["sections"]]
    revenue_titles = [s["title"] for s in onboarding_docs.present(3, revenue)["sections"]]
    assert "In practice for HealthCore Digital" in digital_titles and "In practice for Revenue Cycle" not in digital_titles
    assert "In practice for Revenue Cycle" in revenue_titles and "In practice for HealthCore Digital" not in revenue_titles
    assert onboarding_docs.present(5, revenue)["doc_id"] == "role-revenue-cycle-analyst"
    assert onboarding_docs.present(4, revenue)["doc_id"] == "compliance-uk-gdpr"
    assert profile_of  # el perfil del grafo usa los mismos campos
