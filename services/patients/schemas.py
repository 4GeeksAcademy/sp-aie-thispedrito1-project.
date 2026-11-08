"""Contratos de salida de /patients. Las entradas se validan a mano en el
router (400 `{"errors": [...]}`), no con Pydantic: un 422 de FastAPI repite
el valor recibido, y aquí ese valor puede ser una nota clínica."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PatientListItem(BaseModel):
    patient_ref: str
    department: Optional[str]
    country: str
    assigned_to_me: bool
    sections: list[str]
    pending_claims: int


class ClinicalSection(BaseModel):
    full_name: str
    date_of_birth: str
    diagnosis: str
    clinical_notes: str
    documentation_draft: Optional[str]


class ClaimRead(BaseModel):
    claim_ref: str
    amount_cents: int
    currency: str
    status: str
    reason_code: Optional[str]
    decided_at: Optional[datetime]


class BillingSection(BaseModel):
    insurance_plan: str
    outstanding_balance_cents: int
    currency: str
    claims: list[ClaimRead]


class PatientDetail(BaseModel):
    patient_ref: str
    department: Optional[str]
    country: str
    sections: list[str]
    clinical: Optional[ClinicalSection]
    billing: Optional[BillingSection]
    # Número de la entrada de auditoría que dejó esta lectura: la pantalla lo
    # muestra para que quien consulta sepa que su acceso quedó registrado.
    audit_sequence: int


class PurposeOption(BaseModel):
    code: str
    label: str


class PatientCatalog(BaseModel):
    purposes: list[PurposeOption]
    claim_reason_codes: dict[str, list[str]]
