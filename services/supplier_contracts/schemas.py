"""Contratos de respuesta de `/supplier-contracts` (lo que ve Swagger).

Ninguno tiene campos para datos bancarios, contactos ni texto completo de un
contrato: el contrato de salida ya impide devolverlos.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class StaffRole(BaseModel):
    org_role: Optional[str] = None
    display_name: Optional[str] = None


class SupplierListItem(BaseModel):
    supplier_id: str
    name: str
    country: str
    category: str
    category_label: str
    currency: str
    processes_patient_data: bool
    contract_id: Optional[str] = None
    latest_price: Optional[str] = None
    latest_month: Optional[str] = None
    unit: Optional[str] = None
    monthly_volume: Optional[int] = None
    source: str


class ContractTermRead(BaseModel):
    term: str
    label: str
    status: str
    confidence: float
    value: Optional[Dict[str, Any]] = None
    clause: Optional[str] = None
    page: Optional[int] = None
    quote: Optional[str] = None
    note: Optional[str] = None


class ContractSummary(BaseModel):
    contract_id: str
    source_file: str
    pages: int


class SupplierContractRead(BaseModel):
    supplier_id: str
    contract: Optional[ContractSummary] = None
    terms: List[ContractTermRead] = []
    message: Optional[str] = None


class AlertRead(BaseModel):
    alert_key: str
    kind: str
    supplier_id: str
    contract_id: Optional[str] = None
    recipient_role: str
    recipient: Optional[str] = None
    due_date: Optional[date] = None
    summary: str
    details: Optional[Dict[str, Any]] = None
    refs: List[str] = []
    raised_by: str
    created_at: datetime


class SweepResult(BaseModel):
    created: int
    alert_keys: List[str]


class AgentActionRead(BaseModel):
    id: int
    request_id: str
    actor: str
    action: str
    tool: Optional[str] = None
    outcome: str
    details: Optional[Dict[str, Any]] = None
    created_at: datetime


class Consultation(BaseModel):
    agent_id: str
    intent: str
    supplier_id: Optional[str] = None
    status: str


class AskResponse(BaseModel):
    request_id: str
    answer: str
    refused: bool = False
    plan_source: str
    consultations: List[Consultation] = []
    findings: List[str] = []
    citations: List[str] = []
    missing: List[str] = []
    alerts: List[Dict[str, Any]] = []
    limit_reached: bool = False
    brief_supplier_id: Optional[str] = None
    brief: Optional[Dict[str, Any]] = None


class BriefResponse(BaseModel):
    request_id: str
    status: str
    supplier_id: str
    supplier_name: Optional[str] = None
    message: Optional[str] = None
    sections: Dict[str, List[str]] = {}
    recommendation: List[str] = []
    citations: List[str] = []
    missing: List[str] = []
    consultations: List[Consultation] = []
    markdown: str = ""


class ClearanceRead(BaseModel):
    supplier_id: str
    cleared_at: datetime


class ApprovalRead(BaseModel):
    role: str
    approver: str
    decision: str
    comment: Optional[str] = None


class DraftRead(BaseModel):
    draft_id: str
    supplier_id: str
    contract_id: Optional[str] = None
    purpose: str
    subject: str
    body: str
    status: str
    draft_source: str
    drafted_by: str
    refs: List[str] = []
    approval_reasons: List[str] = []
    approvals: List[ApprovalRead] = []
    created_at: datetime
    sent_at: Optional[datetime] = None
