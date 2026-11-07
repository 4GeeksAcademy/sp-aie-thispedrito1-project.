"""El grafo de etapas: estructura del BRIEFING, comprobaciones y aprobaciones."""

from __future__ import annotations

import pytest

from conftest import answers_for, new_hire
from services.onboarding import checks, repository
from services.onboarding.catalog import STAGES
from services.onboarding.graph import WAIT_APPROVAL, WAIT_CHECK, WAIT_PAPERWORK, build_graph, stage_nodes
from services.onboarding.models import ACCESS_REQUESTED

# --- Estructura -----------------------------------------------------------------


def test_stages_match_the_briefing_table_exactly():
    table = [(s.number, s.title_es, s.deadline_day, s.approver, s.pass_threshold) for s in STAGES]
    assert table == [
        (1, "Bienvenida y trámites", 2, "people_team", None),
        (2, "La empresa", 3, None, 0.8),
        (3, "Cómo operamos", 4, None, 0.8),
        (4, "Cumplimiento", 6, "compliance", 1.0),
        (5, "Tu función", 8, "line_manager", 0.8),
        (6, "Cómo trabajamos", 9, None, 0.8),
        (7, "Accesos y cierre", 10, "vp_people", None),
    ]


def test_compiled_graph_has_explicit_nodes_for_every_stage():
    nodes = stage_nodes()
    assert sorted(nodes) == [1, 2, 3, 4, 5, 6, 7]
    for number in (2, 3, 4, 5, 6):
        steps = {name.split("__")[1] for name in nodes[number]}
        assert {"present", "check", "grade", "reinforce"} <= steps, number
    assert any(n.endswith("__approval") for n in nodes[4])
    assert any(n.endswith("__approval") for n in nodes[5])
    assert not any(n.endswith("__approval") for n in nodes[2] + nodes[3] + nodes[6])


def test_there_is_no_edge_that_skips_the_compliance_stage():
    """Ninguna arista lleva de las etapas 1-3 a las 5-7 sin pasar por la 4."""
    graph = build_graph().compile().get_graph()
    before = {n for n in graph.nodes if n.startswith(("s1_", "s2_", "s3_"))}
    after = {n for n in graph.nodes if n.startswith(("s5_", "s6_", "s7_"))}
    for edge in graph.edges:
        assert not (edge.source in before and edge.target in after), edge


# --- Trámites ---------------------------------------------------------------------


def test_paperwork_waits_until_all_three_marks_are_in(session, driver):
    hire = driver.advance(new_hire(session))
    assert hire.waiting_for["kind"] == WAIT_PAPERWORK
    hire = driver.paperwork(hire, contract_received=True, payroll_docs_received=True)
    assert hire.current_stage == 1 and hire.waiting_for["kind"] == WAIT_PAPERWORK
    hire = driver.paperwork(hire, background_check_completed=True)
    assert hire.completed_stages == [1]
    assert hire.waiting_for == {"kind": WAIT_CHECK, "stage": 2, "attempt": 1, "set_id": "company:en:0"}


def test_paperwork_by_someone_outside_people_does_not_advance(session, driver, audit_rows):
    hire = driver.advance(new_hire(session))
    hire = driver.paperwork(hire, by="claire")
    assert hire.current_stage == 1 and hire.completed_stages == []
    refused = [e for e in audit_rows(hire) if e.action == "paperwork_refused"]
    assert refused and refused[0].outcome == "wrong_role" and refused[0].actor_type == "human"


# --- Comprobaciones ------------------------------------------------------------------


def test_content_stage_never_completes_without_passing_its_check(session, driver):
    hire = driver.to_stage(new_hire(session), 2)
    for _ in range(3):
        hire = driver.check(hire, 2, correct=False)
        assert 2 not in hire.completed_stages
        assert hire.current_stage == 2


def test_80_percent_passes_a_normal_stage(session, driver):
    hire = driver.to_stage(new_hire(session), 2)
    hire = driver.check(hire, 2, wrong=1)  # 4/5
    assert 2 in hire.completed_stages
    assert hire.last_check_result["score"] == 0.8


def test_compliance_needs_100_percent(session, driver):
    hire = driver.to_stage(new_hire(session), 4)
    hire = driver.check(hire, 4, wrong=1)  # 4/5 no basta en cumplimiento
    assert 4 not in hire.completed_stages
    assert hire.waiting_for["kind"] == WAIT_CHECK and hire.waiting_for["attempt"] == 2


def test_failed_check_reinforces_only_failed_sections_and_asks_different_questions(session, driver):
    hire = driver.to_stage(new_hire(session), 3)
    first_set = set(answers_for(hire, 3, 1))
    hire = driver.check(hire, 3, correct=False)
    assert hire.reinforce_sections  # se vuelven a presentar los puntos fallados
    _, questions = checks.question_set(3, __import__("services.onboarding.runner", fromlist=["x"]).profile_of(hire), 1)
    assert set(hire.reinforce_sections) == {q["section"] for q in questions}
    second_set = set(answers_for(hire, 3, 2))
    assert hire.waiting_for["attempt"] == 2
    assert first_set.isdisjoint(second_set)


