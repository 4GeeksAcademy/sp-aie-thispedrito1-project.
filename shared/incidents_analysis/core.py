from __future__ import annotations

import csv
import io
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

VALID_CLINICS: Dict[str, str] = {
    "US-TX-01": "US",
    "US-TX-02": "US",
    "US-TX-03": "US",
    "US-FL-01": "US",
    "US-FL-02": "US",
    "US-FL-03": "US",
    "US-GA-01": "US",
    "US-GA-02": "US",
    "US-GA-03": "US",
    "UK-LON-01": "UK",
    "UK-LON-02": "UK",
    "UK-MAN-01": "UK",
}

VALID_CATEGORIES: Tuple[str, ...] = (
    "APPOINTMENT",
    "BILLING",
    "CLINICAL_CARE",
    "ACCESSIBILITY",
    "ADMINISTRATIVE",
)

VALID_STATUSES: Tuple[str, ...] = ("OPEN", "CLOSED", "DISCARDED")

PATIENT_ID_PATTERN = re.compile(r"^PAT-\d{6}$")

INVALID_RULE_ORDER: Tuple[Tuple[str, str], ...] = (
    ("invalid_clinic", "Invalid or missing clinic_id"),
    ("country_clinic_mismatch", "Country/clinic mismatch"),
    ("invalid_category", "Invalid or missing category"),
    ("empty_description", "Empty description"),
    ("missing_patient_id", "Missing patient_id"),
    ("closed_without_score", "Closed case, no score"),
)

OPTIONAL_INVALID_RULES: Tuple[Tuple[str, str], ...] = (
    ("score_out_of_range", "Score out of range"),
    ("invalid_status", "Invalid or missing status"),
)

REQUIRED_COLUMNS: Tuple[str, ...] = (
    "incident_id",
    "date",
    "clinic_id",
    "country",
    "category",
    "description",
    "status",
    "patient_id",
)


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _to_int(raw_value: str) -> int | None:
    value = _clean(raw_value)
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _validate_columns(fieldnames: List[str] | None) -> None:
    if not fieldnames:
        raise ValueError("CSV has no header")
    missing = set(REQUIRED_COLUMNS).difference(set(fieldnames))
    if missing:
        missing_fields = ", ".join(sorted(missing))
        raise ValueError(f"CSV is missing required columns: {missing_fields}")


def load_csv_rows_from_path(csv_path: Path) -> List[Dict[str, str]]:
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            _validate_columns(reader.fieldnames)
            return [row for row in reader]
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must be UTF-8 encoded") from exc


def load_csv_rows_from_bytes(file_bytes: bytes) -> List[Dict[str, str]]:
    try:
        decoded = file_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must be UTF-8 encoded") from exc

    reader = csv.DictReader(io.StringIO(decoded))
    _validate_columns(reader.fieldnames)
    return [row for row in reader]


