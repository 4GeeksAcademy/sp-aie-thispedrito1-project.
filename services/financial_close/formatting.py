"""Formato de cifras por idioma. Las cifras son las mismas; solo cambia cómo se escriben.

Inglés: 1,120,000.00 · +7.0%   Español: 1.120.000,00 · +7,0 %
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional, Set

DASH = "—"


def _group(integer: str, sep: str) -> str:
    out = []
    while len(integer) > 3:
        out.insert(0, integer[-3:])
        integer = integer[:-3]
    out.insert(0, integer)
    return sep.join(out)


def number(value: Optional[Decimal], lang: str, decimals: int = 2) -> str:
    if value is None:
        return DASH
    quantized = Decimal(value).quantize(Decimal(1).scaleb(-decimals)) if decimals else Decimal(value).quantize(Decimal(1))
    sign = "-" if quantized < 0 else ""
    integer, _, frac = f"{abs(quantized):f}".partition(".")
    thousands, dec = (",", ".") if lang == "en" else (".", ",")
    text = _group(integer, thousands)
    if decimals:
        text += dec + frac
    return sign + text


def money(value: Optional[Decimal], lang: str) -> str:
    return number(value, lang, 2)


def count(value: Optional[Decimal], lang: str) -> str:
    if value is None:
        return DASH
    decimals = 0 if Decimal(value) == Decimal(value).to_integral_value() else 1
    return number(value, lang, decimals)


def pct(value: Optional[Decimal], lang: str, signed: bool = True) -> str:
    if value is None:
        return DASH
    text = number(value, lang, 1)
    if signed and value > 0:
        text = "+" + text
    return text + ("%" if lang == "en" else " %")


def threshold(value: Decimal, lang: str) -> str:
    """Umbral sin decimales innecesarios: 5% / 5 %."""
    return count(value, lang) + ("%" if lang == "en" else " %")


# --- Validación de números en textos redactados -----------------------------------

_TOKEN = re.compile(r"\d[\d.,]*\d|\d")


def canonical(value: Decimal) -> str:
    normalized = abs(Decimal(value)).normalize()
    return f"{normalized:f}"


def _interpretations(token: str) -> Set[str]:
    out = set()
    for thousands, dec in ((",", "."), (".", ",")):
        candidate = token.replace(thousands, "").replace(dec, ".")
        try:
            out.add(canonical(Decimal(candidate)))
        except InvalidOperation:
            continue
    return out


def allowed_numbers(values: Iterable[Decimal]) -> Set[str]:
    return {canonical(v) for v in values if v is not None}


def invented_numbers(text: str, allowed: Set[str]) -> Set[str]:
    """Tokens numéricos del texto que no corresponden a ninguna cifra permitida
    (en formato inglés o español)."""
    return {token for token in _TOKEN.findall(text or "") if not (_interpretations(token) & allowed)}
