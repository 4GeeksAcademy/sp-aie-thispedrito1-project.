"""Contratos de respuesta de /reporting. Uno por endpoint, como el resto de
la API (docs/serialization-audit.md): cada esquema declara exactamente lo
que devuelve, y tests/test_serialization.py lo barre en el OpenAPI."""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel


class ClinicSupplyPerformance(BaseModel):
    """Una fila de KPIs, campos exactos del CONTEXT (seccion 6)."""

    clinic_id: str
    country: Literal["US", "UK"]
    total_supply_cost: float
    supply_consumption_count: int
    critical_stockout_count: int
    expiry_risk_count: int
    currency: Literal["USD", "GBP"]


class MonthlyClinicSupplyPerformanceResponse(BaseModel):
    month_start: date
    clinics: List[ClinicSupplyPerformance]


class PipelineRunStatus(BaseModel):
    run_id: UUID
    pipeline_name: str
    trigger_type: str
    status: str
    phase: str
    month_start: date
    window_end: date
    queued_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    records_processed: Optional[int] = None
    duplicates_dropped: Optional[int] = None
    rows_invalid: Optional[int] = None
    partitions_inserted: Optional[int] = None
    partitions_updated: Optional[int] = None
    partitions_unchanged: Optional[int] = None
    partitions_removed: Optional[int] = None
    partitions_rejected: Optional[int] = None
    warnings: List[str]
    clinics_with_unrecorded_cost: List[str] = []
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    is_stale: bool


class PipelineRunTriggerRequest(BaseModel):
    """Cuerpo opcional. Sin month_start se procesa el ultimo mes cerrado."""

    month_start: Optional[date] = None


class PipelineRunQueued(BaseModel):
    run_id: UUID
    status: Literal["queued"]
    month_start: date
