"""Cálculos deterministas: el modelo nunca hace aritmética (README, exigencia 3).

Todo en `Decimal` (nunca `float`: en dinero, 0,1 + 0,2 debe dar 0,3) y con
redondeo explícito `ROUND_HALF_UP` al final de cada cálculo, no a mitad. Son
funciones puras: no leen la base de datos ni el reloj, así que las pruebas de
`tests/supplier_contracts/test_calculations.py` las fijan con cifras hechas a
mano.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, List, Optional, Sequence, Tuple

from services.supplier_contracts.catalog import GBP_TO_USD

CENT = Decimal("0.01")
PCT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def pct_change(previous: Decimal, current: Decimal) -> Decimal:
    """Variación porcentual de `previous` a `current`, con 2 decimales."""
    previous, current = Decimal(previous), Decimal(current)
    if previous <= 0:
        raise ValueError("El precio anterior debe ser positivo para calcular una variación.")
    return ((current - previous) / previous * 100).quantize(PCT, rounding=ROUND_HALF_UP)


def exceeds_threshold(change_pct: Decimal, threshold_pct: Decimal) -> bool:
    """"Más de un 4 %": estrictamente mayor. Un 4,00 % exacto no alerta."""
    return Decimal(change_pct) > Decimal(threshold_pct)


def annual_impact(previous_price: Decimal, new_price: Decimal, monthly_volume: int, months: int = 12) -> Decimal:
    """Coste anual extra de pasar de `previous_price` a `new_price` con el
    volumen mensual actual. Volumen 0 (solo cotización) → impacto 0."""
    if monthly_volume < 0:
        raise ValueError("El volumen mensual no puede ser negativo.")
    return money((Decimal(new_price) - Decimal(previous_price)) * monthly_volume * months)


def annual_spend(unit_price: Decimal, monthly_volume: int, months: int = 12) -> Decimal:
    return money(Decimal(unit_price) * monthly_volume * months)


def convert(amount: Decimal, from_currency: str, to_currency: str, gbp_to_usd: Decimal = GBP_TO_USD) -> Decimal:
    """Conversión con el tipo de referencia del BRIEFING (1 GBP = 1,27 USD)."""
    amount = Decimal(amount)
    if from_currency == to_currency:
        return money(amount)
    if (from_currency, to_currency) == ("GBP", "USD"):
        return money(amount * gbp_to_usd)
    if (from_currency, to_currency) == ("USD", "GBP"):
        return money(amount / gbp_to_usd)
    raise ValueError(f"Moneda no soportada: {from_currency} → {to_currency}")


def average_monthly_billing(rows: Iterable[Tuple[Decimal, int]]) -> Decimal:
    """Facturación mensual media de pares (precio, volumen)."""
    totals = [Decimal(price) * volume for price, volume in rows]
    if not totals:
        raise ValueError("Sin meses de facturación no hay media que calcular.")
    return money(sum(totals) / len(totals))


def early_exit_penalty(penalty_months: int, average_billing: Decimal) -> Decimal:
    return money(Decimal(penalty_months) * Decimal(average_billing))


@dataclass(frozen=True)
class PriceComparison:
    supplier_id: str
    unit_price: Decimal
    difference: Decimal  # precio del alternativo - precio del evaluado (negativo = más barato)
    difference_pct: Decimal
    annual_saving: Decimal  # ahorro con el volumen del evaluado; 0 si el alternativo es más caro


def compare_prices(
    target_price: Decimal,
    target_monthly_volume: int,
    alternatives: Sequence[Tuple[str, Decimal]],
) -> List[PriceComparison]:
    """Compara el precio evaluado con alternativas de la MISMA categoría y país
    (el filtro lo hace quien llama, que conoce el catálogo). Ordenadas de la
    más barata a la más cara."""
    out = []
    for supplier_id, price in alternatives:
        diff = Decimal(price) - Decimal(target_price)
        saving = max(Decimal(0), -diff) * target_monthly_volume * 12
        out.append(PriceComparison(supplier_id, Decimal(price), money(diff), pct_change(target_price, price), money(saving)))
    return sorted(out, key=lambda c: c.unit_price)


def notice_deadline(expiry: date, notice_days: int) -> date:
    """Último día para avisar de la no renovación."""
    return expiry - timedelta(days=notice_days)


def days_until(target: date, today: date) -> int:
    return (target - today).days


def due_expiry_alert(days_to_expiry: int, thresholds: Sequence[int]) -> Optional[int]:
    """Umbral de vencimiento que toca avisar hoy: el MÁS cercano ya alcanzado.

    Con umbrales (90, 30) y 60 días → 90; con 28 días → 30 (no los dos a la vez:
    el de 90 ya no aporta nada). Un contrato vencido (días < 0) no genera aviso
    de vencimiento: eso ya es otra situación.
    """
    if days_to_expiry < 0:
        return None
    reached = [t for t in thresholds if days_to_expiry <= t]
    return min(reached) if reached else None


def due_notice_alert(days_to_deadline: int, alert_days: int) -> bool:
    """Aviso de preaviso: desde `alert_days` días antes hasta el propio día límite."""
    return 0 <= days_to_deadline <= alert_days
