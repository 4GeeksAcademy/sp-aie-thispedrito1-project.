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

Topología (Parte 3): el flow principal no contiene lógica de ETL; coordina
subflows con entradas y salidas explícitas, cada uno ejecutable por separado:

    monthly_clinic_supply_performance_flow
      ├─ extract_clinic_supply_activity            (subflow: extracción)
      ├─ compute_monthly_clinic_supply_kpis        (subflow: transformación + validación)
      ├─ load_monthly_clinic_supply_performance    (subflow: carga)
      └─ export_supply_performance_eval_snapshot   (subflow opcional, return_state=True)
"""

# Sin `from __future__ import annotations` a propósito: Prefect genera el
# esquema de parámetros de cada flow con Pydantic a partir de las
# anotaciones, y en Python 3.9 (venv local) las anotaciones diferidas llegan
# como texto que no sabe resolver ("CheckParameter is not fully defined").
# Por eso aquí se usa Optional[...] / Dict[...] y no `X | None` ni `dict[...]`.
import argparse
import hashlib
import json
import logging
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    # Ejecutado como script, Python solo pone data/pipelines/ en sys.path.
    sys.path.insert(0, str(ROOT_DIR))

import data.pipelines  # noqa: E402,F401  (añade services/api a sys.path)
import pandas as pd  # noqa: E402
from prefect import flow, get_run_logger, task  # noqa: E402
from prefect.cache_policies import NONE  # noqa: E402
from prefect.exceptions import MissingContextError  # noqa: E402
from prefect.runtime import flow_run as prefect_flow_run  # noqa: E402
from prefect.states import Cancelled  # noqa: E402
from sqlmodel import Session  # noqa: E402

from data.pipelines.monthly_clinic_supply_performance import run_log, storage  # noqa: E402
from data.pipelines.monthly_clinic_supply_performance.models import ensure_reporting_schema  # noqa: E402
from data.process.supply_performance_transforms import (  # noqa: E402
    assemble_monthly_clinic_rows,
    build_quality_summary,
    critical_stockout_count_by_clinic,
    evaluate_capture_coverage,
    expiry_risk_count_by_clinic,
    month_window,
    prepare_supply_events,
    resolve_clinic_countries,
    resolve_month_start,
    supply_consumption_count_by_clinic,
    total_supply_cost_by_clinic,
)
from database import get_inventory_engine  # noqa: E402

FLOW_NAME = "monthly-clinic-supply-performance"

# Umbral de negocio de cobertura (PIPELINE_DESIGN.md 4.4). Por debajo, la
# corrida publica igualmente pero queda completed_with_warnings.
COVERAGE_WARNING_RATIO = 0.95

# Súbelo cuando cambie una regla de data/process/supply_performance_transforms.py:
# invalida la caché de prepare_clinic_supply_events aunque los eventos sean los mismos.
TRANSFORM_VERSION = "2026-09-13.2"

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


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def _logger():
    """Logger de Prefect dentro de una ejecución; logger estándar fuera.

    get_run_logger() lanza MissingContextError si no hay flow/task run
    activo, y las funciones de estas tasks se llaman también sueltas
    (`task.fn(...)` en tests/pipelines/test_pipeline.py, backfills). Una
    task reutilizable no puede depender de que Prefect la esté ejecutando
    solo para escribir un log."""
    try:
        return get_run_logger()
    except MissingContextError:
        return logging.getLogger("data.pipelines.pipeline")


# --- Tasks de control -------------------------------------------------------


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
    _logger().info("pipeline run %s started for month %s", run_id, month_start.isoformat())
    return run_id


@task(
    name="finish_pipeline_run",
    # 3 reintentos: si el cierre del log falla, la corrida quedaría "running"
    # hasta que el heartbeat la marque crashed pese a haber cargado bien.
    retries=3,
    retry_delay_seconds=[5, 15, 45],
    cache_policy=NONE,
)
def finish_pipeline_run(run_id: str, status: str, fields: Dict[str, Any]) -> None:
    with Session(_engine()) as session:
        run_log.finish_run(session, _uuid(run_id), status=status, **fields)


# --- Extracción -------------------------------------------------------------


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
def extract_supply_events(month_start: date, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    window_start, window_end = month_window(month_start)
    with Session(_engine()) as session:
        events = storage.fetch_supply_events(session, window_start, window_end)
        if run_id is not None:
            run_log.update_run(session, _uuid(run_id), phase="extracted", rows_extracted=len(events))
    _logger().info("extracted %d source events", len(events))
    return events


@task(
    name="extract_domain_activity",
    # Mismos 3 reintentos que la extracción principal (mismo servicio, mismo
    # tipo de fallo). Task NO crítica: el subflow la invoca con return_state=True.
    retries=3,
    retry_delay_seconds=[10, 30, 90],
    timeout_seconds=120,
    cache_policy=NONE,
)
def extract_domain_activity(month_start: date) -> Dict[str, Dict[str, int]]:
    window_start, window_end = month_window(month_start)
    with Session(_engine()) as session:
        return storage.fetch_domain_activity(session, window_start, window_end)


@flow(name="extract-clinic-supply-activity")
def extract_clinic_supply_activity(month_start: date, run_id: Optional[str] = None) -> Dict[str, Any]:
    """Subflow de EXTRACCIÓN, solo lectura.

    Entrada: el mes y, si corre dentro del pipeline, el run_id para el
    checkpoint `extracted` (sin run_id se puede ejecutar suelto).
    Salida: `events` (telemetry_events de los 4 tipos del CONTEXT, con tags
    minimizados) y `domain_activity` (filas reales de inventario por clínica,
    o None si esa extracción no crítica falló)."""
    events = extract_supply_events(month_start, run_id)

    domain_state = extract_domain_activity(month_start, return_state=True)
    domain_activity = domain_state.result() if domain_state.is_completed() else None
    if domain_activity is None:
        print("[pipeline] domain activity unavailable, continuing without coverage check")

    return {"events": events, "domain_activity": domain_activity}


# --- Transformación: una task por KPI del CONTEXT ---------------------------


def supply_events_cache_key(context, parameters: Dict[str, Any]) -> str:
    """Clave de caché de la preparación = huella del CONTENIDO de entrada:
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
    name="prepare_clinic_supply_events",
    # Sin reintentos: es determinista, repetir un bug no lo arregla.
    cache_key_fn=supply_events_cache_key,
    # Válida 1 hora (requisito de la Parte 2: no repetir una task que ya
    # corrió bien en la última hora). Es el paso costoso (aplanar, validar y
    # deduplicar con Pandas); las 4 tasks de KPI de abajo son sumas y
    # conteos sobre su resultado.
    cache_expiration=timedelta(hours=1),
)
def prepare_clinic_supply_events(events: List[Dict[str, Any]], month_start: date) -> Dict[str, Any]:
    prepared = prepare_supply_events(events, month_start)
    _logger().info(
        "prepared %d events (%d invalid, %d duplicates dropped)",
        prepared["counts"]["rows_after_dedup"],
        prepared["counts"]["rows_invalid"],
        prepared["counts"]["duplicates_dropped"],
    )
    return prepared


