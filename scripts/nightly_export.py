"""Job nocturno de telemetría — Ticket #DEV-53.

Cada noche, sin intervención manual:

1. Exporta las filas de telemetry_events del día objetivo a
   data/raw/telemetry_YYYY-MM-DD.csv, solo si ese archivo no existe. Es un
   backup de auditoría: el pipeline NO lee este CSV, lee de la base de datos.
2. Lanza el pipeline de negocio (data/pipelines/pipeline.py) como subproceso.
   Sin argumentos recalcula el último mes cerrado: el pipeline es mensual y
   rechaza el mes en curso a propósito. Repetido cada noche recoge eventos
   tardíos y, si no hay cambios, deja las filas `unchanged`.
3. Registra la ejecución en job_runs: pending -> processing -> completed | failed.

Uso (desde la raíz del repo, con el venv de la API):

    services/api/.venv/bin/python scripts/nightly_export.py
    TARGET_DATE=2026-08-20 services/api/.venv/bin/python scripts/nightly_export.py

Proceso independiente de la API: no importa FastAPI ni corre en su hilo. Se
dispara desde el contenedor `scheduler` de docker-compose.yml (supercronic,
services/scheduler/crontab).

Códigos de salida: 0 completed / omitido por duplicado / cancelado por lock;
1 fallo del job; 2 TARGET_DATE inválido.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "services" / "api"
for _path in (str(ROOT_DIR), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from sqlalchemy.engine import Engine  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from services.job_runner import (  # noqa: E402
    ProcessingLockBusy,
    cancel_run,
    create_run,
    ensure_job_runs_table,
    finish_run,
    has_completed_for_date,
    has_processing_lock,
    mark_processing,
    recover_stale_runs,
    redact,
)
from telemetry_models import TelemetryEventRecord  # noqa: E402

JOB_NAME = "nightly_export"
RAW_DIR = ROOT_DIR / "data" / "raw"
PIPELINE_COMMAND = (sys.executable, str(ROOT_DIR / "data" / "pipelines" / "pipeline.py"))
PIPELINE_TIMEOUT_SECONDS = 30 * 60
CSV_COLUMNS = ("id", "timestamp", "service", "event_type", "level", "value", "message", "tags")

logger = logging.getLogger(JOB_NAME)


class TargetDateError(ValueError):
    """TARGET_DATE mal formado o que apunta a un día aún no cerrado."""


class PipelineFailedError(RuntimeError):
    """El subproceso del pipeline terminó con código distinto de 0."""

    def __init__(self, returncode: int, detail: str) -> None:
        self.returncode = returncode
        super().__init__(f"el pipeline terminó con código {returncode}: {detail}")


class JobTerminatedError(Exception):
    """El proceso recibió SIGTERM (p. ej. `docker stop`) a mitad de ejecución."""


class JobStateNotRecordedError(RuntimeError):
    """El trabajo terminó, pero no se pudo escribir su estado final en job_runs."""


# --- Logs --------------------------------------------------------------------


def configure_logging() -> None:
    """Cada línea: timestamp UTC, nivel, nombre del job, estado y target_date."""
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s job=%(job)s status=%(status)s target_date=%(target_date)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False


def log(level: int, status: str, target_date: Optional[date], message: str, *args: Any) -> None:
    logger.log(
        level,
        message,
        *args,
        extra={"job": JOB_NAME, "status": status, "target_date": target_date or "-"},
    )


# --- Fecha objetivo ----------------------------------------------------------


def resolve_target_date(env: Mapping[str, str] = os.environ, today: Optional[date] = None) -> date:
    """TARGET_DATE=YYYY-MM-DD o, por defecto, ayer en UTC.

    Rechaza hoy y el futuro: exportar un día sin cerrar y marcarlo completed
    bloquearía (por idempotencia) volver a exportarlo entero después."""
    today = today or datetime.now(timezone.utc).date()
    raw = (env.get("TARGET_DATE") or "").strip()
    if not raw:
        return today - timedelta(days=1)
    try:
        target_date = date.fromisoformat(raw)
    except ValueError:
        raise TargetDateError(f"TARGET_DATE={raw!r} no tiene formato YYYY-MM-DD") from None
    if target_date >= today:
        raise TargetDateError(
            f"TARGET_DATE={raw} no es un día cerrado en UTC (hoy es {today.isoformat()})"
        )
    return target_date


# --- Paso 1: backup CSV ------------------------------------------------------


def csv_path_for(target_date: date, raw_dir: Path = RAW_DIR) -> Path:
    return raw_dir / f"telemetry_{target_date.isoformat()}.csv"


def export_telemetry_csv(session: Session, target_date: date, raw_dir: Path = RAW_DIR) -> Optional[int]:
    """Exporta telemetry_events de ese día (UTC) a CSV. Devuelve las filas
    escritas, o None si el archivo ya existía y no se tocó.

    Escribe en un .tmp y lo renombra al final: si el proceso muere a mitad,
    no queda un CSV incompleto con el nombre definitivo que la siguiente
    ejecución daría por bueno."""
    path = csv_path_for(target_date, raw_dir)
    if path.exists():
        return None

    raw_dir.mkdir(parents=True, exist_ok=True)
    # telemetry_events.timestamp es `timestamp without time zone` en UTC.
    day_start = datetime(target_date.year, target_date.month, target_date.day)
    day_end = day_start + timedelta(days=1)
    statement = (
        select(TelemetryEventRecord)
        .where(TelemetryEventRecord.timestamp >= day_start, TelemetryEventRecord.timestamp < day_end)
        .order_by(TelemetryEventRecord.timestamp, TelemetryEventRecord.id)
    )

    tmp_path = path.with_name(f".{path.name}.tmp")
    rows = 0
    try:
        with tmp_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(CSV_COLUMNS)
            for record in session.exec(statement):
                writer.writerow(
                    (
                        record.id,
                        record.timestamp.isoformat(),
                        record.service,
                        record.event_type,
                        record.level,
                        "" if record.value is None else record.value,
                        record.message or "",
                        json.dumps(record.tags or {}, ensure_ascii=False, sort_keys=True),
                    )
                )
                rows += 1
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return rows


# --- Paso 2: pipeline como subproceso ----------------------------------------


def run_pipeline(command: Sequence[str], timeout: float) -> int:
    """Lanza el pipeline en su propio proceso y espera. Un timeout o una
    señal matan al hijo (subprocess.run lo hace al propagar la excepción)."""
    result = subprocess.run(
        list(command),
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        last_lines = [line for line in (result.stderr or "").splitlines() if line.strip()][-3:]
        detail = " / ".join(last_lines) or "sin salida de error"
        raise PipelineFailedError(result.returncode, detail)
    return result.returncode


# --- Orquestación ------------------------------------------------------------


def run_nightly_export(
    engine: Engine,
    target_date: date,
    *,
    raw_dir: Path = RAW_DIR,
    pipeline_command: Sequence[str] = PIPELINE_COMMAND,
    pipeline_timeout: float = PIPELINE_TIMEOUT_SECONDS,
) -> str:
    """Ejecuta el job una vez. Devuelve "completed", "skipped" (ese día ya
    estaba completed) o "cancelled" (otra instancia tiene el lock). Si el
    trabajo falla, deja la fila en failed y relanza la excepción."""
    ensure_job_runs_table(engine)

    with Session(engine) as session:
        recovered = recover_stale_runs(session, JOB_NAME)
        if recovered:
            log(logging.WARNING, "failed", target_date,
                "%d ejecución(es) abandonada(s) en pending/processing marcadas como failed", recovered)

        if has_processing_lock(session, JOB_NAME):
            log(logging.INFO, "cancelled", target_date, "otra instancia está en processing; se aborta sin hacer nada")
            return "cancelled"
        if has_completed_for_date(session, JOB_NAME, target_date):
            log(logging.INFO, "skipped", target_date, "ese día ya está completed; omitido por duplicado")
            return "skipped"

        run = create_run(session, JOB_NAME, target_date)
        run_id = run.id
        log(logging.INFO, "pending", target_date, "ejecución registrada (run_id=%s)", run_id)

        try:
            mark_processing(session, run_id)
        except ProcessingLockBusy:
            # Carrera: otra instancia pasó las mismas comprobaciones a la vez
            # y tomó el lock primero. El índice único parcial decidió.
            cancel_run(session, run_id, "otra instancia tomó el lock primero")
            log(logging.INFO, "cancelled", target_date, "otra instancia tomó el lock primero; se aborta")
            return "cancelled"
        except BaseException as error:  # noqa: BLE001 — p. ej. se cae la conexión
            _finish_safely(engine, run_id, "failed", f"{type(error).__name__}: {error}", {}, target_date)
            raise

        # Doble comprobación ya con el lock: entre la consulta de arriba y
        # tomar el lock, otra instancia pudo terminar ese mismo día.
        if has_completed_for_date(session, JOB_NAME, target_date, exclude_run_id=run_id):
            cancel_run(session, run_id, "ese día se completó mientras se tomaba el lock")
            log(logging.INFO, "skipped", target_date, "ese día se completó mientras se tomaba el lock; omitido")
            return "skipped"

    log(logging.INFO, "processing", target_date, "lock tomado; empieza el trabajo (run_id=%s)", run_id)

    # Estado final por defecto: failed. Solo la última línea del try lo cambia
    # a completed, así que CUALQUIER salida anticipada (excepción, Ctrl+C,
    # SIGTERM) termina en failed y nunca deja la fila en processing.
    final_status = "failed"
    error_message: Optional[str] = "interrumpida antes de terminar"
    details: dict[str, Any] = {}
    recorded = False
    try:
        with Session(engine) as session:
            rows = export_telemetry_csv(session, target_date, raw_dir)
        details["rows_exported"] = rows
        csv_name = csv_path_for(target_date, raw_dir).name
        if rows is None:
            log(logging.INFO, "processing", target_date, "%s ya existía; no se reescribe", csv_name)
        else:
            log(logging.INFO, "processing", target_date, "%s exportado con %d fila(s)", csv_name, rows)

        log(logging.INFO, "processing", target_date, "lanzando el pipeline como subproceso")
        try:
            details["pipeline_exit_code"] = run_pipeline(pipeline_command, pipeline_timeout)
        except PipelineFailedError as error:
            details["pipeline_exit_code"] = error.returncode
            raise
        log(logging.INFO, "processing", target_date, "pipeline terminado con código 0")

        final_status, error_message = "completed", None
    except BaseException as error:  # noqa: BLE001 — también SystemExit/KeyboardInterrupt
        error_message = f"{type(error).__name__}: {error}"
        log(logging.ERROR, "failed", target_date, "%s", redact(error_message))
        raise
    finally:
        recorded = _finish_safely(engine, run_id, final_status, error_message, details, target_date)

    if not recorded:
        raise JobStateNotRecordedError(f"run_id={run_id} terminó {final_status} pero job_runs no se actualizó")
    log(logging.INFO, "completed", target_date, "ejecución completada (run_id=%s)", run_id)
    return "completed"


def _finish_safely(
    engine: Engine,
    run_id: Any,
    status: str,
    error_message: Optional[str],
    details: Mapping[str, Any],
    target_date: date,
) -> bool:
    """Escribe el estado final con una sesión NUEVA (si el fallo vino de la
    base de datos, la anterior puede estar inutilizable). Nunca lanza: un
    error aquí taparía la excepción original que se está propagando."""
    try:
        with Session(engine) as session:
            finish_run(session, run_id, status=status, error_message=error_message, **details)
        return True
    except Exception as record_error:  # noqa: BLE001
        log(logging.ERROR, status, target_date,
            "no se pudo registrar el estado final (%s); recover_stale_runs lo marcará failed más adelante",
            redact(f"{type(record_error).__name__}: {record_error}"))
        return False


def _raise_on_sigterm(signum: int, _frame: Any) -> None:
    raise JobTerminatedError(f"señal {signum} recibida")


def main() -> int:
    configure_logging()
    signal.signal(signal.SIGTERM, _raise_on_sigterm)

    try:
        target_date = resolve_target_date()
    except TargetDateError as error:
        log(logging.ERROR, "failed", None, "%s", error)
        return 2

    log(logging.INFO, "starting", target_date, "inicio del job nocturno")
    try:
        from database import get_inventory_engine  # carga services/api/.env (DATABASE_URL)

        outcome = run_nightly_export(get_inventory_engine(), target_date)
    except Exception as error:  # noqa: BLE001 — incluye fallos antes de crear la fila (p. ej. sin conexión)
        log(logging.ERROR, "failed", target_date, "el job terminó con error: %s",
            redact(f"{type(error).__name__}: {error}"))
        return 1

    log(logging.INFO, outcome, target_date, "fin del job nocturno")
    return 0


if __name__ == "__main__":
    sys.exit(main())
