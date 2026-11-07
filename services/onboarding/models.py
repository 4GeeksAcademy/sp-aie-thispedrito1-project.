"""Tablas del onboarding en Supabase (mismo `SQLModel.metadata` que el resto).

Lo que NO hay en estas tablas es tan importante como lo que hay: ninguna
columna para documentos de identidad, datos bancarios, salario ni el
resultado de la verificación de antecedentes (BRIEFING, "Datos sensibles").
La etapa 1 solo guarda tres booleanos.

`onboarding_hires` es la proyección del estado del grafo para las pantallas y
para el seguimiento de plazos: el estado real vive en el checkpointer
(hilo `onboarding-<hire_id>`), y `runner.sync` la reescribe tras cada avance.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import Column, Date, DateTime, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from telemetry_models import _JSONBOrJSON

HIRE_ACTIVE = "active"
HIRE_COMPLETED = "completed"

PHASE_STARTING = "starting"  # creada, el worker aún no ha arrancado el grafo
PHASE_PAPERWORK = "awaiting_paperwork"
PHASE_CHECK = "awaiting_check"
PHASE_APPROVAL = "awaiting_approval"
PHASE_COMPLETED = "completed"

ACCESS_REQUESTED = "requested"
ACCESS_BLOCKED = "blocked"

QUESTION_ANSWERED = "answered"
QUESTION_ROUTED = "routed"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_column() -> Column:
    return Column(_JSONBOrJSON, nullable=True)


def _ts(nullable: bool = False) -> Column:
    return Column(DateTime(timezone=True), nullable=nullable)


class OnboardingStaff(SQLModel, table=True):
    """Directorio de quién puede aprobar qué. Rol ORGANIZATIVO, distinto del
    rol de la plataforma (user/admin de la TinyDB)."""

    __tablename__ = "onboarding_staff"

    user_id: str = Field(primary_key=True, max_length=64)
    display_name: str = Field(max_length=120)
    org_role: str = Field(max_length=32, index=True)
    department: str = Field(max_length=32)
    site: str = Field(max_length=16)


class OnboardingHire(SQLModel, table=True):
    __tablename__ = "onboarding_hires"

    hire_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # Cuenta con la que la persona entra al backoffice para ver su progreso.
    hire_user_id: str = Field(max_length=64, index=True, unique=True)
    full_name: str = Field(max_length=120)
    job_title: str = Field(max_length=120)
    department: str = Field(max_length=32)
    role_variant: str = Field(max_length=48)
    site: str = Field(max_length=16)
    framework: str = Field(max_length=16)
    language: str = Field(max_length=4, default="en")
    manager_user_id: str = Field(max_length=64, index=True)
    start_date: date = Field(sa_column=Column(Date, nullable=False))

    status: str = Field(default=HIRE_ACTIVE, max_length=16, index=True)
    current_stage: int = Field(default=1)
    phase: str = Field(default=PHASE_STARTING, max_length=24)
    # Qué espera el grafo ahora mismo: {"kind", "stage", "attempt", ...}
    waiting_for: Optional[dict[str, Any]] = Field(default=None, sa_column=_json_column())
    completed_stages: List[int] = Field(default_factory=list, sa_column=_json_column())
    attempts: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column())
    failed_attempts: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column())
    last_check_result: Optional[dict[str, Any]] = Field(default=None, sa_column=_json_column())
    reinforce_sections: Optional[List[str]] = Field(default=None, sa_column=_json_column())
    rejection: Optional[dict[str, Any]] = Field(default=None, sa_column=_json_column())
    paperwork: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column())
    approvals: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column())
    compliance_approved_at: Optional[datetime] = Field(default=None, sa_column=_ts(nullable=True))

    # Entrada humana validada por la API, pendiente de que el worker la aplique.
    pending_command: Optional[dict[str, Any]] = Field(default=None, sa_column=_json_column())
    last_error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())
    updated_at: datetime = Field(default_factory=utcnow, sa_column=_ts())
    completed_at: Optional[datetime] = Field(default=None, sa_column=_ts(nullable=True))


class OnboardingAuditEvent(SQLModel, table=True):
    """Una fila por acción. `actor_type` distingue al agente de una persona;
    `actor_ref` es el id del agente o el SEUDÓNIMO de la persona. Sin texto
    libre: solo nombres de acción y resultados de una lista cerrada."""

    __tablename__ = "onboarding_audit_events"
    __table_args__ = (UniqueConstraint("event_key", name="uq_onboarding_audit_event_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    event_key: str = Field(max_length=200)
    hire_id: uuid.UUID = Field(index=True)
    stage: Optional[int] = Field(default=None)
    action: str = Field(max_length=64)
    actor_type: str = Field(max_length=8)
    actor_ref: str = Field(max_length=64)
    outcome: Optional[str] = Field(default=None, max_length=64)
    security_event: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())


class OnboardingNotification(SQLModel, table=True):
    """Bandeja de salida (decisión del usuario: sin correos reales). Una fila
    por destinatario; `dedupe_key` hace idempotente cada aviso."""

    __tablename__ = "onboarding_notifications"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_onboarding_notification_dedupe"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    dedupe_key: str = Field(max_length=240)
    hire_id: uuid.UUID = Field(index=True)
    stage: Optional[int] = Field(default=None)
    kind: str = Field(max_length=40)
    recipient_role: str = Field(max_length=24)
    recipient_user_id: str = Field(max_length=64, index=True)
    subject: str = Field(max_length=200)
    body: str = Field(sa_column=Column(Text, nullable=False))
    sender: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())


class OnboardingQuestion(SQLModel, table=True):
    __tablename__ = "onboarding_questions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hire_id: uuid.UUID = Field(index=True)
    stage: int
    # Ya redactada: el texto original nunca se guarda.
    question: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(max_length=16)
    answer: str = Field(sa_column=Column(Text, nullable=False))
    citations: List[str] = Field(default_factory=list, sa_column=_json_column())
    routed_to: Optional[str] = Field(default=None, max_length=24)
    reason: Optional[str] = Field(default=None, max_length=48)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())


class OnboardingAccessRequest(SQLModel, table=True):
    __tablename__ = "onboarding_access_requests"

    id: Optional[int] = Field(default=None, primary_key=True)
    hire_id: uuid.UUID = Field(index=True)
    system_key: str = Field(max_length=40)
    contains_patient_data: bool
    status: str = Field(max_length=16)
    requested_by_type: str = Field(max_length=8)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_ts())
