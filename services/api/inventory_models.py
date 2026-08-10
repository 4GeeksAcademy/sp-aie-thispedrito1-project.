from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class MedicalSupply(SQLModel, table=True):
    __tablename__ = "medical_supplies"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    sku: str = Field(unique=True, index=True)
    category: str
    unit: str
    country: str


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
