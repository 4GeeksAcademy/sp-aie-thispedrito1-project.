from __future__ import annotations

import sys
from datetime import date, timedelta

from sqlmodel import Session, SQLModel, select

import inventory_repository as repo
from database import get_inventory_engine
from inventory_models import MedicalSupply

# Computed relative to today so supply_expiry_flagged (30-day window) fires
# whenever this seed is run, instead of going stale against a hardcoded date.
NEAR_EXPIRY_DATE = date.today() + timedelta(days=15)

SUPPLIES_SEED = [
    {"name": "Guantes de nitrilo (caja de 100)", "sku": "HCR-PPE-001", "category": "ppe", "unit": "box", "country": "US"},
    {"name": "Mascarilla quirúrgica (pack de 50)", "sku": "HCR-PPE-002", "category": "ppe", "unit": "pack", "country": "UK"},
    {"name": "Apósito adhesivo para heridas", "sku": "HCR-WND-001", "category": "wound_care", "unit": "box", "country": "US"},
    {"name": "Test rápido de estreptococo", "sku": "HCR-DIAG-001", "category": "diagnostics", "unit": "unit", "country": "US"},
    {"name": "Tiras reactivas glucemia (50)", "sku": "HCR-DIAG-002", "category": "diagnostics", "unit": "box", "country": "UK"},
    {"name": "Solución salina 0,9% 500ml", "sku": "HCR-MED-001", "category": "medications", "unit": "vial", "country": "US", "expiry_date": NEAR_EXPIRY_DATE},
]

# supply_id gets resolved to the real row id at insert time, keyed by sku.
# Thresholds picked so two combos land below minimum with the seeded
# deliveries/consumptions above (stock_threshold_triggered demo) and one
# stays comfortably above it (a realistic non-triggering baseline).
THRESHOLDS_SEED = [
    {"sku": "HCR-PPE-001", "clinic_id": 1, "minimum_quantity": 200},  # stock ends at 160 -> triggers
    {"sku": "HCR-DIAG-001", "clinic_id": 2, "minimum_quantity": 75},  # stock ends at 70 -> triggers
    {"sku": "HCR-PPE-002", "clinic_id": 10, "minimum_quantity": 50},  # stock ends at 285 -> does not trigger
]

# user_uuid values are placeholders — replace with real TinyDB user ids if you
# want the seeded orders to trace back to an actual account in your local DB.
SEED_USER_UUID = "00000000-0000-0000-0000-000000000001"

DELIVERIES_SEED = [
    {"sku": "HCR-PPE-001", "quantity": 200, "vendor_name": "MedLine Industries", "clinic_id": 1},
    {"sku": "HCR-PPE-001", "quantity": 150, "vendor_name": "Bound Tree Medical", "clinic_id": 4},
    {"sku": "HCR-PPE-002", "quantity": 300, "vendor_name": "Cardinal Health UK", "clinic_id": 10},
    {"sku": "HCR-DIAG-001", "quantity": 80, "vendor_name": "MedLine Industries", "clinic_id": 2},
]

CONSUMPTIONS_SEED = [
    {"sku": "HCR-PPE-001", "quantity": 40, "consumption_type": "clinical_use", "clinic_id": 1},
    {"sku": "HCR-PPE-002", "quantity": 15, "consumption_type": "expiry_waste", "clinic_id": 10},
    {"sku": "HCR-DIAG-001", "quantity": 10, "consumption_type": "clinical_use", "clinic_id": 2},
]


def run_seed() -> tuple[int, int]:
    engine = get_inventory_engine()
    SQLModel.metadata.create_all(engine)

    inserted = 0
    skipped = 0

    with Session(engine) as session:
        supplies_by_sku: dict[str, MedicalSupply] = {}

        for item in SUPPLIES_SEED:
            existing = session.exec(select(MedicalSupply).where(MedicalSupply.sku == item["sku"])).first()
            if existing:
                supplies_by_sku[item["sku"]] = existing
                skipped += 1
                continue
            created = repo.create_supply(session, item)
            supplies_by_sku[item["sku"]] = created
            inserted += 1

        for item in DELIVERIES_SEED:
            supply = supplies_by_sku[item["sku"]]
            repo.create_delivery(
                session,
                {
                    "supply_id": supply.id,
                    "quantity": item["quantity"],
                    "vendor_name": item["vendor_name"],
                    "clinic_id": item["clinic_id"],
                    "user_uuid": SEED_USER_UUID,
                },
            )
            inserted += 1

        for item in CONSUMPTIONS_SEED:
            supply = supplies_by_sku[item["sku"]]
            repo.create_consumption(
                session,
                {
                    "supply_id": supply.id,
                    "quantity": item["quantity"],
                    "consumption_type": item["consumption_type"],
                    "clinic_id": item["clinic_id"],
                    "user_uuid": SEED_USER_UUID,
                },
            )
            inserted += 1

        for item in THRESHOLDS_SEED:
            supply = supplies_by_sku[item["sku"]]
            existing = session.exec(
                select(repo.SupplyThreshold).where(
                    repo.SupplyThreshold.supply_id == supply.id,
                    repo.SupplyThreshold.clinic_id == item["clinic_id"],
                )
            ).first()
            if existing:
                skipped += 1
                continue
            repo.set_threshold(session, supply.id, item["clinic_id"], item["minimum_quantity"])
            inserted += 1

    return inserted, skipped


def main() -> int:
    try:
        inserted, skipped = run_seed()
    except RuntimeError as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        return 1
    except repo.InsufficientStockError as exc:
        print(f"Seed failed: seeded consumption exceeds seeded delivery: {exc}", file=sys.stderr)
        return 1

    print(f"Inventory seed completed. Inserted: {inserted}. Skipped (already existed): {skipped}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