def test_two_failures_in_compliance_notify_claire_not_the_manager(session, driver):
    hire = driver.to_stage(new_hire(session), 4)
    hire = driver.check(hire, 4, correct=False)
    hire = driver.check(hire, 4, correct=False)
    notes = repository.notifications_for_hire(session, hire.hire_id)
    failed = [n for n in notes if n.kind == "check_failed_twice"]
    assert [n.recipient_user_id for n in failed] == ["claire"]
    assert 4 not in hire.completed_stages  # nunca se salta


def test_two_failures_elsewhere_notify_the_direct_manager(session, driver):
    hire = driver.to_stage(new_hire(session), 3)
    hire = driver.check(hire, 3, correct=False)
    hire = driver.check(hire, 3, correct=False)
    failed = [n for n in repository.notifications_for_hire(session, hire.hire_id) if n.kind == "check_failed_twice"]
    assert [n.recipient_user_id for n in failed] == ["mgr-digital"]


# --- Aprobaciones ----------------------------------------------------------------------


def test_compliance_stage_waits_for_a_human_after_passing(session, driver):
    hire = driver.to_stage(new_hire(session), 4)
    hire = driver.check(hire, 4)
    assert hire.waiting_for == {"kind": WAIT_APPROVAL, "stage": 4, "approver": "compliance"}
    assert 4 not in hire.completed_stages
    assert hire.compliance_approved_at is None


@pytest.mark.parametrize(
    "stage,approver,reason",
    [
        (4, "mgr-digital", "wrong_role"),  # un responsable no aprueba cumplimiento
        (4, "diane", "wrong_role"),  # ni siquiera la VP de Personas
        (5, "mgr-revenue", "not_direct_manager"),  # responsable de otra persona
        (5, "claire", "wrong_role"),
        (7, "people-1", "wrong_role"),  # el cierre es solo de Diane
    ],
)
def test_only_the_briefing_approver_can_unblock(session, driver, audit_rows, stage, approver, reason):
    hire = driver.to_stage(new_hire(session), stage)
    if hire.waiting_for["kind"] == WAIT_CHECK:
        hire = driver.check(hire, stage)
    hire = driver.decide(hire, stage, approver)
    assert hire.waiting_for["kind"] == WAIT_APPROVAL and hire.waiting_for["stage"] == stage
    assert stage not in hire.completed_stages
    refused = [e for e in audit_rows(hire) if e.action == "approval_refused"]
    assert refused[-1].outcome == reason


def test_rejection_restarts_the_stage_with_a_visible_comment(session, driver):
    hire = driver.to_stage(new_hire(session), 4)
    hire = driver.check(hire, 4)
    hire = driver.decide(hire, 4, "claire", "reject", "Please review the incident notification section.")
    assert hire.current_stage == 4 and hire.waiting_for["kind"] == WAIT_CHECK
    assert hire.waiting_for["attempt"] == 1  # inicio de la etapa: comprobación desde cero
    assert hire.rejection == {"stage": 4, "comment": "Please review the incident notification section.", "round": 1}
    rejected = [n for n in repository.notifications_for_hire(session, hire.hire_id) if n.kind == "stage_rejected"]
    assert rejected and "incident notification" in rejected[0].body
    hire = driver.check(hire, 4)
    hire = driver.decide(hire, 4, "claire")
    assert 4 in hire.completed_stages and hire.rejection is None


def test_full_onboarding_reaches_closure_with_department_access(session, driver):
    hire = driver.to_stage(new_hire(session), 7)
    assert hire.waiting_for == {"kind": WAIT_APPROVAL, "stage": 7, "approver": "vp_people"}
    requested = {r.system_key for r in repository.access_requests_for_hire(session, hire.hire_id) if r.status == ACCESS_REQUESTED}
    assert {"github", "data_platform", "patient_comms", "slack"} <= requested
    hire = driver.decide(hire, 7, "diane")
    assert hire.status == "completed" and hire.completed_stages == [1, 2, 3, 4, 5, 6, 7]


# --- Paralelo ----------------------------------------------------------------------------


def test_parallel_onboardings_keep_isolated_state(session, driver):
    austin = driver.advance(new_hire(session))
    london = driver.advance(
        new_hire(session, department="revenue_cycle", role_variant="revenue_cycle_analyst", site="london", framework="uk_gdpr", manager_user_id="mgr-revenue")
    )
    austin = driver.to_stage(austin, 4)
    session.refresh(london)
    assert london.current_stage == 1 and london.waiting_for["kind"] == WAIT_PAPERWORK
    london = driver.to_stage(london, 4)
    assert london.waiting_for["set_id"] == "compliance-uk-gdpr:en:0"
    assert austin.waiting_for["set_id"] == "compliance-hipaa:en:0"
