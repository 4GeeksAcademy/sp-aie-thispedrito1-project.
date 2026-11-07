"""Contratos HTTP de la recepción de RFPs.

Un modelo, varios contratos (regla de docs/serialization-audit.md): el
listado lleva solo lo que pinta la tabla de /rfps; el detalle, todo lo que
Revenue Cycle necesita para saber qué pedir a quién. Ninguno expone
`raw_pdf_path`, `created_by` ni `task_id` (internos).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class RfpTicketQueued(BaseModel):
    ticket_id: uuid.UUID
    rfp_id: str
    status: str


class RfpTicketListItem(BaseModel):
    ticket_id: uuid.UUID
    rfp_id: str
    status: str
    discard_reason: Optional[str]
    requires_compliance_review: bool
    client_name: Optional[str]
    client_country: Optional[str]
    created_at: datetime
    updated_at: datetime


class RfpMetadataRead(BaseModel):
    document_kind: Optional[str]
    classifier_confidence: Optional[float]
    client_name: Optional[str]
    client_country: Optional[str]
    currency: Optional[str]
    program_type: List[str]
    covered_population: Optional[int]
    covered_population_unit: Optional[str]
    deadline: Optional[date]
    deadline_text: Optional[str]
    budget_range: Optional[str]
    departments_needed: List[str]
    unmapped_departments: List[str]
    open_questions: List[str]
    word_count: Optional[int]
    sentence_count: Optional[int]
    estimated_tokens: Optional[int]
    flesch_kincaid_grade: Optional[float]
    gunning_fog: Optional[float]
    flesch_reading_ease: Optional[float]
    readability_note: Optional[str]
    summary: Optional[str]
    who_to_ask: List[Dict[str, Any]]
    conflicts: List[Dict[str, Any]]


class RfpDepartmentSectionRead(BaseModel):
    department_id: str
    department: str
    owner: str
    key_aspects: List[str]
    open_questions: List[str]
    unsupported_claims_removed: int


class RfpTicketDetail(RfpTicketListItem):
    classifier_reason: Optional[str]
    phi_categories: List[str]
    error_message: Optional[str]
    attempts: int
    next_stage: Optional[str]
    completed_at: Optional[datetime]
    metadata: Optional[RfpMetadataRead]
    sections: List[RfpDepartmentSectionRead]


class RfpHandoff(BaseModel):
    ticket_id: uuid.UUID
    next_stage: str
    payload: Dict[str, Any]
