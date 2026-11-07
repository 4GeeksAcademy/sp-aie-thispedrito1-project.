"""El agente de cierre: un único agente con herramientas de cálculo.

Orden fijo (las cifras no dependen del modelo):

    read_period_data → compute_line_totals → compare_to_budget → convert/consolidate_usd
    → find_relevant_deviations → data_quality_report
    → [modelo] plan de explicaciones → payer_claims_drivers / unit_cost_drivers
    → [modelo] redacción (validada; plantilla de respaldo)

Toda herramienta pasa por `AgentRuntime.call` (lista cerrada + registro como
proceso automatizado). Las cifras de un `Analysis` son función pura de los
datos del periodo: mismo periodo y mismos datos → mismas cifras.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from services.financial_close import calculations as calc
from services.financial_close import drivers, narrative
from services.financial_close.catalog import AGENT_ACTOR
from services.financial_close.ingestion import PeriodDataset
from services.financial_close.runtime import ActionLog, AgentRuntime


@dataclass
class Analysis:
    period: date
    dataset: PeriodDataset
    totals: Dict[str, calc.LineTotal]
    comparisons: List[calc.LineComparison]
    consolidated: Dict[str, calc.ConsolidatedLine]
    deviations: List[narrative.Deviation]
    quality: Dict[str, list]
    narrative: narrative.Narrative
    comments: Optional[str] = None
    tools_used: List[str] = field(default_factory=list)


class CloseAgent:
    def __init__(self, log: ActionLog, *, client: Any = None, use_model: bool = True) -> None:
        self.log = log
        self.runtime = AgentRuntime(log)
        self.client = client
        self.use_model = use_model

    def analyze_dataset(self, dataset: PeriodDataset, *, comments: Optional[str] = None) -> Analysis:
        rt = self.runtime
        period = dataset.month
        audit = {"period": period.isoformat()}
        totals = rt.call("compute_line_totals", calc.compute_line_totals, dataset.ledger, audit=audit)
        comparisons = rt.call("compare_to_budget", calc.compare_to_budget, totals, dataset.budget, audit=audit)
        consolidated = rt.call("consolidate_usd", calc.consolidate_usd, comparisons, audit=audit)
        relevant = rt.call("find_relevant_deviations", drivers.find_relevant_deviations, comparisons,
                           audit={**audit, "threshold_pct": "5"})
        quality = rt.call("data_quality_report", drivers.data_quality_report, dataset, comparisons, audit=audit)

        plan = narrative.plan_drivers(relevant, client=self.client, use_model=self.use_model, log=self.log,
                                      actor=AGENT_ACTOR)
        deviations: List[narrative.Deviation] = []
        for comparison in relevant:
            tool, source = plan[comparison.key]
            result = None
            if tool == "payer_claims_drivers":
                result = rt.call(tool, drivers.payer_claims_drivers, dataset, comparison.key,
                                 audit={"line_key": comparison.key, "plan": source})
            elif tool == "unit_cost_drivers":
                result = rt.call(tool, drivers.unit_cost_drivers, dataset, comparison.key, comparison,
                                 audit={"line_key": comparison.key, "plan": source})
            deviations.append(narrative.Deviation(comparison, result, source))

        text = narrative.draft_narrative(period, deviations, consolidated, quality, comments=comments,
                                         client=self.client, use_model=self.use_model, log=self.log,
                                         actor=AGENT_ACTOR)
        return Analysis(period=period, dataset=dataset, totals=totals, comparisons=comparisons,
                        consolidated=consolidated, deviations=deviations, quality=quality, narrative=text,
                        comments=comments)

    def analyze(self, engine, period: date, *, comments: Optional[str] = None) -> Analysis:
        """Lee el periodo en SOLO LECTURA y lo analiza."""
        from services.financial_close.runtime import read_period_data

        dataset = self.runtime.call("read_period_data", read_period_data, engine, period,
                                    audit={"period": period.isoformat(), "access": "read_only"})
        return self.analyze_dataset(dataset, comments=comments)
