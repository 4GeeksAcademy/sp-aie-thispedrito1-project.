"""Tareas en segundo plano del cierre financiero (README: "Ejecuta el cierre como tarea en segundo plano").

- `financial_close.run_close`: ingiere los CSV del periodo si están en
  data/raw/financial-close/<YYYY-MM>/ y prepara el informe. La encola el
  disparador programado (scripts/financial_close_scheduled.py, cada hora desde
  el servicio `scheduler`) o un admin desde la API.
- `financial_close.revise_report`: nueva versión tras un rechazo de Tom.

Por Redis solo viajan identificadores (periodo, report_id): nunca cifras,
comentarios ni descripciones.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from sqlmodel import Session

from services.celery_app import celery_app
from services.tasks.pipeline_tasks import ObservableTask, _engine

RUN_TASK_NAME = "financial_close.run_close"
REVISE_TASK_NAME = "financial_close.revise_report"
ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT_DIR / "data" / "raw" / "financial-close"


def period_dir(period: date) -> Path:
    return DATA_ROOT / period.strftime("%Y-%m")


def _use_model() -> bool:
    import os

    return os.getenv("FINANCIAL_CLOSE_USE_MODEL", "1") != "0"


def execute_close(period: date, trigger: str) -> Dict[str, Any]:
    from services.financial_close import repository, workflow
    from services.financial_close.runtime import ActionLog

    engine = _engine()
    log = ActionLog()
    with Session(engine) as session:
        if trigger == "scheduled" and repository.has_scheduled_report(session, period):
            return {"period": period.isoformat(), "status": "already_done"}
        folder = period_dir(period)
        if folder.exists():
            workflow.ingest_period(session, folder, period, log=log)
        row = workflow.run_close(session, engine, period, trigger=trigger, use_model=_use_model(), log=log)
        return {"period": period.isoformat(), "report_id": str(row.id), "version": row.version,
                "status": row.status}


@celery_app.task(bind=True, base=ObservableTask, name=RUN_TASK_NAME, autoretry_for=(Exception,),
                 dont_autoretry_for=(SoftTimeLimitExceeded, LookupError, ValueError), max_retries=2,
                 retry_backoff=30, retry_jitter=False)
def run_close(self: Task, period: str, trigger: str = "manual") -> Dict[str, Any]:
    return execute_close(date.fromisoformat(period), trigger)


@celery_app.task(bind=True, base=ObservableTask, name=REVISE_TASK_NAME, autoretry_for=(Exception,),
                 dont_autoretry_for=(SoftTimeLimitExceeded, LookupError), max_retries=2, retry_backoff=30,
                 retry_jitter=False)
def revise_report(self: Task, report_id: str) -> Dict[str, Any]:
    from services.financial_close import workflow

    engine = _engine()
    with Session(engine) as session:
        row = workflow.revise(session, engine, uuid.UUID(report_id), use_model=_use_model())
        return {"report_id": str(row.id), "version": row.version, "supersedes_id": report_id}


def enqueue_close(period: date, trigger: str) -> str:
    return run_close.apply_async(kwargs={"period": period.isoformat(), "trigger": trigger}, retry=False).id


def enqueue_revision(report_id: uuid.UUID) -> str:
    return revise_report.apply_async(kwargs={"report_id": str(report_id)}, retry=False).id
