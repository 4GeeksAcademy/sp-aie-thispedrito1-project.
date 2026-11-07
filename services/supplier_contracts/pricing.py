"""Especialista de precios (`agent:pricing-specialist`).

Alcance estrecho: histórico de precios, subidas, impacto y comparativa con
proveedores de la MISMA categoría y país. No lee contratos (no tiene esas
herramientas): si la alternativa más barata no tiene contrato o BAA, eso lo
cruza el supervisor con el especialista de contratos.

Entrada: `PricingRequest`. Salida: `SpecialistResult` con cada cifra como
`Fact` y su referencia a la fila del CSV de la que sale.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

from sqlmodel import Session

from services.supplier_contracts import calculations as calc
from services.supplier_contracts import formatting as fmt
from services.supplier_contracts import repository
from services.supplier_contracts.catalog import (
    AGENT_PRICING,
    CATEGORY_LABELS,
    GBP_TO_USD,
    PRICE_INCREASE_THRESHOLD_PCT,
    REFERENCE_REF,
)
from services.supplier_contracts.models import ScPriceRecord, ScSupplier
from services.supplier_contracts.runtime import (
    ActionLog,
    AgentRuntime,
    Fact,
    PricingRequest,
    Ref,
    SpecialistResult,
    price_ref,
)

THRESHOLD_REF = Ref(kind="reference", source="BRIEFING", locator="Umbrales y alertas")


@dataclass
class PriceChange:
    supplier_id: str
    product: str
    unit: str
    currency: str
    previous: ScPriceRecord
    latest: ScPriceRecord
    change_pct: Decimal
    exceeds_threshold: bool


# --- Herramientas (funciones de código; el agente las usa vía AgentRuntime) -------


def get_price_history(session: Session, supplier_id: str) -> List[ScPriceRecord]:
    return repository.price_history(session, supplier_id)


def detect_price_increases(records: List[ScPriceRecord]) -> List[PriceChange]:
    """Último mes frente al anterior, por proveedor y producto."""
    by_key: Dict[tuple, List[ScPriceRecord]] = {}
    for record in records:
        by_key.setdefault((record.supplier_id, record.product), []).append(record)
    out = []
    for (supplier_id, product), rows in sorted(by_key.items()):
        rows = sorted(rows, key=lambda r: r.month)
        if len(rows) < 2:
            continue
        previous, latest = rows[-2], rows[-1]
        change = calc.pct_change(previous.unit_price, latest.unit_price)
        out.append(PriceChange(supplier_id, product, latest.unit, latest.currency, previous, latest, change,
                               calc.exceeds_threshold(change, PRICE_INCREASE_THRESHOLD_PCT)))
    return out


def compute_increase_impact(change: PriceChange) -> Decimal:
    return calc.annual_impact(change.previous.unit_price, change.latest.unit_price, change.latest.monthly_volume)


def compare_same_category_prices(session: Session, supplier: ScSupplier) -> List[dict]:
    """Último precio de cada proveedor de la misma categoría y país."""
    out = []
    for other in repository.list_suppliers(session):
        if other.supplier_id == supplier.supplier_id or other.category != supplier.category or other.country != supplier.country:
            continue
        history = repository.price_history(session, other.supplier_id)
        if history:
            out.append({"supplier": other, "latest": history[-1]})
    return out


def convert_currency(amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
    return calc.convert(amount, from_currency, to_currency)


# --- El agente -----------------------------------------------------------------


class PricingSpecialist:
    agent_id = AGENT_PRICING

    def __init__(self, session: Session, log: ActionLog) -> None:
        self.session = session
        self.runtime = AgentRuntime(AGENT_PRICING, log)

    def handle(self, request: PricingRequest) -> SpecialistResult:
        audit = {"intent": request.intent, "supplier_id": request.supplier_id}
        if request.intent == "detect_increases" and request.supplier_id is None:
            return self._detect_all(audit)
        supplier = repository.get_supplier(self.session, request.supplier_id or "")
        if supplier is None:
            return SpecialistResult(agent_id=self.agent_id, intent=request.intent, supplier_id=request.supplier_id,
                                    status="not_found", missing=["El proveedor indicado no está en el registro."])
        history = self.runtime.call("get_price_history", get_price_history, self.session, supplier.supplier_id, audit=audit)
        if not history:
            return SpecialistResult(agent_id=self.agent_id, intent=request.intent, supplier_id=supplier.supplier_id,
                                    status="no_data", missing=[f"No hay histórico de precios de {supplier.name}."])
        handler = {
            "price_history": self._history,
            "detect_increases": self._increase,
            "increase_impact": self._impact,
            "compare_alternatives": self._compare,
        }[request.intent]
        return handler(supplier, history, audit)

    # -- intents --

    def _result(self, intent: str, supplier: Optional[ScSupplier], **kwargs) -> SpecialistResult:
        return SpecialistResult(agent_id=self.agent_id, intent=intent,
                                supplier_id=supplier.supplier_id if supplier else None, **kwargs)

    def _history(self, supplier: ScSupplier, history: List[ScPriceRecord], audit: dict) -> SpecialistResult:
        first, last = history[0], history[-1]
        change_12m = calc.pct_change(first.unit_price, last.unit_price)
        months = [
            {"month": r.month.strftime("%Y-%m"), "unit_price": str(r.unit_price), "monthly_volume": r.monthly_volume,
             "ref": price_ref(r.source_file, r.source_row).label()}
            for r in history
        ]
        facts = [
            Fact(key="history", label="Histórico mensual", value=months,
                 refs=[price_ref(r.source_file, r.source_row) for r in history]),
            Fact(key="change_12m_pct", label="Variación en el periodo", value=str(change_12m), unit="%",
                 refs=[price_ref(first.source_file, first.source_row), price_ref(last.source_file, last.source_row)]),
        ]
        finding = (f"{supplier.name} ({last.product}): de {fmt.unit_price(first.unit_price, last.currency, last.unit)} "
                   f"en {first.month:%Y-%m} a {fmt.unit_price(last.unit_price, last.currency, last.unit)} en "
                   f"{last.month:%Y-%m} ({fmt.pct(change_12m)} en {len(history)} meses).")
        return self._result("price_history", supplier, facts=facts, findings=[finding])

    def _change_facts(self, change: PriceChange) -> List[Fact]:
        prev_ref = price_ref(change.previous.source_file, change.previous.source_row)
        last_ref = price_ref(change.latest.source_file, change.latest.source_row)
        return [
            Fact(key="previous_price", label=f"Precio {change.previous.month:%Y-%m}", value=str(change.previous.unit_price),
                 unit=f"{change.currency}/{change.unit}", refs=[prev_ref]),
            Fact(key="latest_price", label=f"Precio {change.latest.month:%Y-%m}", value=str(change.latest.unit_price),
                 unit=f"{change.currency}/{change.unit}", refs=[last_ref]),
            Fact(key="change_pct", label="Variación frente al mes anterior", value=str(change.change_pct), unit="%",
                 refs=[prev_ref, last_ref]),
            Fact(key="threshold_pct", label="Umbral de alerta", value=str(PRICE_INCREASE_THRESHOLD_PCT), unit="%",
                 refs=[THRESHOLD_REF]),
            Fact(key="exceeds_threshold", label="Supera el umbral", value=change.exceeds_threshold, refs=[THRESHOLD_REF]),
        ]

    def _change_sentence(self, supplier_name: str, change: PriceChange) -> str:
        verdict = "supera" if change.exceeds_threshold else "no supera"
        return (f"{supplier_name}: {fmt.pct(change.change_pct)} en {change.latest.month:%Y-%m} "
                f"(de {fmt.unit_price(change.previous.unit_price, change.currency, change.unit)} a "
                f"{fmt.unit_price(change.latest.unit_price, change.currency, change.unit)}); {verdict} el umbral "
                f"de alerta del {fmt.plain_pct(PRICE_INCREASE_THRESHOLD_PCT)}.")

    def _latest_change(self, history: List[ScPriceRecord], audit: dict) -> Optional[PriceChange]:
        changes = self.runtime.call("detect_price_increases", detect_price_increases, history, audit=audit)
        return changes[0] if changes else None

    def _increase(self, supplier: ScSupplier, history: List[ScPriceRecord], audit: dict) -> SpecialistResult:
        change = self._latest_change(history, audit)
        if change is None:
            return self._result("detect_increases", supplier, status="no_data",
                                missing=["Hace falta al menos dos meses de histórico para medir una variación."])
        alerts = ([{"kind": "price_increase", "supplier_id": supplier.supplier_id, "month": f"{change.latest.month:%Y-%m}"}]
                  if change.exceeds_threshold else [])
        return self._result("detect_increases", supplier, facts=self._change_facts(change),
                            findings=[self._change_sentence(supplier.name, change)], alerts=alerts)

    def _detect_all(self, audit: dict) -> SpecialistResult:
        records = repository.all_prices(self.session)
        changes = self.runtime.call("detect_price_increases", detect_price_increases, records, audit=audit)
        names = {s.supplier_id: s.name for s in repository.list_suppliers(self.session)}
        flagged = [c for c in changes if c.exceeds_threshold]
        facts = [Fact(key=f"change_pct:{c.supplier_id}", label=f"Variación {names.get(c.supplier_id)}",
                      value=str(c.change_pct), unit="%",
                      refs=[price_ref(c.previous.source_file, c.previous.source_row),
                            price_ref(c.latest.source_file, c.latest.source_row)]) for c in flagged]
        findings = [self._change_sentence(names.get(c.supplier_id, c.supplier_id), c) for c in flagged]
        if not flagged:
            findings = [f"Ningún proveedor supera el umbral del {fmt.plain_pct(PRICE_INCREASE_THRESHOLD_PCT)} en el último mes."]
        return SpecialistResult(agent_id=self.agent_id, intent="detect_increases", facts=facts, findings=findings,
                                alerts=[{"kind": "price_increase", "supplier_id": c.supplier_id,
                                         "month": f"{c.latest.month:%Y-%m}"} for c in flagged])

    def _impact(self, supplier: ScSupplier, history: List[ScPriceRecord], audit: dict) -> SpecialistResult:
        change = self._latest_change(history, audit)
        if change is None:
            return self._result("increase_impact", supplier, status="no_data",
                                missing=["Sin dos meses de histórico no hay subida que valorar."])
        impact = self.runtime.call("compute_increase_impact", compute_increase_impact, change, audit=audit)
        last_ref = price_ref(change.latest.source_file, change.latest.source_row)
        facts = self._change_facts(change) + [
            Fact(key="monthly_volume", label="Volumen mensual actual", value=change.latest.monthly_volume,
                 unit=change.unit, refs=[last_ref]),
            Fact(key="annual_impact", label="Impacto anual de la subida", value=str(impact), unit=change.currency,
                 refs=[Ref(kind="calculation", source="calculations.annual_impact",
                           locator="(precio nuevo − anterior) × volumen mensual × 12")]),
        ]
        sentence = (f"Impacto anual de la subida de {supplier.name}: {fmt.money(impact, change.currency)} "
                    f"(({fmt.unit_price(change.latest.unit_price, change.currency, change.unit)} − "
                    f"{fmt.unit_price(change.previous.unit_price, change.currency, change.unit)}) × "
                    f"{fmt.integer(change.latest.monthly_volume)} {change.unit}/mes × 12).")
        findings = [sentence]
        if change.currency == "GBP":
            usd = self.runtime.call("convert_currency", convert_currency, impact, "GBP", "USD", audit=audit)
            facts.append(Fact(key="annual_impact_usd", label="Impacto anual en USD", value=str(usd), unit="USD",
                              refs=[Ref(**REFERENCE_REF, kind="reference")]))
            findings.append(f"Equivale a {fmt.money(usd, 'USD')} al tipo de referencia 1 GBP = {GBP_TO_USD} USD.")
        if change.latest.monthly_volume == 0:
            findings.append(f"{supplier.name} no tiene volumen de compra (solo cotización): la subida no tiene coste hoy.")
        return self._result("increase_impact", supplier, facts=facts, findings=findings)

    def _compare(self, supplier: ScSupplier, history: List[ScPriceRecord], audit: dict) -> SpecialistResult:
        latest = history[-1]
        peers = self.runtime.call("compare_same_category_prices", compare_same_category_prices, self.session, supplier,
                                  audit=audit)
        category = CATEGORY_LABELS.get(supplier.category, supplier.category)
        if not peers:
            return self._result("compare_alternatives", supplier, status="no_data",
                                missing=[f"No hay otro proveedor de {category.lower()} en {supplier.country}: "
                                         "no existe una alternativa comparable en el registro."])
        comparisons = calc.compare_prices(latest.unit_price, latest.monthly_volume,
                                          [(p["supplier"].supplier_id, p["latest"].unit_price) for p in peers])
        by_id = {p["supplier"].supplier_id: p for p in peers}
        facts = [Fact(key="current_price", label=f"Precio actual de {supplier.name}", value=str(latest.unit_price),
                      unit=f"{latest.currency}/{latest.unit}", refs=[price_ref(latest.source_file, latest.source_row)])]
        findings = []
        for comp in comparisons:
            peer = by_id[comp.supplier_id]
            record: ScPriceRecord = peer["latest"]
            ref = price_ref(record.source_file, record.source_row)
            facts += [
                Fact(key=f"alt_price:{comp.supplier_id}", label=f"Precio de {peer['supplier'].name}",
                     value=str(comp.unit_price), unit=f"{record.currency}/{record.unit}", refs=[ref]),
                Fact(key=f"alt_annual_saving:{comp.supplier_id}", label=f"Ahorro anual con {peer['supplier'].name}",
                     value=str(comp.annual_saving), unit=record.currency,
                     refs=[ref, price_ref(latest.source_file, latest.source_row)]),
            ]
            kind = "solo cotización" if record.monthly_volume == 0 else "proveedor activo"
            findings.append(
                f"{peer['supplier'].name} ({kind}): {fmt.unit_price(comp.unit_price, record.currency, record.unit)} en "
                f"{record.month:%Y-%m}, {fmt.pct(comp.difference_pct)} frente a {supplier.name}; ahorro anual con el "
                f"volumen actual: {fmt.money(comp.annual_saving, record.currency)}.")
        return self._result("compare_alternatives", supplier, facts=facts, findings=findings)
