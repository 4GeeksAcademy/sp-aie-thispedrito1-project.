"""Control de estado de job_runs: crear, transicionar y consultar ejecuciones.

Máquina de estados (Ticket #DEV-53):

    pending -> processing -> completed
                         \\-> failed
    pending ------------------> failed   (fallo antes de empezar el trabajo)

Sin FastAPI: lo usa un proceso independiente (scripts/nightly_export.py).
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from services.job_runner.models import JOB_STATUSES, TERMINAL_STATUSES, JobRun

# Una ejecución sana dura minutos (el subproceso del pipeline tiene un timeout
# de 30 min en el script). Una fila pending/processing más vieja que esto es
# de un proceso que murió sin pasar por su try/except/finally (kill -9,
# apagado de la máquina).
STALE_AFTER = timedelta(hours=3)

CANCELLED_PREFIX = "Cancelada (sin trabajo):"

MIGRATION_PATH =Path(__file__).resolve().parent / "migrations" / "001_create_job_runs.sql"

_ALLOWED_TRANSITIONS = {
    "pending": {"processing", "failed"},
    "processing": {"completed", "failed"},
}

_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


class ProcessingLockBusy(Exception):
    """Otra ejecución del mismo job ya está en `processing`."""


class InvalidTransition(Exception):
    """Transición no permitida por la máquina de estados."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite devuelve datetimes naive aunque la columna sea timezone=True."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def redact(message: str, limit: int = 1000) -> str:
    """Mismo principio que email_service._redact_emails y run_log.redact: se
    conserva el diagnóstico y se enmascara cualquier dirección de correo.
    Recortado para que un stderr enorme no acabe en la tabla."""
    return _EMAIL_PATTERN.sub("<email>", message)[:limit]


def ensure_job_runs_table(engine: Engine) -> None:
    """Crea job_runs si no existe. En Postgres ejecuta la migración SQL
    literal (fuente de verdad en Supabase); en SQLite (tests) usa el modelo."""
    if engine.dialect.name == "postgresql":
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        statements = [
            statement.strip()
            for statement in _strip_sql_comments(sql).split(";")
            if statement.strip()
        ]
        with engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)
    else:
        JobRun.__table__.create(engine, checkfirst=True)


def _strip_sql_comments(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))


# --- Consultas -------------------------------------------------------------


def get_run(session: Session, run_id: uuid.UUID) -> Optional[JobRun]:
    return session.get(JobRun, run_id)


def list_runs(session: Session, job_name: str, target_date: Optional[date] = None) -> list[JobRun]:
    statement = select(JobRun).where(JobRun.job_name == job_name)
    if target_date is not None:
        statement = statement.where(JobRun.target_date == target_date)
    return list(session.exec(statement.order_by(JobRun.created_at)).all())


def has_processing_lock(session: Session, job_name: str) -> bool:
    """¿Hay una ejecución de este job en `processing`? (el lock está tomado)"""
    return (
        session.exec(
            select(JobRun.id).where(JobRun.job_name == job_name, JobRun.status == "processing")
        ).first()
        is not None
    )


def has_completed_for_date(
    session: Session,
    job_name: str,
    target_date: date,
    exclude_run_id: Optional[uuid.UUID] = None,
) -> bool:
    """¿Ya terminó bien este job para ese día? Por (job_name, target_date):
    solo job_name no basta, porque cada noche procesa un día distinto."""
    statement = select(JobRun.id).where(
        JobRun.job_name == job_name,
        JobRun.target_date == target_date,
        JobRun.status == "completed",
    )
    if exclude_run_id is not None:
        statement = statement.where(JobRun.id != exclude_run_id)
    return session.exec(statement).first() is not None


# --- Transiciones ------------------------------------------------------------


def recover_stale_runs(
    session: Session,
    job_name: str,
    *,
    stale_after: timedelta = STALE_AFTER,
    now: Optional[datetime] = None,
) -> int:
    """Marca `failed` las filas pending/processing abandonadas. No es un
    segundo lock: libera el mismo estado `processing` cuando su dueño murió
    sin poder hacerlo. Devuelve cuántas filas rescató."""
    now = now or _now()
    limit = now - stale_after
    recovered = 0
    for run in session.exec(
        select(JobRun).where(JobRun.job_name == job_name, JobRun.status.in_(("pending", "processing")))
    ).all():
        last_signal = _as_utc(run.started_at) or _as_utc(run.created_at)
        if last_signal < limit:
            run.error_message = (
                f"Abandonada: seguía en '{run.status}' tras más de {stale_after} sin terminar "
                "(el proceso murió sin ejecutar su manejo de errores)"
            )
            run.status = "failed"
            run.finished_at = now
            session.add(run)
            recovered += 1
    if recovered:
        session.commit()
    return recovered


def create_run(session: Session, job_name: str, target_date: date) -> JobRun:
    """Registra la ejecución en `pending` ANTES de empezar ningún trabajo."""
    run = JobRun(job_name=job_name, target_date=target_date, status="pending", created_at=_now())
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _transition(session: Session, run_id: uuid.UUID, new_status: str) -> JobRun:
    if new_status not in JOB_STATUSES:
        raise InvalidTransition(f"estado desconocido: {new_status}")
    run = session.get(JobRun, run_id)
    if run is None:
        raise LookupError(f"job run {run_id} not found")
    if new_status not in _ALLOWED_TRANSITIONS.get(run.status, set()):
        raise InvalidTransition(f"{run.status} -> {new_status} no está permitido")
    run.status = new_status
    return run


def mark_processing(session: Session, run_id: uuid.UUID) -> JobRun:
    """pending -> processing: toma el lock. Si otra instancia ya lo tiene, el
    índice único parcial rechaza el UPDATE y se lanza ProcessingLockBusy."""
    run = _transition(session, run_id, "processing")
    run.started_at = _now()
    session.add(run)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ProcessingLockBusy(f"otra ejecución de {run.job_name} ya está en processing") from None
    session.refresh(run)
    return run


def finish_run(
    session: Session,
    run_id: uuid.UUID,
    *,
    status: str,
    error_message: Optional[str] = None,
    **fields: Any,
) -> JobRun:
    """Estado final (completed | failed). Libera el lock si venía de processing."""
    if status not in TERMINAL_STATUSES:
        raise InvalidTransition(f"{status} no es un estado final")
    run = _transition(session, run_id, status)
    run.finished_at = _now()
    run.error_message = redact(error_message) if error_message else None
    for name, value in fields.items():
        setattr(run, name, value)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def cancel_run(session: Session, run_id: uuid.UUID, reason: str) -> None:
    """Cierra una fila que esta instancia creó pero que NO llegó a hacer trabajo,
    porque al intentar tomar el lock otra instancia se le adelantó (o, ya con
    el lock, descubrió que ese día ya estaba completed).

    Llega en `pending` (perdió la carrera por el lock) o en `processing`
    (ganó el lock pero el día ya estaba hecho). Tras esta función no puede
    quedar en ninguno de esos dos estados.

    Se marca `failed` en vez de borrarla: queda rastro auditable del intento.
    El prefijo fijo CANCELLED_PREFIX permite distinguir estas filas de un
    fallo real (`error_message like 'Cancelada%'`) y no disparar alarmas."""
    finish_run(session, run_id, status="failed", error_message=f"{CANCELLED_PREFIX} {reason}")
