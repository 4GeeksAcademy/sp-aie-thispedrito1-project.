"""Pipeline de Desempeño de Negocio — monthly_clinic_supply_performance.

Produce el "Reporte Mensual de Desempeño de Insumos por Clínica" (CEO y CCO):
lee telemetry_events en SOLO LECTURA y escribe los 4 KPIs del CONTEXT en
reporting.monthly_clinic_supply_performance. Diseño completo en
data/pipelines/PIPELINE_DESIGN.md.

Ejecución (desde la raíz del repo, con el venv de la API, que ya tiene
Prefect y la conexión a Supabase vía services/api/.env):

    services/api/.venv/bin/python data/pipelines/pipeline.py
    services/api/.venv/bin/python data/pipelines/pipeline.py --month-start 2026-08-01

Sin --month-start procesa el último mes cerrado. Frecuencia prevista:
mensual, el día 1 a las 02:00 UTC (cierre) y el día 8 a las 02:00 UTC
(reconciliación de eventos tardíos), más disparos manuales desde
POST /reporting/pipeline-runs.
"""

# Sin `from __future__ import annotations` a propósito: Prefect genera el
# esquema de parámetros del flow con Pydantic a partir de las anotaciones, y
# en Python 3.9 (venv local) las anotaciones diferidas llegan como texto que
# no sabe resolver ("CheckParameter is not fully defined"). Por eso aquí se
# usa Optional[...] y no la sintaxis `X | None`.
import argparse
import hashlib
import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    # Ejecutado como script, Python solo pone data/pipelines/ en sys.path.
    sys.path.insert(0, str(ROOT_DIR))

import data.pipelines  # noqa: E402,F401  (añade services/api a sys.path)
from prefect import flow, get_run_logger, task  # noqa: E402
from prefect.cache_policies import NONE  # noqa: E402
from prefect.runtime import flow_run as prefect_flow_run  # noqa: E402
from prefect.states import Cancelled  # noqa: E402
from sqlmodel import Session  # noqa: E402

from data.pipelines.monthly_clinic_supply_performance import run_log, storage  # noqa: E402
from data.pipelines.monthly_clinic_supply_performance.models import ensure_reporting_schema  # noqa: E402
from data.process.supply_performance_transforms import (  # noqa: E402
    evaluate_capture_coverage,
    month_window,
    resolve_month_start,
    transform_supply_events,
)
from database import get_inventory_engine  # noqa: E402

FLOW_NAME = "monthly-clinic-supply-performance"

# Umbral de negocio de cobertura (PIPELINE_DESIGN.md 4.4). Por debajo, la
# corrida publica igualmente pero queda completed_with_warnings.
COVERAGE_WARNING_RATIO = 0.95

# Súbelo cuando cambie una regla de transform_supply_events: invalida la
# caché de la task de transformación aunque los eventos sean los mismos.
TRANSFORM_VERSION = "2026-09-13.1"

EVAL_DIR = ROOT_DIR / "data" / "eval" / "monthly_clinic_supply_performance"


class CaptureGapError(Exception):
    """Validación bloqueante: la captura está rota, no se publica nada."""


# --- Engine -----------------------------------------------------------------

_engine_override = None


def use_engine(engine) -> None:
    """Para tests: sustituye Supabase por otro engine (SQLite en memoria).
    None vuelve al engine real de database.py."""
    global _engine_override
    _engine_override = engine


def _engine():
    return _engine_override if _engine_override is not None else get_inventory_engine()


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


# --- Tasks ------------------------------------------------------------------


def _retry_unless_window_locked(task_obj, task_run, state) -> bool:
    """Reintentar no libera un mes bloqueado por otra corrida: ese caso
    termina en el acto. Cualquier otro fallo (timeout del pooler) sí se
    reintenta."""
    return not isinstance(state.data, run_log.WindowLockedError)


