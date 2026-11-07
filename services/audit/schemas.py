"""Contratos de salida de /audit-log (solo lectura)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class AuditEntryRead(BaseModel):
    sequence: int
    entry_id: str
    occurred_at: str
    actor_type: str
    actor_id: Optional[str]
    actor_label: str
    on_behalf_of: Optional[str]
    on_behalf_of_label: Optional[str]
    action: str
    resource_type: str
    resource_id: Optional[str]
    patient_ref: Optional[str]
    department: Optional[str]
    context: Optional[str]
    origin: str
    outcome: str
    details: dict[str, Any]
    prev_hash: str
    entry_hash: str


class AuditPageRead(BaseModel):
    items: list[AuditEntryRead]
    next_cursor: Optional[int]
    total: int
    # Lo que realmente se aplicó: "all" (Admin) o el código del departamento.
    scope: str


class AuditActor(BaseModel):
    actor_type: str
    actor_id: Optional[str]
    label: str
    entries: int


class AuditCatalogRead(BaseModel):
    actions: dict[str, str]  # acción → tipo de recurso
    resource_types: list[str]
    actor_types: list[str]
    outcomes: list[str]
    purposes: dict[str, str]


class IntegrityRead(BaseModel):
    ok: bool
    checked: int
    head_sequence: Optional[int]
    head_hash: str
    broken_at: Optional[int]
    reason: Optional[str]
    verified_at: str


class UnusualAccessRead(BaseModel):
    actor_id: str
    actor_label: str
    recent_count: int
    baseline_average: float


class AnomaliesRead(BaseModel):
    window_hours: int
    baseline_days: int
    items: list[UnusualAccessRead]


class MyPatientAccess(BaseModel):
    """Vista del empleado: quién accedió a sus pacientes asignados. Sin IP ni
    huellas (minimización: le basta saber quién, qué, cuándo y por qué)."""

    sequence: int
    occurred_at: str
    actor_type: str
    actor_label: str
    action: str
    patient_ref: Optional[str]
    context: Optional[str]
    outcome: str


class MyPatientAccessPage(BaseModel):
    items: list[MyPatientAccess]
    next_cursor: Optional[int]
    total: int
