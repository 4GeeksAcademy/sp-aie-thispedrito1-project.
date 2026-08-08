from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

import inventory_repository as repo
from database import get_inventory_db
from inventory_models import MedicalSupply
from models import (
    InventoryOrderRead,
    MedicalSupplyCreate,
    MedicalSupplyRead,
    SupplyConsumptionCreate,
    SupplyConsumptionRead,
    SupplyDeliveryCreate,
    SupplyDeliveryRead,
)
from security import get_current_user

router = APIRouter(prefix="/inventory", tags=["inventory"], dependencies=[Depends(get_current_user)])


def _to_supply_read(session: Session, supply: MedicalSupply) -> MedicalSupplyRead:
    current_stock = repo.get_current_stock(session, supply.id)
    return MedicalSupplyRead(
        id=supply.id,
        name=supply.name,
        sku=supply.sku,
        category=supply.category,
        unit=supply.unit,
        country=supply.country,
        current_stock=current_stock,
    )


@router.get("/products", response_model=list[MedicalSupplyRead])
def list_products(session: Session = Depends(get_inventory_db)) -> list[MedicalSupplyRead]:
    supplies = repo.list_supplies(session)
    return [_to_supply_read(session, supply) for supply in supplies]


@router.post("/products", response_model=MedicalSupplyRead, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: MedicalSupplyCreate, session: Session = Depends(get_inventory_db)
) -> MedicalSupplyRead:
    data = payload.model_dump(mode="json")
    supply = repo.create_supply(session, data)
    return _to_supply_read(session, supply)


@router.get("/products/{supply_id}", response_model=MedicalSupplyRead)
def get_product(supply_id: int, session: Session = Depends(get_inventory_db)) -> MedicalSupplyRead:
    supply = repo.get_supply(session, supply_id)
    if supply is None:
        raise HTTPException(status_code=404, detail="Medical supply not found")
    return _to_supply_read(session, supply)


@router.post("/orders/inbound", response_model=SupplyDeliveryRead, status_code=status.HTTP_201_CREATED)
def create_inbound_order(
    payload: SupplyDeliveryCreate,
    session: Session = Depends(get_inventory_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> SupplyDeliveryRead:
    supply = repo.get_supply(session, payload.supply_id)
    if supply is None:
        raise HTTPException(status_code=404, detail="Medical supply not found")

    data = payload.model_dump()
    data["user_uuid"] = current_user["id"]
    delivery = repo.create_delivery(session, data)
    return SupplyDeliveryRead.model_validate(delivery)


@router.post("/orders/outbound", response_model=SupplyConsumptionRead, status_code=status.HTTP_201_CREATED)
def create_outbound_order(
    payload: SupplyConsumptionCreate,
    session: Session = Depends(get_inventory_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> SupplyConsumptionRead:
    supply = repo.get_supply(session, payload.supply_id)
    if supply is None:
        raise HTTPException(status_code=404, detail="Medical supply not found")

    data = payload.model_dump(mode="json")
    data["user_uuid"] = current_user["id"]
    try:
        consumption = repo.create_consumption(session, data)
    except repo.InsufficientStockError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SupplyConsumptionRead.model_validate(consumption)


@router.get("/orders", response_model=list[InventoryOrderRead])
def list_orders(session: Session = Depends(get_inventory_db)) -> list[InventoryOrderRead]:
    return repo.list_orders_with_supply_data(session)