@task(
    name="start_pipeline_run",
    # 2 reintentos: la task solo crea el esquema si falta y escribe una fila;
    # un corte transitorio de Supabase se absorbe en segundos, y si persiste
    # no tiene sentido esperar más antes de que exista ni siquiera el log.
    retries=2,
    retry_delay_seconds=[5, 15],
    retry_condition_fn=_retry_unless_window_locked,
    cache_policy=NONE,
)
def start_pipeline_run(
    month_start: date, trigger_type: str, triggered_by: Optional[str], run_id: Optional[str]
) -> str:
    engine = _engine()
    ensure_reporting_schema(engine)
    with Session(engine) as session:
        if run_id is None:
            run = run_log.create_queued_run(
                session, month_start=month_start, trigger_type=trigger_type, triggered_by=triggered_by
            )
            run_id = str(run.run_id)
        flow_run_id = prefect_flow_run.get_id()
        run_log.mark_running(session, _uuid(run_id), str(flow_run_id) if flow_run_id else None)
    get_run_logger().info("pipeline run %s started for month %s", run_id, month_start.isoformat())
    return run_id


@task(
    name="extract_supply_events",
    # 3 reintentos (10s, 30s, 90s): el fallo esperable es transitorio (el
    # pooler de Supabase cortando una conexión, el plan gratuito despertando).
    # ~2 minutos en total cubren eso; más allá es una caída real y conviene
    # fallar y avisar en vez de retrasar el paquete de la junta.
    retries=3,
    retry_delay_seconds=[10, 30, 90],
    timeout_seconds=120,
    cache_policy=NONE,  # siempre lee la fuente actual: nunca reutilizar una extracción vieja
)
def extract_supply_events(run_id: str, month_start: date) -> list[dict[str, Any]]:
    window_start, window_end = month_window(month_start)
    with Session(_engine()) as session:
        events = storage.fetch_supply_events(session, window_start, window_end)
        run_log.update_run(session, _uuid(run_id), phase="extracted", rows_extracted=len(events))
    get_run_logger().info("extracted %d source events", len(events))
    return events


@task(
    name="extract_domain_activity",
    # Mismos 3 reintentos que la extracción principal (mismo servicio, mismo
    # tipo de fallo). Task NO crítica: el flow la invoca con return_state=True.
    retries=3,
    retry_delay_seconds=[10, 30, 90],
    timeout_seconds=120,
    cache_policy=NONE,
)
def extract_domain_activity(month_start: date) -> dict[str, dict[str, int]]:
    window_start, window_end = month_window(month_start)
    with Session(_engine()) as session:
        return storage.fetch_domain_activity(session, window_start, window_end)


def supply_events_cache_key(context, parameters: dict[str, Any]) -> str:
    """Clave de caché de la transformación = huella del CONTENIDO de entrada:
    mes + versión de las reglas + (id, timestamp) de cada evento extraído.

    Los ids de telemetry_events son únicos y la tabla es append-only (una fila
    nunca cambia), así que la lista de ids identifica exactamente los datos.
    Consecuencias:
    - Misma extracción en la próxima hora (p. ej. reintento o doble clic
      tras un fallo en la carga) -> misma clave -> no se recalcula.
    - Llega un evento tardío -> cambia la lista -> clave nueva -> se
      recalcula aunque no haya pasado la hora. Por eso la clave NO es solo
      el mes: una caché por mes haría que la reconciliación reutilizara el
      cálculo viejo y perdiera justo los eventos que busca."""
    events = parameters["events"]
    fingerprint = {
        "month_start": parameters["month_start"].isoformat(),
        "transform_version": TRANSFORM_VERSION,
        "events": [[event["id"], event["timestamp"]] for event in events],
    }
    digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode("utf-8")).hexdigest()
    return f"{FLOW_NAME}-transform-{digest}"


@task(
    name="transform_monthly_clinic_metrics",
    # Sin reintentos: es determinista, repetir un bug no lo arregla.
    cache_key_fn=supply_events_cache_key,
    # Válida 1 hora (requisito del ticket: no repetir una task que ya corrió
    # bien en la última hora). Pasada la hora se recalcula aunque la huella
    # coincida, para que un cambio de TRANSFORM_VERSION olvidado no viva
    # indefinidamente en caché.
    cache_expiration=timedelta(hours=1),
)
def transform_monthly_clinic_metrics(events: list[dict[str, Any]], month_start: date) -> dict[str, Any]:
    result = transform_supply_events(events, month_start)
    get_run_logger().info(
        "transformed %d events into %d clinic rows (%d rejected)",
        result["counts"]["rows_after_dedup"],
        len(result["rows"]),
        len(result["rejected"]),
    )
    return result


