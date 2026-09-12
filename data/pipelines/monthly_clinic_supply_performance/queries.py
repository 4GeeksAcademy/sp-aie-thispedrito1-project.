"""Lecturas que exponen los endpoints de services/reporting/.

Viven aqui y no en services/ para que ninguna logica del pipeline (que es
un KPI publicado, como se ordena, cuando un informe esta atrasado) quede
duplicada en la capa HTTP.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlmodel import Session

from data.pipelines.monthly_clinic_supply_performance import run_log, storage


def get_monthly_clinic_supply_performance(session: Session, month_start: Optional[date]) -> Optional[dict[str, Any]]:
    """Contrato de GET /reporting/monthly-clinic-supply-performance (CONTEXT,
    seccion 6). Sin month_start: el mes calculado mas reciente. None si ese
    mes no esta calculado.

    Orden: pais y luego clinic_id numerico, para que las filas USD y GBP
    salgan agrupadas lado a lado — nunca hay un total que las sume."""
    target = month_start or storage.get_latest_month_start(session)
    if target is None:
        return None
    rows = storage.get_published_rows(session, target)
    if not rows:
        return None
    rows = sorted(rows, key=lambda row: (row.country, int(row.clinic_id)))
    return {
        "month_start": target,
        "clinics": [
            {
                "clinic_id": row.clinic_id,
                "country": row.country,
                "total_supply_cost": float(row.total_supply_cost),
                "supply_consumption_count": row.supply_consumption_count,
                "critical_stockout_count": row.critical_stockout_count,
                "expiry_risk_count": row.expiry_risk_count,
                "currency": row.currency,
            }
            for row in rows
        ],
    }


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def get_latest_run_status(session: Session) -> Optional[dict[str, Any]]:
    """Metadata de la ultima corrida para GET /reporting/pipeline-runs/latest.

    Deliberadamente sin triggered_by (user_uuid): es rastro de auditoria en
    la base de datos, el dashboard no lo necesita."""
    run = run_log.get_latest_run(session)
    if run is None:
        return None
    started_at, finished_at = _aware(run.started_at), _aware(run.finished_at)
    duration = (finished_at - started_at).total_seconds() if started_at and finished_at else None
    return {
        "run_id": run.run_id,
        "pipeline_name": run.pipeline_name,
        "trigger_type": run.trigger_type,
        "status": run.status,
        "phase": run.phase,
        "month_start": run.window_start,
        "window_end": run.window_end,
        "queued_at": _aware(run.queued_at),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(duration, 3) if duration is not None else None,
        "records_processed": run.rows_extracted,
        "duplicates_dropped": run.duplicates_dropped,
        "rows_invalid": run.rows_invalid,
        "partitions_inserted": run.partitions_inserted,
        "partitions_updated": run.partitions_updated,
        "partitions_unchanged": run.partitions_unchanged,
        "partitions_removed": run.partitions_removed,
        "partitions_rejected": run.partitions_rejected,
        "warnings": list((run.quality_checks or {}).get("warnings", [])),
        "error_type": run.error_type,
        "error_message": run.error_message,
        "is_stale": run_log.is_report_stale(run_log.get_latest_completed_window(session)),
    }
