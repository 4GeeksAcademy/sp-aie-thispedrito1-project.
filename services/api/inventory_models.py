from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class MedicalSupply(SQLModel, table=True):
    __tablename__ = "medical_supplies"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    sku: str = Field(unique=True, index=True)
    category: str
    unit: str
    country: str
    expiry_date: Optional[date] = None
    """Nullable: not every supply (e.g. reusable equipment) expires. Lets
    supply_expiry_flagged telemetry compare against 'today' per product."""


class SupplyDelivery(SQLModel, table=True):
    __tablename__ = "supply_deliveries"

    id: Optional[int] = Field(default=None, primary_key=True)
    supply_id: int = Field(foreign_key="medical_supplies.id")
    quantity: int
    vendor_name: str
    clinic_id: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user_uuid: str


class SupplyConsumption(SQLModel, table=True):
    __tablename__ = "supply_consumptions"

    id: Optional[int] = Field(default=None, primary_key=True)
    supply_id: int = Field(foreign_key="medical_supplies.id")
    quantity: int
    consumption_type: str
    clinic_id: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user_uuid: str


class SupplyThreshold(SQLModel, table=True):
    """Configurable minimum stock per supply+clinic. Missing row = no alert
    configured for that pair yet (stock_threshold_triggered simply never
    fires for it, rather than guessing a default)."""

    __tablename__ = "supply_thresholds"
    __table_args__ = (UniqueConstraint("supply_id", "clinic_id", name="uq_supply_clinic_threshold"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    supply_id: int = Field(foreign_key="medical_supplies.id")
    clinic_id: int
    minimum_quantity: int