@task(name="validate_monthly_aggregates", cache_policy=NONE)
def validate_monthly_aggregates(
    transformed: dict[str, Any], domain_activity: Optional[dict[str, dict[str, int]]]
) -> dict[str, Any]:
    validation = evaluate_capture_coverage(
        transformed["partition_event_counts"], domain_activity, COVERAGE_WARNING_RATIO
    )
    if validation["blocking_error"]:
        raise CaptureGapError(validation["blocking_error"])
    return validation


@task(
    name="load_monthly_clinic_supply_performance",
    # 3 reintentos (15s, 60s, 3 min): la carga es una sola transacción, así
    # que un reintento tras un corte parte de cero y no puede duplicar nada.
    # Esperas más largas que en la extracción porque aquí el fallo típico es
    # un lock o una conexión saturada del pooler, que tarda más en liberarse.
    retries=3,
    retry_delay_seconds=[15, 60, 180],
    timeout_seconds=180,
    cache_policy=NONE,  # una carga nunca se da por hecha desde caché
)
def load_monthly_clinic_supply_performance(
    run_id: str, month_start: date, transformed: dict[str, Any]
) -> dict[str, int]:
    with Session(_engine()) as session:
        counts = storage.load_monthly_rows(
            session,
            run_id=_uuid(run_id),
            month_start=month_start,
            rows=transformed["rows"],
            rejected=transformed["rejected"],
            partition_event_counts=transformed["partition_event_counts"],
        )
        run_log.update_run(session, _uuid(run_id), phase="loaded")
    get_run_logger().info("load result: %s", counts)
    return counts


@task(name="export_eval_snapshot", cache_policy=NONE)
def export_eval_snapshot(
    run_id: str,
    month_start: date,
    transformed: dict[str, Any],
    validation: dict[str, Any],
    load_counts: dict[str, int],
) -> str:
    """Salida de validación en data/eval/ (paso OPCIONAL). Solo agregados y
    métricas de calidad: ni userId, ni sessionId, ni payloads de eventos."""
    target_dir = EVAL_DIR / month_start.isoformat()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{run_id}.json"
    snapshot = {
        "run_id": run_id,
        "month_start": month_start.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": transformed["counts"],
        "load": load_counts,
        "rejected": transformed["rejected"],
        "quality": transformed["quality"],
        "coverage": validation["coverage"],
        "warnings": validation["warnings"],
        "rows": [{**row, "month_start": row["month_start"].isoformat(), "total_supply_cost": str(row["total_supply_cost"])} for row in transformed["rows"]],
    }
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


@task(
    name="finish_pipeline_run",
    # 3 reintentos: si el cierre del log falla, la corrida quedaría "running"
    # hasta que el heartbeat la marque crashed pese a haber cargado bien.
    retries=3,
    retry_delay_seconds=[5, 15, 45],
    cache_policy=NONE,
)
def finish_pipeline_run(run_id: str, status: str, fields: dict[str, Any]) -> None:
    with Session(_engine()) as session:
        run_log.finish_run(session, _uuid(run_id), status=status, **fields)


# --- Flow -------------------------------------------------------------------


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def _record_failure(run_id: str, error: BaseException) -> None:
    """Fallo explícito en el log. Si ni siquiera eso se puede escribir
    (Supabase caído), el heartbeat marcará la corrida como crashed."""
    try:
        with Session(_engine()) as session:
            run_log.mark_run_failed(session, _uuid(run_id), error)
    except Exception as log_error:  # noqa: BLE001
        print(f"[pipeline] could not record failure for run {run_id}: {type(log_error).__name__}", file=sys.stderr)


