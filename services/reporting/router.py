"""Endpoints del pipeline de desempeño de negocio (CONTEXT, seccion 6).

Modulo propio, separado de services/telemetry y de GET /telemetry/report.
Aqui solo hay HTTP: autenticacion, validacion de entrada y codigos de
estado. Toda la logica (que KPI se publica, como se lanza una corrida, que
es un lock) se importa de data/pipelines/ — nunca al reves.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlmodel import Session

from data.pipelines.monthly_clinic_supply_performance import queries, run_log, trigger
from database import get_inventory_db
from security import get_current_user, require_admin
from services.reporting.schemas import (
    MonthlyClinicSupplyPerformanceResponse,
    PipelineRunQueued,
    PipelineRunStatus,
    PipelineRunTriggerRequest,
)

router = APIRouter(prefix="/reporting", tags=["reporting"], dependencies=[Depends(get_current_user)])


@router.get(
    "/monthly-clinic-supply-performance",
    response_model=MonthlyClinicSupplyPerformanceResponse,
)
def get_monthly_clinic_supply_performance(
    month_start: Optional[date] = Query(
        default=None, description="Primer día del mes (YYYY-MM-01). Por defecto, el mes calculado más reciente."
    ),
    session: Session = Depends(get_inventory_db),
) -> Dict[str, Any]:
    """Consulta de KPIs: el feed que consumirá el dashboard de la Parte 3.

    Sin caché a proposito: quien escribe esta tabla es el pipeline, que no
    puede invalidar el TTLCache de este proceso (PIPELINE_DESIGN.md 5.2)."""
    if month_start is not None and month_start.day != 1:
        raise HTTPException(status_code=400, detail="month_start must be the first day of a month (YYYY-MM-01).")
    result = queries.get_monthly_clinic_supply_performance(session, month_start)
    if result is None:
        raise HTTPException(status_code=404, detail="No computed report for that month.")
    return result


@router.get("/pipeline-runs/latest", response_model=PipelineRunStatus)
def get_latest_pipeline_run(session: Session = Depends(get_inventory_db)) -> Dict[str, Any]:
    """Consulta de estado: estado, inicio, fin y registros procesados de la
    ultima corrida, mas is_stale si el ultimo mes cerrado aun no se calculo."""
    result = queries.get_latest_run_status(session)
    if result is None:
        raise HTTPException(status_code=404, detail="The pipeline has never run.")
    return result


@router.post(
    "/pipeline-runs",
    response_model=PipelineRunQueued,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_pipeline_run(
    background_tasks: BackgroundTasks,
    payload: Optional[PipelineRunTriggerRequest] = None,
    session: Session = Depends(get_inventory_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Disparo manual. Solo admin: recalcular el paquete de la junta no es una
    accion de cualquier usuario autenticado.

    202 en cuanto la corrida queda encolada (con su lock); el flow corre en
    segundo plano y su resultado se consulta en /pipeline-runs/latest."""
    require_admin(current_user)
    requested_month = payload.month_start if payload else None
    try:
        run = trigger.trigger_monthly_run(session, requested_month, triggered_by=current_user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except run_log.WindowLockedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "A pipeline run for that month is already queued or running.",
                "run_id": str(exc.active_run_id) if exc.active_run_id else None,
            },
        ) from exc

    background_tasks.add_task(trigger.launch_manual_run, str(run.run_id), run.window_start, current_user["id"])
    return {"run_id": run.run_id, "status": "queued", "month_start": run.window_start}
