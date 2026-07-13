#!/usr/bin/env python3
"""HealthCore incident CSV analysis CLI.

Usage:
    python scripts/analyze.py incidents-healthcore.csv
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.incidents_analysis import (  # noqa: E402
    INVALID_RULE_ORDER,
    OPTIONAL_INVALID_RULES,
    VALID_CATEGORIES,
    VALID_STATUSES,
    analyze_rows,
    export_results_csv,
    load_csv_rows_from_path,
    pct,
)


def print_report(results: Dict[str, object], source_file: str) -> None:
    total_records = int(results["total_records"])
    valid_records = int(results["valid_records"])
    invalid_records = int(results["invalid_records"])

    invalid_reasons = dict(results["invalid_reasons"])
    category_counts = dict(results["category_counts"])
    status_counts = dict(results["status_counts"])
    country_counts = dict(results["country_counts"])
    score_counts = dict(results["score_counts"])

    scored_closed_cases = int(results["scored_closed_cases"])
    total_closed = int(results["total_closed"])
    average_score = float(results["average_score"])

    print("=" * 60)
    print("  HEALTHCORE - PATIENT INCIDENT REPORT ANALYSIS")
    print(f"  Source file: {source_file}")
    print("=" * 60)
    print()
    print(f"TOTAL RECORDS IN FILE .......... {total_records}")
    print(f"  |- Valid records ............... {valid_records}")
    print(f"  '- Invalid / incomplete ........ {invalid_records}")
    print()

    print("INVALID RECORDS BREAKDOWN")
    for index, (reason_key, reason_label) in enumerate(INVALID_RULE_ORDER):
        branch = "'" if index == len(INVALID_RULE_ORDER) - 1 else "|"
        print(f"  {branch}- {reason_label:.<30} {int(invalid_reasons.get(reason_key, 0))}")
    for reason_key, reason_label in OPTIONAL_INVALID_RULES:
        count = int(invalid_reasons.get(reason_key, 0))
        if count > 0:
            print(f"  '- {reason_label:.<30} {count}")
    print()

    print("BREAKDOWN BY CATEGORY (valid records)")
    for index, category in enumerate(VALID_CATEGORIES):
        branch = "'" if index == len(VALID_CATEGORIES) - 1 else "|"
        count = int(category_counts.get(category, 0))
        print(f"  {branch}- {category:.<28} {count:>3}  ({pct(count, valid_records):.1f}%)")
    print()

    print("BREAKDOWN BY STATUS (valid records)")
    for index, status in enumerate(VALID_STATUSES):
        branch = "'" if index == len(VALID_STATUSES) - 1 else "|"
        count = int(status_counts.get(status, 0))
        print(f"  {branch}- {status:.<31} {count:>3}  ({pct(count, valid_records):.1f}%)")
    print()

    print("BREAKDOWN BY COUNTRY (valid records) - recomendado")
    for index, country in enumerate(("US", "UK")):
        branch = "'" if index == 1 else "|"
        count = int(country_counts.get(country, 0))
        print(f"  {branch}- {country:.<33} {count:>3}  ({pct(count, valid_records):.1f}%)")
    print()

    print("SATISFACTION INDEX (closed cases)")
    print(f"  Scored cases: {scored_closed_cases} of {total_closed}")
    print(f"  Average score: {average_score:.2f} / 5.00")

    score_labels = {
        1: "Very dissatisfied",
        2: "Dissatisfied",
        3: "Neutral",
        4: "Satisfied",
        5: "Very satisfied",
    }
    for index, score in enumerate(range(1, 6)):
        branch = "'" if index == 4 else "|"
        label = score_labels[score]
        count = int(score_counts.get(str(score), 0))
        print(f"  {branch}- Score {score} ({label}) ... {count}")

    print()
    print("=" * 60)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/analyze.py <csv_file>")
        return 1

    csv_file = Path(sys.argv[1]).resolve()
    if not csv_file.exists() or not csv_file.is_file():
        print(f"Error: file not found: {csv_file}")
        return 1

    try:
        rows = load_csv_rows_from_path(csv_file)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    results = analyze_rows(rows)
    print_report(results, csv_file.name)

    try:
        export_choice = input("Deseas exportar los resultados a CSV? [s / n]: ").strip().lower()
    except EOFError:
        export_choice = "n"

    if export_choice in {"y", "s"}:
        output_path = Path("results.csv")
        export_results_csv(results, output_path)
        print(f"Results exported to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
