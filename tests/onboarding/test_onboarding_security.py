"""Seguridad y trazabilidad: identidad del agente, bloqueo de accesos y datos sensibles."""

from __future__ import annotations

import logging

import pytest

from conftest import new_hire
from services.onboarding import access, approvals, qa, repository, sensitive
from services.onboarding.catalog import AGENT_ACTOR_ID
from services.onboarding.models import ACCESS_BLOCKED, ACCESS_REQUESTED
from services.onboarding.permissions import AGENT_CAPABILITIES, HUMAN_CAPABILITIES, HUMAN_ONLY_CAPABILITIES

# --- Identidad y permisos del agente -------------------------------------------------


def test_agent_has_no_human_only_capability():
    assert AGENT_CAPABILITIES.isdisjoint(HUMAN_ONLY_CAPABILITIES)


@pytest.mark.parametrize("role", sorted(HUMAN_CAPABILITIES))
def test_agent_is_narrower_than_every_human_role(role):
    """Para cada rol humano hay algo que la persona puede hacer y el agente no,
    y nada de lo exclusivo de las personas está en manos del agente."""
    human = HUMAN_CAPABILITIES[role]
    assert human - AGENT_CAPABILITIES, role


def test_agent_identity_cannot_approve_any_stage():
    agent = approvals.Approver(user_id=AGENT_ACTOR_ID, org_role="agent", department="digital")
    ref = approvals.HireRef(hire_user_id="h", department="digital", manager_user_id="mgr-digital")
    for stage in (1, 4, 5, 7):
        assert approvals.authorize(stage, agent, ref) is not None


def test_agent_actions_are_logged_as_automated(session, driver, audit_rows, caplog):
    caplog.set_level(logging.INFO, logger="healthcore.onboarding.audit")
    hire = driver.to_stage(new_hire(session), 3)
    rows = audit_rows(hire)
    by_action = {}
    for row in rows:
        by_action.setdefault(row.action, set()).add((row.actor_type, row.actor_ref if row.actor_type == "agent" else "pseudonym"))
    # Lo que hace el agente: agente. Lo que hace Personas: humano, en seudónimo.
    for action in ("onboarding_started", "content_presented", "check_graded", "stage_completed"):
        assert by_action[action] == {("agent", AGENT_ACTOR_ID)}, action
    assert by_action["paperwork_marked"] == {("human", "pseudonym")}
    human_refs = {row.actor_ref for row in rows if row.actor_type == "human"}
    assert "people-1" not in human_refs  # nunca el id en claro
    lines = [r.getMessage() for r in caplog.records if r.name == "healthcore.onboarding.audit"]
    assert any('"automated": true' in line and '"actor_type": "agent"' in line for line in lines)


# --- Restricción específica: accesos antes de la etapa 4 ---------------------------------


def test_patient_data_access_before_compliance_is_blocked_logged_and_notified(session, driver):
    hire = driver.to_stage(new_hire(session, hire_user_id="hire-blocked"), 2)
    with pytest.raises(access.AccessBlockedError):
        access.issue_access_request(session, hire, "ehr_epic", requested_by_type="human", actor_user_id="hire-blocked")
    rows = repository.access_requests_for_hire(session, hire.hire_id)
    assert [(r.system_key, r.status) for r in rows] == [("ehr_epic", ACCESS_BLOCKED)]
    events = [e for e in repository.list_audit(session, hire.hire_id) if e.security_event]
    assert [(e.action, e.outcome) for e in events] == [("patient_data_access_blocked", "stage_4_not_approved")]
    notes = repository.notifications_for_hire(session, hire.hire_id)
    assert {n.recipient_user_id for n in notes if n.kind == "security_event"} == {"claire"}


def test_non_patient_systems_can_be_requested_any_time(session, driver):
    hire = driver.to_stage(new_hire(session), 2)
    request = access.issue_access_request(session, hire, "slack", requested_by_type="human", actor_user_id=hire.hire_user_id)
    assert request.status == ACCESS_REQUESTED


def test_patient_data_access_is_allowed_once_compliance_is_approved(session, driver):
    hire = driver.to_stage(new_hire(session), 5)  # la 4 ya aprobada por Claire
    assert hire.compliance_approved_at is not None
    request = access.issue_access_request(session, hire, "ehr_epic", requested_by_type="human", actor_user_id=hire.hire_user_id)
    assert request.status == ACCESS_REQUESTED


def test_compliance_pass_without_claire_does_not_unlock_access(session, driver):
    hire = driver.to_stage(new_hire(session), 4)
    hire = driver.check(hire, 4)  # 100 %, pero falta la aprobación
    with pytest.raises(access.AccessBlockedError):
        access.issue_access_request(session, hire, "data_platform", requested_by_type="human")


# --- Datos sensibles -------------------------------------------------------------------

