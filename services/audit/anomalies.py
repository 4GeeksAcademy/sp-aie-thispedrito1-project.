"""Detección de patrones de acceso inusuales (BRIEFING §5).

"Un usuario que accede a un volumen de registros de pacientes muy por encima
de su promedio histórico en un periodo corto": lo primero que Claire pediría
ver ante una sospecha.

Se calcula al consultar, a partir del propio registro de auditoría (las
lecturas `patient_record_viewed` de personas). No escribe nada.

- Ventana reciente: las últimas 24 h.
- Línea base: los 30 días anteriores a esa ventana, expresados como media por
  ventana de 24 h (días sin accesos cuentan como 0: si no, quien casi nunca
  consulta fichas tendría una media artificialmente alta).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlmodel import Session, select

from services.audit import catalog
from services.audit.models import AuditLogEntry
from services.audit.writer import format_timestamp

UNUSUAL_WINDOW = timedelta(hours=24)
BASELINE_DAYS = 30


@dataclass(frozen=True)
class UnusualAccess:
    actor_id: str
    recent_count: int
    baseline_average: float


def view_counts(
    session: Session, *, since: datetime, until: datetime, department_id: Optional[str]
) -> dict[str, int]:
    conditions = [
        AuditLogEntry.action == catalog.PATIENT_RECORD_VIEWED,
        AuditLogEntry.actor_type == catalog.ACTOR_HUMAN,
        AuditLogEntry.occurred_at >= format_timestamp(since),
        AuditLogEntry.occurred_at < format_timestamp(until),
    ]
    if department_id is not None:
        conditions.append(AuditLogEntry.department_id == department_id)
    rows = session.exec(
        select(AuditLogEntry.actor_id, func.count()).where(*conditions).group_by(AuditLogEntry.actor_id)
    ).all()
    return {str(actor_id): int(count) for actor_id, count in rows if actor_id}


# Por debajo de este volumen en 24 h nunca hay alerta: abrir unas pocas fichas
# es trabajo normal, también el primer día de alguien sin historial.
MIN_RECENT_ACCESSES = 10
# Cuántas veces su media diaria tiene que superar alguien para ser inusual.
RATIO_THRESHOLD = 3.0


def is_unusual_access(recent_count: int, baseline_average: float) -> bool:
    """¿Es inusual que alguien haya abierto `recent_count` fichas de pacientes
    en las últimas 24 h, si su media histórica es `baseline_average` por día?

    Learn by Doing delegado al asistente (2026-11-07). Dos condiciones, las dos
    necesarias:

    - Volumen absoluto: al menos `MIN_RECENT_ACCESSES`. Evita alertas por
      "2 fichas frente a una media de 0,3", que son ruido para Compliance.
    - Volumen relativo: al menos `RATIO_THRESHOLD` veces su media. Quien abre
      20 fichas al día por su trabajo no salta por abrir 25.

    Sin historial (media 0) solo cuenta el mínimo absoluto: una persona nueva
    que abre 40 fichas el primer día sí merece una mirada.
    """
    if recent_count < MIN_RECENT_ACCESSES:
        return False
    return recent_count >= RATIO_THRESHOLD * baseline_average


def detect_unusual_access(
    session: Session,
    *,
    now: Optional[datetime] = None,
    window: timedelta = UNUSUAL_WINDOW,
    baseline_days: int = BASELINE_DAYS,
    department_id: Optional[str] = None,
) -> list[UnusualAccess]:
    now = now or datetime.now(timezone.utc)
    window_start = now - window
    baseline_start = window_start - timedelta(days=baseline_days)

    recent = view_counts(session, since=window_start, until=now, department_id=department_id)
    history = view_counts(session, since=baseline_start, until=window_start, department_id=department_id)
    windows_in_baseline = timedelta(days=baseline_days) / window

    flagged = [
        UnusualAccess(actor_id=actor_id, recent_count=count, baseline_average=round(average, 2))
        for actor_id, count in recent.items()
        if is_unusual_access(count, average := history.get(actor_id, 0) / windows_in_baseline)
    ]
    return sorted(flagged, key=lambda item: item.recent_count, reverse=True)
