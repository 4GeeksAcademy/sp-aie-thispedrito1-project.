#!/usr/bin/env python3
"""Seed the incident manager with the historical customer-incident CSV.

Reads the legacy CSV from the incidents-file-analyzer project, validates each
row with the shared validation logic, applies the CSV -> model transformations
defined in the HealthCore CONTEXT, and inserts the valid records with
origin "customer". Idempotent: rows already seeded (tracked by the CSV
``incident_id``) are skipped on re-runs.

Usage (run with the API virtualenv, which has TinyDB installed):
    services/api/.venv/bin/python scripts/seed_incidents.py [path/to/incidents.csv]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "services" / "api"
for path in (str(ROOT_DIR), str(API_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from packages.shared.incidents_validation import (  # noqa: E402
    clean_row,
    load_csv_rows_from_path,
    validate_incident_payload,
    validate_row,
)

try:
    from incident_repository import IncidentRepository  # noqa: E402
except ModuleNotFoundError as exc:
    print(f"Error: missing dependency '{exc.name}'.")
    print("Run this script with the API virtualenv, e.g.:")
    print("  services/api/.venv/bin/python scripts/seed_incidents.py")
    raise SystemExit(1) from exc

DEFAULT_CSV_PATH = ROOT_DIR / "services" / "incidents-healthcore.csv"

# CSV -> model transformations defined in CONTEXT-incidencias-healthcore.
STATUS_MAP = {
    "OPEN": "open",
    "CLOSED": "resolved",
    "DISCARDED": "discarded",
}

CATEGORY_MAP = {
    "APPOINTMENT": "patient_experience",
    "BILLING": "billing_error",
    "CLINICAL_CARE": "patient_experience",
    "ACCESSIBILITY": "patient_experience",
    "ADMINISTRATIVE": "other",
}

BRANCH_MAP = {
    "US-TX-01": "central",
    "US-TX-02": "austin_north",
    "US-TX-03": "houston_med_center",
    "US-FL-01": "miami_brickell",
    "US-FL-02": "orlando_east",
    "US-FL-03": "tampa_bay",
    "US-GA-01": "atlanta_midtown",
    "US-GA-02": "atlanta_midtown",
    "US-GA-03": "savannah",
    "UK-LON-01": "london_city",
    "UK-LON-02": "london_west",
    "UK-MAN-01": "manchester_central",
}

TITLE_MAX_LENGTH = 120


def transform_row(cleaned: dict[str, object]) -> dict[str, str] | None:
    """Map a validated CSV row to an incident payload. Return None if untransformable."""
    description = str(cleaned["description"])
    title = description[:TITLE_MAX_LENGTH].strip()
    if not title:
        return None

    raw_date = str(cleaned["date"])
    try:
        year, month, day = raw_date.split("-")
        created_at = f"{int(year):04d}-{int(month):02d}-{int(day):02d}T00:00:00+00:00"
    except ValueError:
        return None

    return {
        "title": title,
        "description": description,
        "category": CATEGORY_MAP.get(str(cleaned["category"]), ""),
        "status": STATUS_MAP.get(str(cleaned["status"]), ""),
        "origin": "customer",
        "branch": BRANCH_MAP.get(str(cleaned["clinic_id"]), "central"),
        "created_at": created_at,
        "updated_at": created_at,
    }


def row_source_id(cleaned: dict[str, object], payload: dict[str, str] | None) -> str:
    incident_id = str(cleaned["incident_id"])
    if incident_id:
        return incident_id
    if payload:
        return f"{payload['title']}|{payload['created_at']}"
    return ""


def main() -> int:
    csv_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_CSV_PATH
    if not csv_path.is_file():
        print(f"Error: CSV file not found: {csv_path}")
        return 1

    try:
        rows = load_csv_rows_from_path(csv_path)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    repo = IncidentRepository()
    already_seeded = repo.existing_seed_source_ids()
    seen_in_run: set[str] = set()

    inserted = 0
    skipped_existing = 0
    invalid_rows: list[tuple[str, str]] = []

    for index, row in enumerate(rows, start=2):  # line 1 is the CSV header
        cleaned = clean_row(row)
        label = str(cleaned["incident_id"]) or f"line {index}"

        reasons = validate_row(cleaned)
        if reasons:
            invalid_rows.append((label, ", ".join(sorted(reasons))))
            continue

        payload = transform_row(cleaned)
        if payload is None:
            invalid_rows.append((label, "untransformable title or date"))
            continue

        model_errors = validate_incident_payload(payload)
        if model_errors:
            details = "; ".join(f"{err['field']}: {err['message']}" for err in model_errors)
            invalid_rows.append((label, details))
            continue

        source_id = row_source_id(cleaned, payload)
        if source_id in already_seeded or source_id in seen_in_run:
            skipped_existing += 1
            continue
        seen_in_run.add(source_id)

        record = dict(payload)
        record["source_id"] = source_id
        repo.insert_seed(record)
        inserted += 1

    print("=" * 60)
    print("  SEED INCIDENTS - HISTORICAL CUSTOMER CSV")
    print(f"  Source file: {csv_path.name}")
    print("=" * 60)
    print(f"Rows in file ............ {len(rows)}")
    print(f"Inserted ................ {inserted}")
    print(f"Skipped (already seeded)  {skipped_existing}")
    print(f"Invalid (not inserted) .. {len(invalid_rows)}")

    if invalid_rows:
        print()
        print("INVALID ROWS (reported, not inserted):")
        for label, reason in invalid_rows:
            print(f"  - {label}: {reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
