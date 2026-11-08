"""Tablas del cierre financiero en Supabase (mismo `SQLModel.metadata`).

- Fuentes ingeridas por periodo (`fc_ledger_entries`, `fc_budget_lines`,
  `fc_operation_metrics`, `fc_ingestions`), cada fila con su origen
  (`source_file` + `source_row`). Una descripción con datos sensibles nunca se
  guarda: `description` queda en NULL y solo se conserva su huella.
- Informes versionados (`fc_reports`): cifras estructuradas (`figures`), los
  dos textos (inglés y español) y la trazabilidad. Un rechazo no modifica el
  informe: genera la versión siguiente (`supersedes_id`).
- Revisión, distribución y avisos (`fc_review_decisions`, `fc_distributions`,
  `fc_notifications`) y el registro de acciones (`fc_agent_actions`).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import Column, Date, DateTime, Numeric, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from services.audit.models import AuditLogEntry
from telemetry_models import _JSONBOrJSON

REPORT_PENDING = "pending_review"
REPORT_APPROVED = "approved"
REPORT_REJECTED = "rejected"
REPORT_DISTRIBUTED = "distributed"
REPORT_STATUSES = (REPORT_PENDING, REPORT_APPROVED, REPORT_REJECTED, REPORT_DISTRIBUTED)

TRIGGER_SCHEDULED = "scheduled"
TRIGGER_MANUAL = "manual"
TRIGGER_REVISION = "revision"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json() -> Column:
    return Column(_JSONBOrJSON, nullable=True)


def _ts(nullable: bool = False) -> Column:
    return Column(DateTime(timezone=True), nullable=nullable)


def _money(nullable: bool = True) -> Column:
    return Column(Numeric(14, 2), nullable=nullable)


class FcStaff(SQLModel, table=True):
    """Directorio de personas del cierre (rol ORGANIZATIVO, no el admin de la plataforma)."""

    __tablename__ = "fc_staff"

    user_id: str = Field(primary_key=True, max_length=64)
    display_name: str = Field(max_length=120)
    org_role: str = Field(max_length=40, index=True)


class FcIngestion(SQLModel, table=True):
    __tablename__ = "fc_ingestions"

    period: date = Field(sa_column=Column(Date, primary_key=True))
    fingerprint: str = Field(max_length=64)
    missing_files: Optional[list] = Field(default=None, sa_column=_json())
    ingested_at: datetime = Field(default_factory=utcnow, sa_column=_ts())


class FcLedgerEntry(SQLModel, table=True):
    __tablename__ = "fc_ledger_entries"
    __table_args__ = (UniqueConstraint("period", "source_row", name="uq_fc_ledger_row"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    period: date = Field(sa_column=Column(Date, nullable=False, index=True))
    entry_id: str = Field(max_length=40, index=True)
    entry_date: Optional[date] = Field(default=None, sa_column=Column(Date, nullable=True))
    country: str = Field(max_length=8)
    payer: str = Field(max_length=40)
    category: str = Field(max_length=48)
    currency: str = Field(max_length=3)
    amount: Optional[Decimal] = Field(default=None, sa_column=_money())
    source_file: str = Field(max_length=40)
    source_row: int
    source_ref: str = Field(max_length=64)
    description: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    description_sha256: str = Field(max_length=64)
    status: str = Field(max_length=16)
    issues: Optional[list] = Field(default=None, sa_column=_json())
    sensitive_categories: Optional[list] = Field(default=None, sa_column=_json())
    quantity: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(14, 4), nullable=True))
    unit_price: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(14, 4), nullable=True))


class FcBudgetLine(SQLModel, table=True):
    __tablename__ = "fc_budget_lines"
    __table_args__ = (UniqueConstraint("period", "source_row", name="uq_fc_budget_row"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    period: date = Field(sa_column=Column(Date, nullable=False, index=True))
    country: str = Field(max_length=8)
    payer: str = Field(max_length=40)
    category: str = Field(max_length=48)
    currency: str = Field(max_length=3)
    amount: Optional[Decimal] = Field(default=None, sa_column=_money())
    source_row: int
    status: str = Field(max_length=16)
    issues: Optional[list] = Field(default=None, sa_column=_json())


class FcOperationMetric(SQLModel, table=True):
    __tablename__ = "fc_operation_metrics"
    __table_args__ = (UniqueConstraint("period", "source_row", name="uq_fc_operation_row"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    period: date = Field(sa_column=Column(Date, nullable=False, index=True))
    country: str = Field(max_length=8)
    payer: str = Field(max_length=40)
    metric: str = Field(max_length=64)
    value: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(14, 4), nullable=True))
    source_row: int
    status: str = Field(max_length=16)
    issues: Optional[list] = Field(default=None, sa_column=_json())


class FcReport(SQLModel, table=True):
    __tablename__ = "fc_reports"
    __table_args__ = (UniqueConstraint("period", "version", name="uq_fc_report_version"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    period: date = Field(sa_column=Column(Date, nullable=False, index=True))
    version: int
    status: str = Field(max_length=24, index=True)
    trigger: str = Field(max_length=16)
    supersedes_id: Optional[uuid.UUID] = Field(default=None)
    data_fingerprint: str = Field(max_length=64)
    figures_sha256: str = Field(max_length=64)
    narrative_source: str = Field(max_length=16)  # model | template | mixed
    figures: Optional[dict] = Field(default=None, sa_column=_json())
    trace: Optional[dict] = Field(default=None, sa_column=_json())
    quality: Optional[dict] = Field(default=None, sa_column=_json())
    report_en: str = Field(sa_column=Column(Text, nullable=False))
    report_es: str = Field(sa_column=Column(Text, nullable=False))
    request_id: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())
    decided_at: Optional[datetime] = Field(default=None, sa_column=_ts(nullable=True))
    distributed_at: Optional[datetime] = Field(default=None, sa_column=_ts(nullable=True))


class FcReviewDecision(SQLModel, table=True):
    __tablename__ = "fc_review_decisions"

    id: Optional[int] = Field(default=None, primary_key=True)
    report_id: uuid.UUID = Field(index=True)
    decision: str = Field(max_length=16)  # approve | reject
    comments: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))  # ya validados sin PHI
    decided_by: str = Field(max_length=64)  # human:<seudónimo>
    decided_at: datetime = Field(default_factory=utcnow, sa_column=_ts())


class FcDistribution(SQLModel, table=True):
    """Bandeja interna: una fila por destinatario del BRIEFING (sin correo real)."""

    __tablename__ = "fc_distributions"
    __table_args__ = (UniqueConstraint("report_id", "recipient_role", name="uq_fc_distribution_recipient"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    report_id: uuid.UUID = Field(index=True)
    recipient_role: str = Field(max_length=40)
    recipient_name: str = Field(max_length=120)
    delivered_at: datetime = Field(default_factory=utcnow, sa_column=_ts())


class FcNotification(SQLModel, table=True):
    """Avisos internos: a Tom (informe pendiente) y a Claire (registro con datos
    de paciente, solo con su entry_id)."""

    __tablename__ = "fc_notifications"

    id: Optional[int] = Field(default=None, primary_key=True)
    notification_key: str = Field(max_length=120, unique=True)
    recipient_role: str = Field(max_length=40, index=True)
    kind: str = Field(max_length=40)
    period: date = Field(sa_column=Column(Date, nullable=False))
    report_id: Optional[uuid.UUID] = Field(default=None)
    entry_id: Optional[str] = Field(default=None, max_length=40)
    summary: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())


class FcAgentAction(SQLModel, table=True):
    """Cada acción del agente (actor `process:agent:financial-close`) y cada
    decisión humana (`human:<seudónimo>`). `details` solo lleva
    identificadores, recuentos y huellas: nunca descripciones ni prompts."""

    __tablename__ = "fc_agent_actions"

    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: str = Field(max_length=64, index=True)
    actor: str = Field(max_length=64, index=True)
    actor_type: str = Field(max_length=24)  # automated_process | human
    action: str = Field(max_length=48)
    tool: Optional[str] = Field(default=None, max_length=64)
    outcome: str = Field(max_length=24)
    details: Optional[dict] = Field(default=None, sa_column=_json())
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())


FC_TABLES: List = [
    FcStaff.__table__,
    FcIngestion.__table__,
    FcLedgerEntry.__table__,
    FcBudgetLine.__table__,
    FcOperationMetric.__table__,
    FcReport.__table__,
    FcReviewDecision.__table__,
    FcDistribution.__table__,
    FcNotification.__table__,
    FcAgentAction.__table__,
    # Registro de Auditoría: la preparación del informe por el agente se audita.
    AuditLogEntry.__table__,
]
