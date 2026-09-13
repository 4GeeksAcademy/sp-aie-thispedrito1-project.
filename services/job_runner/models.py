"""Tabla job_runs: log de ejecuciones de los jobs programados (Ticket #DEV-53).

Capa de ORQUESTACIÓN nocturna. No confundir con reporting.pipeline_runs
(data/pipelines/monthly_clinic_supply_performance/models.py), que registra
las fases internas del ETL: el job nocturno escribe aquí; el pipeline que
lanza como subproceso escribe allí durante su propia ejecución.

El DDL de Postgres vive en migrations/001_create_job_runs.sql y es la fuente
de verdad en Supabase. Este modelo describe la misma tabla para el ORM y
crea la versión SQLite que usan los tests.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Text, text
from sqlmodel import Field, SQLModel

JOB_STATUSES = ("pending", "processing", "completed", "failed")
TERMINAL_STATUSES = ("completed", "failed")


class JobRun(SQLModel, table=True):
    """Una fila por ejecución: pending -> processing -> completed | failed."""

    __tablename__ = "job_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'processing', 'completed', 'failed')",
            name="ck_job_runs_status",
        ),
        # Consultas de idempotencia: "¿hay un completed para (job, día)?".
        Index("ix_job_runs_job_name_target_date", "job_name", "target_date"),
        # El estado `processing` ES el lock. Este índice no añade otro
        # mecanismo: solo hace que la transición a `processing` sea atómica,
        # para que dos instancias que arrancan a la vez no puedan ganar las dos.
        Index(
            "uq_job_runs_single_processing",
            "job_name",
            unique=True,
            postgresql_where=text("status = 'processing'"),
            sqlite_where=text("status = 'processing'"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    job_name: str = Field(max_length=100)
    target_date: date
    status: str = Field(max_length=20)
    started_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    finished_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    # Detalle del resultado, más allá de los campos mínimos del ticket.
    # rows_exported es None cuando el CSV ya existía y no se reescribió.
    rows_exported: Optional[int] = None
    pipeline_exit_code: Optional[int] = None
