"""Cálculos deterministas con cifras hechas a mano (README: "verificable con pruebas")."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from services.supplier_contracts import calculations as calc


def test_pct_change_is_exact_and_rounded_half_up():
    assert calc.pct_change(Decimal("18.00"), Decimal("19.26")) == Decimal("7.00")
    assert calc.pct_change(Decimal("3"), Decimal("3.1350")) == Decimal("4.50")
    # 1/3 = 0,333… % → 0,33 %
    assert calc.pct_change(Decimal("300"), Decimal("301")) == Decimal("0.33")
    assert calc.pct_change(Decimal("2"), Decimal("1.9")) == Decimal("-5.00")


def test_pct_change_rejects_non_positive_previous_price():
    with pytest.raises(ValueError):
        calc.pct_change(Decimal("0"), Decimal("1"))


@pytest.mark.parametrize("change,expected", [("4.00", False), ("4.01", True), ("7.00", True), ("2.00", False)])
def test_threshold_is_strictly_greater_than_four_percent(change, expected):
    assert calc.exceeds_threshold(Decimal(change), Decimal("4")) is expected


def test_annual_impact_by_hand():
    # (19,26 − 18,00) × 2.500 × 12 = 37.800,00
    assert calc.annual_impact(Decimal("18.00"), Decimal("19.26"), 2500) == Decimal("37800.00")
    # Solo cotización: sin volumen, sin coste.
    assert calc.annual_impact(Decimal("17.20"), Decimal("18.00"), 0) == Decimal("0.00")
    with pytest.raises(ValueError):
        calc.annual_impact(Decimal("1"), Decimal("2"), -1)


def test_annual_spend_matches_briefing_contract_values():
    # BRIEFING: los valores anuales de los contratos son precio base × volumen × 12.
    assert calc.annual_spend(Decimal("3.20"), 14000) == Decimal("537600.00")
    assert calc.annual_spend(Decimal("18.00"), 2500) == Decimal("540000.00")
    assert calc.annual_spend(Decimal("14.50"), 700) == Decimal("121800.00")
    assert calc.annual_spend(Decimal("1.95"), 9000) == Decimal("210600.00")


def test_currency_conversion_uses_briefing_reference_rate():
    assert calc.convert(Decimal("121800"), "GBP", "USD") == Decimal("154686.00")
    assert calc.convert(Decimal("127"), "USD", "GBP") == Decimal("100.00")
    assert calc.convert(Decimal("10"), "USD", "USD") == Decimal("10.00")
    with pytest.raises(ValueError):
        calc.convert(Decimal("1"), "EUR", "USD")


def test_compare_prices_orders_cheapest_first_and_never_counts_negative_savings():
    result = calc.compare_prices(Decimal("18.00"), 2500, [("B", Decimal("19.00")), ("A", Decimal("17.20"))])
    assert [c.supplier_id for c in result] == ["A", "B"]
    # (18,00 − 17,20) × 2.500 × 12 = 24.000
    assert result[0].annual_saving == Decimal("24000.00")
    assert result[0].difference_pct == Decimal("-4.44")
    assert result[1].annual_saving == Decimal("0.00")


def test_exit_penalty_is_months_times_average_billing():
    average = calc.average_monthly_billing([(Decimal("18"), 2500), (Decimal("20"), 2500)])
    assert average == Decimal("47500.00")
    assert calc.early_exit_penalty(2, average) == Decimal("95000.00")
    with pytest.raises(ValueError):
        calc.average_monthly_billing([])


def test_notice_deadline_and_days():
    expiry = date(2027, 1, 6)
    assert calc.notice_deadline(expiry, 45) == date(2026, 11, 22)
    assert calc.days_until(date(2026, 11, 22), date(2026, 11, 7)) == 15


@pytest.mark.parametrize("days,expected", [(91, None), (90, 90), (60, 90), (31, 90), (30, 30), (0, 30), (-1, None)])
def test_expiry_alert_picks_the_closest_reached_threshold(days, expected):
    assert calc.due_expiry_alert(days, (90, 30)) == expected


@pytest.mark.parametrize("days,expected", [(16, False), (15, True), (0, True), (-1, False)])
def test_notice_alert_window_is_fifteen_days(days, expected):
    assert calc.due_notice_alert(days, 15) is expected
