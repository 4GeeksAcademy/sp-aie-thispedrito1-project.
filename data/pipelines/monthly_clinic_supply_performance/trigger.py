"""Disparo manual del pipeline desde POST /reporting/pipeline-runs.

Dos pasos separados a proposito:

1. `trigger_monthly_run` (sincrono, dentro de la peticion): valida el mes y
   crea la fila `queued` que actua de lock. Asi la API puede responder 400
   (mes invalido) o 409 (ya hay una corrida activa) antes de lanzar nada.
2. `launch_manual_run` (en segundo plano, tras responder 202): ejecuta el
   flow real importado de data/pipelines/pipeline.py, sin servidor ni worker
   de Prefect (Prefect arranca su API efimera en el propio proceso).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Session

from data.pipelines.monthly_clinic_supply_performance import run_log
from data.pipelines.monthly_clinic_supply_performance.models import PipelineRun
from data.process.supply_performance_transforms import resolve_month_start


def trigger_monthly_run(
    session: Session, month_start: Optional[date], triggered_by: str, today: Optional[date] = None
) -> PipelineRun:
    """Lanza ValueError si el mes no es valido y run_log.WindowLockedError si
    ese mes ya tiene una corrida activa."""
    target = resolve_month_start(month_start, today or datetime.now(timezone.utc).date())
    return run_log.create_queued_run(session, month_start=target, trigger_type="manual", triggered_by=triggered_by)


def launch_manual_run(run_id: str, month_start: date, triggered_by: Optional[str]) -> None:
    # Import diferido: Prefect tarda en importarse y solo hace falta cuando
    # alguien pulsa el boton, no en cada arranque de la API.
    from data.pipelines.pipeline import run_manual_flow

    run_manual_flow(run_id, month_start, triggered_by)
