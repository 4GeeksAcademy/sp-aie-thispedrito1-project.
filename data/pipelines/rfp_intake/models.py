"""Tablas de la recepción de RFPs en Supabase (BRIEFING del Hito 9, §2.3).

Se registran en el mismo `SQLModel.metadata` que inventario y telemetría:
el `create_all` del arranque de la API las crea solas, sin SQL manual.

Las columnas de las Partes 2 y 3 (`draft_content`, `evaluation_results`,
`approval_status`, `approver`, `approved_at`) existen ya y quedan vacías:
`create_all` no añade columnas a una tabla existente, y sin ellas la Parte 2
tendría que empezar con una migración.

Ninguna columna guarda datos de pacientes. Lo que más se le acerca es
`phi_categories`: las CATEGORÍAS detectadas ("patient_name"), nunca el texto.
Tampoco se guarda el nombre original del archivo subido (podría ser
"resumen_<paciente>.pdf"): el PDF se llama como el ticket.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import Column, DateTime, ForeignKey, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from telemetry_models import _JSONBOrJSON

# Estados de la Parte 1 (README) + `error` (decisión del usuario, 2026-10-19):
# un fallo técnico no es un rechazo del clasificador y no debe parecerlo.
STATUS_ANALYZING = "analizando"
STATUS_DISCARDED = "descartado"
STATUS_COMPLETE = "analisis_completo"
STATUS_ERROR = "error"
STATUSES = (STATUS_ANALYZING, STATUS_DISCARDED, STATUS_COMPLETE, STATUS_ERROR)

DISCARD_NOT_RFP = "not_rfp"
DISCARD_PHI = "phi_detected"

# Siguiente etapa del mismo ticket: la recogerá la Parte 2 (generadores).
NEXT_STAGE_DRAFTING = "generando_borrador"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_column() -> Column:
    return Column(_JSONBOrJSON, nullable=True)


class RfpTicket(SQLModel, table=True):
    __tablename__ = "rfp_tickets"

    ticket_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    rfp_id: str = Field(index=True, unique=True, max_length=32)
    status: str = Field(default=STATUS_ANALYZING, index=True, max_length=32)
    raw_pdf_path: str = Field(max_length=255)
    created_by: str = Field(max_length=64)  # user_id de TinyDB: rastro de auditoría
    discard_reason: Optional[str] = Field(default=None, max_length=32)
    classifier_reason: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    requires_compliance_review: bool = Field(default=False)
    phi_categories: List[str] = Field(default_factory=list, sa_column=_json_column())
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    task_id: Optional[str] = Field(default=None, max_length=64)
    attempts: int = Field(default=0)
    # Contrato de handoff a la Parte 2 (docs/rfp-intake.md): ticket_id +
    # payload del synthesizer. `next_stage` es la bandera que la Parte 2 lee.
    handoff_payload: Optional[dict[str, Any]] = Field(default=None, sa_column=_json_column())
    next_stage: Optional[str] = Field(default=None, index=True, max_length=32)
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    completed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class RfpMetadata(SQLModel, table=True):
    __tablename__ = "rfp_metadata"

    ticket_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("rfp_tickets.ticket_id", ondelete="CASCADE"), primary_key=True)
    )
    document_kind: Optional[str] = Field(default=None, max_length=32)
    classifier_confidence: Optional[float] = None
    client_name: Optional[str] = Field(default=None, max_length=200)
    client_country: Optional[str] = Field(default=None, max_length=2)  # US / UK
    currency: Optional[str] = Field(default=None, max_length=3)
    program_type: List[str] = Field(default_factory=list, sa_column=_json_column())
    covered_population: Optional[int] = None
    covered_population_unit: Optional[str] = Field(default=None, max_length=16)
    deadline: Optional[date] = None
    deadline_text: Optional[str] = Field(default=None, max_length=120)
    budget_range: Optional[str] = Field(default=None, max_length=200)
    departments_needed: List[str] = Field(default_factory=list, sa_column=_json_column())
    unmapped_departments: List[str] = Field(default_factory=list, sa_column=_json_column())
    open_questions: List[str] = Field(default_factory=list, sa_column=_json_column())
    # Legibilidad (estimador de coste de procesamiento)
    word_count: Optional[int] = None
    sentence_count: Optional[int] = None
    estimated_tokens: Optional[int] = None
    flesch_kincaid_grade: Optional[float] = None
    gunning_fog: Optional[float] = None
    flesch_reading_ease: Optional[float] = None
    readability_note: Optional[str] = Field(default=None, max_length=200)
    # Resultado del synthesizer
    summary: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    who_to_ask: List[dict[str, Any]] = Field(default_factory=list, sa_column=_json_column())
    conflicts: List[dict[str, Any]] = Field(default_factory=list, sa_column=_json_column())


class RfpDepartmentSection(SQLModel, table=True):
    __tablename__ = "rfp_department_sections"
    __table_args__ = (UniqueConstraint("ticket_id", "department_id", name="uq_rfp_section_department"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    ticket_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("rfp_tickets.ticket_id", ondelete="CASCADE"), index=True, nullable=False)
    )
    department_id: str = Field(max_length=16)
    owner: str = Field(max_length=64)
    key_aspects: List[str] = Field(default_factory=list, sa_column=_json_column())
    open_questions: List[str] = Field(default_factory=list, sa_column=_json_column())
    excerpt_indexes: List[int] = Field(default_factory=list, sa_column=_json_column())
    unsupported_claims_removed: int = Field(default=0)
    # Partes 2 y 3: vacías en la Parte 1.
    draft_content: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    evaluation_results: Optional[dict[str, Any]] = Field(default=None, sa_column=_json_column())
    approval_status: Optional[str] = Field(default=None, max_length=32)
    approver: Optional[str] = Field(default=None, max_length=64)
    approved_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