@flow(name=FLOW_NAME, log_prints=True)
def monthly_clinic_supply_performance_flow(
    month_start: Optional[date] = None,
    trigger_type: str = "cli",
    triggered_by: Optional[str] = None,
    run_id: Optional[str] = None,
) -> dict[str, Any]:
    """Flow principal: extracción -> transformación -> validación -> carga.

    Tolerancia a fallos parciales: extract_domain_activity y
    export_eval_snapshot se invocan con return_state=True, así que su fallo
    se inspecciona aquí y la corrida sigue (con aviso) en vez de propagarse.
    Las tasks críticas sí propagan: el except registra el fallo en
    reporting.pipeline_runs y lo relanza para que Prefect marque Failed."""
    target_month = resolve_month_start(month_start, _utc_today())

    try:
        run_id = start_pipeline_run(target_month, trigger_type, triggered_by, run_id)
    except run_log.WindowLockedError as locked:
        return Cancelled(message=f"window_locked: active run {locked.active_run_id}")

    try:
        events = extract_supply_events(run_id, target_month)

        domain_state = extract_domain_activity(target_month, return_state=True)
        domain_activity = domain_state.result() if domain_state.is_completed() else None
        if domain_activity is None:
            print("[pipeline] domain activity unavailable, continuing without coverage check")

        transformed = transform_monthly_clinic_metrics(events, target_month)
        validation = validate_monthly_aggregates(transformed, domain_activity)
        load_counts = load_monthly_clinic_supply_performance(run_id, target_month, transformed)

        snapshot_state = export_eval_snapshot(
            run_id, target_month, transformed, validation, load_counts, return_state=True
        )
        warnings = list(validation["warnings"])
        if snapshot_state.is_failed():
            warnings.append("eval_snapshot_failed")
            print("[pipeline] eval snapshot failed, load already committed — continuing")

        status = "completed_with_warnings" if warnings else "completed"
        counts = transformed["counts"]
        finish_pipeline_run(
            run_id,
            status,
            {
                "rows_extracted": counts["rows_extracted"],
                "duplicates_dropped": counts["duplicates_dropped"],
                "rows_invalid": counts["rows_invalid"],
                "partitions_inserted": load_counts["inserted"],
                "partitions_updated": load_counts["updated"],
                "partitions_unchanged": load_counts["unchanged"],
                "partitions_removed": load_counts["removed"],
                "partitions_rejected": load_counts["rejected"],
                "source_min_event_timestamp": transformed["source_min_event_timestamp"],
                "source_max_event_timestamp": transformed["source_max_event_timestamp"],
                "quality_checks": {
                    "warnings": warnings,
                    "coverage_warning_ratio": COVERAGE_WARNING_RATIO,
                    "coverage": validation["coverage"],
                    "clinics_without_events": validation["clinics_without_events"],
                    "rejected_partitions": transformed["rejected"],
                    "events_by_type": counts["events_by_type"],
                    "published_values_changed": load_counts["updated"] + load_counts["removed"] > 0,
                    **transformed["quality"],
                },
            },
        )
    except Exception as error:
        _record_failure(run_id, error)
        raise

    return {
        "run_id": run_id,
        "month_start": target_month.isoformat(),
        "status": status,
        "rows_extracted": counts["rows_extracted"],
        "rows_after_dedup": counts["rows_after_dedup"],
        "load": load_counts,
        "warnings": warnings,
    }


def run_manual_flow(run_id: str, month_start: date, triggered_by: Optional[str]) -> None:
    """Punto de entrada del disparo manual (POST /reporting/pipeline-runs),
    que ya creó la fila `queued` con el lock. Corre en segundo plano: el
    resultado queda en reporting.pipeline_runs, nunca se propaga a nadie."""
    try:
        monthly_clinic_supply_performance_flow(
            month_start=month_start, trigger_type="manual", triggered_by=triggered_by, run_id=run_id
        )
    except Exception as error:  # noqa: BLE001 — ya registrado en pipeline_runs por el flow
        print(f"[pipeline] manual run {run_id} failed: {type(error).__name__}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Reporte Mensual de Desempeño de Insumos por Clínica")
    parser.add_argument(
        "--month-start",
        type=date.fromisoformat,
        default=None,
        help="Primer día del mes a procesar (YYYY-MM-01). Por defecto, el último mes cerrado.",
    )
    args = parser.parse_args(argv)

    try:
        result = monthly_clinic_supply_performance_flow(month_start=args.month_start)
    except Exception as error:  # noqa: BLE001
        print(f"[pipeline] failed: {type(error).__name__}: {run_log.redact(str(error))}", file=sys.stderr)
        return 1

    if not isinstance(result, dict):  # Cancelled: el mes ya tenía una corrida activa
        print(f"[pipeline] cancelled: {getattr(result, 'message', result)}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
