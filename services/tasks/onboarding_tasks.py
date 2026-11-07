"""Tareas asíncronas del orquestador de incorporaciones.

README: "Ejecuta el agente fuera del ciclo petición-respuesta, como tarea en
segundo plano". La API solo valida y guarda la entrada humana
(`pending_command`) y encola `onboarding.advance_hire` con el `hire_id`: por
Redis no viaja ni una respuesta de comprobación ni un comentario. El worker
abre el checkpointer, reanuda el grafo y sincroniza.

`onboarding.followup_sweep` es el barrido de plazos (recordatorios y
escalados). Se lanza cada hora con scripts/onboarding_followup.py desde cron
(o el servicio `scheduler`), y también se puede encolar a mano.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from sqlmodel import Session

from services.celery_app import celery_app
from services.tasks.pipeline_tasks import ObservableTask, _engine

logger = logging.getLogger("healthcore.tasks")

ONBOARDING_ADVANCE_TASK_NAME = "onboarding.advance_hire"
ONBOARDING_SWEEP_TASK_NAME = "onboarding.followup_sweep"
ONBOARDING_MAX_RETRIES = 2
ONBOARDING_RETRY_BACKOFF_SECONDS = 10


class OnboardingTask(ObservableTask):
    def on_failure(self, exc: BaseException, task_id: str, args: Any, kwargs: Any, einfo: Any) -> None:
        super().on_failure(exc, task_id, args, kwargs, einfo)  # DLQ + log
        hire_id = kwargs.get("hire_id")
        if not hire_id:
            return
        from services.onboarding import repository

        try:
            with Session(_engine()) as session:
                hire = repository.get_hire(session, uuid.UUID(hire_id))
                if hire is not None:
                    hire.last_error = f"Error técnico al avanzar la incorporación ({type(exc).__name__})."
                    session.add(hire)
                    session.commit()
        except Exception as db_error:  # noqa: BLE001
            logger.critical("onboarding_error_write_failed onboarding_id=%s db_error_type=%s", hire_id, type(db_error).__name__)


@celery_app.task(
    bind=True,
    base=OnboardingTask,
    name=ONBOARDING_ADVANCE_TASK_NAME,
    autoretry_for=(Exception,),
    dont_autoretry_for=(SoftTimeLimitExceeded, LookupError),
    max_retries=ONBOARDING_MAX_RETRIES,
    retry_backoff=ONBOARDING_RETRY_BACKOFF_SECONDS,
    retry_jitter=False,
)
def advance_hire(self: Task, hire_id: str) -> Dict[str, Any]:
    from services.onboarding.runner import advance, open_saver

    with Session(_engine()) as session, open_saver() as saver:
        phase = advance(session, uuid.UUID(hire_id), saver=saver)
    return {"hire_id": hire_id, "phase": phase}


def enqueue_onboarding_advance(hire_id: uuid.UUID) -> str:
    """retry=False: si Redis no responde, la API lo sabe ya (503)."""
    return advance_hire.apply_async(kwargs={"hire_id": str(hire_id)}, retry=False).id


@celery_app.task(bind=True, base=ObservableTask, name=ONBOARDING_SWEEP_TASK_NAME)
def followup_sweep(self: Task) -> Dict[str, int]:
    from services.onboarding.followup import sweep

    with Session(_engine()) as session:
        return sweep(session)
