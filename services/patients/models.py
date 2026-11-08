"""Tablas `patient_records` y `patient_insurance_claims` (Supabase).

Datos 100 % sintéticos. Aquí SÍ hay contenido clínico (es el sistema clínico);
lo que nunca lo tiene es el registro de auditoría, que solo guarda el
`patient_ref` opaco.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel

CLAIM_PENDING = "pending"
CLAIM_APPROVED = "approved"
CLAIM_DENIED = "denied"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PatientRecord(SQLModel, table=True):
    __tablename__ = "patient_records"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=36)
    patient_ref: str = Field(unique=True, index=True, max_length=16)
    # Departamento del equipo de cuidado: el único que ve el contenido clínico.
    department_id: str = Field(index=True, max_length=36)
    # Empleado asignado ("quién accedió a mis pacientes").
    assigned_user_id: Optional[str] = Field(default=None, index=True, max_length=36)
    country: str = Field(max_length=2)
    full_name: str = Field(max_length=120)
    date_of_birth: str = Field(max_length=10)
    diagnosis: str = Field(max_length=200)
    clinical_notes: str = Field(max_length=4000)
    documentation_draft: Optional[str] = Field(default=None, max_length=4000)
    insurance_plan: str = Field(max_length=80)
    outstanding_balance_cents: int = 0
    currency: str = Field(max_length=3)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class InsuranceClaim(SQLModel, table=True):
    __tablename__ = "patient_insurance_claims"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True, max_length=36)
    claim_ref: str = Field(unique=True, max_length=16)
    patient_record_id: str = Field(foreign_key="patient_records.id", index=True, max_length=36)
    amount_cents: int
    currency: str = Field(max_length=3)
    status: str = Field(default=CLAIM_PENDING, max_length=16)
    reason_code: Optional[str] = Field(default=None, max_length=40)
    decided_by: Optional[str] = Field(default=None, max_length=36)
    decided_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
