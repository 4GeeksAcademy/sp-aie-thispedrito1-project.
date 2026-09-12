"""Transformaciones del pipeline monthly_clinic_supply_performance.

Pandas puro: sin base de datos ni Prefect, para poder testearlas con datos
escritos a mano y reutilizarlas en backfills. Implementa las reglas de
data/pipelines/PIPELINE_DESIGN.md seccion 2.4 con las definiciones LITERALES
de la seccion 4 del CONTEXT:

- total_supply_cost        = suma de los costos de inbound_order_created del mes
- supply_consumption_count = conteo de outbound_order_created del mes
- critical_stockout_count  = conteo de stock_threshold_triggered del mes
- expiry_risk_count        = conteo de supply_expiry_flagged del mes

Los conteos son de eventos DEDUPLICADOS (un mismo hecho reenviado no cuenta
dos veces). Que "conteo de eventos" signifique "veces que cayo bajo minimo"
y "lotes marcados" lo garantiza la captura, no esta capa: ver
_check_stock_threshold (routes/inventory.py) e inventory_alerts.py.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

import pandas as pd

INBOUND = "inbound_order_created"
OUTBOUND = "outbound_order_created"
STOCKOUT = "stock_threshold_triggered"
EXPIRY = "supply_expiry_flagged"
SOURCE_EVENT_TYPES = (INBOUND, OUTBOUND, STOCKOUT, EXPIRY)

CURRENCY_BY_COUNTRY = {"US": "USD", "UK": "GBP"}
CLINIC_ID_MIN, CLINIC_ID_MAX = 1, 12

KPI_FIELDS = (
    "country",
    "total_supply_cost",
    "supply_consumption_count",
    "critical_stockout_count",
    "expiry_risk_count",
    "currency",
)

# Unicas propiedades de `tags` que salen de la extraccion. userId/requestId y
# el resto del payload se descartan aqui mismo (minimizacion, seccion 7 del
# diseno); sessionId se queda solo para contar sesiones activas.
_KEPT_TAGS = (
    "eventId",
    "sessionId",
    "clinic_id",
    "country",
    "quantity",
    "unit_cost",
    "delivery_id",
    "consumption_id",
)

_CENT = Decimal("0.01")


# --- Ventanas mensuales ---------------------------------------------------


def first_day_of_month(value: date) -> date:
    return value.replace(day=1)


def add_months(month_start: date, months: int) -> date:
    index = month_start.year * 12 + (month_start.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def resolve_month_start(requested: Optional[date], today: date) -> date:
    """Mes a procesar. Sin valor: el ultimo mes CERRADO (el anterior a hoy).

    Rechaza fechas que no son dia 1 y el mes en curso o futuros: un mes a
    medias en el paquete de la junta se leeria como un mes completo con
    malos resultados (PIPELINE_DESIGN.md, 3.2)."""
    current_month = first_day_of_month(today)
    if requested is None:
        return add_months(current_month, -1)
    if requested.day != 1:
        raise ValueError("month_start must be the first day of a month (YYYY-MM-01).")
    if requested >= current_month:
        raise ValueError("month_start must be a closed month (before the current month).")
    return requested


def month_window(month_start: date) -> tuple[datetime, datetime]:
    """[inicio, fin) en UTC, naive: telemetry_events.timestamp es
    `timestamp without time zone` guardado en UTC."""
    end = add_months(month_start, 1)
    return (
        datetime(month_start.year, month_start.month, 1),
        datetime(end.year, end.month, 1),
    )


# --- Paso 1: aplanar ------------------------------------------------------


def events_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Filas crudas de telemetry_events -> DataFrame plano con solo las
    columnas que necesitan los KPIs. `timestamp` a UTC antes de cualquier
    agrupacion, igual que services/telemetry/analysis.py."""
    records = []
    for row in rows:
        tags = row.get("tags") or {}
        record = {"id": row["id"], "timestamp": row["timestamp"], "event_type": row["event_type"]}
        for key in _KEPT_TAGS:
            record[key] = tags.get(key)
        records.append(record)

    columns = ["id", "timestamp", "event_type", *_KEPT_TAGS]
    # dtype=object a proposito: con la inferencia por defecto, una columna de
    # enteros con algun None (p. ej. unit_cost ausente, delivery_id solo en
    # entradas) pasa a float64 y clinic_id=1 llega como 1.0, que la
    # validacion rechazaria. Solo timestamp se convierte a fecha.
    frame = pd.DataFrame(records, columns=columns, dtype=object)
    # format="ISO8601" a proposito: datetime.isoformat() omite los
    # microsegundos cuando valen 0, asi que la misma columna mezcla
    # "10:00:00" y "10:00:00.519000". Sin esto Pandas 2 infiere el formato de
    # la primera fila y revienta con las demas. Bug real: lo encontro la
    # primera ejecucion contra Supabase, no los tests con horas "limpias".
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="ISO8601")
    return frame


