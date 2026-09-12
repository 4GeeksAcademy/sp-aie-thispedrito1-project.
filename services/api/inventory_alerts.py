from __future__ import annotations

import uuid
from datetime import date

from sqlmodel import Session, select

import inventory_repository as repo
import telemetry_service
from telemetry_models import TelemetryEventRecord

EXPIRY_WINDOW_DAYS = 30

# Fixed namespace so the same (clinic, product, expiry_date) lot always maps
# to the same eventId, on any machine and across API restarts. Value is an
# arbitrary uuid4 generated once for this purpose — never change it, or
# every lot already flagged would be flagged again.
_EXPIRY_EVENT_NAMESPACE = uuid.UUID("5b0d6c3e-8a4f-4d7e-9a61-2f3c1b7e9d42")


def expiry_event_id(clinic_id: int, product_id: int, expiry_date: date) -> str:
    """Deterministic eventId for one lot at one clinic. One real-world fact
    ('this clinic's lot of this product is about to expire') = one id."""
    return str(uuid.uuid5(_EXPIRY_EVENT_NAMESPACE, f"supply_expiry_flagged:{clinic_id}:{product_id}:{expiry_date.isoformat()}"))


def _already_flagged(session: Session, event_id: str) -> bool:
    row = session.exec(
        select(TelemetryEventRecord.id).where(
            TelemetryEventRecord.event_type == "supply_expiry_flagged",
            TelemetryEventRecord.tags["eventId"].as_string() == event_id,
        )
    ).first()
    return row is not None


def flag_expiring_supplies(
    session: Session, *, days: int = EXPIRY_WINDOW_DAYS, today: date | None = None
) -> int:
    """Emits AND persists supply_expiry_flagged once per lot per clinic —
    the first time that clinic's stock of that lot enters the expiry window.
    Returns how many new events were emitted.

    Replaces the old startup check, which fired once per API start for every
    expiring supply (with uvicorn --reload, on every saved file), with
    clinic_id=None, and without persisting. The business pipeline's
    expiry_risk_count is literally "count of supply_expiry_flagged in the
    month" (CONTEXT), and that only equals "lots flagged" if each lot emits
    once: the deterministic eventId plus the existence check below make
    re-running this safe (idempotent) no matter how often it runs.

    Lots are identified by (product_id, expiry_date) because MedicalSupply
    has a single expiry_date per product — there is no batch table yet."""
    emitted = 0
    for supply, clinic_id, stock, days_until_expiry in repo.list_clinic_stock_expiring_within(
        session, days=days, today=today
    ):
        event_id = expiry_event_id(clinic_id, supply.id, supply.expiry_date)
        if _already_flagged(session, event_id):
            continue
        telemetry_service.emit_backend_event(
            event_type="supply_expiry_flagged",
            user_id=None,
            properties={
                "clinic_id": clinic_id,
                "country": supply.country,
                "product_id": supply.id,
                "product_category": supply.category,
                "expiry_date": supply.expiry_date.isoformat(),
                "days_until_expiry": days_until_expiry,
                "quantity_at_risk": stock,
            },
            db_session=session,
            event_id=event_id,
        )
        emitted += 1
    return emitted
