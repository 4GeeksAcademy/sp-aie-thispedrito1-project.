"""Especialista de contratos (`agent:contracts-specialist`).

Alcance estrecho: términos extraídos de los contratos, si una subida cabe en
la cláusula de revisión de precio, fechas límite (vencimiento y preaviso) y
estado del acuerdo de protección de datos. No ve precios: la subida a evaluar
le llega en la petición (`ContractsRequest.increase_pct`), calculada por el
especialista de precios y pasada por el supervisor.

Toda afirmación cita su cláusula y página (`clause_ref`). Si no hay contrato,
o la cláusula no existe o es dudosa, el resultado lo declara en `missing` y
la conclusión queda "no determinable"; nunca se supone.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session

from services.supplier_contracts import calculations as calc
from services.supplier_contracts import formatting as fmt
from services.supplier_contracts import repository
from services.supplier_contracts.catalog import (
    AGENT_CONTRACTS,
    AGREEMENT_BAA,
    AGREEMENT_DPA,
    AGREEMENT_NOT_APPLICABLE,
    EXPIRY_ALERT_DAYS,
    NOTICE_DEADLINE_ALERT_DAYS,
    REFERENCE_REF,
    REVIEW_CAP_PERCENT,
    REVIEW_CAP_US_CPI,
    REVIEW_FIXED,
    US_CPI_ANNUAL_PCT,
    required_agreement,
)
from services.supplier_contracts.ingestion import TERM_LABELS
from services.supplier_contracts.models import TERM_MISSING, TERM_OK, ScContract, ScContractTerm, ScSupplier
from services.supplier_contracts.runtime import (
    ActionLog,
    AgentRuntime,
    ContractsRequest,
    Fact,
    Ref,
    SpecialistResult,
    clause_ref,
)

PERMITTED = "permitted"
NOT_PERMITTED = "not_permitted"
UNDETERMINED = "undetermined"


# --- Herramientas ---------------------------------------------------------------


def get_contract_terms(session: Session, supplier_id: str) -> Tuple[Optional[ScContract], Dict[str, ScContractTerm]]:
    contract = repository.contract_for_supplier(session, supplier_id)
    if contract is None:
        return None, {}
    return contract, repository.contract_terms(session, contract.contract_id)


def evaluate_price_review_clause(increase_pct: Decimal, review: Optional[Dict[str, Any]], us_cpi_pct: Decimal) -> Dict[str, Any]:
    """¿Cabe `increase_pct` en la cláusula de revisión de precio?

    Devuelve {"verdict": permitted|not_permitted|undetermined, "cap_pct": str|None,
    "excess_pct": str|None, "basis": str}. `review` es el valor extraído del
    término `price_review` (None si la cláusula falta o es dudosa).
    """
    increase = Decimal(increase_pct)
    if review is None or review.get("type") is None:
        return {"verdict": UNDETERMINED, "cap_pct": None, "excess_pct": None,
                "basis": "La cláusula de revisión de precio no existe o no se pudo leer con confianza."}
    if review["type"] == REVIEW_FIXED:
        cap, basis = Decimal(0), "precio fijo durante toda la vigencia"
    elif review["type"] == REVIEW_CAP_US_CPI:
        cap, basis = Decimal(us_cpi_pct), f"tope anual del IPC de EE. UU. ({fmt.plain_pct(us_cpi_pct)})"
    elif review["type"] == REVIEW_CAP_PERCENT:
        cap = Decimal(review["cap_pct"])
        basis = f"tope anual del {fmt.plain_pct(cap)}"
    else:
        return {"verdict": UNDETERMINED, "cap_pct": None, "excess_pct": None,
                "basis": "Tipo de revisión de precio no reconocido."}
    excess = max(Decimal(0), increase - cap)
    return {
        "verdict": PERMITTED if increase <= cap else NOT_PERMITTED,
        "cap_pct": str(cap),
        "excess_pct": str(excess.quantize(Decimal("0.01"))),
        "basis": basis,
    }


def compute_contract_deadlines(expiry: date, notice_days: Optional[int], today: date) -> Dict[str, Any]:
    days_to_expiry = calc.days_until(expiry, today)
    out: Dict[str, Any] = {
        "expiry": expiry,
        "days_to_expiry": days_to_expiry,
        "expiry_alert_days": calc.due_expiry_alert(days_to_expiry, EXPIRY_ALERT_DAYS),
        "notice_deadline": None,
        "days_to_notice_deadline": None,
        "notice_alert": False,
    }
    if notice_days is not None:
        deadline = calc.notice_deadline(expiry, notice_days)
        days = calc.days_until(deadline, today)
        out.update(notice_deadline=deadline, days_to_notice_deadline=days,
                   notice_alert=calc.due_notice_alert(days, NOTICE_DEADLINE_ALERT_DAYS))
    return out


def check_data_protection_agreement(supplier: ScSupplier, contract: Optional[ScContract],
                                    term: Optional[ScContractTerm]) -> Dict[str, Any]:
    """Estado del acuerdo exigido: not_required | signed | missing | doubtful."""
    from services.supplier_contracts.catalog import SUPPLIERS_BY_ID

    spec = SUPPLIERS_BY_ID.get(supplier.supplier_id)
    required = required_agreement(spec) if spec else (
        (AGREEMENT_BAA if supplier.country == "US" else AGREEMENT_DPA) if supplier.processes_patient_data else None)
    if required is None:
        return {"required": None, "status": "not_required"}
    if contract is None:
        return {"required": required, "status": "missing", "reason": "no_contract"}
    if term is None or term.status == TERM_MISSING:
        return {"required": required, "status": "missing", "reason": "no_clause"}
    if term.status != TERM_OK:
        return {"required": required, "status": "doubtful", "reason": "low_confidence"}
    value = term.value or {}
    if value.get("agreement") == required and value.get("signed"):
        return {"required": required, "status": "signed"}
    return {"required": required, "status": "missing", "reason": "wrong_or_unsigned_agreement"}


def lookup_clause(terms: Dict[str, ScContractTerm], topic: str) -> Optional[ScContractTerm]:
    return terms.get(topic)


# --- El agente -------------------------------------------------------------------


class ContractsSpecialist:
    agent_id = AGENT_CONTRACTS

    def __init__(self, session: Session, log: ActionLog, today: Optional[date] = None) -> None:
        self.session = session
        self.today = today or date.today()
        self.runtime = AgentRuntime(AGENT_CONTRACTS, log)

    def handle(self, request: ContractsRequest) -> SpecialistResult:
        audit = {"intent": request.intent, "supplier_id": request.supplier_id, "topic": request.topic}
        if request.intent == "data_protection_status" and request.supplier_id is None:
            return self._protection_all(audit)
        supplier = repository.get_supplier(self.session, request.supplier_id or "")
        if supplier is None:
            return SpecialistResult(agent_id=self.agent_id, intent=request.intent, supplier_id=request.supplier_id,
                                    status="not_found", missing=["El proveedor indicado no está en el registro."])
        contract, terms = self.runtime.call("get_contract_terms", get_contract_terms, self.session,
                                            supplier.supplier_id, audit=audit)
        if request.intent == "data_protection_status":
            return self._protection_one(supplier, contract, terms, audit)
        if contract is None:
            return self._no_contract(request.intent, supplier)
        handler = {
            "contract_terms": lambda: self._terms(supplier, contract, terms),
            "increase_permission": lambda: self._permission(supplier, contract, terms, request.increase_pct, audit),
            "notice_deadline": lambda: self._deadlines(supplier, contract, terms, audit),
            "clause_lookup": lambda: self._clause(supplier, contract, terms, request.topic, audit),
        }[request.intent]
        return handler()

    # -- helpers --

    def _result(self, intent: str, supplier: ScSupplier, **kwargs) -> SpecialistResult:
        return SpecialistResult(agent_id=self.agent_id, intent=intent, supplier_id=supplier.supplier_id, **kwargs)

    def _no_contract(self, intent: str, supplier: ScSupplier) -> SpecialistResult:
        return self._result(intent, supplier, status="no_contract",
                            findings=[f"No hay contrato registrado con {supplier.name}: solo cotizaciones o pedidos "
                                      "puntuales. No puedo citar ninguna cláusula."],
                            missing=[f"Contrato con {supplier.name}"])

    @staticmethod
    def _ref(contract: ScContract, term: ScContractTerm) -> Ref:
        return clause_ref(contract.source_file, term.clause, term.page)

    def _term_fact(self, contract: ScContract, term: ScContractTerm) -> Fact:
        return Fact(key=f"term:{term.term}", label=TERM_LABELS.get(term.term, term.term),
                    value={"value": term.value, "status": term.status, "confidence": term.confidence,
                           "quote": term.quote},
                    refs=[self._ref(contract, term)] if term.clause else [])

    def _missing_term(self, contract: ScContract, term: Optional[ScContractTerm], name: str) -> Optional[str]:
        if term is None or term.status == TERM_MISSING:
            return f"{contract.contract_id} no tiene cláusula de {TERM_LABELS.get(name, name).lower()}."
        if term.status != TERM_OK:
            return (f"{contract.contract_id} §{term.clause}, p. {term.page}: la extracción de "
                    f"{TERM_LABELS.get(name, name).lower()} es dudosa (confianza {term.confidence:.2f}); revisar a mano.")
        return None

    # -- intents --

    def _terms(self, supplier: ScSupplier, contract: ScContract, terms: Dict[str, ScContractTerm]) -> SpecialistResult:
        facts, findings, missing = [], [], []
        for name in TERM_LABELS:
            term = terms.get(name)
            gap = self._missing_term(contract, term, name)
            if gap:
                missing.append(gap)
                continue
            facts.append(self._term_fact(contract, term))
            findings.append(f"{TERM_LABELS[name]}: {describe_term(term)} ({contract.contract_id} §{term.clause}, p. {term.page}).")
        return self._result("contract_terms", supplier, facts=facts, findings=findings, missing=missing)

    def _permission(self, supplier, contract, terms, increase_pct, audit) -> SpecialistResult:
        if increase_pct is None:
            return self._result("increase_permission", supplier, status="no_data",
                                missing=["Falta la subida a evaluar."])
        term = terms.get("price_review")
        gap = self._missing_term(contract, term, "price_review")
        review = None if gap else term.value
        verdict = self.runtime.call("evaluate_price_review_clause", evaluate_price_review_clause,
                                    Decimal(increase_pct), review, US_CPI_ANNUAL_PCT, audit=audit)
        refs = [self._ref(contract, term)] if term is not None and term.clause else []
        if review and review.get("type") == REVIEW_CAP_US_CPI:
            refs.append(Ref(kind="reference", **REFERENCE_REF))
        facts = [
            Fact(key="increase_pct", label="Subida evaluada", value=str(increase_pct), unit="%"),
            Fact(key="permission", label="¿Lo permite el contrato?", value=verdict, refs=refs),
        ]
        citation = f"{contract.contract_id} §{term.clause}, p. {term.page}" if refs else contract.contract_id
        if verdict["verdict"] == UNDETERMINED:
            finding = f"No puedo determinar si {supplier.name} puede subir un {fmt.plain_pct(Decimal(increase_pct))}: {verdict['basis']}"
        elif verdict["verdict"] == PERMITTED:
            finding = (f"Permitida: una subida del {fmt.plain_pct(Decimal(increase_pct))} está dentro del "
                       f"{verdict['basis']} ({citation}).")
        else:
            finding = (f"No permitida: una subida del {fmt.plain_pct(Decimal(increase_pct))} supera el "
                       f"{verdict['basis']} en {fmt.plain_pct(Decimal(verdict['excess_pct']))} ({citation}).")
        missing = [gap] if gap else []
        if review and review.get("annual"):
            missing.append("El histórico no registra la fecha de la última revisión anual: se evalúa la subida "
                           "detectada frente al tope anual de la cláusula.")
        return self._result("increase_permission", supplier, facts=facts, findings=[finding], missing=missing)

    def _deadlines(self, supplier, contract, terms, audit) -> SpecialistResult:
        expiry_term, notice_term = terms.get("expiry_date"), terms.get("non_renewal_notice")
        gaps = [g for g in (self._missing_term(contract, expiry_term, "expiry_date"),
                            self._missing_term(contract, notice_term, "non_renewal_notice")) if g]
        if self._missing_term(contract, expiry_term, "expiry_date") or not (expiry_term.value or {}).get("date"):
            return self._result("notice_deadline", supplier, status="no_data", missing=gaps or [
                f"{contract.contract_id} no permite leer la fecha de vencimiento."])
        expiry = date.fromisoformat(expiry_term.value["date"])
        notice_days = (notice_term.value or {}).get("days") if notice_term and notice_term.status == TERM_OK else None
        d = self.runtime.call("compute_contract_deadlines", compute_contract_deadlines, expiry, notice_days, self.today,
                              audit=audit)
        expiry_ref = self._ref(contract, expiry_term)
        calc_ref = Ref(kind="calculation", source="calculations.notice_deadline", locator="vencimiento − días de preaviso")
        facts = [Fact(key="expiry_date", label="Vencimiento", value=expiry.isoformat(), refs=[expiry_ref]),
                 Fact(key="days_to_expiry", label="Días hasta el vencimiento", value=d["days_to_expiry"], unit="días",
                      refs=[expiry_ref])]
        findings = [f"{contract.contract_id} vence el {expiry.isoformat()} (dentro de {d['days_to_expiry']} días; "
                    f"{contract.contract_id} §{expiry_term.clause}, p. {expiry_term.page})."]
        alerts = []
        if notice_days is not None:
            notice_ref = self._ref(contract, notice_term)
            facts += [
                Fact(key="notice_days", label="Preaviso de no renovación", value=notice_days, unit="días", refs=[notice_ref]),
                Fact(key="notice_deadline", label="Último día para avisar", value=d["notice_deadline"].isoformat(),
                     refs=[expiry_ref, notice_ref, calc_ref]),
                Fact(key="days_to_notice_deadline", label="Días hasta la fecha límite de preaviso",
                     value=d["days_to_notice_deadline"], unit="días", refs=[expiry_ref, notice_ref]),
            ]
            when = ("ya ha pasado" if d["days_to_notice_deadline"] < 0
                    else f"quedan {d['days_to_notice_deadline']} días")
            findings.append(
                f"Para no renovar hay que avisar por escrito a {supplier.name} como tarde el "
                f"{d['notice_deadline'].isoformat()} ({notice_days} días antes del vencimiento; {when}; "
                f"{contract.contract_id} §{notice_term.clause}, p. {notice_term.page}). Si no se avisa, el contrato se "
                f"renueva automáticamente.")
            if d["notice_alert"]:
                alerts.append({"kind": "notice_deadline", "supplier_id": supplier.supplier_id,
                               "due_date": d["notice_deadline"].isoformat()})
        if d["expiry_alert_days"] is not None:
            alerts.append({"kind": f"expiry_{d['expiry_alert_days']}", "supplier_id": supplier.supplier_id,
                           "due_date": expiry.isoformat()})
        return self._result("notice_deadline", supplier, facts=facts, findings=findings, missing=gaps, alerts=alerts)

    def _clause(self, supplier, contract, terms, topic, audit) -> SpecialistResult:
        if topic is None:
            return self._terms(supplier, contract, terms)
        term = self.runtime.call("lookup_clause", lookup_clause, terms, topic, audit=audit)
        gap = self._missing_term(contract, term, topic)
        if gap and (term is None or term.status == TERM_MISSING):
            return self._result("clause_lookup", supplier, findings=[gap], missing=[gap])
        finding = (f"{TERM_LABELS[topic]}: {describe_term(term)}. Texto: «{term.quote}» "
                   f"({contract.contract_id} §{term.clause}, p. {term.page}).")
        return self._result("clause_lookup", supplier, facts=[self._term_fact(contract, term)], findings=[finding],
                            missing=[gap] if gap else [])

    def _protection_one(self, supplier, contract, terms, audit) -> SpecialistResult:
        term = terms.get("data_protection")
        status = self.runtime.call("check_data_protection_agreement", check_data_protection_agreement,
                                   supplier, contract, term, audit=audit)
        refs = [self._ref(contract, term)] if contract and term is not None and term.clause else []
        facts = [Fact(key="data_protection", label="Acuerdo de protección de datos", value=status, refs=refs)]
        finding, alerts, missing = describe_protection(supplier, contract, term, status), [], []
        if status["status"] in {"missing", "doubtful"}:
            alerts.append({"kind": "data_protection_missing", "supplier_id": supplier.supplier_id})
            missing.append(f"{status['required']} firmado con {supplier.name}")
        return self._result("data_protection_status", supplier, facts=facts, findings=[finding], alerts=alerts,
                            missing=missing)

    def _protection_all(self, audit) -> SpecialistResult:
        facts, findings, alerts, missing = [], [], [], []
        for supplier in repository.list_suppliers(self.session):
            if not supplier.processes_patient_data:
                continue
            contract, terms = self.runtime.call("get_contract_terms", get_contract_terms, self.session,
                                                supplier.supplier_id, audit=audit)
            term = terms.get("data_protection")
            status = self.runtime.call("check_data_protection_agreement", check_data_protection_agreement,
                                       supplier, contract, term, audit={**audit, "supplier_id": supplier.supplier_id})
            refs = [self._ref(contract, term)] if contract and term is not None and term.clause else []
            facts.append(Fact(key=f"data_protection:{supplier.supplier_id}", label=f"Acuerdo de {supplier.name}",
                              value=status, refs=refs))
            if status["status"] != "signed":
                findings.append(describe_protection(supplier, contract, term, status))
                alerts.append({"kind": "data_protection_missing", "supplier_id": supplier.supplier_id})
                missing.append(f"{status['required']} firmado con {supplier.name}")
        if not findings:
            findings.append("Todos los proveedores que procesan datos de pacientes tienen su acuerdo firmado.")
        return SpecialistResult(agent_id=self.agent_id, intent="data_protection_status", facts=facts,
                                findings=findings, alerts=alerts, missing=missing)


def describe_protection(supplier: ScSupplier, contract: Optional[ScContract], term: Optional[ScContractTerm],
                        status: Dict[str, Any]) -> str:
    required = status.get("required")
    if status["status"] == "not_required":
        return f"{supplier.name} no procesa datos de pacientes: no necesita BAA ni DPA."
    if status["status"] == "signed":
        return (f"{supplier.name} tiene firmado su {required} ({contract.contract_id} §{term.clause}, p. {term.page}).")
    if status.get("reason") == "no_contract":
        return (f"{supplier.name} procesa datos de pacientes y no tiene contrato ni {required} firmado "
                f"(solo cotizaciones o pedidos puntuales). No debe recibir datos de pacientes hasta firmarlo.")
    if status["status"] == "doubtful":
        return f"{supplier.name}: la cláusula de protección de datos de {contract.contract_id} es dudosa; revisar a mano."
    return (f"{supplier.name} procesa datos de pacientes y su contrato {contract.contract_id} no incluye {required}: "
            f"se alerta a Cumplimiento (Claire Whitfield).")


def describe_term(term: ScContractTerm) -> str:
    """Valor de un término en una frase. Para estados no OK, quien llama ya
    lo ha declarado como dudoso/ausente."""
    value = term.value or {}
    if term.term == "expiry_date":
        return f"vence el {value.get('date')}" if value.get("date") else "fecha no legible"
    if term.term == "non_renewal_notice":
        return f"preaviso de no renovación de {value.get('days')} días antes del vencimiento"
    if term.term == "price_review":
        kind = value.get("type")
        if kind == REVIEW_FIXED:
            return "precio fijo durante toda la vigencia"
        if kind == REVIEW_CAP_US_CPI:
            return "revisión anual con tope del IPC de EE. UU."
        if kind == REVIEW_CAP_PERCENT:
            return f"revisión anual con tope del {fmt.plain_pct(Decimal(value.get('cap_pct')))}"
        return "revisión de precio sin tope legible"
    if term.term == "exit_terms":
        if value.get("penalty_months"):
            return f"rescisión anticipada con penalización de {value['penalty_months']} meses de facturación media"
        if value.get("notice_days"):
            return f"salida con {value['notice_days']} días de preaviso, sin penalización"
        if value.get("without_penalty"):
            return "salida en cualquier momento, sin penalización"
        return "condiciones de salida no legibles"
    if term.term == "data_protection":
        agreement = value.get("agreement")
        if agreement == AGREEMENT_NOT_APPLICABLE:
            return "el proveedor no accede a datos de pacientes"
        return f"{agreement} {'firmado' if value.get('signed') else 'sin firma constatada'}"
    if term.term == "annual_value":
        return f"valor anual estimado de {fmt.money(Decimal(value['amount']), value['currency'], 0)}"
    return str(value)
