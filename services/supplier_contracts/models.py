"""Tablas de proveedores y contratos en Supabase (mismo `SQLModel.metadata`).

Lo que NO hay es tan importante como lo que hay: ninguna columna guarda el
texto completo de un contrato, datos bancarios ni datos de contacto. De un
contrato solo quedan los términos extraídos, cada uno con su cita (ya
enmascarada), su cláusula y su página.

Todo registro de origen lleva su referencia (`source_file` + `source_row`, o
`contract_id` + `clause` + `page`): es lo que permite que cada cifra de una
respuesta o de un brief se rastree hasta su origen.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional

from sqlalchemy import Column, Date, DateTime, Numeric, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from telemetry_models import _JSONBOrJSON

TERM_OK = "ok"
TERM_DOUBTFUL = "doubtful"
TERM_MISSING = "missing"

DRAFT_PENDING = "pending_approval"
DRAFT_APPROVED = "approved"
DRAFT_REJECTED = "rejected"
DRAFT_SENT = "sent"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json() -> Column:
    return Column(_JSONBOrJSON, nullable=True)


def _ts(nullable: bool = False) -> Column:
    return Column(DateTime(timezone=True), nullable=nullable)


class ScStaff(SQLModel, table=True):
    """Directorio de quién aprueba qué (rol ORGANIZATIVO, distinto del
    user/admin de la plataforma)."""

    __tablename__ = "sc_staff"

    user_id: str = Field(primary_key=True, max_length=64)
    display_name: str = Field(max_length=120)
    org_role: str = Field(max_length=32, index=True)


class ScIngestionRun(SQLModel, table=True):
    __tablename__ = "sc_ingestion_runs"

    run_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    load_date: date = Field(sa_column=Column(Date, nullable=False))
    files: List[Any] = Field(default_factory=list, sa_column=_json())  # [{file, sha256, rows}]
    rejected_rows: List[Any] = Field(default_factory=list, sa_column=_json())  # [{file, row, reason}]
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())


class ScSupplier(SQLModel, table=True):
    __tablename__ = "sc_suppliers"

    supplier_id: str = Field(primary_key=True, max_length=16)
    name: str = Field(max_length=120)
    country: str = Field(max_length=2)
    category: str = Field(max_length=32, index=True)
    currency: str = Field(max_length=3)
    processes_patient_data: bool = False
    source_file: str = Field(max_length=120)
    source_row: int


class ScPriceRecord(SQLModel, table=True):
    __tablename__ = "sc_price_records"
    __table_args__ = (UniqueConstraint("supplier_id", "product", "month", name="uq_sc_price_month"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    supplier_id: str = Field(max_length=16, index=True)
    product: str = Field(max_length=120)
    unit: str = Field(max_length=16)
    currency: str = Field(max_length=3)
    month: date = Field(sa_column=Column(Date, nullable=False))  # día 1 del mes
    unit_price: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    monthly_volume: int
    source_file: str = Field(max_length=120)
    source_row: int


class ScContract(SQLModel, table=True):
    __tablename__ = "sc_contracts"

    contract_id: str = Field(primary_key=True, max_length=16)
    supplier_id: str = Field(max_length=16, index=True, unique=True)
    source_file: str = Field(max_length=120)
    sha256: str = Field(max_length=64)
    pages: int


class ScContractTerm(SQLModel, table=True):
    __tablename__ = "sc_contract_terms"
    __table_args__ = (UniqueConstraint("contract_id", "term", name="uq_sc_contract_term"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    contract_id: str = Field(max_length=16, index=True)
    term: str = Field(max_length=32)
    value: Optional[dict] = Field(default=None, sa_column=_json())
    status: str = Field(max_length=16)  # ok | doubtful | missing
    confidence: float = 0.0
    clause: Optional[str] = Field(default=None, max_length=16)
    clause_title: Optional[str] = Field(default=None, max_length=120)
    page: Optional[int] = None
    quote: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))  # ya enmascarada
    note: Optional[str] = Field(default=None, max_length=300)


class ScComplianceClearance(SQLModel, table=True):
    """Cumplimiento confirma que se puede continuar con un proveedor sin
    acuerdo de protección de datos (BRIEFING, "Restricción específica")."""

    __tablename__ = "sc_compliance_clearances"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    supplier_id: str = Field(max_length=16, index=True)
    cleared_by: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())


class ScAlert(SQLModel, table=True):
    __tablename__ = "sc_alerts"

    # Clave determinista (tipo + proveedor + fecha objetivo): un barrido
    # repetido no duplica avisos.
    alert_key: str = Field(primary_key=True, max_length=120)
    kind: str = Field(max_length=32, index=True)
    supplier_id: str = Field(max_length=16, index=True)
    contract_id: Optional[str] = Field(default=None, max_length=16)
    recipient_role: str = Field(max_length=32, index=True)
    due_date: Optional[date] = Field(default=None, sa_column=Column(Date, nullable=True))
    summary: str = Field(sa_column=Column(Text, nullable=False))
    details: Optional[dict] = Field(default=None, sa_column=_json())
    refs: List[Any] = Field(default_factory=list, sa_column=_json())
    raised_by: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())


class ScCommunicationDraft(SQLModel, table=True):
    __tablename__ = "sc_communication_drafts"

    draft_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    supplier_id: str = Field(max_length=16, index=True)
    contract_id: Optional[str] = Field(default=None, max_length=16)
    purpose: str = Field(max_length=40)
    subject: str = Field(max_length=200)
    body: str = Field(sa_column=Column(Text, nullable=False))
    drafted_by: str = Field(max_length=64)  # identidad del agente
    draft_source: str = Field(max_length=16)  # model | template
    required_roles: List[Any] = Field(default_factory=list, sa_column=_json())
    approval_reasons: List[Any] = Field(default_factory=list, sa_column=_json())
    refs: List[Any] = Field(default_factory=list, sa_column=_json())
    status: str = Field(default=DRAFT_PENDING, max_length=24, index=True)
    requested_by: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())
    sent_at: Optional[datetime] = Field(default=None, sa_column=_ts(nullable=True))


class ScApproval(SQLModel, table=True):
    __tablename__ = "sc_approvals"
    __table_args__ = (UniqueConstraint("draft_id", "role", name="uq_sc_approval_role"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    draft_id: uuid.UUID = Field(index=True)
    role: str = Field(max_length=32)
    decision: str = Field(max_length=16)  # approved | rejected
    decided_by: str = Field(max_length=64)
    comment: Optional[str] = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())


class ScAgentAction(SQLModel, table=True):
    """Registro de cada acción de cada agente (y de cada decisión humana).

    `details` lleva solo identificadores, intenciones y recuentos; nunca la
    pregunta, el prompt ni texto de contrato. De un prompt solo queda su huella.
    """

    __tablename__ = "sc_agent_actions"

    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: str = Field(max_length=64, index=True)
    actor: str = Field(max_length=64, index=True)  # agent:... o human:<seudónimo>
    action: str = Field(max_length=48)
    tool: Optional[str] = Field(default=None, max_length=64)
    outcome: str = Field(max_length=24)
    details: Optional[dict] = Field(default=None, sa_column=_json())
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())


SC_TABLES = [
    ScStaff.__table__,
    ScIngestionRun.__table__,
    ScSupplier.__table__,
    ScPriceRecord.__table__,
    ScContract.__table__,
    ScContractTerm.__table__,
    ScComplianceClearance.__table__,
    ScAlert.__table__,
    ScCommunicationDraft.__table__,
    ScApproval.__table__,
    ScAgentAction.__table__,
]
