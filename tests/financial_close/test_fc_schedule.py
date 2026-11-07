"""Calendario: tercer día hábil de cada mes, 06:00 hora de Austin (BRIEFING)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from services.financial_close import schedule


@pytest.mark.parametrize("year, month, expected", [
    (2026, 11, date(2026, 11, 4)),  # 1-nov domingo → lun 2, mar 3, mié 4
    (2026, 3, date(2026, 3, 4)),    # 1-mar domingo
    (2026, 1, date(2026, 1, 6)),    # 1-ene jueves festivo → vie 2, lun 5, mar 6
    (2026, 7, date(2026, 7, 6)),    # 4-jul sábado se observa el vie 3 → 1, 2, 6
    (2026, 9, date(2026, 9, 3)),    # Labor Day es el lun 7: no afecta → 1, 2, 3
    (2027, 1, date(2027, 1, 6)),    # 1-ene viernes festivo → lun 4, mar 5, mié 6
    (2027, 12, date(2027, 12, 3)),  # 31-dic-2027 es festivo observado (1-ene-2028 cae en sábado): no afecta al día 3
])
def test_third_business_day(year, month, expected):
    assert schedule.nth_business_day(year, month) == expected


def test_first_business_day_skips_weekend_and_holiday():
    assert schedule.nth_business_day(2026, 1, 1) == date(2026, 1, 2)
    assert schedule.nth_business_day(2026, 8, 1) == date(2026, 8, 3)  # 1-ago sábado


def test_close_is_due_only_at_six_in_austin():
    # 4-nov-2026, horario estándar (CST, UTC-6): 06:00 local = 12:00 UTC.
    assert schedule.is_close_due(datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc))
    assert schedule.is_close_due(datetime(2026, 11, 4, 12, 59, tzinfo=timezone.utc))
    assert not schedule.is_close_due(datetime(2026, 11, 4, 11, 0, tzinfo=timezone.utc))
    assert not schedule.is_close_due(datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc))


def test_daylight_saving_time_moves_the_utc_hour():
    # 5-oct-2026 es el 3.er día hábil y Austin está en CDT (UTC-5): 06:00 local = 11:00 UTC.
    assert schedule.is_close_due(datetime(2026, 10, 5, 11, 0, tzinfo=timezone.utc))
    assert not schedule.is_close_due(datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc))


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError):
        schedule.is_close_due(datetime(2026, 11, 4, 12, 0))


def test_period_to_close_is_previous_month():
    assert schedule.period_to_close(datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)) == date(2026, 10, 1)
    assert schedule.period_to_close(datetime(2027, 1, 6, 12, 0, tzinfo=timezone.utc)) == date(2026, 12, 1)


def test_next_run():
    assert schedule.next_run(datetime(2026, 11, 7, tzinfo=timezone.utc)) == datetime(2026, 12, 3, 12, 0,
                                                                                      tzinfo=timezone.utc)
