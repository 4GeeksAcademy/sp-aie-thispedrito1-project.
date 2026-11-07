"""Brief de renegociación: el supervisor combina a los dos especialistas.

Orden (BRIEFING, "Restricción específica"):
1. Si el proveedor procesa datos de pacientes, se comprueba su BAA/DPA. Si
   falta y Cumplimiento no ha autorizado continuar, el brief SE DETIENE, se
   alerta a Claire Whitfield y la respuesta lo dice.
2. Precios: histórico, subida + impacto y comparativa (misma categoría y país).
3. Contratos: términos, si la subida cabe en la cláusula de revisión y fechas
   límite (preaviso, vencimiento).
4. Cruce: estado del acuerdo de la alternativa más barata (si procesa datos de
   pacientes) y coste de salida anticipada.
5. Recomendación determinista (`recommend_actions`) y Markdown con todas las
   referencias numeradas.

Ninguna cifra la escribe un modelo: todas salen de `Fact`s con su referencia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field
from sqlmodel import Session

from services.supplier_contracts import calculations as calc
from services.supplier_contracts import formatting as fmt
from services.supplier_contracts import repository
from services.supplier_contracts.alerts import persist_alerts
from services.supplier_contracts.catalog import (
    AGENT_SUPERVISOR,
    CATEGORY_LABELS,
    NOTICE_DEADLINE_ALERT_DAYS,
    PEOPLE,
    ROLE_COMPLIANCE,
)
from services.supplier_contracts.contracts import NOT_PERMITTED, PERMITTED
from services.supplier_contracts.runtime import ActionLog, ContractsRequest, PricingRequest, SpecialistResult
from services.supplier_contracts.supervisor import Supervisor


class BriefResult(BaseModel):
    request_id: str
    status: str  # ready | blocked | not_found
    supplier_id: str
    supplier_name: Optional[str] = None
    message: Optional[str] = None
    sections: Dict[str, List[str]] = Field(default_factory=dict)
    recommendation: List[str] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)
    consultations: List[Dict[str, Any]] = Field(default_factory=list)
    markdown: str = ""


# --- Comprobación previa de protección de datos --------------------------------

BLOCKED_MESSAGE = (
    "Brief detenido: {name} procesa datos de pacientes y no tiene firmado su {agreement}. Se ha alertado a "
    "Cumplimiento ({compliance}). El brief y cualquier comunicación con el proveedor solo podrán prepararse cuando "
    "Cumplimiento confirme que se puede continuar."
)


def data_protection_gate(session: Session, supervisor: Supervisor, supplier_id: str) -> Tuple[bool, Optional[str], Optional[SpecialistResult]]:
    """(puede continuar, mensaje, resultado del especialista)."""
    supplier = repository.get_supplier(session, supplier_id)
    if supplier is None or not supplier.processes_patient_data:
        return True, None, None
    result = supervisor.consult(ContractsRequest(intent="data_protection_status", supplier_id=supplier_id))
    status = (result.fact("data_protection").value if result and result.fact("data_protection") else {}) or {}
    if status.get("status") == "signed":
        return True, None, result
    persist_alerts(session, supervisor, supervisor.log, results=[result] if result else [])
    if repository.has_clearance(session, supplier_id):
        supervisor.log.record(AGENT_SUPERVISOR, "data_protection_gate", "cleared_by_compliance", supplier_id=supplier_id)
        return True, (f"Cumplimiento autorizó continuar con {supplier.name} aunque falta su "
                      f"{status.get('required')}."), result
    supervisor.log.record(AGENT_SUPERVISOR, "data_protection_gate", "blocked", supplier_id=supplier_id)
    return False, BLOCKED_MESSAGE.format(name=supplier.name, agreement=status.get("required") or "acuerdo",
                                         compliance=PEOPLE[ROLE_COMPLIANCE]), result


# --- Recomendación ----------------------------------------------------------------


@dataclass
class BriefInputs:
    """Lo que la recomendación necesita, ya calculado y con referencias aparte."""

    supplier_name: str
    currency: str
    change_pct: Optional[Decimal] = None  # subida del último mes
    exceeds_threshold: bool = False
    permission: Optional[str] = None  # permitted | not_permitted | undetermined | None (sin contrato)
    cap_pct: Optional[Decimal] = None
    annual_impact: Optional[Decimal] = None
    alternative_name: Optional[str] = None  # alternativa más barata, si la hay
    alternative_saving: Optional[Decimal] = None  # ahorro anual con el volumen actual
    alternative_has_contract: bool = False
    alternative_agreement_ok: Optional[bool] = None  # None = no procesa datos de pacientes
    notice_deadline: Optional[str] = None
    days_to_notice_deadline: Optional[int] = None
    exit_summary: Optional[str] = None
    exit_cost: Optional[Decimal] = None
    has_contract: bool = True


def recommend_actions(inputs: BriefInputs) -> List[str]:
    """Recomendación del brief: lista de acciones en español, de la más urgente
    a la menos. Pura y determinista (sin modelo): solo combina `inputs`.

    Delegado por el usuario (Learn by Doing). Criterio:
    1. Un preaviso que vence pronto va primero: si se pasa, el contrato se
       renueva solo y el resto de palancas pierde fuerza.
    2. La subida: rechazarla si el contrato no la permite; negociarla si la
       permite; no afirmar nada si la cláusula falta o es dudosa.
    3. La alternativa: palanca de negociación, pero nunca un cambio de
       proveedor sin BAA/DPA firmado.
    4. Coste de salida frente al impacto anual: ¿rescindir o esperar?
    Toda cifra sale de `inputs` (lo vigila un test).
    """
    actions: List[str] = []
    impact = fmt.money(inputs.annual_impact, inputs.currency) if inputs.annual_impact else None
    days = inputs.days_to_notice_deadline

    # 1. Plazo de preaviso.
    urgent_notice = days is not None and 0 <= days <= NOTICE_DEADLINE_ALERT_DAYS
    if urgent_notice:
        actions.append(f"Decidir antes del {inputs.notice_deadline} (quedan {days} días) si se envía el preaviso de no "
                       "renovación: si no se avisa, el contrato se renueva automáticamente con el precio nuevo.")
    elif days is not None and days < 0:
        actions.append(f"La fecha límite de preaviso ({inputs.notice_deadline}) ya pasó: el contrato se renovará y "
                       "la salida dependerá de su cláusula de salida.")

    # 2. La subida frente a la cláusula de revisión.
    pct = fmt.plain_pct(inputs.change_pct) if inputs.change_pct is not None else None
    if inputs.permission == NOT_PERMITTED:
        cap = fmt.plain_pct(inputs.cap_pct) if inputs.cap_pct is not None else "el tope"
        actions.append(f"Rechazar la subida del {pct}: supera el tope contractual del {cap}. Exigir que la "
                       f"facturación se mantenga dentro de ese tope" + (f" (la subida costaría {impact} al año)." if impact else "."))
    elif inputs.permission == PERMITTED and inputs.exceeds_threshold:
        cap = fmt.plain_pct(inputs.cap_pct) if inputs.cap_pct is not None else "el tope"
        actions.append(f"La subida del {pct} cabe en el tope del {cap}: no se puede rechazar por contrato, pero sí "
                       f"negociar" + (f" (impacto de {impact} al año)." if impact else "."))
    elif inputs.permission == "undetermined":
        actions.append("No se puede afirmar si la subida está permitida: revisar a mano la cláusula de revisión de "
                       "precio antes de responder al proveedor.")
    elif not inputs.has_contract:
        actions.append(f"No hay contrato con {inputs.supplier_name}: el precio se negocia pedido a pedido. Valorar "
                       "formalizar un contrato con tope de revisión de precio.")

    # 3. Alternativa de la misma categoría y país.
    if inputs.alternative_name and inputs.alternative_saving and inputs.alternative_saving > 0:
        saving = fmt.money(inputs.alternative_saving, inputs.currency)
        if inputs.alternative_agreement_ok is False:
            actions.append(f"Usar la cotización de {inputs.alternative_name} (ahorro de {saving} al año) como palanca "
                           "de negociación, pero no cambiar de proveedor hasta que firme su acuerdo de protección de "
                           "datos y Cumplimiento lo valide.")
        else:
            note = " Hoy solo hay cotización, sin contrato." if not inputs.alternative_has_contract else ""
            actions.append(f"Pedir a {inputs.alternative_name} una oferta formal: ahorro estimado de {saving} al año "
                           f"con el volumen actual.{note}")
    elif inputs.alternative_name:
        actions.append(f"{inputs.alternative_name} no es más barato: la comparativa no da palanca de precio.")
    elif inputs.has_contract:
        actions.append("No hay alternativa de la misma categoría y país: negociar sin la opción de cambiar de proveedor.")

    # 4. ¿Rescindir antes o esperar al vencimiento?
    if inputs.exit_cost is not None and inputs.annual_impact:
        exit_cost = fmt.money(inputs.exit_cost, inputs.currency)
        if inputs.exit_cost > inputs.annual_impact:
            actions.append(f"Salir antes del vencimiento cuesta {exit_cost} de penalización, más que el impacto anual "
                           f"de la subida ({impact}): es preferible no renovar que rescindir.")
        else:
            actions.append(f"La penalización de salida ({exit_cost}) es menor que el impacto anual de la subida "
                           f"({impact}): la rescisión anticipada es una opción real.")
    elif inputs.exit_summary and "sin penalización" in inputs.exit_summary:
        actions.append("El contrato permite salir sin penalización: es una palanca adicional en la negociación.")

    if inputs.notice_deadline and not urgent_notice and not (days is not None and days < 0):
        actions.append(f"Fecha límite de preaviso de no renovación: {inputs.notice_deadline}.")
    if not actions:
        actions.append("Sin acciones urgentes: mantener el seguimiento mensual de precios.")
    return actions


# --- Construcción -----------------------------------------------------------------


def _fact_value(result: Optional[SpecialistResult], key: str) -> Any:
    fact = result.fact(key) if result else None
    return fact.value if fact else None


def build_brief(session: Session, supplier_id: str, *, requester: str, today: Optional[date] = None,
                log: Optional[ActionLog] = None) -> BriefResult:
    log = log or ActionLog()
    log.record(requester, "build_brief", "requested", supplier_id=supplier_id)
    supervisor = Supervisor(session, log, today=today)
    supplier = repository.get_supplier(session, supplier_id)
    if supplier is None:
        log.flush(session)
        return BriefResult(request_id=log.request_id, status="not_found", supplier_id=supplier_id,
                           message="El proveedor indicado no está en el registro.")

    def run() -> BriefResult:
        allowed, gate_message, protection = data_protection_gate(session, supervisor, supplier_id)
        if not allowed:
            return BriefResult(request_id=log.request_id, status="blocked", supplier_id=supplier_id,
                               supplier_name=supplier.name, message=gate_message,
                               sections={"Protección de datos": protection.findings if protection else []},
                               missing=protection.missing if protection else [],
                               citations=[r.label() for r in protection.refs()] if protection else [],
                               consultations=_consultations(supervisor))

        history = supervisor.consult(PricingRequest(intent="price_history", supplier_id=supplier_id))
        impact = supervisor.consult(PricingRequest(intent="increase_impact", supplier_id=supplier_id))
        compare = supervisor.consult(PricingRequest(intent="compare_alternatives", supplier_id=supplier_id))
        terms = supervisor.consult(ContractsRequest(intent="contract_terms", supplier_id=supplier_id))
        change_pct = _fact_value(impact, "change_pct")
        permission = (supervisor.consult(ContractsRequest(intent="increase_permission", supplier_id=supplier_id,
                                                          increase_pct=change_pct))
                      if change_pct is not None and terms and terms.status == "ok" else None)
        deadlines = (supervisor.consult(ContractsRequest(intent="notice_deadline", supplier_id=supplier_id))
                     if terms and terms.status == "ok" else None)

        # Alternativa más barata (la comparativa viene ordenada de más barata a más cara).
        alternative = _cheapest_alternative(session, compare)
        alt_protection = None
        if alternative and alternative["supplier"].processes_patient_data:
            alt_protection = supervisor.consult(ContractsRequest(intent="data_protection_status",
                                                                 supplier_id=alternative["supplier"].supplier_id))
        exit_cost, exit_refs = _exit_cost(supervisor, terms, history)

        verdict = _fact_value(permission, "permission") or {}
        alt_status = _fact_value(alt_protection, "data_protection") or {}
        inputs = BriefInputs(
            supplier_name=supplier.name,
            currency=supplier.currency,
            change_pct=Decimal(change_pct) if change_pct is not None else None,
            exceeds_threshold=bool(_fact_value(impact, "exceeds_threshold")),
            permission=verdict.get("verdict") if verdict else None,
            cap_pct=Decimal(verdict["cap_pct"]) if verdict.get("cap_pct") is not None else None,
            annual_impact=Decimal(_fact_value(impact, "annual_impact")) if _fact_value(impact, "annual_impact") else None,
            alternative_name=alternative["supplier"].name if alternative else None,
            alternative_saving=alternative["saving"] if alternative else None,
            alternative_has_contract=bool(alternative and repository.contract_for_supplier(
                session, alternative["supplier"].supplier_id)),
            alternative_agreement_ok=(alt_status.get("status") == "signed") if alt_protection else None,
            notice_deadline=_fact_value(deadlines, "notice_deadline"),
            days_to_notice_deadline=_fact_value(deadlines, "days_to_notice_deadline"),
            exit_summary=next((f for f in (terms.findings if terms else []) if f.startswith("Salida")), None),
            exit_cost=exit_cost,
            has_contract=bool(terms and terms.status == "ok"),
        )
        recommendation = recommend_actions(inputs)
        log.record(AGENT_SUPERVISOR, "recommend", "ok", supplier_id=supplier_id, actions=len(recommendation))

        sections: Dict[str, List[str]] = {}
        sections["Protección de datos"] = (protection.findings if protection else
                                           [f"{supplier.name} no procesa datos de pacientes: no necesita BAA ni DPA."])
        if gate_message:
            sections["Protección de datos"].append(gate_message)
        sections["Histórico de precios"] = history.findings if history else []
        sections["Subida e impacto"] = (impact.findings if impact else []) + (permission.findings if permission else [])
        sections["Comparativa (misma categoría y país)"] = (compare.findings if compare else []) + (
            alt_protection.findings if alt_protection else [])
        margin = (terms.findings if terms else []) + (deadlines.findings if deadlines else [])
        if exit_cost is not None:
            margin.append(f"Coste estimado de salida anticipada: {fmt.money(exit_cost, supplier.currency)} "
                          f"({'; '.join(exit_refs)}).")
        sections["Margen contractual"] = margin

        results = [r for r in (protection, history, impact, compare, terms, permission, deadlines, alt_protection) if r]
        citations, missing = [], []
        for result in results:
            citations += [r.label() for r in result.refs() if r.label() not in citations]
            missing += [m for m in result.missing if m not in missing]
        citations += [r for r in exit_refs if r not in citations]
        brief = BriefResult(
            request_id=log.request_id, status="ready", supplier_id=supplier_id, supplier_name=supplier.name,
            message=gate_message, sections=sections, recommendation=recommendation, citations=citations,
            missing=missing, consultations=_consultations(supervisor),
        )
        brief.markdown = render_markdown(brief, supplier, history, today or date.today())
        return brief

    try:
        result = run()
    finally:
        log.flush(session)
    if supervisor.limit_reached:
        result.missing.append("Se alcanzó el límite de consultas por petición: el brief puede estar incompleto.")
    return result


def _consultations(supervisor: Supervisor) -> List[Dict[str, Any]]:
    return [{"agent_id": r.agent_id, "intent": r.intent, "supplier_id": r.supplier_id, "status": r.status}
            for r in supervisor.results]


def _cheapest_alternative(session: Session, compare: Optional[SpecialistResult]) -> Optional[Dict[str, Any]]:
    if not compare or compare.status != "ok":
        return None
    for fact in compare.facts:
        if fact.key.startswith("alt_price:"):
            sid = fact.key.split(":", 1)[1]
            saving = compare.fact(f"alt_annual_saving:{sid}")
            supplier = repository.get_supplier(session, sid)
            if supplier is not None:
                return {"supplier": supplier, "price": Decimal(fact.value),
                        "saving": Decimal(saving.value) if saving else Decimal(0)}
    return None


def _exit_cost(supervisor: Supervisor, terms: Optional[SpecialistResult],
               history: Optional[SpecialistResult]) -> Tuple[Optional[Decimal], List[str]]:
    exit_fact = terms.fact("term:exit_terms") if terms else None
    months = ((exit_fact.value or {}).get("value") or {}).get("penalty_months") if exit_fact else None
    rows = _fact_value(history, "history")
    if not months or not rows:
        return None, []
    pairs = [(Decimal(r["unit_price"]), int(r["monthly_volume"])) for r in rows]

    def compute() -> Decimal:
        return calc.early_exit_penalty(months, calc.average_monthly_billing(pairs))

    cost = supervisor.runtime.call("compute_exit_cost", compute, audit={"supplier_id": terms.supplier_id})
    refs = [r.label() for r in exit_fact.refs] + [f"{rows[0]['ref']} a {rows[-1]['ref']}"]
    return cost, refs


def render_markdown(brief: BriefResult, supplier, history: Optional[SpecialistResult], today: date) -> str:
    lines = [
        f"# Brief de renegociación — {supplier.name} ({supplier.supplier_id})",
        "",
        f"Preparado el {today.isoformat()} por el supervisor de proveedores (agentes de precios y de contratos). "
        f"Categoría: {CATEGORY_LABELS.get(supplier.category, supplier.category)}, {supplier.country}, "
        f"{supplier.currency}. Ninguna comunicación con el proveedor sale sin aprobación humana.",
        "",
    ]
    for title, items in brief.sections.items():
        lines += [f"## {title}", ""] + [f"- {item}" for item in items] + [""]
        if title == "Histórico de precios" and history and history.fact("history"):
            lines += ["| Mes | Precio unitario | Volumen mensual | Origen |", "|---|---|---|---|"]
            for row in history.fact("history").value:
                lines.append(f"| {row['month']} | {row['unit_price']} {supplier.currency} | {row['monthly_volume']} | "
                             f"{row['ref']} |")
            lines.append("")
    lines += ["## Recomendación", ""] + [f"{i}. {item}" for i, item in enumerate(brief.recommendation, 1)] + [""]
    if brief.missing:
        lines += ["## Datos que no existen o no se pudieron confirmar", ""] + [f"- {m}" for m in brief.missing] + [""]
    lines += ["## Trazabilidad", ""] + [f"[{i}] {c}" for i, c in enumerate(brief.citations, 1)]
    return "\n".join(lines)
