"""Formato de cifras para las respuestas en español (12.345,67 USD; 7,00 %)."""

from __future__ import annotations

from decimal import Decimal


def _es(value: Decimal, decimals: int) -> str:
    text = f"{Decimal(value):,.{decimals}f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def money(value: Decimal, currency: str, decimals: int = 2) -> str:
    return f"{_es(value, decimals)} {currency}"


def unit_price(value: Decimal, currency: str, unit: str) -> str:
    return f"{_es(value, 4)} {currency}/{unit}"


def pct(value: Decimal) -> str:
    sign = "+" if Decimal(value) > 0 else ""
    return f"{sign}{_es(value, 2)} %"


def plain_pct(value: Decimal) -> str:
    return f"{_es(value, 2)} %"


def integer(value: int) -> str:
    return _es(Decimal(value), 0)