# --- Paso 2: validar ------------------------------------------------------


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _row_is_valid(row: pd.Series) -> bool:
    if row["event_type"] not in SOURCE_EVENT_TYPES:
        return False
    if not _is_int(row["clinic_id"]) or not CLINIC_ID_MIN <= row["clinic_id"] <= CLINIC_ID_MAX:
        return False
    if row["country"] not in CURRENCY_BY_COUNTRY:
        return False
    if row["event_type"] in (INBOUND, OUTBOUND):
        if not _is_int(row["quantity"]) or row["quantity"] <= 0:
            return False
    if row["event_type"] == INBOUND and row["unit_cost"] is not None:
        if not _is_number(row["unit_cost"]) or row["unit_cost"] < 0:
            return False
    return True


def validate_events(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Excluye filas que no cumplen el esquema minimo y cuenta cuantas. Una
    fila invalida no tumba la corrida (rechazo parcial, como POST
    /telemetry/events)."""
    if frame.empty:
        return frame, 0
    mask = frame.apply(_row_is_valid, axis=1).astype(bool)
    return frame[mask].copy(), int((~mask).sum())


# --- Paso 3: deduplicar ---------------------------------------------------


def deduplicate_events(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Dos niveles (PIPELINE_DESIGN.md, 3.1):

    1. eventId: el cliente reintenta el mismo sobre; POST /telemetry/events
       no deduplica y lo guarda dos veces.
    2. delivery_id / consumption_id: la misma orden real emitida dos veces
       (cada track() genera un eventId nuevo).

    Se conserva la primera ocurrencia por (timestamp, id): orden determinista,
    asi dos corridas sobre los mismos datos eligen siempre la misma fila."""
    if frame.empty:
        return frame, 0
    before = len(frame)
    ordered = frame.sort_values(["timestamp", "id"], kind="mergesort")

    with_event_id = ordered[ordered["eventId"].notna()].drop_duplicates(subset=["eventId"], keep="first")
    ordered = pd.concat([with_event_id, ordered[ordered["eventId"].isna()]]).sort_values(
        ["timestamp", "id"], kind="mergesort"
    )

    for event_type, natural_key in ((INBOUND, "delivery_id"), (OUTBOUND, "consumption_id")):
        is_type_with_key = (ordered["event_type"] == event_type) & ordered[natural_key].notna()
        keyed = ordered[is_type_with_key].drop_duplicates(subset=[natural_key], keep="first")
        ordered = pd.concat([ordered[~is_type_with_key], keyed]).sort_values(["timestamp", "id"], kind="mergesort")

    return ordered.reset_index(drop=True), before - len(ordered)


# --- Paso 4: agregar ------------------------------------------------------


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def aggregate_monthly_clinic_metrics(frame: pd.DataFrame, month_start: date) -> dict[str, Any]:
    """Una fila por clinic_id con los 4 KPIs + currency.

    - Coste en Decimal (nunca float al sumar dinero), redondeado a centimos.
      Entradas sin unit_cost se excluyen de la suma y se cuentan aparte:
      coste desconocido no es coste cero.
    - Solo clinicas con al menos un evento generan fila: sin catalogo no se
      conoce el pais (ni la moneda) de una clinica sin actividad capturada.
    - Una clinica con eventos de mas de un pais en el mes se rechaza entera:
      nunca se mezclan USD y GBP en una fila."""
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    partition_event_counts: dict[str, dict[str, int]] = {}
    missing_cost_by_clinic: dict[str, int] = {}

    if not frame.empty:
        for clinic_id, clinic_events in frame.groupby("clinic_id", sort=True):
            clinic_key = str(int(clinic_id))
            counts = {event_type: int((clinic_events["event_type"] == event_type).sum()) for event_type in SOURCE_EVENT_TYPES}
            partition_event_counts[clinic_key] = counts

            countries = sorted(set(clinic_events["country"]))
            if len(countries) != 1:
                rejected.append({"clinic_id": clinic_key, "reason": "mixed_country", "countries": countries})
                continue
            country = countries[0]

            inbound = clinic_events[clinic_events["event_type"] == INBOUND]
            with_cost = inbound[inbound["unit_cost"].notna()]
            total_cost = sum(
                (Decimal(int(q)) * Decimal(str(c)) for q, c in zip(with_cost["quantity"], with_cost["unit_cost"])),
                Decimal("0"),
            )
            missing = len(inbound) - len(with_cost)
            if missing:
                missing_cost_by_clinic[clinic_key] = missing

            rows.append(
                {
                    "clinic_id": clinic_key,
                    "country": country,
                    "month_start": month_start,
                    "total_supply_cost": _money(total_cost),
                    "supply_consumption_count": counts[OUTBOUND],
                    "critical_stockout_count": counts[STOCKOUT],
                    "expiry_risk_count": counts[EXPIRY],
                    "currency": CURRENCY_BY_COUNTRY[country],
                }
            )

    rows.sort(key=lambda row: int(row["clinic_id"]))
    return {
        "rows": rows,
        "rejected": rejected,
        "partition_event_counts": partition_event_counts,
        "quality": {
            "inbound_events_missing_cost": missing_cost_by_clinic,
            "events_per_day": events_per_day(frame),
            "active_sessions": int(frame["sessionId"].nunique()) if not frame.empty else 0,
            "reporting_clinics": len(partition_event_counts),
        },
    }


def events_per_day(frame: pd.DataFrame) -> dict[str, dict[str, int]]:
    """Histograma diario por tipo: donde se ve un hueco o una rafaga de
    captura dentro del mes (PIPELINE_DESIGN.md, 3.4)."""
    result: dict[str, dict[str, int]] = defaultdict(dict)
    if frame.empty:
        return {}
    days = frame["timestamp"].dt.strftime("%Y-%m-%d")
    for (day, event_type), count in frame.groupby([days, frame["event_type"]]).size().items():
        result[day][event_type] = int(count)
    return dict(sorted(result.items()))


def transform_supply_events(rows: list[dict[str, Any]], month_start: date) -> dict[str, Any]:
    """Pasos 1-4 encadenados. Es lo que ejecuta la task de transformacion."""
    frame = events_to_frame(rows)
    window_start, window_end = month_window(month_start)
    in_window = (frame["timestamp"] >= pd.Timestamp(window_start, tz=timezone.utc)) & (
        frame["timestamp"] < pd.Timestamp(window_end, tz=timezone.utc)
    )
    frame = frame[in_window]

    valid, rows_invalid = validate_events(frame)
    deduplicated, duplicates_dropped = deduplicate_events(valid)
    result = aggregate_monthly_clinic_metrics(deduplicated, month_start)

    timestamps = deduplicated["timestamp"] if not deduplicated.empty else pd.Series([], dtype="datetime64[ns, UTC]")
    result["counts"] = {
        "rows_extracted": len(rows),
        "rows_invalid": rows_invalid,
        "duplicates_dropped": duplicates_dropped,
        "rows_after_dedup": len(deduplicated),
        "events_by_type": {t: int((deduplicated["event_type"] == t).sum()) if not deduplicated.empty else 0 for t in SOURCE_EVENT_TYPES},
    }
    result["source_min_event_timestamp"] = timestamps.min().to_pydatetime() if len(timestamps) else None
    result["source_max_event_timestamp"] = timestamps.max().to_pydatetime() if len(timestamps) else None
    return result


# --- Validacion de calidad (cobertura contra dominio) ---------------------


def evaluate_capture_coverage(
    partition_event_counts: dict[str, dict[str, int]],
    domain_activity: Optional[dict[str, dict[str, int]]],
    coverage_warning_ratio: float,
) -> dict[str, Any]:
    """Compara eventos deduplicados con filas reales de inventario
    (supply_deliveries / supply_consumptions) por clinica. No alimenta
    ningun KPI: decide si los numeros son publicables.

    - blocking_error: cero eventos de entrada/salida en todo el mes mientras
      el dominio SI tiene filas -> la captura esta rota (el bug del
      2026-08-21 habria dado un informe de ceros verosimiles).
    - warnings: clinicas con cobertura < coverage_warning_ratio o con
      actividad de dominio y ningun evento.
    - domain_activity None = la extraccion de dominio fallo (task no
      critica): se publica igual, con aviso de cobertura no verificada."""
    if domain_activity is None:
        return {"blocking_error": None, "warnings": ["coverage_unavailable"], "coverage": None, "clinics_without_events": []}

    deliveries = domain_activity.get("deliveries", {})
    consumptions = domain_activity.get("consumptions", {})
    total_domain_rows = sum(deliveries.values()) + sum(consumptions.values())
    total_order_events = sum(c.get(INBOUND, 0) + c.get(OUTBOUND, 0) for c in partition_event_counts.values())

    if total_order_events == 0 and total_domain_rows > 0:
        return {
            "blocking_error": (
                f"capture_gap: 0 order events in telemetry_events but {total_domain_rows} inventory rows in the window"
            ),
            "warnings": [],
            "coverage": None,
            "clinics_without_events": sorted(set(deliveries) | set(consumptions), key=int),
        }

    coverage: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    clinics_without_events: list[str] = []
    for clinic_id in sorted(set(deliveries) | set(consumptions) | set(partition_event_counts), key=int):
        events = partition_event_counts.get(clinic_id, {})
        clinic_coverage: dict[str, Any] = {}
        for label, event_type, domain in (("inbound", INBOUND, deliveries), ("outbound", OUTBOUND, consumptions)):
            domain_rows = domain.get(clinic_id, 0)
            event_count = events.get(event_type, 0)
            ratio = round(event_count / domain_rows, 3) if domain_rows else None
            clinic_coverage[label] = {"events": event_count, "domain_rows": domain_rows, "capture_ratio": ratio}
            if ratio is not None and ratio < coverage_warning_ratio:
                warnings.append(f"low_capture_ratio:{clinic_id}:{label}")
        coverage[clinic_id] = clinic_coverage
        if clinic_id not in partition_event_counts:
            clinics_without_events.append(clinic_id)

    return {
        "blocking_error": None,
        "warnings": warnings,
        "coverage": coverage,
        "clinics_without_events": clinics_without_events,
    }


def row_values(row: dict[str, Any]) -> dict[str, Any]:
    """Valores KPI de una fila en forma comparable y serializable a JSON
    (para detectar cambios y guardarlos en pipeline_run_partitions)."""
    values = {field: row[field] for field in KPI_FIELDS}
    values["total_supply_cost"] = str(_money(Decimal(str(values["total_supply_cost"]))))
    values["supply_consumption_count"] = int(values["supply_consumption_count"])
    values["critical_stockout_count"] = int(values["critical_stockout_count"])
    values["expiry_risk_count"] = int(values["expiry_risk_count"])
    return values
