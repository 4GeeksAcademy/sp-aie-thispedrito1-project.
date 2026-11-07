"""API del orquestador de incorporaciones (/onboarding).

El worker de Celery se sustituye por una ejecución en línea (`router._enqueue`
→ `runner.advance` con un checkpointer SQLite temporal), sobre la misma base
SQLite en memoria de la fixture `client`.
"""

from __future__ import annotations

from typing import Dict

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from auth_repository import AuthRepository
from models import UserRole
from security import create_access_token, hash_password
from services.onboarding import checks, router as onboarding_router, runner
from services.onboarding.models import OnboardingStaff

SSN = "123-45-6789"
# Un solo hash bcrypt para todo el módulo (cada uno cuesta ~0,25 s).
PASSWORD_HASH = hash_password("Secret12345")
# Fecha futura fija: con una fecha pasada, las etapas saldrían vencidas.
START_DATE = "2030-01-07"

STAFF = {
    "diane": ("Diane Foster", "vp_people", "people", "austin"),
    "people": ("Ruth Okafor", "people_team", "people", "austin"),
    "claire": ("Claire Whitfield", "compliance_approver", "compliance", "london"),
    "mgr_digital": ("Marco Ruiz", "line_manager", "digital", "austin"),
    "mgr_revenue": ("Hannah Price", "line_manager", "revenue_cycle", "london"),
}


@pytest.fixture()
def people(client: TestClient, inventory_engine, tmp_path, monkeypatch) -> Dict[str, Dict[str, str]]:
    """Usuarios en la TinyDB de tests + directorio del onboarding + cola en línea."""
    repo = AuthRepository()
    accounts: Dict[str, Dict[str, str]] = {}
    for key in list(STAFF) + ["hire_austin", "hire_blocked", "outsider"]:
        user, _ = repo.create_user(email=f"{key}@healthcore.test", hashed_password=PASSWORD_HASH, role=UserRole.user, profile_data={"name": key})
        accounts[key] = {"id": user["id"], "headers": {"Authorization": f"Bearer {create_access_token(subject=user['id'], role='user')}"}}
    admin, _ = repo.create_user(email="admin2@healthcore.test", hashed_password=PASSWORD_HASH, role=UserRole.admin, profile_data={"name": "admin"})
    accounts["platform_admin"] = {"id": admin["id"], "headers": {"Authorization": f"Bearer {create_access_token(subject=admin['id'], role='admin')}"}}
    with Session(inventory_engine) as session:
        for key, (name, role, department, site) in STAFF.items():
            session.add(OnboardingStaff(user_id=accounts[key]["id"], display_name=name, org_role=role, department=department, site=site))
        session.commit()

    checkpoint = tmp_path / "checkpoints.sqlite"

    def run_inline(hire_id):
        with Session(inventory_engine) as session, runner.open_saver(database_url="", sqlite_path=checkpoint) as saver:
            runner.advance(session, hire_id, saver=saver)

    monkeypatch.setattr(onboarding_router, "_enqueue", run_inline)
    return accounts


def _create(client, people, key="hire_austin", **overrides):
    body = {
        "hire_user_id": people[key]["id"],
        "full_name": "Alex Rivera",
        "job_title": "Software Engineer",
        "department": "digital",
        "role_variant": "digital_engineer",
        "site": "austin",
        "framework": "hipaa",
        "language": "en",
        "manager_user_id": people["mgr_digital"]["id"],
        "start_date": START_DATE,
    }
    body.update(overrides)
    return client.post("/onboarding/hires", json=body, headers=people["people"]["headers"])


def _paperwork(client, people, hire_id):
    marks = {"contract_received": True, "payroll_docs_received": True, "background_check_completed": True}
    return client.post(f"/onboarding/hires/{hire_id}/paperwork", json=marks, headers=people["people"]["headers"])


def _answer(client, people, key="hire_austin", correct=True):
    me = client.get("/onboarding/me", headers=people[key]["headers"]).json()
    check = me["check"]
    stage = me["waiting_for"]["stage"]
    hire = runner.profile_of  # noqa: F841 (solo para dejar claro que las respuestas salen del banco)
    _, questions = checks.question_set(stage, _profile(me), check["attempt"])
    answers = {q["id"]: (q["answer"] if correct else (q["answer"] + 1) % len(q["options"])) for q in questions}
    return client.post("/onboarding/me/check", json={"stage": stage, "attempt": check["attempt"], "answers": answers}, headers=people[key]["headers"])


def _profile(me):
    from data.pipelines.onboarding_docs import HireProfile

    return HireProfile(me["language"], me["framework"], "digital_engineer", "digital")


