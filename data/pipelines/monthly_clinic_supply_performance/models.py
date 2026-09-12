"""Tablas del esquema `reporting` (data/pipelines/PIPELINE_DESIGN.md, 2.6).

Registradas en el mismo `SQLModel.metadata` que inventario y telemetria: el
startup de la API y la fixture de tests las crean con el resto. Postgres
necesita que el esquema exista antes (ver `ensure_reporting_schema`).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Column, DateTime, Index, Integer, Numeric, UniqueConstraint, text
from sqlalchemy.engine import Engine
from sqlmodel import Field, SQLModel

from telemetry_models import _JSONBOrJSON

REPORTING_SCHEMA = "reporting"
PIPELINE_NAME = "monthly_clinic_supply_performance"

ACTIVE_RUN_STATUSES = ("queued", "running")


class MonthlyClinicSupplyPerformance(SQLModel, table=True):
    """Tabla de destino, exactamente la de la seccion 5 del CONTEXT. Una fila
    por clinic_id por mes calendario; el unique es el que sostiene el upsert."""

    __tablename__ = "monthly_clinic_supply_performance"
    __table_args__ = (
        UniqueConstraint("clinic_id", "month_start"),
        {"schema": REPORTING_SCHEMA},
    )

    # En Supabase la columna tiene `default gen_random_uuid()` (DDL literal
    # del CONTEXT, abajo). El default de Python existe para la SQLite de los
    # tests, que no tiene esa funcion — mismo criterio que TelemetryEventRecord.
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    clinic_id: str
    country: str
    month_start: date
    total_supply_cost: Decimal = Field(
        default=Decimal("0"), sa_column=Column(Numeric, nullable=False, server_default="0")
    )
    supply_consumption_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    critical_stockout_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    expiry_risk_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    currency: str
    computed_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class PipelineRun(SQLModel, table=True):
    """Log de ejecucion (PIPELINE_DESIGN.md, 3.3). Una fila por corrida."""

    __tablename__ = "pipeline_runs"
    __table_args__ = (
        # Lock por ventana: no puede haber dos corridas activas del mismo mes.
        # Indice unico PARCIAL: las corridas terminadas del mismo mes no chocan.
        Index(
            "uq_pipeline_runs_active_window",
            "pipeline_name",
            "window_start",
            unique=True,
            postgresql_where=text("status in ('queued', 'running')"),
            sqlite_where=text("status in ('queued', 'running')"),
        ),
        Index("ix_pipeline_runs_latest", "pipeline_name", "queued_at"),
        {"schema": REPORTING_SCHEMA},
    )

    run_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    pipeline_name: str = Field(default=PIPELINE_NAME)
    prefect_flow_run_id: Optional[str] = None
    trigger_type: str  # scheduled | manual | backfill | reconciliation | cli
    triggered_by: Optional[str] = None  # user_uuid (TinyDB) en disparos manuales; nunca email
    window_start: date
    window_end: date  # exclusivo
    status: str  # queued | running | completed | completed_with_warnings | failed | crashed | cancelled
    phase: str  # queued | started | extracted | loaded | finished
    queued_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    started_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    heartbeat_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    finished_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    rows_extracted: Optional[int] = None
    duplicates_dropped: Optional[int] = None
    rows_invalid: Optional[int] = None
    partitions_inserted: Optional[int] = None
    partitions_updated: Optional[int] = None
    partitions_unchanged: Optional[int] = None
    partitions_removed: Optional[int] = None
    partitions_rejected: Optional[int] = None
    source_min_event_timestamp: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    source_max_event_timestamp: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    quality_checks: dict[str, Any] = Field(default_factory=dict, sa_column=Column(_JSONBOrJSON, nullable=False))
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    code_version: Optional[str] = None


class PipelineRunPartition(SQLModel, table=True):
    """Rastro por particion (clinica, mes) de cada corrida: que paso con la
    fila publicada y con que valores, para auditar recalculos por eventos
    tardios sin perder el numero anterior."""

    __tablename__ = "pipeline_run_partitions"
    __table_args__ = {"schema": REPORTING_SCHEMA}

    run_id: uuid.UUID = Field(foreign_key=f"{REPORTING_SCHEMA}.pipeline_runs.run_id", primary_key=True)
    clinic_id: str = Field(primary_key=True)
    month_start: date = Field(primary_key=True)
    action: str  # inserted | updated | unchanged | removed | rejected
    reason: Optional[str] = None
    previous_values: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(_JSONBOrJSON))
    new_values: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(_JSONBOrJSON))
    source_event_counts: dict[str, Any] = Field(default_factory=dict, sa_column=Column(_JSONBOrJSON, nullable=False))


# DDL literal de la seccion 5 del CONTEXT (solo anadido `if not exists`). En
# Postgres se ejecuta antes de create_all, que ve la tabla ya creada y la
# salta: asi Supabase tiene la definicion exacta, gen_random_uuid() incluido.
_CONTEXT_TABLE_DDL = """
create table if not exists reporting.monthly_clinic_supply_performance (
  id uuid primary key default gen_random_uuid(),
  clinic_id text not null,
  country text not null,
  month_start date not null,
  total_supply_cost numeric not null default 0,
  supply_consumption_count integer not null default 0,
  critical_stockout_count integer not null default 0,
  expiry_risk_count integer not null default 0,
  currency text not null,
  computed_at timestamptz not null default now(),
  unique (clinic_id, month_start)
)
"""

REPORTING_TABLES = (
    MonthlyClinicSupplyPerformance.__table__,
    PipelineRun.__table__,
    PipelineRunPartition.__table__,
)


def ensure_reporting_schema(engine: Engine) -> None:
    """Crea el esquema `reporting` y sus tablas si faltan. Idempotente.

    Postgres: esquema + DDL literal del CONTEXT + el resto via metadata.
    SQLite (tests): no hay esquemas con nombre; la fixture adjunta una base
    en memoria llamada `reporting` y aqui solo se crean las tablas."""
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text(f"create schema if not exists {REPORTING_SCHEMA}"))
            connection.execute(text(_CONTEXT_TABLE_DDL))
    SQLModel.metadata.create_all(engine, tables=list(REPORTING_TABLES))
