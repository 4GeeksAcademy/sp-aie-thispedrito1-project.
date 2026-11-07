"""Ingesta: formato común con origen, registros dudosos y datos sensibles."""

from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal

import pytest

from fc_helpers import LOAD_DATE, PATIENT_NAME, PERIOD
from services.financial_close import ingestion, seed_data
from services.financial_close.catalog import LINES


def test_closing_month_is_last_complete_month():
    assert seed_data.closing_month(LOAD_DATE) == PERIOD


def test_each_line_has_several_records_summing_exactly_to_the_briefing(dataset):
    sums, counts = defaultdict(Decimal), defaultdict(int)
    for record in dataset.ledger:
        if record.is_ok:
            key = f"{record.country}.{record.payer}.{record.category}"
            sums[key] += abs(record.amount)
            counts[key] += 1
    for line in LINES:
        if line.actual is None:
            assert line.key not in sums  # "Sin registros": no se inventa ninguno
        else:
            assert sums[line.key] == line.actual, line.key
            assert counts[line.key] >= 2, line.key


def test_records_keep_their_origin(dataset):
    first = dataset.ledger[0]
    assert first.source_file == "ledger.csv" and first.source_row == 2
    assert first.entry_id == "JE-2026OCT-0001" and first.source_ref.startswith("REMIT-")
    assert all(b.source_file == "budget.csv" and b.source_row >= 2 for b in dataset.budget)


def test_revenue_is_aggregated_by_clinic_and_payer(dataset):
    revenue = [r for r in dataset.ledger if r.category == "patient_services"]
    assert all("aggregated" in r.description for r in revenue)


def test_supplier_records_carry_quantity_and_unit_price(dataset):
    lab = [r for r in dataset.ledger if r.is_ok and r.category == "laboratory" and r.country == "US"]
    assert {r.unit_price for r in lab} == {Decimal("32.10")}
    assert sum(r.quantity for r in lab) == 1500


def test_patient_record_is_doubtful_and_its_description_is_withheld(dataset):
    flagged = [r for r in dataset.ledger if not r.is_ok]
    assert len(flagged) == 1
    record = flagged[0]
    assert record.issues == ("sensitive_data:patient_data",)
    assert record.description is None and record.description_withheld
    assert len(record.description_sha256) == 64
    assert PATIENT_NAME not in repr(record)


def _ledger_row(**overrides):
    row = {"entry_id": "JE-X-1", "date": "2026-10-05", "country": "US", "payer": "n/a", "category": "laboratory",
           "description": "ClearPath Labs - invoice 1/1: 10 tests @ 30.00 USD", "currency": "USD",
           "amount": "-300.00", "source_ref": "AP-1"}
    row.update(overrides)
    return row


@pytest.mark.parametrize("overrides, issue", [
    ({"amount": "300.00"}, "sign_inconsistent"),
    ({"currency": "GBP"}, "currency_mismatch"),
    ({"amount": "-310.00"}, "unit_price_mismatch"),
    ({"description": "ClearPath Labs monthly invoice"}, "missing_unit_detail"),
    ({"date": "2026-09-30"}, "outside_period"),
    ({"amount": "abc"}, "invalid_amount"),
    ({"source_ref": ""}, "missing_field"),
    ({"payer": "private"}, "payer_on_expense"),
    ({"category": "patient_services", "payer": "unknown", "amount": "10"}, "unknown_payer"),
    ({"description": "ClearPath Labs 10 tests @ 30.00 USD, IBAN GB29NWBK60161331926819"},
     "sensitive_data:bank_details"),
    ({"category": "payroll", "description": "Bonus for Dr. Laura Mendes, October"}, "sensitive_data:individual_salary"),
])
def test_inconsistent_records_are_marked_doubtful(overrides, issue):
    record = ingestion.normalize_ledger_row(_ledger_row(**overrides), 2, PERIOD)
    assert record.status == "doubtful" and issue in record.issues


def test_clean_record_is_ok():
    record = ingestion.normalize_ledger_row(_ledger_row(), 2, PERIOD)
    assert record.is_ok and record.quantity == 10 and record.unit_price == Decimal("30.00")


def test_duplicate_entry_id_is_doubtful(tmp_path):
    path = tmp_path / "ledger.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=seed_data.LEDGER_COLUMNS)
        writer.writeheader()
        writer.writerow(_ledger_row())
        writer.writerow(_ledger_row())
    records = ingestion.read_ledger(path, PERIOD)
    assert records[0].is_ok and "duplicate_entry_id" in records[1].issues


def test_missing_file_is_declared_not_fatal(tmp_path, data_dir):
    (tmp_path / "budget.csv").write_bytes((data_dir / "budget.csv").read_bytes())
    dataset = ingestion.read_dataset(tmp_path, PERIOD)
    assert dataset.missing_files == ["ledger.csv", "operations.csv"]
    assert dataset.budget and not dataset.ledger


def test_missing_column_fails_loudly(tmp_path):
    (tmp_path / "ledger.csv").write_text("entry_id,amount\nX,1\n")
    with pytest.raises(ValueError):
        ingestion.read_ledger(tmp_path / "ledger.csv", PERIOD)


def test_generation_is_deterministic(tmp_path):
    seed_data.generate_all(tmp_path / "a", LOAD_DATE)
    seed_data.generate_all(tmp_path / "b", LOAD_DATE)
    for name in ("ledger.csv", "budget.csv", "operations.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_incomplete_scenario_drops_medicare_operations(incomplete_dir):
    dataset = ingestion.read_dataset(incomplete_dir, PERIOD)
    assert not [o for o in dataset.operations if o.payer == "medicare_medicaid"]
