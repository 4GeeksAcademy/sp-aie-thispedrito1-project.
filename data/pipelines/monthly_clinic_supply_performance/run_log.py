"""Log de ejecucion en reporting.pipeline_runs (PIPELINE_DESIGN.md 3.3 y 3.6)."""

from __future__ import annotations

import re
import subprocess
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from data.pipelines import ROOT_DIR
from data.pipelines.monthly_clinic_supply_performance.models import (
    ACTIVE_RUN_STATUSES,
    PIPELINE_NAME,
    PipelineRun,
)
from data.process.supply_performance_transforms import add_months

HEARTBEAT_STALE_MINUTES = 30

_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


class WindowLockedError(Exception):
    """Ya hay una corrida activa (queued/running) para ese mes."""

    def __init__(self, active_run_id: Optional[uuid.UUID]) -> None:
        self.active_run_id = active_run_id
        super().__init__(f"window_locked: active run {active_run_id}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite devuelve datetimes naive aunque la columna sea timezone=True."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def redact(message: str, limit: int = 500) -> str:
    """Mismo principio que email_service._redact_emails: el diagnostico se
    conserva, cualquier direccion de correo se enmascara. Recortado para que
    un traceback enorme no acabe en la tabla."""
    return _EMAIL_PATTERN.sub("<email>", message)[:limit]


def current_code_version() -> Optional[str]:
    """SHA corto de git del codigo que calcula, para reproducir un numero
    publicado. None si no hay git disponible (p. ej. dentro de Docker)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def get_active_run(session: Session, month_start: date) -> Optional[PipelineRun]:
    return session.exec(
        select(PipelineRun).where(
            PipelineRun.pipeline_name == PIPELINE_NAME,
            PipelineRun.window_start == month_start,
            PipelineRun.status.in_(ACTIVE_RUN_STATUSES),
        )
    ).first()


def is_stale(run: PipelineRun, now: Optional[datetime] = None) -> bool:
    """Corrida activa sin heartbeat reciente: murio sin ejecutar su manejo de
    errores (proceso matado, estado Crashed de Prefect)."""
    last_signal = _as_utc(run.heartbeat_at) or _as_utc(run.queued_at)
    return last_signal < (now or _now()) - timedelta(minutes=HEARTBEAT_STALE_MINUTES)


def _active_runs(session: Session, month_start: date) -> list:
    return session.exec(
        select(PipelineRun).where(
            PipelineRun.pipeline_name == PIPELINE_NAME,
            PipelineRun.window_start == month_start,
            PipelineRun.status.in_(ACTIVE_RUN_STATUSES),
        )
    ).all()


def find_blocking_run(session: Session, month_start: date, now: Optional[datetime] = None) -> Optional[PipelineRun]:
    """Comprobacion de SOLO LECTURA, en una consulta: la corrida activa y viva
    que bloquea el mes, si la hay. La usa POST /reporting/pipeline-runs para
    responder 409 sin escribir (Ticket #DEV-55: el 202 debe salir en <200 ms,
    y crear el lock en Supabase costaba ~500 ms). No sustituye al lock: el
    worker lo toma despues con create_queued_run y el indice unico parcial.
    Una corrida caducada no bloquea, igual que en create_queued_run."""
    return next((run for run in _active_runs(session, month_start) if not is_stale(run, now)), None)


def release_stale_runs(session: Session, month_start: date, now: Optional[datetime] = None) -> int:
    """Una corrida activa sin heartbeat reciente murio sin ejecutar su
    manejo de errores (proceso matado, estado Crashed de Prefect). Se marca
    crashed para liberar el lock del mes."""
    now = now or _now()
    released = 0
    for run in _active_runs(session, month_start):
        if is_stale(run, now):
            run.status = "crashed"
            run.finished_at = now
            run.error_type = "stale_heartbeat"
            run.error_message = f"No heartbeat for more than {HEARTBEAT_STALE_MINUTES} minutes"
            session.add(run)
            released += 1
    if released:
        session.commit()
    return released


def create_queued_run(
    session: Session,
    *,
    month_start: date,
    trigger_type: str,
    triggered_by: Optional[str] = None,
    run_id: Optional[uuid.UUID] = None,
) -> PipelineRun:
    """Inserta la fila `queued` que ES el lock del mes: el indice unico
    parcial impide una segunda corrida activa. Si choca, WindowLockedError
    con el run_id de la corrida que ya esta en marcha.

    `run_id`: el que reservo la API al encolar (Ticket #DEV-55), para que el
    id devuelto en el 202 sea el de la fila que crea el worker."""
    release_stale_runs(session, month_start)
    run = PipelineRun(
        run_id=run_id or uuid.uuid4(),
        pipeline_name=PIPELINE_NAME,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        window_start=month_start,
        window_end=add_months(month_start, 1),
        status="queued",
        phase="queued",
        queued_at=_now(),
        quality_checks={},
        code_version=current_code_version(),
    )
    session.add(run)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        active = get_active_run(session, month_start)
        raise WindowLockedError(active.run_id if active else None) from None
    session.refresh(run)
    return run


def mark_running(session: Session, run_id: uuid.UUID, prefect_flow_run_id: Optional[str]) -> PipelineRun:
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise LookupError(f"pipeline run {run_id} not found")
    now = _now()
    run.status = "running"
    run.phase = "started"
    run.started_at = now
    run.heartbeat_at = now
    run.prefect_flow_run_id = prefect_flow_run_id
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def update_run(session: Session, run_id: uuid.UUID, **fields: Any) -> None:
    """Checkpoint: actualiza fase/contadores y renueva el heartbeat."""
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise LookupError(f"pipeline run {run_id} not found")
    for name, value in fields.items():
        setattr(run, name, value)
    run.heartbeat_at = _now()
    session.add(run)
    session.commit()


def finish_run(session: Session, run_id: uuid.UUID, *, status: str, **fields: Any) -> None:
    update_run(session, run_id, status=status, phase="finished", finished_at=_now(), **fields)


def mark_run_failed(session: Session, run_id: uuid.UUID, error: BaseException) -> None:
    """Estado final de una corrida que fallo. error_message redactado: nunca
    un traceback crudo ni datos de usuario."""
    update_run(
        session,
        run_id,
        status="failed",
        finished_at=_now(),
        error_type=type(error).__name__,
        error_message=redact(str(error)),
    )


def get_latest_run(session: Session) -> Optional[PipelineRun]:
    return session.exec(
        select(PipelineRun)
        .where(PipelineRun.pipeline_name == PIPELINE_NAME)
        .order_by(PipelineRun.queued_at.desc())
    ).first()


def get_latest_completed_window(session: Session) -> Optional[date]:
    return session.exec(
        select(PipelineRun.window_start)
        .where(
            PipelineRun.pipeline_name == PIPELINE_NAME,
            PipelineRun.status.in_(("completed", "completed_with_warnings")),
        )
        .order_by(PipelineRun.window_start.desc())
    ).first()


def is_report_stale(latest_completed_window: Optional[date], now: Optional[datetime] = None) -> bool:
    """Heartbeat de negocio (PIPELINE_DESIGN.md 3.4): pasadas las 06:00 UTC
    del dia 1, el mes anterior ya deberia estar calculado."""
    now = now or _now()
    current_month = date(now.year, now.month, 1)
    expected_window = add_months(current_month, -1)
    deadline_passed = now.day > 1 or now.hour >= 6
    if not deadline_passed:
        expected_window = add_months(expected_window, -1)
    return latest_completed_window is None or latest_completed_window < expected_window
