"""Seguimiento de plazos: recordatorios, escalados y vista de bloqueos.

BRIEFING, "Aprobaciones y escalado":
- Recordatorio a la persona incorporada 24 horas antes del plazo de cada etapa.
- Escalado al responsable directo cuando una etapa supera su plazo.
- La etapa 4 NO escala por plazo: se notifica a Diane, pero nunca se ofrece
  una vía para saltarla (el aviso lo dice y el grafo no tiene esa arista).

Plazos: "Día N" del BRIEFING = N-ésimo día hábil (lunes a viernes) contando
la fecha de inicio como día 1 (si empieza en fin de semana, el día 1 es el
lunes siguiente). El plazo vence al terminar ese día en la zona horaria de la
sede (America/Chicago para Austin, Europe/London para Londres). No se
modelan festivos: el BRIEFING no da calendario y no se inventa uno.

`sweep()` lo ejecuta el agente (actor `agent`) cada hora: tarea Celery
`onboarding.followup_sweep` o scripts/onboarding_followup.py. Es idempotente:
cada aviso tiene una clave por etapa, ronda y tipo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlmodel import Session

from services.onboarding import repository
from services.onboarding.catalog import (
    ACTOR_AGENT,
    RECIPIENT_HIRE,
    RECIPIENT_MANAGER,
    RECIPIENT_VP_PEOPLE,
    SITE_TIMEZONES,
    STAGES,
    get_stage,
)
from services.onboarding.models import HIRE_ACTIVE, PHASE_APPROVAL, PHASE_COMPLETED, PHASE_PAPERWORK, PHASE_STARTING, OnboardingHire

REMINDER_WINDOW = timedelta(hours=24)


def nth_business_day(start: date, n: int) -> date:
    if n < 1:
        raise ValueError("Los días del BRIEFING empiezan en 1")
    day = start
    while day.weekday() >= 5:  # empieza en fin de semana → lunes
        day += timedelta(days=1)
    counted = 1
    while counted < n:
        day += timedelta(days=1)
        if day.weekday() < 5:
            counted += 1
    return day


def stage_due_at(start: date, deadline_day: int, site: str) -> datetime:
    """Instante (UTC) en que vence el "Día N" de una etapa."""
    tz = ZoneInfo(SITE_TIMEZONES[site])
    last_day = nth_business_day(start, deadline_day)
    local_end = datetime.combine(last_day + timedelta(days=1), time(0, 0), tzinfo=tz)
    return local_end.astimezone(timezone.utc)


def deadlines_for(hire: OnboardingHire) -> Dict[int, datetime]:
    return {stage.number: stage_due_at(hire.start_date, stage.deadline_day, hire.site) for stage in STAGES}


def is_open(hire: OnboardingHire) -> bool:
    return hire.status == HIRE_ACTIVE and hire.phase not in (PHASE_STARTING, PHASE_COMPLETED)


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _round(hire: OnboardingHire) -> int:
    rejection = hire.rejection or {}
    return int(rejection.get("round", 0)) + 1 if rejection.get("stage") == hire.current_stage else 1


def sweep(session: Session, now: Optional[datetime] = None) -> Dict[str, int]:
    now = _as_utc(now or datetime.now(timezone.utc))
    counts = {"reminders": 0, "escalations": 0, "compliance_delays": 0}
    for hire in repository.active_hires(session):
        if not is_open(hire) or hire.current_stage in (hire.completed_stages or []):
            continue
        stage = get_stage(hire.current_stage)
        due = stage_due_at(hire.start_date, stage.deadline_day, hire.site)
        due_text = due.astimezone(ZoneInfo(SITE_TIMEZONES[hire.site])).strftime("%Y-%m-%d %H:%M")
        base = f"{hire.hire_id}:s{stage.number}:r{_round(hire)}:followup"
        params: Dict[str, Any] = {"stage_number": stage.number, "due": due_text}

        if due - REMINDER_WINDOW <= now < due:
            if repository.record_notification(session, hire, key=f"{base}:reminder", stage=stage.number, kind="reminder", recipient_role=RECIPIENT_HIRE, params=params):
                repository.record_audit(session, event_key=f"{base}:reminder", hire_id=hire.hire_id, stage=stage.number, action="deadline_reminder_sent", actor_type=ACTOR_AGENT)
                counts["reminders"] += 1
        elif now >= due:
            if stage.escalates_when_overdue:
                kind, recipient, action, counter = "escalation", RECIPIENT_MANAGER, "deadline_escalated", "escalations"
            else:
                kind, recipient, action, counter = "compliance_delay", RECIPIENT_VP_PEOPLE, "compliance_delay_reported", "compliance_delays"
            if repository.record_notification(session, hire, key=f"{base}:overdue", stage=stage.number, kind=kind, recipient_role=recipient, params=params):
                repository.record_audit(session, event_key=f"{base}:overdue", hire_id=hire.hire_id, stage=stage.number, action=action, actor_type=ACTOR_AGENT, outcome=recipient)
                counts[counter] += 1
    session.commit()
    return counts


# --- Vista del equipo de Personas -------------------------------------------


@dataclass(frozen=True)
class StageTimeline:
    number: int
    due_at: datetime
    status: str  # completed | current | upcoming


def blocked_reasons(hire: OnboardingHire, now: Optional[datetime] = None) -> List[str]:
    """Por qué una incorporación no puede avanzar sola ahora mismo (vacío = no
    está bloqueada). Lenguaje de negocio, en español, sin datos personales."""
    if hire.status != HIRE_ACTIVE:
        return []
    now = _as_utc(now or datetime.now(timezone.utc))
    reasons: List[str] = []
    stage = get_stage(hire.current_stage)
    if hire.phase == PHASE_PAPERWORK:
        reasons.append("Esperando que Personas confirme los trámites")
    elif hire.phase == PHASE_APPROVAL:
        who = {"compliance": "Cumplimiento", "line_manager": "su responsable directo", "vp_people": "Diane Foster"}
        reasons.append(f"Esperando la aprobación de {who.get(stage.approver or '', 'una persona')}")
    if is_open(hire) and now >= stage_due_at(hire.start_date, stage.deadline_day, hire.site):
        reasons.append("Etapa vencida" if stage.escalates_when_overdue else "Cumplimiento fuera de plazo (no se salta)")
    failed = int((hire.failed_attempts or {}).get(str(stage.number), 0))
    if failed >= 2:
        reasons.append(f"{failed} intentos fallidos en la comprobación")
    if hire.last_error:
        reasons.append("Error técnico pendiente de reintento")
    return reasons


def timeline(hire: OnboardingHire) -> List[StageTimeline]:
    completed = set(hire.completed_stages or [])
    items = []
    for stage in STAGES:
        status = "completed" if stage.number in completed else ("current" if stage.number == hire.current_stage and hire.status == HIRE_ACTIVE else "upcoming")
        items.append(StageTimeline(stage.number, stage_due_at(hire.start_date, stage.deadline_day, hire.site), status))
    return items
