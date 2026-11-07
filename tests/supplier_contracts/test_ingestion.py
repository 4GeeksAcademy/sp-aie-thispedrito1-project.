"""Ingesta: datos del BRIEFING, referencias de origen y extracción de contratos."""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal

import pytest

from services.supplier_contracts import ingestion, seed_data
from services.supplier_contracts.catalog import CONTRACTS, SENSITIVE_BILLING, SUPPLIERS
from services.supplier_contracts.models import TERM_DOUBTFUL, TERM_MISSING, TERM_OK

LOAD_DATE = date(2026, 11, 7)  # la misma fecha de carga que conftest.py


def _prices(dataset, supplier_id):
    return sorted((p for p in dataset.prices if p["supplier_id"] == supplier_id), key=lambda p: p["month"])


def test_price_csv_has_the_briefing_columns_and_twelve_months(data_dir):
    with (data_dir / "price_history.csv").open() as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == ["supplier_id", "product", "unit", "currency", "month", "unit_price", "monthly_volume"]
        rows = list(reader)
    assert len(rows) == 7 * 12
    months = sorted({r["month"] for r in rows})
    assert months[0] == "2025-12" and months[-1] == "2026-11"  # el último mes es el de carga


def test_every_price_keeps_its_source_row(dataset, data_dir):
    lines = (data_dir / "price_history.csv").read_text().splitlines()
    for price in dataset.prices:
        line = lines[price["source_row"] - 1]  # la cabecera es la fila 1
        assert line.startswith(f"{price['supplier_id']},") and f"{price['unit_price']:.4f}" in line


@pytest.mark.parametrize("supplier_id,change", [("HC-S03", "7.00"), ("HC-S01", "4.50"), ("HC-S06", "2.00"), ("HC-S07", "0.00")])
def test_last_month_reproduces_briefing_scenarios(dataset, supplier_id, change):
    from services.supplier_contracts.calculations import pct_change

    rows = _prices(dataset, supplier_id)
    assert pct_change(rows[-2]["unit_price"], rows[-1]["unit_price"]) == Decimal(change)


def test_first_eleven_months_drift_at_most_one_percent(dataset):
    from services.supplier_contracts.calculations import pct_change

    for spec in SUPPLIERS:
        rows = _prices(dataset, spec.supplier_id)
        assert rows[0]["unit_price"] == spec.base_price
        for prev, cur in zip(rows[:-2], rows[1:-1]):
            assert abs(pct_change(prev["unit_price"], cur["unit_price"])) <= Decimal("1.00")
        assert all(r["monthly_volume"] == spec.monthly_volume for r in rows)


def test_invalid_rows_are_rejected_not_fixed(tmp_path, dataset):
    path = tmp_path / "price_history.csv"
    good = "HC-S01,Disposable consultation kit,kit,USD,2026-01,3.2000,14000"
    path.write_text("\n".join([
        "supplier_id,product,unit,currency,month,unit_price,monthly_volume",
        good,
        "HC-S01,Disposable consultation kit,kit,GBP,2026-02,3.2000,14000",  # moneda incorrecta
        "HC-S99,Kit,kit,USD,2026-02,3.2000,1",  # proveedor desconocido
        "HC-S01,Disposable consultation kit,kit,USD,2026-1,3.2000,14000",  # mes mal formado
        "HC-S01,Disposable consultation kit,kit,USD,2026-03,,14000",  # precio vacío
        good,  # duplicado
    ]) + "\n")
    suppliers = {s["supplier_id"]: s for s in dataset.suppliers}
    rows, rejected = ingestion.load_prices(path, suppliers)
    assert len(rows) == 1 and rows[0]["source_row"] == 2
    assert [r.row for r in rejected] == [3, 4, 5, 6, 7]


def test_wrong_columns_are_rejected(tmp_path, dataset):
    path = tmp_path / "price_history.csv"
    path.write_text("supplier,price\nHC-S01,3\n")
    with pytest.raises(ValueError):
        ingestion.load_prices(path, {})


