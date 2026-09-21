from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

import inventory_repository as repo
import telemetry_service
from cache import cache
from database import get_inventory_db
from inventory_models import MedicalSupply
from models import (
    DirectStockEditAttempt,
    DirectStockEditRejection,
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
        expiry_date=supply.expiry_date,
    )


def _check_stock_threshold(
    session: Session, supply: MedicalSupply, clinic_id: int, user_id: str, quantity_delta: int
) -> None:
    """Emits stock_threshold_triggered only when this order makes the
    clinic's stock CROSS its configured minimum: above it before the order,
    at/below it after. quantity_delta is the stock movement the order just
    committed (+quantity inbound, -quantity outbound), so the pre-order
    stock is derived without a second read.

    Crossing-only on purpose: the old "stock <= threshold after any order"
    rule re-fired on every order while the clinic stayed below minimum, so
    counting events measured order activity, not stockouts. The business
    pipeline's critical_stockout_count is literally "count of
    stock_threshold_triggered in the month" (CONTEXT, data/pipelines/
    PIPELINE_DESIGN.md), and that count only means "times a clinic fell
    below minimum" if each fall emits exactly once. Consequence: a clinic
    that was already below minimum when the threshold was configured emits
    nothing until it recovers and falls again.

    Persisted to telemetry_events via the request's own session (same
    Depends(get_inventory_db) the tests override), which is what makes the
    KPI computable at all; emit_backend_event swallows persistence errors
    so a telemetry hiccup never fails an order already committed."""
    threshold = repo.get_threshold(session, supply.id, clinic_id)
    if threshold is None:
        return  # no minimum configured for this supply+clinic yet
    stock_after = repo.get_current_stock_for_clinic(session, supply.id, clinic_id)
    stock_before = stock_after - quantity_delta
    if stock_before > threshold >= stock_after:
        telemetry_service.emit_backend_event(
            event_type="stock_threshold_triggered",
            user_id=user_id,
            properties={
                "clinic_id": clinic_id,
                "country": supply.country,
                "product_id": supply.id,
                "product_category": supply.category,
                "current_stock": stock_after,
                "threshold_value": threshold,
            },
            db_session=session,
        )


PRODUCTS_CACHE_KEY = "inventory_products"
PRODUCTS_CACHE_TTL_SECONDS = 30


@router.get("/products", response_model=list[MedicalSupplyRead])
def list_products(session: Session = Depends(get_inventory_db)) -> list[MedicalSupplyRead]:
    cached = cache.get(PRODUCTS_CACHE_KEY)
    if cached is not None:
        return cached

    supplies = repo.list_supplies(session)
    result = [_to_supply_read(session, supply) for supply in supplies]
    cache.set(PRODUCTS_CACHE_KEY, result, PRODUCTS_CACHE_TTL_SECONDS)
    return result


@router.post("/products", response_model=MedicalSupplyRead, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: MedicalSupplyCreate, session: Session = Depends(get_inventory_db)
) -> MedicalSupplyRead:
    # Not mode="json" here: that would serialize expiry_date to an ISO
    # string, and SQLite's Date column type (used by the test suite) only
    # accepts real date objects, unlike Postgres which is more lenient.
    data = payload.model_dump()
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
    cache.invalidate(PRODUCTS_CACHE_KEY)
    _check_stock_threshold(session, supply, payload.clinic_id, current_user["id"], quantity_delta=payload.quantity)
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
        telemetry_service.emit_backend_event(
            event_type="outbound_order_rejected",
            user_id=current_user["id"],
            properties={
                "clinic_id": payload.clinic_id,
                "country": supply.country,
                "product_id": supply.id,
                "product_category": supply.category,
                "quantity_requested": payload.quantity,
                "current_stock": exc.available,
                "department": None,
            },
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cache.invalidate(PRODUCTS_CACHE_KEY)
    _check_stock_threshold(session, supply, payload.clinic_id, current_user["id"], quantity_delta=-payload.quantity)
    return SupplyConsumptionRead.model_validate(consumption)


@router.patch(
    "/products/{supply_id}/stock",
    response_model=DirectStockEditRejection,
    responses={400: {"model": DirectStockEditRejection}},
)
def reject_direct_stock_edit(
    supply_id: int,
    payload: DirectStockEditAttempt,
    session: Session = Depends(get_inventory_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> None:
    """No route is ever allowed to write current_stock directly — it only
    ever moves through /orders/inbound and /orders/outbound, per
    CONTEXT-healthcore.es.md section 6. This endpoint exists specifically to
    give that rule a concrete, always-400 enforcement point instead of a
    generic 404, and to emit direct_stock_edit_rejected when it's hit."""
    supply = repo.get_supply(session, supply_id)
    if supply is None:
        raise HTTPException(status_code=404, detail="Medical supply not found")

    telemetry_service.emit_backend_event(
        event_type="direct_stock_edit_rejected",
        user_id=current_user["id"],
        properties={
            "clinic_id": payload.clinic_id,
            "country": supply.country,
            "product_id": supply.id,
            "product_category": supply.category,
            "attempted_field": "current_stock",
        },
    )
    raise HTTPException(
        status_code=400,
        detail="Direct stock edits are not allowed. Use /inventory/orders/inbound or /inventory/orders/outbound.",
    )


@router.get("/orders", response_model=list[InventoryOrderRead])
def list_orders(session: Session = Depends(get_inventory_db)) -> list[InventoryOrderRead]:
    return repo.list_orders_with_supply_data(session)
