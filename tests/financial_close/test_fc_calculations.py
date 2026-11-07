"""Herramientas de cálculo deterministas (README: "Cubre cada herramienta con pruebas")."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fc_helpers import LAB_US, MEDICARE, PERIOD
from services.financial_close import calculations as calc
from services.financial_close import drivers, ingestion
from services.financial_close.ingestion import BudgetRecord, LedgerRecord


def _comparisons(dataset):
    return calc.compare_to_budget(calc.compute_line_totals(dataset.ledger), dataset.budget)


def _record(entry_id, amount, category="laboratory", status="ok", country="US", payer="n/a", currency="USD"):
    return LedgerRecord(entry_id=entry_id, entry_date=PERIOD, country=country, payer=payer, category=category,
                        currency=currency, amount=Decimal(amount), source_ref="R", source_row=2, description="d",
                        description_sha256="0" * 64, status=status, issues=())


def test_line_totals_exclude_doubtful_records():
    totals = calc.compute_line_totals([_record("A", "-100"), _record("B", "-50"), _record("C", "-999", status="doubtful")])
    assert totals["US.n/a.laboratory"].amount == Decimal("150.00")
    assert totals["US.n/a.laboratory"].entry_ids == ("A", "B")


def test_variance_absolute_and_percentage():
    assert calc.variance(Decimal("520000"), Decimal("468000")) == (Decimal("-52000.00"), Decimal("-10"))
    absolute, pct = calc.variance(Decimal("0"), Decimal("10"))
    assert absolute == Decimal("10.00") and pct is None


@pytest.mark.parametrize("pct, relevant", [
    (Decimal("5"), True), (Decimal("-5"), True), (Decimal("4.96"), False), (Decimal("-4.999"), False),
    (Decimal("7"), True), (None, False),
])
def test_threshold_uses_the_exact_value(pct, relevant):
    # 4,96 % se mostraría como "5,0 %" pero NO llega al umbral.
    assert calc.is_relevant(pct) is relevant


def test_expected_results_of_the_briefing(dataset):
    by_key = {c.key: c for c in _comparisons(dataset)}
    assert {c.key for c in by_key.values() if c.relevant} == {MEDICARE, LAB_US}
    assert by_key[MEDICARE].variance_pct_display == Decimal("-10.0")
    assert by_key[LAB_US].variance_pct_display == Decimal("7.0")
    assert by_key["US.n/a.clinical_supplies"].variance_pct_display == Decimal("4.5")
    assert by_key["UK.private.patient_services"].variance_pct_display == Decimal("4.2")
    assert not by_key["US.n/a.clinical_supplies"].relevant and not by_key["UK.private.patient_services"].relevant


def test_missing_line_is_neither_zero_nor_minus_100(dataset):
    line = {c.key: c for c in _comparisons(dataset)}["UK.n/a.clinical_software_licences"]
    assert line.status == calc.STATUS_MISSING
    assert line.actual is None and line.variance is None and line.variance_pct is None and not line.relevant


def test_unbudgeted_line_is_reported():
    budget = [BudgetRecord("US", "n/a", "laboratory", "USD", Decimal("100"), 2, "ok", ())]
    out = calc.compare_to_budget(calc.compute_line_totals([_record("A", "-50", category="travel")]), budget)
    assert {c.key: c.status for c in out} == {"US.n/a.laboratory": "missing", "US.n/a.travel": "unbudgeted"}


def test_convert_to_usd_uses_briefing_rate():
    assert calc.convert_to_usd(Decimal("271000"), "GBP") == Decimal("344170.00")
    assert calc.convert_to_usd(Decimal("10"), "USD") == Decimal("10.00")
    with pytest.raises(ValueError):
        calc.convert_to_usd(Decimal("1"), "EUR")


def test_consolidation_in_usd(dataset):
    out = calc.consolidate_usd(_comparisons(dataset))
    assert out["revenue"].actual == Decimal("2027420.00")  # 1.588.000 + 346.000 × 1,27
    assert out["revenue"].budget == Decimal("2095450.00")
    assert out["expense"].actual == Decimal("1709135.81")
    # El presupuesto comparable excluye las licencias (8.000 GBP × 1,27 = 10.160 USD).
    assert out["expense"].budget - out["expense"].comparable_budget == Decimal("10160.00")
    assert out["expense"].incomplete and not out["revenue"].incomplete
    assert out["result"].actual == out["revenue"].actual - out["expense"].actual


def test_payer_claims_drivers_explain_medicare(dataset):
    result = drivers.payer_claims_drivers(dataset, MEDICARE)
    assert result.status == "ok" and result.conclusion == "denial_rate"
    assert result.value("claims_submitted") == result.value("claims_submitted_budget") == 4000
    assert result.value("denial_rate_change_pp") == Decimal("8.6")
    assert (result.value("paid_claims_budget"), result.value("paid_claims")) == (3440, 3096)
    assert result.value("paid_claims_change_pct") == Decimal("-10.0")  # cuadra con la desviación de ingresos


def test_unit_cost_drivers_explain_us_lab_by_price(dataset):
    comparison = {c.key: c for c in _comparisons(dataset)}[LAB_US]
    result = drivers.unit_cost_drivers(dataset, LAB_US, comparison)
    assert result.conclusion == "price"
    assert result.value("units") == result.value("units_budget") == 1500
    assert (result.value("unit_price_budget"), result.value("unit_price")) == (Decimal("30.00"), Decimal("32.10"))
    assert result.value("unit_price_change_pct") == Decimal("7.0")


def test_drivers_declare_missing_operational_data(incomplete_dir):
    dataset = ingestion.read_dataset(incomplete_dir, PERIOD)
    result = drivers.payer_claims_drivers(dataset, MEDICARE)
    assert result.status == "no_data" and "denial_rate_pct" in result.missing and not result.facts


def test_data_quality_never_carries_descriptions(dataset):
    quality = drivers.data_quality_report(dataset, _comparisons(dataset))
    assert [m["line_key"] for m in quality["missing_lines"]] == ["UK.n/a.clinical_software_licences"]
    [doubtful] = quality["doubtful_records"]
    assert doubtful["entry_id"] == "JE-2026OCT-0053" and doubtful["patient_data"]
    assert doubtful["amount"] is None and "description" not in doubtful


def test_same_data_same_figures(dataset):
    assert _comparisons(dataset) == _comparisons(dataset)
    assert calc.consolidate_usd(_comparisons(dataset)) == calc.consolidate_usd(_comparisons(dataset))
