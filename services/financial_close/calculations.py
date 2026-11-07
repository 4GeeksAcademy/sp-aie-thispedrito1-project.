"""Herramientas de cálculo deterministas del cierre (README: "Los cálculos no los hace el modelo").

Funciones puras sobre `Decimal`: misma entrada → misma salida, sin red ni base
de datos. Cada resultado lleva los `entry_id` (o filas de origen) que lo
producen, para el anexo de trazabilidad.

Convenciones:
- El ledger guarda los gastos en negativo; las comparaciones con el
  presupuesto (positivo, como en el BRIEFING) usan el valor absoluto.
- Los registros dudosos nunca entran en un total.
- Una partida presupuestada SIN registros es "missing": su real es `None`,
  nunca 0, y no tiene desviación (BRIEFING: ni 0 ni −100 %).
- La comparación con el umbral usa el porcentaje EXACTO; el redondeo a un
  decimal es solo para mostrarlo (un 4,96 % no es una desviación del 5 %).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from services.financial_close.catalog import DEVIATION_THRESHOLD_PCT, FX_TO_USD, REVENUE_CATEGORY
from services.financial_close.ingestion import BudgetRecord, LedgerRecord

CENT = Decimal("0.01")
TENTH = Decimal("0.1")

STATUS_OK = "ok"
STATUS_MISSING = "missing"  # presupuestada, sin registros
STATUS_UNBUDGETED = "unbudgeted"  # registros válidos sin presupuesto


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def pct_display(value: Optional[Decimal]) -> Optional[Decimal]:
    return None if value is None else value.quantize(TENTH, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class LineTotal:
    key: str
    currency: str
    amount: Optional[Decimal]  # positivo (ingreso o gasto en valor absoluto); None si no hay registros
    entry_ids: Tuple[str, ...]


@dataclass(frozen=True)
class LineComparison:
    key: str
    kind: str  # revenue | expense
    currency: str
    budget: Optional[Decimal]
    actual: Optional[Decimal]
    variance: Optional[Decimal]
    variance_pct: Optional[Decimal]  # exacto
    status: str
    relevant: bool
    entry_ids: Tuple[str, ...]
    budget_row: Optional[int]

    @property
    def variance_pct_display(self) -> Optional[Decimal]:
        return pct_display(self.variance_pct)


def compute_line_totals(records: Iterable[LedgerRecord]) -> Dict[str, LineTotal]:
    """Suma por partida (país.pagador.categoría) de los registros válidos."""
    sums: Dict[str, Decimal] = {}
    ids: Dict[str, List[str]] = {}
    currency: Dict[str, str] = {}
    for record in records:
        if not record.is_ok:
            continue
        key = f"{record.country}.{record.payer}.{record.category}"
        sums[key] = sums.get(key, Decimal("0")) + abs(record.amount)
        ids.setdefault(key, []).append(record.entry_id)
        currency[key] = record.currency
    return {key: LineTotal(key, currency[key], money(total), tuple(sorted(ids[key]))) for key, total in sums.items()}


def variance(budget: Decimal, actual: Decimal) -> Tuple[Decimal, Optional[Decimal]]:
    """Desviación absoluta y porcentual (exacta) de real frente a presupuesto."""
    absolute = money(actual - budget)
    if budget == 0:
        return absolute, None
    return absolute, (actual - budget) * 100 / budget


def is_relevant(variance_pct: Optional[Decimal], threshold: Decimal = DEVIATION_THRESHOLD_PCT) -> bool:
    """BRIEFING: igual o superior al umbral, a favor o en contra, con el valor exacto."""
    return variance_pct is not None and abs(variance_pct) >= threshold


def compare_to_budget(totals: Dict[str, LineTotal], budget: Iterable[BudgetRecord]) -> List[LineComparison]:
    """Una comparación por partida presupuestada, más las partidas con
    registros válidos que no tienen presupuesto."""
    out: List[LineComparison] = []
    seen = set()
    for line in budget:
        if not line.status == "ok":
            continue
        seen.add(line.line_key)
        kind = "revenue" if line.category == REVENUE_CATEGORY else "expense"
        total = totals.get(line.line_key)
        if total is None:
            out.append(LineComparison(line.line_key, kind, line.currency, line.amount, None, None, None,
                                      STATUS_MISSING, False, (), line.source_row))
            continue
        absolute, pct = variance(line.amount, total.amount)
        out.append(LineComparison(line.line_key, kind, line.currency, line.amount, total.amount, absolute, pct,
                                  STATUS_OK, is_relevant(pct), total.entry_ids, line.source_row))
    for key, total in sorted(totals.items()):
        if key in seen:
            continue
        kind = "revenue" if key.endswith(REVENUE_CATEGORY) else "expense"
        out.append(LineComparison(key, kind, total.currency, None, total.amount, None, None, STATUS_UNBUDGETED,
                                  False, total.entry_ids, None))
    return out


def convert_to_usd(amount: Decimal, currency: str) -> Decimal:
    """Conversión al tipo de consolidación del BRIEFING (1 GBP = 1,27 USD)."""
    if currency not in FX_TO_USD:
        raise ValueError(f"Moneda sin tipo de cambio: {currency}")
    return money(amount * FX_TO_USD[currency])


@dataclass(frozen=True)
class ConsolidatedLine:
    budget: Decimal
    actual: Decimal
    comparable_budget: Decimal  # presupuesto SIN las partidas sin registros
    variance: Decimal
    variance_pct: Optional[Decimal]
    incomplete: bool
    entry_ids: Tuple[str, ...] = field(default_factory=tuple)


def consolidate_usd(comparisons: Iterable[LineComparison]) -> Dict[str, ConsolidatedLine]:
    """Ingresos, gastos y resultado en USD. Una partida sin registros no
    cuenta como 0: el real excluye esa partida y la desviación se mide
    contra el presupuesto comparable; `incomplete` lo declara."""
    acc = {kind: {"budget": Decimal("0"), "actual": Decimal("0"), "comparable": Decimal("0"), "incomplete": False,
                  "ids": []} for kind in ("revenue", "expense")}
    for line in comparisons:
        bucket = acc[line.kind]
        if line.budget is not None:
            bucket["budget"] += convert_to_usd(line.budget, line.currency)
        if line.status == STATUS_MISSING:
            bucket["incomplete"] = True
            continue
        if line.actual is not None:
            bucket["actual"] += convert_to_usd(line.actual, line.currency)
            bucket["ids"].extend(line.entry_ids)
        if line.budget is not None:
            bucket["comparable"] += convert_to_usd(line.budget, line.currency)
    out: Dict[str, ConsolidatedLine] = {}
    for kind, b in acc.items():
        absolute, pct = variance(b["comparable"], b["actual"])
        out[kind] = ConsolidatedLine(b["budget"], b["actual"], b["comparable"], absolute, pct, b["incomplete"],
                                     tuple(sorted(b["ids"])))
    rev, exp = out["revenue"], out["expense"]
    result_comparable = rev.comparable_budget - exp.comparable_budget
    result_actual = rev.actual - exp.actual
    absolute, pct = variance(result_comparable, result_actual)
    out["result"] = ConsolidatedLine(rev.budget - exp.budget, result_actual, result_comparable, absolute, pct,
                                     rev.incomplete or exp.incomplete, tuple(sorted(rev.entry_ids + exp.entry_ids)))
    return out