# --- Alta --------------------------------------------------------------------------------


def test_people_team_creates_an_onboarding_that_starts_in_the_background(client, people):
    response = _create(client, people)
    assert response.status_code == 201, response.text
    hire_id = response.json()["hire_id"]
    detail = client.get(f"/onboarding/hires/{hire_id}", headers=people["people"]["headers"]).json()
    assert detail["phase"] == "awaiting_paperwork" and detail["current_stage"] == 1
    assert detail["can_mark_paperwork"] is True
    assert detail["blocked_reasons"] == ["Esperando que Personas confirme los trámites"]
    assert [s["deadline_day"] for s in detail["stages"]] == [2, 3, 4, 6, 8, 9, 10]


def test_sensitive_fields_are_rejected_without_echoing_their_value(client, people):
    response = _create(client, people, ssn=SSN)
    assert response.status_code == 400
    assert response.json()["detail"]["errors"][0]["field"] == "ssn"
    assert SSN not in response.text


def test_only_people_team_can_create(client, people):
    body = {"hire_user_id": people["hire_austin"]["id"]}
    response = client.post("/onboarding/hires", json=body, headers=people["mgr_digital"]["headers"])
    assert response.status_code == 403


@pytest.mark.parametrize(
    "overrides,field",
    [
        ({"language": "es", "framework": "uk_gdpr", "site": "london"}, "language"),
        ({"role_variant": "nurse"}, "role_variant"),
        ({"role_variant": "revenue_cycle_analyst"}, "role_variant"),
        ({"department": "revenue_cycle", "role_variant": "revenue_cycle_analyst"}, "manager_user_id"),
    ],
)
def test_create_rejects_combinations_without_official_material_or_wrong_manager(client, people, overrides, field):
    response = _create(client, people, **overrides)
    assert response.status_code == 400
    assert response.json()["detail"]["errors"][0]["field"] == field


# --- Trámites -------------------------------------------------------------------------------


def test_paperwork_rejects_background_check_details(client, people):
    hire_id = _create(client, people).json()["hire_id"]
    body = {"contract_received": True, "background_check_result": "clear, no record"}
    response = client.post(f"/onboarding/hires/{hire_id}/paperwork", json=body, headers=people["people"]["headers"])
    assert response.status_code == 400
    assert response.json()["detail"]["errors"][0]["field"] == "background_check_result"
    assert "no record" not in response.text


def test_paperwork_by_compliance_is_forbidden(client, people):
    hire_id = _create(client, people).json()["hire_id"]
    marks = {"contract_received": True, "payroll_docs_received": True, "background_check_completed": True}
    response = client.post(f"/onboarding/hires/{hire_id}/paperwork", json=marks, headers=people["claire"]["headers"])
    assert response.status_code == 403


# --- Vista propia y comprobación ---------------------------------------------------------------


def test_new_hire_sees_content_and_questions_without_answers(client, people):
    hire_id = _create(client, people).json()["hire_id"]
    assert _paperwork(client, people, hire_id).status_code == 202
    me = client.get("/onboarding/me", headers=people["hire_austin"]["headers"]).json()
    assert me["waiting_for"]["kind"] == "check" and me["current_stage"] == 2
    assert me["content"]["doc_id"] == "company" and me["content"]["sections"][0]["citation"].startswith("company §")
    assert "answer" not in me["check"]["questions"][0]
    assert me["remaining_stages"] == [2, 3, 4, 5, 6, 7]


def test_failed_check_shows_reinforcement_and_a_new_attempt(client, people):
    hire_id = _create(client, people).json()["hire_id"]
    _paperwork(client, people, hire_id)
    assert _answer(client, people, correct=False).status_code == 202
    me = client.get("/onboarding/me", headers=people["hire_austin"]["headers"]).json()
    assert me["check"]["attempt"] == 2 and me["content"]["reinforcement"] is True
    assert me["last_check_result"]["passed"] is False


def test_answers_for_a_closed_attempt_are_refused(client, people):
    hire_id = _create(client, people).json()["hire_id"]
    _paperwork(client, people, hire_id)
    response = client.post("/onboarding/me/check", json={"stage": 2, "attempt": 5, "answers": {}}, headers=people["hire_austin"]["headers"])
    assert response.status_code == 409


# --- Aprobaciones y vistas ------------------------------------------------------------------------


def _to_compliance_approval(client, people):
    hire_id = _create(client, people).json()["hire_id"]
    _paperwork(client, people, hire_id)
    for _ in range(3):  # etapas 2, 3 y 4
        assert _answer(client, people).status_code == 202
    return hire_id


