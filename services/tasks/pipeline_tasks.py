"""Tarea asíncrona del recálculo del informe mensual (Ticket #DEV-55).

Antes, POST /reporting/pipeline-runs ejecutaba el flow de Prefect con
`BackgroundTasks`: respondía 202, pero el trabajo corría DENTRO del proceso
de FastAPI (si la API se reiniciaba a mitad, la corrida moría). Ahora la API
solo encola y este módulo lo ejecuta en el worker, un proceso aparte.

Este archivo lo importan los dos procesos. Por eso no importa Prefect a nivel
de módulo: la API solo necesita `enqueue_monthly_pipeline_run` y no debe
cargar Prefect al arrancar (el import es diferido, dentro de la tarea).
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date
from typing import Any, Dict, Optional

from celery import Task
from celery.exceptions import Retry, SoftTimeLimitExceeded
from sqlmodel import Session

from data.pipelines.monthly_clinic_supply_performance import run_log
from data.pipelines.monthly_clinic_supply_performance.models import PipelineRun
from services.celery_app import celery_app
from services.tasks.dead_letters import record_dead_letter
from services.tasks.redaction import describe_error, install_celery_log_redaction

logger = logging.getLogger("healthcore.tasks")
install_celery_log_redaction()

PIPELINE_TASK_NAME = "reporting.run_monthly_clinic_supply_performance"

# max_retries=3 literal del ticket: 1 ejecución + 3 reintentos = 4 ejecuciones
# antes de ir a la DLQ (decisión del usuario, 2026-09-13).
MAX_RETRIES = 3
# Backoff exponencial factor * 2**reintento: 10 s, 20 s, 40 s.
RETRY_BACKOFF_SECONDS = 10
RETRY_BACKOFF_MAX_SECONDS = 5 * 60

# Estados de reporting.pipeline_runs que todavía retienen el lock del mes.
ACTIVE_RUN_STATUSES = ("queued", "running")

_engine_override = None


def use_engine(engine) -> None:
    """Tests: sustituye Supabase por una SQLite en memoria (mismo patrón que
    pipeline.use_engine). None vuelve al engine real."""
    global _engine_override
    _engine_override = engine


def _engine():
    if _engine_override is not None:
        return _engine_override
    from database import get_inventory_engine

    return get_inventory_engine()


class ObservableTask(Task):
    """Base de toda tarea del proyecto: log estructurado y DLQ.

    Cada ejecución deja una línea con task_id, intento, estado y duración; los
    fallos añaden el tipo y el mensaje completo del error. Nunca datos de
    usuario: los argumentos son identificadores y el mensaje pasa por
    describe_error (enmascara correos)."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        task_id = self.request.id
        attempt = self.request.retries + 1
        started = time.monotonic()
        logger.info("task_started task=%s task_id=%s attempt=%s", self.name, task_id, attempt)
        try:
            result = super().__call__(*args, **kwargs)
        except Retry as retry:
            logger.warning(
                "task_retry task=%s task_id=%s attempt=%s status=retry duration_ms=%.1f "
                "next_retry_in_s=%s error_type=%s error=%s",
                self.name, task_id, attempt, _elapsed_ms(started), retry.when,
                type(retry.exc).__name__, describe_error(retry.exc) if retry.exc else "",
            )
            raise
        except Exception as error:
            logger.error(
                "task_failed task=%s task_id=%s attempt=%s status=failure duration_ms=%.1f error_type=%s error=%s",
                self.name, task_id, attempt, _elapsed_ms(started), type(error).__name__, describe_error(error),
            )
            raise
        logger.info(
            "task_succeeded task=%s task_id=%s attempt=%s status=success duration_ms=%.1f",
            self.name, task_id, attempt, _elapsed_ms(started),
        )
        return result

    def on_failure(self, exc: BaseException, task_id: str, args: Any, kwargs: Any, einfo: Any) -> None:
        """Celery solo llega aquí cuando la tarea falla DEFINITIVAMENTE: un
        reintento programado pasa por on_retry, no por on_failure."""
        attempt = self.request.retries + 1
        try:
            recorded = record_dead_letter(
                _engine(), task_id=task_id, task_name=self.name, attempt=attempt, error=exc, task_kwargs=kwargs
            )
        except Exception as db_error:  # noqa: BLE001 — sin base de datos, al menos el log
            logger.critical(
                "dead_letter_write_failed task=%s task_id=%s attempt=%s error_type=%s error=%s db_error_type=%s",
                self.name, task_id, attempt, type(exc).__name__, describe_error(exc), type(db_error).__name__,
            )
            return
        if recorded:
            logger.error(
                "task_dead_lettered task=%s task_id=%s attempt=%s error_type=%s",
                self.name, task_id, attempt, type(exc).__name__,
            )