@task(name="compute_total_supply_cost", cache_policy=NONE)
def compute_total_supply_cost(supply_events: pd.DataFrame) -> Dict[str, Any]:
    """KPI "Costo de insumos por clínica": SUM(quantity * unit_cost) de
    inbound_order_created por clínica. Devuelve `total_supply_cost`
    (Decimal por clínica) y `missing_cost` (entradas sin coste utilizable)."""
    return total_supply_cost_by_clinic(supply_events)


@task(name="compute_supply_consumption_count", cache_policy=NONE)
def compute_supply_consumption_count(supply_events: pd.DataFrame) -> Dict[str, int]:
    """KPI "Volumen de consumo de insumos": conteo de outbound_order_created por clínica."""
    return supply_consumption_count_by_clinic(supply_events)


@task(name="compute_critical_stockout_count", cache_policy=NONE)
def compute_critical_stockout_count(supply_events: pd.DataFrame) -> Dict[str, int]:
    """KPI "Frecuencia de quiebre crítico": conteo de stock_threshold_triggered por clínica."""
    return critical_stockout_count_by_clinic(supply_events)


@task(name="compute_expiry_risk_count", cache_policy=NONE)
def compute_expiry_risk_count(supply_events: pd.DataFrame) -> Dict[str, int]:
    """KPI "Conteo de riesgo de vencimiento": conteo de supply_expiry_flagged por clínica."""
    return expiry_risk_count_by_clinic(supply_events)