def analyze_rows(rows: List[Dict[str, str]]) -> Dict[str, object]:
    invalid_reasons = Counter(
        {
            **{rule: 0 for rule, _ in INVALID_RULE_ORDER},
            **{rule: 0 for rule, _ in OPTIONAL_INVALID_RULES},
        }
    )
    category_counts = Counter({category: 0 for category in VALID_CATEGORIES})
    status_counts = Counter({status: 0 for status in VALID_STATUSES})
    country_counts = Counter({"US": 0, "UK": 0})
    score_counts = Counter({score: 0 for score in range(1, 6)})

    valid_records = 0
    scored_closed_cases = 0

    for row in rows:
        clinic_id = _clean(row.get("clinic_id"))
        country = _clean(row.get("country")).upper()
        category = _clean(row.get("category")).upper()
        description = _clean(row.get("description"))
        status = _clean(row.get("status")).upper()
        patient_id = _clean(row.get("patient_id"))

        score_raw = row.get("satisfaction_score", "")
        score = _to_int(score_raw)
        score_present = _clean(score_raw) != ""

        row_invalid_reasons: set[str] = set()

        if clinic_id not in VALID_CLINICS:
            row_invalid_reasons.add("invalid_clinic")

        if clinic_id in VALID_CLINICS and country != VALID_CLINICS[clinic_id]:
            row_invalid_reasons.add("country_clinic_mismatch")

        if category not in VALID_CATEGORIES:
            row_invalid_reasons.add("invalid_category")

        if len(description) < 5:
            row_invalid_reasons.add("empty_description")

        if not PATIENT_ID_PATTERN.match(patient_id):
            row_invalid_reasons.add("missing_patient_id")

        if status == "CLOSED" and not score_present:
            row_invalid_reasons.add("closed_without_score")

        if score_present and (score is None or not (1 <= score <= 5)):
            row_invalid_reasons.add("score_out_of_range")

        if status not in VALID_STATUSES:
            row_invalid_reasons.add("invalid_status")

        if row_invalid_reasons:
            for reason in row_invalid_reasons:
                if reason in invalid_reasons:
                    invalid_reasons[reason] += 1
            continue

        valid_records += 1
        category_counts[category] += 1
        status_counts[status] += 1
        country_counts[country] += 1

        if status == "CLOSED" and score is not None and 1 <= score <= 5:
            scored_closed_cases += 1
            score_counts[score] += 1

    total_records = len(rows)
    invalid_records = total_records - valid_records
    total_closed = status_counts["CLOSED"]
    weighted_sum = sum(score * count for score, count in score_counts.items())
    average_score = (weighted_sum / scored_closed_cases) if scored_closed_cases else 0.0

    return {
        "total_records": total_records,
        "valid_records": valid_records,
        "invalid_records": invalid_records,
        "invalid_reasons": dict(invalid_reasons),
        "category_counts": dict(category_counts),
        "status_counts": dict(status_counts),
        "country_counts": dict(country_counts),
        "score_counts": {str(k): v for k, v in score_counts.items()},
        "scored_closed_cases": scored_closed_cases,
        "total_closed": total_closed,
        "average_score": average_score,
    }


def pct(count: int, total: int) -> float:
    if total == 0:
        return 0.0
    return (count / total) * 100


def _results_to_rows(results: Dict[str, object]) -> List[Dict[str, str]]:
    valid_records = int(results["valid_records"])

    invalid_reasons = dict(results["invalid_reasons"])
    category_counts = dict(results["category_counts"])
    status_counts = dict(results["status_counts"])
    country_counts = dict(results["country_counts"])
    score_counts = dict(results["score_counts"])

    rows: List[Dict[str, str]] = [
        {"metric": "total_records", "value": str(results["total_records"]), "percentage": ""},
        {"metric": "valid_records", "value": str(results["valid_records"]), "percentage": ""},
        {"metric": "invalid_records", "value": str(results["invalid_records"]), "percentage": ""},
    ]

    for reason_key, _ in INVALID_RULE_ORDER:
        rows.append(
            {
                "metric": f"invalid.{reason_key}",
                "value": str(invalid_reasons.get(reason_key, 0)),
                "percentage": "",
            }
        )

    for category in VALID_CATEGORIES:
        count = int(category_counts.get(category, 0))
        rows.append(
            {
                "metric": f"category.{category}",
                "value": str(count),
                "percentage": f"{pct(count, valid_records):.1f}",
            }
        )

    for status in VALID_STATUSES:
        count = int(status_counts.get(status, 0))
        rows.append(
            {
                "metric": f"status.{status}",
                "value": str(count),
                "percentage": f"{pct(count, valid_records):.1f}",
            }
        )

    for country in ("US", "UK"):
        count = int(country_counts.get(country, 0))
        rows.append(
            {
                "metric": f"country.{country}",
                "value": str(count),
                "percentage": f"{pct(count, valid_records):.1f}",
            }
        )

    for score in range(1, 6):
        rows.append(
            {
                "metric": f"satisfaction.score_{score}",
                "value": str(int(score_counts.get(str(score), 0))),
                "percentage": "",
            }
        )

    rows.append(
        {
            "metric": "satisfaction.average",
            "value": f"{float(results['average_score']):.2f}",
            "percentage": "",
        }
    )

    return rows


def csv_results_content(results: Dict[str, object]) -> str:
    rows = _results_to_rows(results)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["metric", "value", "percentage"])
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def export_results_csv(results: Dict[str, object], output_file: Path) -> None:
    output_file.write_text(csv_results_content(results), encoding="utf-8")