SSN = "123-45-6789"
NINO = "AB123456C"
IBAN = "GB33BUKB20201555555555"
SALARY = "salary is $85,000"
BACKGROUND = "background check came back with a conviction"


@pytest.mark.parametrize(
    "text,category",
    [
        (f"my SSN is {SSN}", "national_id"),
        (f"NI number {NINO}", "national_id"),
        ("passport number 123456789", "national_id"),
        (f"IBAN {IBAN}", "bank_details"),
        ("sort code 20-00-00 account number 12345678", "bank_details"),
        (f"my {SALARY}", "salary"),
        ("£42k per year", "salary"),
        (BACKGROUND, "background_check_detail"),
    ],
)
def test_sensitive_scanner_catches_every_briefing_category(text, category):
    result = sensitive.scan(text)
    assert category in result.categories
    for secret in (SSN, NINO, IBAN, "85,000", "123456789", "conviction"):
        assert secret not in result.redacted


@pytest.mark.parametrize(
    "text",
    [
        "Do we have to pay claims within 30 days?",
        "Breaches must be notified within 60 days of discovery in the US.",
        "My background check is completed.",
        "Can I use Jira for my 90-day goals?",
    ],
)
def test_sensitive_scanner_lets_normal_questions_through(text):
    assert not sensitive.contains_sensitive(text)


def test_rejection_comment_with_sensitive_data_is_refused_without_echo():
    with pytest.raises(approvals.DecisionError) as error:
        approvals.validate_decision({"decision": "reject", "comment": f"Fix your SSN {SSN} in Workday"})
    assert error.value.field == "comment" and SSN not in error.value.message


def test_sensitive_data_never_reaches_logs_db_checkpoints_prompts_or_messages(session, driver, db_path, checkpoint_path, caplog):
    caplog.set_level(logging.DEBUG)
    prompts = []

    def fake_retrieve(text, profile, *, min_score):
        return [{"citation": "company §Our history", "text": "HealthCore was founded in 2011.", "stage": 2, "score": 0.9}]

    def fake_llm(messages):
        prompts.append(messages)
        return "HealthCore was founded in 2011 [1]."

    hire = driver.to_stage(new_hire(session), 4)
    question = f"Founded when? Also my SSN is {SSN}, IBAN {IBAN}, my {SALARY} and my {BACKGROUND}."
    qa.answer_question(session, hire, question, retrieve_fn=fake_retrieve, llm_call=fake_llm)
    with pytest.raises(approvals.DecisionError):
        approvals.validate_decision({"decision": "reject", "comment": f"NI {NINO}"})
    hire = driver.check(hire, 4)
    hire = driver.decide(hire, 4, "claire", "reject", "Review the access logging section, please.")
    session.commit()

    secrets = (SSN, NINO, IBAN, "85,000", "conviction")
    haystacks = {
        "logs": "\n".join(r.getMessage() for r in caplog.records),
        "prompts": repr(prompts),
        "db": db_path.read_bytes().decode("latin-1"),
        "checkpoints": checkpoint_path.read_bytes().decode("latin-1"),
        "messages": "\n".join(n.body + n.subject for n in repository.notifications_for_hire(session, hire.hire_id)),
        "questions": "\n".join(q.question + q.answer for q in repository.questions_for_hire(session, hire.hire_id)),
    }
    for where, text in haystacks.items():
        for secret in secrets:
            assert secret not in text, f"{secret} apareció en {where}"
    assert prompts and "[ID number removed]" in prompts[0][1]["content"]
    assert any(e.action == "sensitive_data_redacted" for e in repository.list_audit(session, hire.hire_id))


# --- Registro de Auditoría: el agente queda como PROCESO -----------------------------

def test_access_requests_reach_the_audit_log_with_the_right_actor(session, driver):
    from sqlmodel import select

    from services.audit import catalog as audit_catalog
    from services.audit.chain import verify_chain
    from services.audit.models import AuditLogEntry

    hire = driver.to_stage(new_hire(session, hire_user_id="hire-audit"), 2)
    with pytest.raises(access.AccessBlockedError):
        access.issue_access_request(session, hire, "ehr_epic", requested_by_type="agent")
    access.issue_access_request(session, hire, "slack", requested_by_type="human", actor_user_id="hire-audit")
    session.commit()

    entries = session.exec(select(AuditLogEntry).order_by(AuditLogEntry.sequence)).all()
    by_action = {entry.action: entry for entry in entries}
    blocked = by_action["access_request_blocked"]
    assert (blocked.actor_type, blocked.actor_id, blocked.outcome) == ("process", "onboarding_agent", "blocked")
    assert blocked.department_id == audit_catalog.department_id("people_workforce")
    issued = by_action["access_request_issued"]
    assert (issued.actor_type, issued.actor_id) == ("human", "hire-audit")
    assert verify_chain(entries).ok
