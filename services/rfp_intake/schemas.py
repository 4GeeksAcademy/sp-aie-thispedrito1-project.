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

from pydantic import BaseModel, field_validator


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
    # Parte 2: borrador y su evaluación (None/[] mientras no ha empezado).
    draft_status: Optional[str] = None
    draft_iterations: int = 0
    draft_content: Optional[str] = None
    drafted_at: Optional[datetime] = None
    evaluation_results: Optional[Dict[str, Any]] = None
    evaluation_history: List[Dict[str, Any]] = []
    # Parte 3: aprobación humana. `approval_request` es lo que el responsable
    # necesita para decidir; `approval_history`, quién decidió qué y cuándo
    # (sin el texto de los comentarios).
    approval_status: Optional[str] = None
    approval_request: Optional[Dict[str, Any]] = None
    approval_round: int = 0
    approval_history: List[Dict[str, Any]] = []
    approver: Optional[str] = None
    approved_at: Optional[datetime] = None
    confirmed_capacity: Optional[int] = None

    @field_validator("evaluation_history", "approval_history", mode="before")
    @classmethod
    def _null_history(cls, value: Any) -> Any:
        # Filas de la Parte 1 anteriores a la migración: la columna llega NULL.
        return value or []


class RfpTicketDetail(RfpTicketListItem):
    classifier_reason: Optional[str]
    phi_categories: List[str]
    error_message: Optional[str]
    attempts: int
    next_stage: Optional[str]
    completed_at: Optional[datetime]
    metadata: Optional[RfpMetadataRead]
    sections: List[RfpDepartmentSectionRead]
    # Parte 3: arbitraje en curso / último (trigger, árbitro, petición) y si
    # el documento final ya existe.
    arbitration: Optional[Dict[str, Any]] = None
    has_final_document: bool = False


class RfpHandoff(BaseModel):
    ticket_id: uuid.UUID
    next_stage: str
    payload: Dict[str, Any]


class RfpResponseHandoff(BaseModel):
    """Contrato de entrada de la Parte 3 (docs/rfp-response.md)."""
    ticket_id: uuid.UUID
    status: str
    next_stage: str
    payload: Dict[str, Any]


class SectionDecisionRequest(BaseModel):
    """Lo que envía el responsable de un departamento. La validación de
    negocio (motivo obligatorio, rondas, capacidad, PHI) la hace
    data/pipelines/rfp_intake/decisions.py contra la petición del grafo."""
    decision: str
    comments: Optional[str] = None
    confirmed_capacity: Optional[int] = None
    # Huella del borrador que se estaba leyendo: si el grafo lo cambió
    # entre medias, la decisión no se aplica a una versión que nadie vio.
    draft_digest: Optional[str] = None


class ArbitrationDecisionRequest(BaseModel):
    option: str
    new_population: Optional[int] = None
    note: Optional[str] = None


class RfpDecisionQueued(BaseModel):
    ticket_id: uuid.UUID
    department_id: Optional[str] = None
    approval_status: str


class RfpFinalDocumentRead(BaseModel):
    """FinalDocument del BRIEFING §2.3 + el Markdown listo para descargar."""
    ticket_id: uuid.UUID
    rfp_id: str
    sections: List[Dict[str, Any]]
    currency: Optional[str]
    client_country: Optional[str]
    approvals: List[Dict[str, Any]]
    content_markdown: str
    generated_at: datetime


class RfpTraceEventRead(BaseModel):
    event_id: str
    part: int
    node: str
    agent: str
    department_id: Optional[str]
    input: Dict[str, Any]
    output: Dict[str, Any]
    timestamp: datetime
