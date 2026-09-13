"""Módulo job_runner: control de estado de jobs programados (tabla job_runs).

Lo usa scripts/nightly_export.py. No importa nada de FastAPI ni de services/api.
"""

from services.job_runner.models import JOB_STATUSES, JobRun
from services.job_runner.repository import (
    CANCELLED_PREFIX,
    STALE_AFTER,
    InvalidTransition,
    ProcessingLockBusy,
    cancel_run,
    create_run,
    ensure_job_runs_table,
    finish_run,
    get_run,
    has_completed_for_date,
    has_processing_lock,
    list_runs,
    mark_processing,
    recover_stale_runs,
    redact,
)

__all__ = [
    "CANCELLED_PREFIX",
    "JOB_STATUSES",
    "STALE_AFTER",
    "InvalidTransition",
    "JobRun",
    "ProcessingLockBusy",
    "cancel_run",
    "create_run",
    "ensure_job_runs_table",
    "finish_run",
    "get_run",
    "has_completed_for_date",
    "has_processing_lock",
    "list_runs",
    "mark_processing",
    "recover_stale_runs",
    "redact",
]
