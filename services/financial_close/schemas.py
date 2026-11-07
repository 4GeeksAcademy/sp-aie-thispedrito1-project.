"""Contratos de respuesta de `/financial-close` (ninguno lleva descripciones retenidas)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ReportListItem(BaseModel):
    id: str
    period: date
    version: int
    status: str
    trigger: str
    narrative_source: str
    figures_sha256: str
    supersedes_id: Optional[str] = None
    created_at: datetime


class ReviewDecisionRead(BaseModel):
    decision: str
    comments: Optional[str] = None
    decided_by: str
    decided_at: datetime


class DistributionRead(BaseModel):
    recipient_role: str
    recipient_name: str
    delivered_at: datetime


class ReportRead(ReportListItem):
    language: str
    markdown: str
    figures: Dict[str, Any]
    quality: Dict[str, Any]
    decisions: List[ReviewDecisionRead]
    distribution: List[DistributionRead]
    reviewer: str
    can_decide: bool


class TraceRecord(BaseModel):
    entry_id: str
    source_file: str
    source_row: int
    source_ref: str
    entry_date: Optional[date] = None
    country: str
    payer: str
    category: str
    currency: str
    amount: Optional[str] = None
    status: str
    description: Optional[str] = None
    description_withheld: bool


class FigureTrace(BaseModel):
    figure_id: str
    value: Optional[str] = None
    unit: str
    sources: List[Dict[str, Any]]
    ledger_records: List[TraceRecord]


class InboxItem(BaseModel):
    kind: str
    period: date
    report_id: Optional[str] = None
    entry_id: Optional[str] = None
    summary: str
    created_at: datetime


class StaffRole(BaseModel):
    org_role: Optional[str] = None
    display_name: Optional[str] = None
    is_reviewer: bool
    next_scheduled_run: datetime


class AgentActionRead(BaseModel):
    request_id: str
    actor: str
    actor_type: str
    action: str
    tool: Optional[str] = None
    outcome: str
    details: Optional[Dict[str, Any]] = None
    created_at: datetime


class RunEnqueued(BaseModel):
    task_id: Optional[str] = None
    period: date
    status: str
