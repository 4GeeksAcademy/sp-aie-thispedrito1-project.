"""Acceso a datos del pipeline: extraccion (solo lectura) y carga idempotente.

Todas las funciones reciben una Session: quien decide el engine (Supabase
real o la SQLite de los tests) es el llamante, no este modulo.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, func
from sqlalchemy.dialects import postgresql, sqlite
from sqlmodel import Session, select

from data.pipelines.monthly_clinic_supply_performance.models import (
    MonthlyClinicSupplyPerformance,
    PipelineRunPartition,
)
from data.process.supply_performance_transforms import KPI_FIELDS, SOURCE_EVENT_TYPES, row_values
from inventory_models import SupplyConsumption, SupplyDelivery
from telemetry_models import TelemetryEventRecord

# --- Extraccion (telemetry_events e inventario, SOLO LECTURA) -------------


def fetch_supply_events(session: Session, window_start: datetime, window_end: datetime) -> list[dict[str, Any]]:
    """PIPELINE_DESIGN.md 2.2: filtro por event_type y rango en SQL (indices
    existentes), refinado despues con Pandas. Solo SELECT: este pipeline
    nunca escribe en telemetry_events.

    Devuelve dicts con timestamp en ISO: son el input de la task de
    transformacion, que Prefect serializa para calcular su clave de cache."""
    statement = (
        select(
            TelemetryEventRecord.id,
            TelemetryEventRecord.timestamp,
            TelemetryEventRecord.event_type,
            TelemetryEventRecord.tags,
        )
        .where(
            TelemetryEventRecord.event_type.in_(SOURCE_EVENT_TYPES),
            TelemetryEventRecord.timestamp >= window_start,
            TelemetryEventRecord.timestamp < window_end,
        )
        .order_by(TelemetryEventRecord.timestamp, TelemetryEventRecord.id)
    )
    return [
        {"id": row_id, "timestamp": timestamp.isoformat(), "event_type": event_type, "tags": tags or {}}
        for row_id, timestamp, event_type, tags in session.exec(statement).all()
    ]


def fetch_domain_activity(session: Session, window_start: datetime, window_end: datetime) -> dict[str, dict[str, int]]:
    """Filas reales de inventario por clinica en la ventana. Solo auditan la
    captura (cobertura), no alimentan ningun KPI."""

    def count_by_clinic(model) -> dict[str, int]:
        statement = (
            select(model.clinic_id, func.count())
            .where(model.created_at >= window_start, model.created_at < window_end)
            .group_by(model.clinic_id)
        )
        return {str(clinic_id): int(count) for clinic_id, count in session.exec(statement).all()}

    return {"deliveries": count_by_clinic(SupplyDelivery), "consumptions": count_by_clinic(SupplyConsumption)}


# --- Carga idempotente ----------------------------------------------------


def _existing_rows(session: Session, month_start: date) -> dict[str, MonthlyClinicSupplyPerformance]:
    rows = session.exec(
        select(MonthlyClinicSupplyPerformance).where(MonthlyClinicSupplyPerformance.month_start == month_start)
    ).all()
    return {row.clinic_id: row for row in rows}


def _upsert_statement(dialect_name: str, values: dict[str, Any]):
    """INSERT ... ON CONFLICT (clinic_id, month_start) DO UPDATE, apoyado en
    el unique del CONTEXT. Postgres (Supabase) y SQLite (tests) comparten la
    misma sintaxis de ON CONFLICT en SQLAlchemy, cada uno con su dialecto."""
    table = MonthlyClinicSupplyPerformance.__table__
    insert = postgresql.insert if dialect_name == "postgresql" else sqlite.insert
    statement = insert(table).values(**values)
    return statement.on_conflict_do_update(
        index_elements=["clinic_id", "month_start"],
        set_={field: statement.excluded[field] for field in (*KPI_FIELDS, "computed_at")},
    )


def load_monthly_rows(
    session: Session,
    *,
    run_id: uuid.UUID,
    month_start: date,
    rows: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    partition_event_counts: dict[str, dict[str, int]],
) -> dict[str, int]:
    """Carga de un mes completo en UNA transaccion (PIPELINE_DESIGN.md 3.1).

    1. Lee lo ya publicado para ese mes.
    2. Por cada fila calculada: igual -> unchanged (no se toca computed_at);
       distinta o nueva -> upsert ON CONFLICT (clinic_id, month_start).
    3. Filas publicadas que ya no salen del calculo -> se borran.
    4. Registra en pipeline_run_partitions que paso con cada particion, con
       valores anteriores y nuevos.

    Si algo falla antes del commit, rollback: no queda ninguna fila a medias.
    Repetir la carga con los mismos rows deja la tabla identica y marca todo
    como unchanged — eso es la idempotencia que exige el hito."""
    dialect_name = session.get_bind().dialect.name
    now = datetime.now(timezone.utc)
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "removed": 0, "rejected": len(rejected)}

    try:
        existing = _existing_rows(session, month_start)
        audit: list[PipelineRunPartition] = []

        for row in rows:
            clinic_id = row["clinic_id"]
            new_values = row_values(row)
            previous = existing.pop(clinic_id, None)
            previous_values = row_values(previous.model_dump()) if previous is not None else None

            if previous_values == new_values:
                action = "unchanged"
            else:
                action = "updated" if previous is not None else "inserted"
                session.exec(
                    _upsert_statement(
                        dialect_name,
                        {
                            "id": uuid.uuid4(),
                            "clinic_id": clinic_id,
                            "month_start": month_start,
                            "country": row["country"],
                            "total_supply_cost": row["total_supply_cost"],
                            "supply_consumption_count": row["supply_consumption_count"],
                            "critical_stockout_count": row["critical_stockout_count"],
                            "expiry_risk_count": row["expiry_risk_count"],
                            "currency": row["currency"],
                            "computed_at": now,
                        },
                    )
                )
            counts[action] += 1
            audit.append(
                PipelineRunPartition(
                    run_id=run_id,
                    clinic_id=clinic_id,
                    month_start=month_start,
                    action=action,
                    previous_values=previous_values,
                    new_values=new_values,
                    source_event_counts=partition_event_counts.get(clinic_id, {}),
                )
            )

        for clinic_id, stale in existing.items():
            # Solo una particion que el calculo nuevo no produce: rechazada
            # (p. ej. mixed_country) o sin eventos tras deduplicar. Un numero
            # publicado nunca desaparece sin dejar rastro.
            session.exec(delete(MonthlyClinicSupplyPerformance).where(MonthlyClinicSupplyPerformance.id == stale.id))
            counts["removed"] += 1
            audit.append(
                PipelineRunPartition(
                    run_id=run_id,
                    clinic_id=clinic_id,
                    month_start=month_start,
                    action="removed",
                    reason="not_in_recomputed_window",
                    previous_values=row_values(stale.model_dump()),
                    new_values=None,
                    source_event_counts=partition_event_counts.get(clinic_id, {}),
                )
            )

        for item in rejected:
            if any(entry.clinic_id == item["clinic_id"] for entry in audit):
                continue  # ya auditada como removed arriba
            audit.append(
                PipelineRunPartition(
                    run_id=run_id,
                    clinic_id=item["clinic_id"],
                    month_start=month_start,
                    action="rejected",
                    reason=item["reason"],
                    previous_values=None,
                    new_values={"countries": item.get("countries", [])},
                    source_event_counts=partition_event_counts.get(item["clinic_id"], {}),
                )
            )
        # Una particion rechazada que estaba publicada queda como "removed"
        # con motivo del rechazo, no como dos filas de auditoria.
        rejected_reasons = {item["clinic_id"]: item["reason"] for item in rejected}
        for entry in audit:
            if entry.action == "removed" and entry.clinic_id in rejected_reasons:
                entry.reason = rejected_reasons[entry.clinic_id]

        session.add_all(audit)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return counts


def get_published_rows(session: Session, month_start: date) -> list[MonthlyClinicSupplyPerformance]:
    return list(
        session.exec(
            select(MonthlyClinicSupplyPerformance)
            .where(MonthlyClinicSupplyPerformance.month_start == month_start)
            .order_by(MonthlyClinicSupplyPerformance.country, MonthlyClinicSupplyPerformance.clinic_id)
        ).all()
    )


def get_latest_month_start(session: Session) -> Optional[date]:
    return session.exec(select(func.max(MonthlyClinicSupplyPerformance.month_start))).one()