@task(name="assemble_monthly_clinic_supply_performance", cache_policy=NONE)
def assemble_monthly_clinic_supply_performance(
    month_start: date,
    supply_events: pd.DataFrame,
    total_supply_cost: Dict[str, Any],
    supply_consumption_count: Dict[str, int],
    critical_stockout_count: Dict[str, int],
    expiry_risk_count: Dict[str, int],
) -> Dict[str, Any]:
    """Une los 4 KPIs en filas de reporting.monthly_clinic_supply_performance:
    país y moneda por clínica, y rechazo de clínicas con dos países."""
    clinics = resolve_clinic_countries(supply_events)
    rows = assemble_monthly_clinic_rows(
        month_start,
        clinics["countries"],
        total_supply_cost["total_supply_cost"],
        supply_consumption_count,
        critical_stockout_count,
        expiry_risk_count,
    )
    return {
        "rows": rows,
        "rejected": clinics["rejected"],
        "partition_event_counts": clinics["partition_event_counts"],
        "quality": build_quality_summary(
            supply_events, total_supply_cost["missing_cost"], len(clinics["partition_event_counts"])
        ),
    }


@task(name="validate_monthly_aggregates", cache_policy=NONE)
def validate_monthly_aggregates(
    partition_event_counts: Dict[str, Dict[str, int]], domain_activity: Optional[Dict[str, Dict[str, int]]]
) -> Dict[str, Any]:
    validation = evaluate_capture_coverage(partition_event_counts, domain_activity, COVERAGE_WARNING_RATIO)
    if validation["blocking_error"]:
        raise CaptureGapError(validation["blocking_error"])
    return validation


@flow(name="compute-monthly-clinic-supply-kpis")
def compute_monthly_clinic_supply_kpis(
    events: List[Dict[str, Any]],
    month_start: date,
    domain_activity: Optional[Dict[str, Dict[str, int]]] = None,
) -> Dict[str, Any]:
    """Subflow de TRANSFORMACIÓN: de eventos crudos a las filas de KPIs.

    Entrada: los eventos extraídos, el mes y (opcional) la actividad de
    inventario para validar la cobertura.
    Salida (sin DataFrames, serializable): `rows` con los 4 KPIs por clínica,
    `rejected`, `partition_event_counts`, `quality`, `counts`, `validation`
    y el rango de timestamps de la fuente.
    Lanza CaptureGapError si la captura está rota: no hay filas que publicar."""
    prepared = prepare_clinic_supply_events(events, month_start)
    supply_events = prepared["events"]

    total_supply_cost = compute_total_supply_cost(supply_events)
    supply_consumption_count = compute_supply_consumption_count(supply_events)
    critical_stockout_count = compute_critical_stockout_count(supply_events)
    expiry_risk_count = compute_expiry_risk_count(supply_events)

    assembled = assemble_monthly_clinic_supply_performance(
        month_start,
        supply_events,
        total_supply_cost,
        supply_consumption_count,
        critical_stockout_count,
        expiry_risk_count,
    )
    validation = validate_monthly_aggregates(assembled["partition_event_counts"], domain_activity)

    return {
        **assembled,
        "counts": prepared["counts"],
        "source_min_event_timestamp": prepared["source_min_event_timestamp"],
        "source_max_event_timestamp": prepared["source_max_event_timestamp"],
        "validation": validation,
    }


# --- Carga ------------------------------------------------------------------


@task(
    name="upsert_monthly_clinic_supply_performance_rows",
    # 3 reintentos (15s, 60s, 3 min): la carga es una sola transacción, así
    # que un reintento tras un corte parte de cero y no puede duplicar nada.
    # Esperas más largas que en la extracción porque aquí el fallo típico es
    # un lock o una conexión saturada del pooler, que tarda más en liberarse.
    retries=3,
    retry_delay_seconds=[15, 60, 180],
    timeout_seconds=180,
    cache_policy=NONE,  # una carga nunca se da por hecha desde caché
)
def upsert_monthly_clinic_supply_performance_rows(
    run_id: str,
    month_start: date,
    rows: List[Dict[str, Any]],
    rejected: List[Dict[str, Any]],
    partition_event_counts: Dict[str, Dict[str, int]],
) -> Dict[str, int]:
    with Session(_engine()) as session:
        counts = storage.load_monthly_rows(
            session,
            run_id=_uuid(run_id),
            month_start=month_start,
            rows=rows,
            rejected=rejected,
            partition_event_counts=partition_event_counts,
        )
        run_log.update_run(session, _uuid(run_id), phase="loaded")
    _logger().info("load result: %s", counts)
    return counts


