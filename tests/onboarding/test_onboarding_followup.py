"""Plazos, recordatorios, escalado y la vista de bloqueos."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from conftest import new_hire
from services.onboarding import followup, repository


def test_business_days_count_the_start_date_as_day_one():
    monday = date(2026, 11, 2)
    assert followup.nth_business_day(monday, 1) == monday
    assert followup.nth_business_day(monday, 2) == date(2026, 11, 3)
    assert followup.nth_business_day(monday, 6) == date(2026, 11, 9)  # salta el fin de semana
    assert followup.nth_business_day(monday, 10) == date(2026, 11, 13)
    saturday = date(2026, 11, 7)
    assert followup.nth_business_day(saturday, 1) == date(2026, 11, 9)


def test_deadline_ends_at_local_midnight_of_the_site():
    monday = date(2026, 11, 2)
    # Día 2 = martes 3; vence al empezar el miércoles 4 en Austin (UTC-6 en noviembre).
    assert followup.stage_due_at(monday, 2, "austin") == datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)
    assert followup.stage_due_at(monday, 2, "london") == datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)


def _notes(session, hire, kind):
    return [n for n in repository.notifications_for_hire(session, hire.hire_id) if n.kind == kind]


def test_reminder_24_hours_before_the_deadline_once(session, driver):
    hire = driver.to_stage(new_hire(session, start_date=date(2026, 11, 2)), 2)
    due = followup.stage_due_at(hire.start_date, 3, hire.site)
    assert followup.sweep(session, due - timedelta(hours=25))["reminders"] == 0
    assert followup.sweep(session, due - timedelta(hours=23))["reminders"] == 1
    assert followup.sweep(session, due - timedelta(hours=1))["reminders"] == 0  # idempotente
    reminders = _notes(session, hire, "reminder")
    assert [n.recipient_user_id for n in reminders] == [hire.hire_user_id]


def test_overdue_stage_escalates_to_the_direct_manager(session, driver, audit_rows):
    hire = driver.to_stage(new_hire(session, start_date=date(2026, 11, 2)), 3)
    due = followup.stage_due_at(hire.start_date, 4, hire.site)
    counts = followup.sweep(session, due + timedelta(minutes=1))
    assert counts["escalations"] == 1
    assert [n.recipient_user_id for n in _notes(session, hire, "escalation")] == ["mgr-digital"]
    escalated = [e for e in audit_rows(hire) if e.action == "deadline_escalated"]
    assert escalated and escalated[0].actor_type == "agent"
    assert "Etapa vencida" in followup.blocked_reasons(hire, due + timedelta(minutes=1))


def test_overdue_compliance_notifies_diane_and_never_escalates_or_skips(session, driver):
    hire = driver.to_stage(new_hire(session, start_date=date(2026, 11, 2)), 4)
    due = followup.stage_due_at(hire.start_date, 6, hire.site)
    counts = followup.sweep(session, due + timedelta(hours=2))
    assert counts == {"reminders": 0, "escalations": 0, "compliance_delays": 1}
    assert [n.recipient_user_id for n in _notes(session, hire, "compliance_delay")] == ["diane"]
    assert not _notes(session, hire, "escalation")
    session.refresh(hire)
    assert hire.current_stage == 4 and 4 not in hire.completed_stages


def test_people_view_explains_why_an_onboarding_is_blocked(session, driver):
    hire = driver.to_stage(new_hire(session, start_date=date(2026, 11, 2)), 4)
    hire = driver.check(hire, 4)
    reasons = followup.blocked_reasons(hire, datetime(2026, 11, 2, 12, tzinfo=timezone.utc))
    assert reasons == ["Esperando la aprobación de Cumplimiento"]
    hire = driver.decide(hire, 4, "claire")
    assert followup.blocked_reasons(hire, datetime(2026, 11, 2, 12, tzinfo=timezone.utc)) == []
