"""Calendario del cierre: tercer día hábil de cada mes, 06:00 hora de Austin (BRIEFING).

Cron no sabe qué es un "día hábil", así que el servicio `scheduler` lanza
scripts/financial_close_scheduled.py CADA HORA y este módulo decide si toca:
`is_close_due(ahora)` es True solo en la hora 06 local (America/Chicago) del
tercer día hábil. Trabajar en hora local con `zoneinfo` hace que el cambio de
horario de verano no mueva la ejecución.

Días hábiles: lunes a viernes, sin los festivos federales de EE. UU. que
pueden caer en los primeros días de un mes (Año Nuevo, Día de la
Independencia y Labor Day), con su día "observado" cuando caen en fin de
semana.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Set
from zoneinfo import ZoneInfo

from services.financial_close.catalog import SCHEDULE_BUSINESS_DAY, SCHEDULE_HOUR, SCHEDULE_TIMEZONE
from services.financial_close.seed_data import closing_month

AUSTIN = ZoneInfo(SCHEDULE_TIMEZONE)


def _observed(day: date) -> date:
    """Festivo en sábado → viernes anterior; en domingo → lunes siguiente."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def us_holidays(year: int) -> Set[date]:
    """Festivos federales que pueden afectar a los primeros días hábiles de un mes."""
    labor_day = date(year, 9, 1)
    while labor_day.weekday() != 0:  # primer lunes de septiembre
        labor_day += timedelta(days=1)
    return {
        _observed(date(year, 1, 1)),
        _observed(date(year + 1, 1, 1)),  # Año Nuevo en sábado se observa el 31-dic anterior
        _observed(date(year, 7, 4)),
        labor_day,
    }


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in us_holidays(day.year)


def nth_business_day(year: int, month: int, n: int = SCHEDULE_BUSINESS_DAY) -> date:
    """El n-ésimo día hábil del mes (1 = el primero).

    Lanza ValueError si n < 1 o si el mes no tiene n días hábiles: devolver
    un día del mes siguiente lanzaría el cierre en una fecha equivocada.
    """
    if n < 1:
        raise ValueError("n debe ser 1 o mayor")
    day = date(year, month, 1)
    count = 0
    while day.month == month:
        if is_business_day(day):
            count += 1
            if count == n:
                return day
        day += timedelta(days=1)
    raise ValueError(f"{year}-{month:02d} no tiene {n} días hábiles")


def is_close_due(now: datetime) -> bool:
    """True si `now` (con zona) cae en la hora de ejecución del cierre en Austin."""
    if now.tzinfo is None:
        raise ValueError("now debe llevar zona horaria")
    local = now.astimezone(AUSTIN)
    return local.hour == SCHEDULE_HOUR and local.date() == nth_business_day(local.year, local.month)


def period_to_close(now: datetime) -> date:
    """Mes de cierre: el último mes completo antes de la fecha local de Austin."""
    return closing_month(now.astimezone(AUSTIN).date())


def next_run(after: datetime) -> datetime:
    """Próxima ejecución programada (para mostrarla en la pantalla)."""
    local = after.astimezone(AUSTIN)
    year, month = local.year, local.month
    for _ in range(3):
        day = nth_business_day(year, month)
        candidate = datetime(day.year, day.month, day.day, SCHEDULE_HOUR, tzinfo=AUSTIN)
        if candidate > local:
            return candidate.astimezone(timezone.utc)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    raise RuntimeError("Sin ejecución en los próximos meses")