def test_only_claire_can_approve_compliance(client, people):
    hire_id = _to_compliance_approval(client, people)
    for key in ("mgr_digital", "diane", "platform_admin", "hire_austin"):
        response = client.post(f"/onboarding/hires/{hire_id}/approvals", json={"stage": 4, "decision": "approve"}, headers=people[key]["headers"])
        assert response.status_code in (403, 404), key
    detail = client.get(f"/onboarding/hires/{hire_id}", headers=people["claire"]["headers"]).json()
    assert detail["can_decide"] is True
    refused = [e for e in detail["audit"] if e["action"] == "approval_refused"]
    assert len(refused) >= 3 and all(e["security_event"] for e in refused)
    response = client.post(f"/onboarding/hires/{hire_id}/approvals", json={"stage": 4, "decision": "approve"}, headers=people["claire"]["headers"])
    assert response.status_code == 202
    detail = client.get(f"/onboarding/hires/{hire_id}", headers=people["claire"]["headers"]).json()
    assert detail["compliance_approved"] is True and detail["current_stage"] == 5
    assert detail["approvals"]["4"] == {"org_role": "compliance_approver", "department": "compliance"}


def test_reject_needs_a_comment_and_comments_cannot_carry_sensitive_data(client, people):
    hire_id = _to_compliance_approval(client, people)
    headers = people["claire"]["headers"]
    assert client.post(f"/onboarding/hires/{hire_id}/approvals", json={"stage": 4, "decision": "reject"}, headers=headers).status_code == 400
    response = client.post(f"/onboarding/hires/{hire_id}/approvals", json={"stage": 4, "decision": "reject", "comment": f"SSN {SSN} wrong"}, headers=headers)
    assert response.status_code == 400 and SSN not in response.text


def test_views_are_scoped_by_role(client, people):
    hire_id = _create(client, people).json()["hire_id"]
    assert client.get("/onboarding/hires", headers=people["outsider"]["headers"]).status_code == 403
    assert client.get(f"/onboarding/hires/{hire_id}", headers=people["outsider"]["headers"]).status_code == 403
    assert len(client.get("/onboarding/hires", headers=people["mgr_digital"]["headers"]).json()) == 1
    assert client.get("/onboarding/hires", headers=people["mgr_revenue"]["headers"]).json() == []
    assert len(client.get("/onboarding/hires", headers=people["claire"]["headers"]).json()) == 1
    detail = client.get(f"/onboarding/hires/{hire_id}", headers=people["mgr_digital"]["headers"]).json()
    assert detail["audit"] is None  # el responsable no ve la auditoría
    assert client.get("/onboarding/whoami", headers=people["hire_austin"]["headers"]).json()["is_hire"] is True


# --- Accesos y dudas ------------------------------------------------------------------------------


def test_access_to_patient_systems_before_stage_4_is_blocked_and_claire_is_told(client, people):
    hire_id = _create(client, people, key="hire_blocked").json()["hire_id"]
    _paperwork(client, people, hire_id)
    response = client.post("/onboarding/me/access-requests", json={"system_key": "ehr_epic"}, headers=people["hire_blocked"]["headers"])
    assert response.status_code == 403 and response.json()["status"] == "blocked"
    inbox = client.get("/onboarding/notifications", headers=people["claire"]["headers"]).json()
    assert any(n["kind"] == "security_event" and "Epic" in n["body"] for n in inbox)
    ok = client.post("/onboarding/me/access-requests", json={"system_key": "slack"}, headers=people["hire_blocked"]["headers"])
    assert ok.status_code == 201 and ok.json()["status"] == "requested"


def test_question_endpoint_redacts_and_answers_from_material(client, people, monkeypatch):
    from data.pipelines import onboarding_docs
    from services.onboarding import qa

    hits = [{"citation": "company §Our history", "text": "Founded in 2011.", "stage": 2, "score": 0.9}]
    monkeypatch.setattr(onboarding_docs, "retrieve", lambda *a, **k: hits)
    monkeypatch.setattr(qa, "_default_llm_call", lambda messages: "HealthCore was founded in 2011 [1].")
    hire_id = _create(client, people).json()["hire_id"]
    _paperwork(client, people, hire_id)
    response = client.post("/onboarding/me/questions", json={"question": f"When was it founded? my SSN is {SSN}"}, headers=people["hire_austin"]["headers"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "answered" and body["citations"] == ["company §Our history"]
    assert SSN not in response.text and "[ID number removed]" in body["question"]
