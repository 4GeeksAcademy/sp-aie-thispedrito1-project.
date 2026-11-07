"""Contratos de respuesta del onboarding (los cuerpos de entrada se validan a mano).

Por qué las ENTRADAS no son modelos Pydantic: un 422 de FastAPI repite en la
respuesta el valor recibido. Si alguien manda `"ssn": "123-45-6789"` en los
trámites, ese número volvería en la respuesta. La validación manual responde
400 con `{"errors": [{"field", "message"}]}` nombrando el campo, nunca su
valor (mismo formato que incidencias).

Las SALIDAS sí son modelos, uno por consumidor: el listado del equipo de
Personas no lleva lo mismo que la vista de la propia persona.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class StageRead(BaseModel):
    number: int
    key: str
    title: str
    deadline_day: int
    due_at: datetime
    status: str  # completed | current | upcoming
    approver: Optional[str] = None
    pass_threshold: Optional[float] = None


class OnboardingListItem(BaseModel):
    hire_id: uuid.UUID
    full_name: str
    job_title: str
    department: str
    site: str
    framework: str
    language: str
    start_date: date
    status: str
    current_stage: int
    current_stage_title: str
    phase: str
    current_due_at: Optional[datetime] = None
    blocked: bool
    blocked_reasons: List[str]
    completed_stages: List[int]


class WaitingForRead(BaseModel):
    kind: str
    stage: int
    attempt: Optional[int] = None
    approver: Optional[str] = None


class AccessRequestRead(BaseModel):
    system_key: str
    system_name: str
    contains_patient_data: bool
    status: str
    requested_by_type: str
    created_at: datetime


class AuditEventRead(BaseModel):
    stage: Optional[int] = None
    action: str
    actor_type: str
    actor_ref: str
    outcome: Optional[str] = None
    security_event: bool
    created_at: datetime


class OnboardingDetail(OnboardingListItem):
    role_variant: str
    stages: List[StageRead]
    waiting_for: Optional[WaitingForRead] = None
    paperwork: Dict[str, bool]
    approvals: Dict[str, Dict[str, Any]]
    attempts: Dict[str, int]
    failed_attempts: Dict[str, int]
    last_check_result: Optional[Dict[str, Any]] = None
    rejection: Optional[Dict[str, Any]] = None
    compliance_approved: bool
    access_requests: List[AccessRequestRead]
    # Qué puede hacer QUIEN consulta ahora mismo (para pintar los botones).
    can_mark_paperwork: bool
    can_decide: bool
    decide_refusal: Optional[str] = None
    audit: Optional[List[AuditEventRead]] = None


class ContentSectionRead(BaseModel):
    title: str
    body: str
    citation: str


class ContentRead(BaseModel):
    doc_id: str
    title: str
    language: str
    sections: List[ContentSectionRead]
    reinforcement: bool


class CheckQuestionRead(BaseModel):
    id: str
    question: str
    options: List[str]


class CheckRead(BaseModel):
    set_id: str
    attempt: int
    threshold: float
    questions: List[CheckQuestionRead]


class QuestionRead(BaseModel):
    id: uuid.UUID
    stage: int
    question: str
    status: str
    answer: str
    citations: List[str]
    routed_to: Optional[str] = None
    created_at: datetime


class SystemRead(BaseModel):
    key: str
    name: str
    contains_patient_data: bool


class MyOnboarding(BaseModel):
    hire_id: uuid.UUID
    full_name: str
    job_title: str
    language: str
    framework: str
    status: str
    phase: str
    current_stage: int
    stages: List[StageRead]
    remaining_stages: List[int]
    content: Optional[ContentRead] = None
    check: Optional[CheckRead] = None
    last_check_result: Optional[Dict[str, Any]] = None
    rejection: Optional[Dict[str, Any]] = None
    waiting_for: Optional[WaitingForRead] = None
    questions: List[QuestionRead]
    access_requests: List[AccessRequestRead]
    systems: List[SystemRead]
    compliance_approved: bool


class OnboardingRole(BaseModel):
    is_hire: bool
    hire_id: Optional[uuid.UUID] = None
    org_role: Optional[str] = None
    department: Optional[str] = None
    can_view_all: bool
    can_view_reports: bool
    can_create: bool


class NotificationRead(BaseModel):
    id: int
    hire_id: uuid.UUID
    stage: Optional[int] = None
    kind: str
    subject: str
    body: str
    sender: str
    created_at: datetime


class QueuedResponse(BaseModel):
    hire_id: uuid.UUID
    status: str = "queued"


class OnboardingCreated(BaseModel):
    hire_id: uuid.UUID
    status: str


class AccessRequestResult(BaseModel):
    system_key: str
    status: str


class CatalogRead(BaseModel):
    stages: List[Dict[str, Any]]
    departments: Dict[str, str]
    role_variants: Dict[str, Dict[str, str]]
    sites: Dict[str, str]
    frameworks: Dict[str, str]
    systems: List[SystemRead]
