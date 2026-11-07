"""Herramientas deterministas que sostienen las hipótesis y el control de calidad.

- `payer_claims_drivers`: reclamaciones presentadas, tasa de denegación y
  reclamaciones pagadas (presentadas × (1 − denegación)) de un pagador, real
  frente a presupuesto, desde operations.csv.
- `unit_cost_drivers`: unidades y precio unitario de una partida de proveedor.
  Unidades reales = suma de las cantidades de sus registros del ledger;
  unidades presupuestadas = `<indicador>_budget` de operations.csv; precio
  unitario = importe / unidades.
- `data_quality_report`: partidas sin registros, archivos ausentes, registros
  dudosos (solo `entry_id` + motivos) y partidas sin presupuesto.

Si falta un indicador, la herramienta no lo sustituye por nada: devuelve
`status = "no_data"` con la lista de lo que falta, y el informe dice que la
desviación no se puede explicar con datos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional, Tuple

from services.financial_close.calculations import (
    STATUS_MISSING,
    STATUS_UNBUDGETED,
    LineComparison,
    pct_display,
    variance,
)
from services.financial_close.catalog import (
    BUDGET_SUFFIX,
    CLAIMS_SUBMITTED,
    DENIAL_RATE,
    LINES_BY_KEY,
)
from services.financial_close.ingestion import LedgerRecord, OperationRecord, PeriodDataset

HUNDRED = Decimal("100")


@dataclass(frozen=True)
class DriverFact:
    key: str  # metric o metric_budget, paid_claims, unit_price, ...
    value: Decimal
    unit: str  # count | pct | pp | USD | GBP
    sources: Tuple[str, ...]  # "operations.csv row 4", entry_ids o "calculation"


@dataclass
class DriverResult:
    tool: str
    line_key: str
    status: str = "ok"  # ok | no_data
    facts: List[DriverFact] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    conclusion: Optional[str] = None  # denial_rate | price | volume | none

    def value(self, key: str) -> Optional[Decimal]:
        return next((f.value for f in self.facts if f.key == key), None)


def _metric(operations: List[OperationRecord], country: str, payer: str, metric: str) -> Optional[OperationRecord]:
    return next((o for o in operations if o.is_ok and o.country == country and o.payer == payer
                 and o.metric == metric), None)


def _row(record: OperationRecord) -> str:
    return f"{record.source_file} row {record.source_row}"


def _q(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def payer_claims_drivers(dataset: PeriodDataset, line_key: str) -> DriverResult:
    country, payer, _ = line_key.split(".")
    result = DriverResult(tool="payer_claims_drivers", line_key=line_key)
    needed = [CLAIMS_SUBMITTED, CLAIMS_SUBMITTED + BUDGET_SUFFIX, DENIAL_RATE, DENIAL_RATE + BUDGET_SUFFIX]
    records = {m: _metric(dataset.operations, country, payer, m) for m in needed}
    result.missing = [m for m, r in records.items() if r is None]
    if result.missing:
        result.status = "no_data"
        return result
    claims_a, claims_b = records[CLAIMS_SUBMITTED].value, records[CLAIMS_SUBMITTED + BUDGET_SUFFIX].value
    den_a, den_b = records[DENIAL_RATE].value, records[DENIAL_RATE + BUDGET_SUFFIX].value
    paid_a = _q(claims_a * (HUNDRED - den_a) / HUNDRED, "1")
    paid_b = _q(claims_b * (HUNDRED - den_b) / HUNDRED, "1")
    _, paid_pct = variance(paid_b, paid_a)
    _, claims_pct = variance(claims_b, claims_a)
    for key, value, unit, rec in (
        (CLAIMS_SUBMITTED + BUDGET_SUFFIX, claims_b, "count", records[CLAIMS_SUBMITTED + BUDGET_SUFFIX]),
        (CLAIMS_SUBMITTED, claims_a, "count", records[CLAIMS_SUBMITTED]),
        (DENIAL_RATE + BUDGET_SUFFIX, den_b, "rate", records[DENIAL_RATE + BUDGET_SUFFIX]),
        (DENIAL_RATE, den_a, "rate", records[DENIAL_RATE]),
    ):
        result.facts.append(DriverFact(key, value, unit, (_row(rec),)))
    calc = ("calculation",)
    result.facts += [
        DriverFact("claims_change_pct", pct_display(claims_pct), "pct", calc),
        DriverFact("denial_rate_change_pp", den_a - den_b, "pp", calc),
        DriverFact("paid_claims_budget", paid_b, "count", calc),
        DriverFact("paid_claims", paid_a, "count", calc),
        DriverFact("paid_claims_change_pct", pct_display(paid_pct), "pct", calc),
    ]
    result.conclusion = "denial_rate" if den_a != den_b else ("volume" if claims_a != claims_b else "none")
    return result


def unit_cost_drivers(dataset: PeriodDataset, line_key: str, comparison: LineComparison) -> DriverResult:
    spec = LINES_BY_KEY.get(line_key)
    result = DriverResult(tool="unit_cost_drivers", line_key=line_key)
    if spec is None or spec.unit_metric is None:
        result.status, result.missing = "no_data", ["unit_metric"]
        return result
    country = spec.country
    budget_units_rec = _metric(dataset.operations, country, "n/a", spec.unit_metric + BUDGET_SUFFIX)
    records: List[LedgerRecord] = [r for r in dataset.ledger if r.is_ok and r.entry_id in comparison.entry_ids]
    actual_units = sum((r.quantity for r in records if r.quantity is not None), Decimal("0"))
    if budget_units_rec is None:
        result.missing.append(spec.unit_metric + BUDGET_SUFFIX)
    if not records or actual_units == 0 or comparison.actual is None or comparison.budget is None:
        result.missing.append("ledger_quantities")
    if result.missing:
        result.status = "no_data"
        return result
    budget_units = budget_units_rec.value
    price_b = _q(comparison.budget / budget_units)
    price_a = _q(comparison.actual / actual_units)
    _, units_pct = variance(budget_units, actual_units)
    _, price_pct = variance(price_b, price_a)
    ids = tuple(r.entry_id for r in records)
    result.facts += [
        DriverFact("units_budget", budget_units, "count", (_row(budget_units_rec),)),
        DriverFact("units", actual_units, "count", ids),
        DriverFact("unit_price_budget", price_b, spec.currency, ("calculation",)),
        DriverFact("unit_price", price_a, spec.currency, ids),
        DriverFact("units_change_pct", pct_display(units_pct), "pct", ("calculation",)),
        DriverFact("unit_price_change_pct", pct_display(price_pct), "pct", ("calculation",)),
    ]
    result.conclusion = "price" if abs(price_pct or 0) >= abs(units_pct or 0) else "volume"
    return result


def driver_tool_for(line_key: str) -> Optional[str]:
    """La herramienta que corresponde a una partida (también es el plan por reglas)."""
    country, payer, category = line_key.split(".")
    spec = LINES_BY_KEY.get(line_key)
    if payer != "n/a":
        return "payer_claims_drivers"
    if spec is not None and spec.unit_metric:
        return "unit_cost_drivers"
    return None


def find_relevant_deviations(comparisons: List[LineComparison]) -> List[LineComparison]:
    return [c for c in comparisons if c.relevant]


def data_quality_report(dataset: PeriodDataset, comparisons: List[LineComparison]) -> Dict[str, list]:
    """Lo que falta o es dudoso. Nunca incluye descripciones."""
    doubtful = []
    for r in dataset.ledger:
        if r.is_ok:
            continue
        doubtful.append({
            "source": r.source_file, "entry_id": r.entry_id, "source_row": r.source_row, "issues": list(r.issues),
            "description_withheld": r.description_withheld,
            "patient_data": "patient_data" in r.sensitive_categories,
            # El importe de un registro con datos sensibles no se muestra: el
            # BRIEFING pide mencionarlo SOLO por su entry_id.
            "amount": None if r.sensitive_categories or r.amount is None else str(r.amount),
            "currency": None if r.sensitive_categories else r.currency,
        })
    for b in dataset.budget:
        if b.status != "ok":
            doubtful.append({"source": b.source_file, "entry_id": None, "source_row": b.source_row,
                             "issues": list(b.issues), "description_withheld": False, "patient_data": False,
                             "amount": None, "currency": None})
    for o in dataset.operations:
        if o.status != "ok":
            doubtful.append({"source": o.source_file, "entry_id": None, "source_row": o.source_row,
                             "issues": list(o.issues), "description_withheld": False, "patient_data": False,
                             "amount": None, "currency": None})
    return {
        "missing_lines": [{"line_key": c.key, "budget": str(c.budget), "currency": c.currency,
                           "budget_row": c.budget_row} for c in comparisons if c.status == STATUS_MISSING],
        "unbudgeted_lines": [{"line_key": c.key, "actual": str(c.actual), "currency": c.currency,
                              "entry_ids": list(c.entry_ids)} for c in comparisons if c.status == STATUS_UNBUDGETED],
        "missing_files": list(dataset.missing_files),
        "doubtful_records": doubtful,
    }