def test_contracts_match_the_briefing_table(dataset):
    by_id = {c.contract_id: c for c in dataset.contracts}
    assert set(by_id) == {"CT-HC-01", "CT-HC-02", "CT-HC-03", "CT-HC-04"}
    for spec in CONTRACTS:
        terms = by_id[spec.contract_id].terms
        assert by_id[spec.contract_id].supplier_id == spec.supplier_id
        assert terms["expiry_date"].value["date"] == date.fromordinal(LOAD_DATE.toordinal() + spec.expiry_in_days).isoformat()
        assert terms["non_renewal_notice"].value["days"] == spec.non_renewal_notice_days
        assert terms["price_review"].value["type"] == spec.review_type
        assert Decimal(terms["annual_value"].value["amount"]) == spec.annual_value
        assert terms["annual_value"].value["currency"] == spec.currency


def test_every_extracted_term_cites_clause_and_page(dataset):
    for contract in dataset.contracts:
        for term in contract.terms.values():
            if term.status == TERM_MISSING:
                continue
            assert term.clause and term.page in (1, 2, 3) and term.quote
            assert term.confidence >= 0.8 and term.status == TERM_OK


def test_page_numbers_are_the_real_pdf_pages(dataset):
    terms = {c.contract_id: c.terms for c in dataset.contracts}["CT-HC-02"]
    assert (terms["expiry_date"].clause, terms["expiry_date"].page) == ("2.1", 1)
    assert (terms["price_review"].clause, terms["price_review"].page) == ("4.2", 2)
    assert terms["exit_terms"].value["penalty_months"] == 2


def test_thames_contract_has_no_dpa_and_confidentiality_is_not_mistaken_for_one(dataset):
    thames = {c.contract_id: c for c in dataset.contracts}["CT-HC-03"]
    assert thames.terms["data_protection"].status == TERM_MISSING
    assert thames.terms["data_protection"].clause is None


def test_ambiguous_clause_is_marked_doubtful_not_accepted():
    pages = [
        "2. Term and Expiry\n2.1 This Agreement expires twelve months after signature.\n",
        "4. Pricing\n4.2 Any price increase shall be agreed between the parties.\n",
    ]
    terms = ingestion.extract_terms(ingestion.split_clauses(pages))
    assert terms["expiry_date"].status == TERM_DOUBTFUL and terms["expiry_date"].value == {"date": None}
    assert terms["price_review"].status == TERM_DOUBTFUL and terms["price_review"].value["type"] is None
    assert terms["non_renewal_notice"].status == TERM_MISSING


def test_contradictory_clauses_are_doubtful():
    pages = ["2. Term\n2.1 This Agreement expires on 2027-01-01 (the \"Expiry Date\").\n"
             "2.2 For the avoidance of doubt, this Agreement expires on 2027-06-01 (the \"Expiry Date\").\n"]
    term = ingestion.extract_terms(ingestion.split_clauses(pages))["expiry_date"]
    assert term.status == TERM_DOUBTFUL and term.confidence < 0.8


def test_wrapped_lines_are_joined_into_their_clause():
    pages = ["3. Renewal\n3.1 Either party gives written notice of\nnon-renewal no later than 45 days before\nthe Expiry Date.\n"]
    term = ingestion.extract_terms(ingestion.split_clauses(pages))["non_renewal_notice"]
    assert term.value == {"days": 45} and term.status == TERM_OK


def test_billing_clause_is_never_quoted(dataset):
    for contract in dataset.contracts:
        quotes = " ".join(t.quote or "" for t in contract.terms.values())
        for value in SENSITIVE_BILLING[contract.contract_id].values():
            assert value not in quotes
        assert all(t.clause is None or not t.clause.startswith("8.") for t in contract.terms.values())


def test_generator_is_deterministic(tmp_path):
    assert seed_data.build_price_rows(LOAD_DATE) == seed_data.build_price_rows(LOAD_DATE)
