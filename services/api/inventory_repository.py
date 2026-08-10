from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from inventory_models import MedicalSupply, SupplyConsumption, SupplyDelivery


class InsufficientStockError(Exception):
    """Raised when an outbound order would push a supply's stock below zero."""

    def __init__(self, supply_name: str, available: int, requested: int) -> None:
        self.supply_name = supply_name
        self.available = available
        self.requested = requested
        super().__init__(
            f"Insufficient stock for supply '{supply_name}'. "
            f"Available: {available}, requested: {requested}."
        )


def get_current_stock(session: Session, supply_id: int) -> int:
    deliveries = session.exec(
        select(SupplyDelivery).where(SupplyDelivery.supply_id == supply_id)
    ).all()
    consumptions = session.exec(
        select(SupplyConsumption).where(SupplyConsumption.supply_id == supply_id)
    ).all()
    return sum(delivery.quantity for delivery in deliveries) - sum(
        consumption.quantity for consumption in consumptions
    )


def create_supply(session: Session, data: dict[str, Any]) -> MedicalSupply:
    supply = MedicalSupply(**data)
    session.add(supply)
    session.commit()
    session.refresh(supply)
    return supply


def list_supplies(session: Session) -> list[MedicalSupply]:
    return list(session.exec(select(MedicalSupply)).all())


def get_supply(session: Session, supply_id: int) -> MedicalSupply | None:
    return session.get(MedicalSupply, supply_id)


def create_delivery(session: Session, data: dict[str, Any]) -> SupplyDelivery:
    delivery = SupplyDelivery(**data)
    session.add(delivery)
    session.commit()
    session.refresh(delivery)
    return delivery


def create_consumption(session: Session, data: dict[str, Any]) -> SupplyConsumption:
    supply = get_supply(session, data["supply_id"])
    if supply is None:
        raise ValueError("Medical supply not found")

    available = get_current_stock(session, data["supply_id"])
    if data["quantity"] > available:
        raise InsufficientStockError(supply.name, available, data["quantity"])

    consumption = SupplyConsumption(**data)
    session.add(consumption)
    session.commit()
    session.refresh(consumption)
    return consumption


def list_orders_with_supply_data(session: Session) -> list[dict[str, Any]]:
    """Both order types, each with the supply's name/sku already attached.

    Supplies are fetched once into a dict and reused for every order below —
    the alternative (looking up the supply inside the loop) is the classic N+1
    query problem the milestone brief warns about.
    """
    supplies = {s.id: s for s in session.exec(select(MedicalSupply)).all()}
    deliveries = session.exec(select(SupplyDelivery)).all()
    consumptions = session.exec(select(SupplyConsumption)).all()

    orders: list[dict[str, Any]] = []
    for delivery in deliveries:
        supply = supplies.get(delivery.supply_id)
        orders.append(
            {
                "order_type": "inbound",
                "id": delivery.id,
                "supply_id": delivery.supply_id,
                "supply_name": supply.name if supply else "Unknown supply",
                "supply_sku": supply.sku if supply else "",
                "quantity": delivery.quantity,
                "clinic_id": delivery.clinic_id,
                "created_at": delivery.created_at,
                "user_uuid": delivery.user_uuid,
                "vendor_name": delivery.vendor_name,
                "consumption_type": None,
            }
        )
    for consumption in consumptions:
        supply = supplies.get(consumption.supply_id)
        orders.append(
            {
                "order_type": "outbound",
                "id": consumption.id,
                "supply_id": consumption.supply_id,
                "supply_name": supply.name if supply else "Unknown supply",
                "supply_sku": supply.sku if supply else "",
                "quantity": consumption.quantity,
                "clinic_id": consumption.clinic_id,
                "created_at": consumption.created_at,
                "user_uuid": consumption.user_uuid,
                "vendor_name": None,
                "consumption_type": consumption.consumption_type,
            }
        )

    orders.sort(key=lambda order: order["created_at"])
    return orders