@flow(name="load-monthly-clinic-supply-performance")
def load_monthly_clinic_supply_performance(
    run_id: str,
    month_start: date,
    rows: List[Dict[str, Any]],
    rejected: List[Dict[str, Any]],
    partition_event_counts: Dict[str, Dict[str, int]],
) -> Dict[str, int]:
    """Subflow de CARGA idempotente en reporting.monthly_clinic_supply_performance.

    Entrada: el run_id de una corrida existente (cada fila publicada queda
    auditada contra él en pipeline_run_partitions), el mes y la salida de
    compute_monthly_clinic_supply_kpis. Salida: inserted/updated/unchanged/
    removed/rejected."""
    return upsert_monthly_clinic_supply_performance_rows(run_id, month_start, rows, rejected, partition_event_counts)


# --- Paso opcional ----------------------------------------------------------


@task(name="write_supply_performance_eval_snapshot", cache_policy=NONE)
def write_supply_performance_eval_snapshot(
    run_id: str, month_start: date, transformed: Dict[str, Any], load_counts: Dict[str, int]
) -> str:
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
        "coverage": transformed["validation"]["coverage"],
        "warnings": transformed["validation"]["warnings"],
        "rows": [
            {**row, "month_start": row["month_start"].isoformat(), "total_supply_cost": str(row["total_supply_cost"])}
            for row in transformed["rows"]
        ],
    }
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


@flow(name="export-supply-performance-eval-snapshot")
def export_supply_performance_eval_snapshot(
    run_id: str, month_start: date, transformed: Dict[str, Any], load_counts: Dict[str, int]
) -> str:
    """Subflow OPCIONAL: salida de validación en data/eval/. Solo agregados y
    métricas de calidad: ni userId, ni sessionId, ni payloads de eventos.
    El flow principal lo invoca con return_state=True: si falla, la carga ya
    está confirmada y la corrida solo gana un aviso."""
    return write_supply_performance_eval_snapshot(run_id, month_start, transformed, load_counts)


# --- Flow principal ---------------------------------------------------------


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
) -> Dict[str, Any]:
    """Flow principal: coordina los subflows, no contiene lógica de ETL.

    Tolerancia a fallos parciales: extract_domain_activity (dentro del
    subflow de extracción) y el subflow export_supply_performance_eval_snapshot
    se invocan con return_state=True, así que su fallo se inspecciona y la
    corrida sigue con aviso. Los subflows críticos sí propagan: el except
    registra el fallo en reporting.pipeline_runs y lo relanza para que
    Prefect marque Failed."""
    target_month = resolve_month_start(month_start, _utc_today())

    try:
        run_id = start_pipeline_run(target_month, trigger_type, triggered_by, run_id)
    except run_log.WindowLockedError as locked:
        return Cancelled(message=f"window_locked: active run {locked.active_run_id}")

    try:
        extracted = extract_clinic_supply_activity(target_month, run_id)
        transformed = compute_monthly_clinic_supply_kpis(
            extracted["events"], target_month, extracted["domain_activity"]
        )
        load_counts = load_monthly_clinic_supply_performance(
            run_id,
            target_month,
            transformed["rows"],
            transformed["rejected"],
            transformed["partition_event_counts"],
        )

        snapshot_state = export_supply_performance_eval_snapshot(
            run_id, target_month, transformed, load_counts, return_state=True
        )
        warnings = list(transformed["validation"]["warnings"])
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
                    "coverage": transformed["validation"]["coverage"],
                    "clinics_without_events": transformed["validation"]["clinics_without_events"],
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


def main(argv: Optional[List[str]] = None) -> int:
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