def _elapsed_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000


def _release_run_if_still_active(run_id: str, error: BaseException) -> None:
    """Si el flow falló antes de registrar su propio fallo (p. ej. creó la
    fila `queued` pero Supabase cayó antes de marcarla `running`), esa fila
    retendría el lock del mes 30 minutos y los reintentos saldrían como
    `cancelled`. Sin fila (falló antes de crearla) no hay nada que liberar."""
    try:
        with Session(_engine()) as session:
            run = session.get(PipelineRun, uuid.UUID(run_id))
            if run is not None and run.status in ACTIVE_RUN_STATUSES:
                run_log.mark_run_failed(session, run.run_id, error)
    except Exception as release_error:  # noqa: BLE001 — el heartbeat lo liberará
        logger.warning("could_not_release_run run_id=%s error_type=%s", run_id, type(release_error).__name__)


@celery_app.task(
    bind=True,
    base=ObservableTask,
    name=PIPELINE_TASK_NAME,
    autoretry_for=(Exception,),
    # Un timeout no es un fallo transitorio: repetir 15 minutos de trabajo
    # tres veces más solo retrasaría el aviso. Va directo a la DLQ.
    dont_autoretry_for=(SoftTimeLimitExceeded,),
    max_retries=MAX_RETRIES,
    retry_backoff=RETRY_BACKOFF_SECONDS,
    retry_backoff_max=RETRY_BACKOFF_MAX_SECONDS,
    # Celery sortea por defecto la espera entre 0 y el tope, y un reintento
    # podría salir inmediato. El ticket exige lo contrario.
    retry_jitter=False,
)
def run_monthly_clinic_supply_performance(
    self: Task, month_start: str, run_id: Optional[str] = None, triggered_by: Optional[str] = None
) -> Dict[str, Any]:
    """Ejecuta el flow completo para un mes. Recibe solo identificadores: el
    flow lee él mismo los eventos de Supabase (regla 1 del patrón).

    Idempotente por diseño del pipeline (recalcula la ventana completa y
    sustituye), así que un reintento nunca suma dos veces. Cada intento tiene
    su propia fila en reporting.pipeline_runs: el primero la crea con el
    run_id que reservó la API (el que devolvió el 202); los reintentos crean
    otra, porque la fallida ya soltó el lock y otra corrida pudo tomarlo
    durante la espera."""
    attempt_run_id = run_id if self.request.retries == 0 else None
    # Imports diferidos: solo el worker carga Prefect (ver docstring del módulo).
    from prefect.exceptions import CancelledRun

    from data.pipelines.pipeline import monthly_clinic_supply_performance_flow

    try:
        result = monthly_clinic_supply_performance_flow(
            month_start=date.fromisoformat(month_start),
            trigger_type="manual",
            triggered_by=triggered_by,
            run_id=attempt_run_id,
        )
    except CancelledRun as cancelled:
        # El flow devuelve Cancelled si el mes ya tenía otra corrida viva, y
        # Prefect 3 lo convierte en esta excepción al llamarlo directamente.
        # No es un fallo: sin esta rama, autoretry lo reintentaría 3 veces y
        # lo mandaría a la DLQ. Pasa si dos peticiones superan a la vez la
        # comprobación de la API (reserve_monthly_run).
        return _cancelled(month_start, str(cancelled))
    except Exception as error:
        if attempt_run_id is not None:
            _release_run_if_still_active(attempt_run_id, error)
        raise

    if not isinstance(result, dict):  # defensivo: un estado devuelto en vez de lanzado
        return _cancelled(month_start, getattr(result, "message", None))
    return result


def _cancelled(month_start: str, reason: Optional[str]) -> Dict[str, Any]:
    return {"status": "cancelled", "month_start": month_start, "reason": reason}


def enqueue_monthly_pipeline_run(run_id: str, month_start: date, triggered_by: Optional[str]) -> str:
    """Productor: deja el encargo en Redis y devuelve el task_id sin esperar.

    retry=False: si Redis no responde, falla ya (la API responde 503) en vez
    de reintentar la publicación mientras el cliente espera."""
    async_result = run_monthly_clinic_supply_performance.apply_async(
        kwargs={"month_start": month_start.isoformat(), "run_id": run_id, "triggered_by": triggered_by},
        retry=False,
    )
    return async_result.id
