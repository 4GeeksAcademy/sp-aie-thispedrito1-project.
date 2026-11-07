"""Comunicaciones a proveedores: el sistema redacta, las personas deciden.

1. Antes de redactar nada para un proveedor con datos de pacientes, la misma
   comprobación de BAA/DPA que el brief (`brief.data_protection_gate`).
2. El supervisor reúne los hechos con los especialistas y el MODELO redacta el
   correo en inglés (BRIEFING: las comunicaciones con proveedores van en
   inglés). El código valida el borrador: un número que no esté en los hechos
   o un dato sensible → se descarta y se usa la plantilla. El saludo nunca
   lleva el nombre de una persona de contacto.
3. Aprobadores (BRIEFING, "Aprobaciones"): Marcus siempre; + Claire si el
   proveedor procesa datos de pacientes; + la Dra. Okonkwo si el contrato vale
   más de 500.000 USD al año (o su equivalente en GBP a 1,27).
4. Solo un borrador con TODAS sus aprobaciones se puede marcar como enviado.
   Un rechazo lo cierra. No hay canal de salida real: "enviado" es un registro
   en la bandeja de salida, revalidado contra datos sensibles.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session

from services.supplier_contracts import calculations as calc
from services.supplier_contracts import repository
from services.supplier_contracts.brief import data_protection_gate
from services.supplier_contracts.catalog import (
    AGENT_SUPERVISOR,
    HIGH_VALUE_CONTRACT_USD,
    PEOPLE,
    ROLE_CEO,
    ROLE_CLINICAL_OPS,
    ROLE_COMPLIANCE,
)
from services.supplier_contracts.models import (
    DRAFT_APPROVED,
    DRAFT_PENDING,
    DRAFT_REJECTED,
    DRAFT_SENT,
    ScApproval,
    ScCommunicationDraft,
    utcnow,
)
from services.supplier_contracts.runtime import ActionLog, ContractsRequest, PricingRequest, digest, human_actor
from services.supplier_contracts.sensitive import contains_sensitive, is_patient_level_request, redact
from services.supplier_contracts.supervisor import Supervisor

logger = logging.getLogger("healthcore.supplier_contracts.communications")

PURPOSES = ("price_increase_objection", "renegotiation_request", "non_renewal_notice")
DRAFT_TIMEOUT_S = 30.0


class CommunicationBlocked(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DecisionError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# --- Aprobadores -------------------------------------------------------------------


def required_approvals(processes_patient_data: bool, annual_value_usd: Optional[Decimal]) -> Tuple[List[str], List[str]]:
    roles = [ROLE_CLINICAL_OPS]
    reasons = ["Cualquier comunicación a un proveedor la aprueba el Dr. Marcus Reid."]
    if processes_patient_data:
        roles.append(ROLE_COMPLIANCE)
        reasons.append("El proveedor procesa datos de pacientes: también aprueba Claire Whitfield.")
    if annual_value_usd is not None and annual_value_usd > HIGH_VALUE_CONTRACT_USD:
        roles.append(ROLE_CEO)
        reasons.append(f"El contrato supera {HIGH_VALUE_CONTRACT_USD:,.0f} USD anuales "
                       f"({annual_value_usd:,.2f} USD): también aprueba la Dra. Sandra Okonkwo.")
    return roles, reasons


def annual_value_usd(terms_result) -> Tuple[Optional[Decimal], List[str]]:
    fact = terms_result.fact("term:annual_value") if terms_result else None
    value = ((fact.value or {}).get("value") or {}) if fact else {}
    if not value.get("amount"):
        return None, []
    amount = calc.convert(Decimal(value["amount"]), value["currency"], "USD")
    return amount, [r.label() for r in fact.refs]


# --- Redacción --------------------------------------------------------------------

DRAFT_PROMPT = """You draft a short, professional email from HealthCore Clinical Operations to a supplier. \
Write in English. Use ONLY the facts given in <facts>; do not add any number, date, percentage or amount that \
is not there. Do not name any individual person: address the supplier's team. Never include bank details or \
personal contact data. Do not mention competitors by name. Reply with JSON: {"subject": "...", "body": "..."}."""

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _normalize_number(token: str) -> str:
    """"19.2600" → "19.26", "540,000" → "540000", "06" → "6"."""
    cleaned = token.replace(",", "")
    if "." in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(".")
    integer, _, decimals = cleaned.partition(".")
    integer = integer.lstrip("0") or "0"
    return f"{integer}.{decimals}" if decimals else integer


def numbers_in(text: str) -> set:
    return {_normalize_number(n) for n in _NUMBER.findall(text or "")}


def validate_draft(subject: str, body: str, facts: Dict[str, Any]) -> Optional[str]:
    """None si el borrador es aceptable; si no, el motivo (sin repetir el texto)."""
    allowed = numbers_in(json.dumps(facts, default=str)) | numbers_in(json.dumps(list(facts.keys())))
    invented = numbers_in(f"{subject}\n{body}") - allowed
    if invented:
        return "invented_figures"
    if contains_sensitive(subject) or contains_sensitive(body):
        return "sensitive_data"
    if is_patient_level_request(body):
        return "patient_data"
    return None


def template_draft(purpose: str, facts: Dict[str, Any]) -> Tuple[str, str]:
    name, contract = facts["supplier_name"], facts.get("contract_id") or "our current agreement"
    if purpose == "non_renewal_notice":
        subject = f"Notice of non-renewal - {contract}"
        body = (f"Dear {name} team,\n\nIn accordance with the renewal and notice clause of {contract} "
                f"({facts.get('notice_clause', 'renewal clause')}), HealthCore gives notice that it does not intend to "
                f"renew the agreement expiring on {facts.get('expiry_date')}.\n\nKind regards,\n"
                f"HealthCore Clinical Operations")
    elif purpose == "price_increase_objection":
        subject = f"Price increase under {contract}"
        body = (f"Dear {name} team,\n\nWe have received a price of {facts.get('latest_price')} {facts.get('currency')} "
                f"per {facts.get('unit')}, an increase of {facts.get('change_pct')}% over the previous month "
                f"({facts.get('previous_price')} {facts.get('currency')}). The price review clause of {contract} "
                f"({facts.get('review_clause')}) limits increases to {facts.get('cap_pct')}%. We therefore ask you to "
                f"keep invoicing within that limit.\n\nKind regards,\nHealthCore Clinical Operations")
    else:
        subject = f"Request to review pricing - {contract}"
        body = (f"Dear {name} team,\n\nWe would like to review the current price of {facts.get('latest_price')} "
                f"{facts.get('currency')} per {facts.get('unit')} under {contract}. We have received competitive "
                f"quotes for the same service and would welcome a proposal before {facts.get('notice_deadline') or 'the next review'}."
                f"\n\nKind regards,\nHealthCore Clinical Operations")
    return subject, body


def model_draft(purpose: str, facts: Dict[str, Any], client: Any = None) -> Tuple[str, str]:
    from data.pipelines import rag
    from data.process.rag import get_llm_client

    llm = client or get_llm_client()
    if hasattr(llm, "with_options"):
        llm = llm.with_options(timeout=DRAFT_TIMEOUT_S, max_retries=0)
    completion = llm.chat.completions.create(
        model=rag.get_generation_model(),
        messages=[{"role": "system", "content": DRAFT_PROMPT},
                  {"role": "user", "content": f"Purpose: {purpose}\n<facts>{json.dumps(facts, default=str)}</facts>"}],
        temperature=0.2,
    )
    content = completion.choices[0].message.content or ""
    start, end = content.find("{"), content.rfind("}")
    data = json.loads(content[start:end + 1])
    return str(data["subject"]).strip(), str(data["body"]).strip()


def _gather_facts(supervisor: Supervisor, session: Session, supplier, purpose: str) -> Tuple[Dict[str, Any], List[str], Any]:
    sid = supplier.supplier_id
    facts: Dict[str, Any] = {"supplier_name": supplier.name, "currency": supplier.currency}
    refs: List[str] = []
    terms = supervisor.consult(ContractsRequest(intent="contract_terms", supplier_id=sid))
    contract = repository.contract_for_supplier(session, sid)
    if contract:
        facts["contract_id"] = contract.contract_id
    if purpose in ("price_increase_objection", "renegotiation_request"):
        impact = supervisor.consult(PricingRequest(intent="increase_impact", supplier_id=sid))
        if impact and impact.status == "ok":
            for key in ("previous_price", "latest_price", "change_pct"):
                facts[key] = impact.fact(key).value
            facts["unit"] = impact.fact("latest_price").unit.split("/")[-1]
            refs += [r.label() for r in impact.refs()]
            if contract:
                permission = supervisor.consult(ContractsRequest(intent="increase_permission", supplier_id=sid,
                                                                 increase_pct=facts["change_pct"]))
                verdict = permission.fact("permission") if permission else None
                if verdict and verdict.value.get("cap_pct") is not None:
                    facts["cap_pct"] = verdict.value["cap_pct"]
                    facts["review_clause"] = verdict.refs[0].locator if verdict.refs else None
                    refs += [r.label() for r in permission.refs()]
    if purpose in ("non_renewal_notice", "renegotiation_request") and contract:
        deadlines = supervisor.consult(ContractsRequest(intent="notice_deadline", supplier_id=sid))
        if deadlines and deadlines.status == "ok":
            facts["expiry_date"] = deadlines.fact("expiry_date").value
            if deadlines.fact("notice_deadline"):
                facts["notice_deadline"] = deadlines.fact("notice_deadline").value
                facts["notice_clause"] = deadlines.fact("notice_days").refs[0].locator
            refs += [r.label() for r in deadlines.refs()]
    return facts, refs, terms


def draft_communication(session: Session, supplier_id: str, purpose: str, *, requester_id: str,
                        today: Optional[date] = None, use_model: bool = True, client: Any = None,
                        log: Optional[ActionLog] = None) -> ScCommunicationDraft:
    if purpose not in PURPOSES:
        raise ValueError("purpose")
    log = log or ActionLog()
    log.record(human_actor(requester_id), "request_draft", "received", supplier_id=supplier_id, purpose=purpose)
    supervisor = Supervisor(session, log, today=today)
    try:
        supplier = repository.get_supplier(session, supplier_id)
        if supplier is None:
            raise LookupError(supplier_id)
        allowed, message, _ = data_protection_gate(session, supervisor, supplier_id)
        if not allowed:
            raise CommunicationBlocked(message.replace("Brief detenido", "Comunicación detenida"))
        facts, refs, terms = _gather_facts(supervisor, session, supplier, purpose)
        if purpose == "non_renewal_notice" and "expiry_date" not in facts:
            raise CommunicationBlocked(f"No hay contrato con {supplier.name}: no existe una renovación que cancelar.")
        if purpose == "price_increase_objection" and "cap_pct" not in facts:
            raise CommunicationBlocked(
                f"No hay cláusula de revisión de precio con {supplier.name} que citar: no se redacta una objeción "
                "sin base contractual.")
        value_usd, value_refs = annual_value_usd(terms)
        roles, reasons = required_approvals(supplier.processes_patient_data, value_usd)

        source, subject, body = "template", *template_draft(purpose, facts)
        if use_model:
            try:
                candidate = model_draft(purpose, facts, client=client)
                problem = validate_draft(*candidate, facts)
                if problem is None:
                    source, (subject, body) = "model", candidate
                else:
                    log.record(AGENT_SUPERVISOR, "draft_validation", "rejected", reason=problem)
            except Exception as exc:  # proveedor caído, JSON roto: plantilla
                log.record(AGENT_SUPERVISOR, "draft_model", "fallback", error_type=type(exc).__name__)
        # Última barrera: lo que se guarda siempre pasa por el enmascarado.
        subject, body = redact(subject), redact(body)

        draft = ScCommunicationDraft(
            supplier_id=supplier_id, contract_id=facts.get("contract_id"), purpose=purpose, subject=subject, body=body,
            drafted_by=AGENT_SUPERVISOR, draft_source=source, required_roles=roles, approval_reasons=reasons,
            refs=refs + value_refs, requested_by=human_actor(requester_id),
        )
        supervisor.runtime.call("draft_supplier_communication", repository.add_draft, session, draft,
                                audit={"supplier_id": supplier_id, "purpose": purpose, "source": source,
                                       "body_sha256": digest(body), "required_roles": roles})
        return draft
    finally:
        log.flush(session)


# --- Decisiones humanas ------------------------------------------------------------


def draft_view(session: Session, draft: ScCommunicationDraft) -> Dict[str, Any]:
    approvals = repository.approvals_for(session, draft.draft_id)
    decided = {a.role: a for a in approvals}
    return {
        "draft_id": str(draft.draft_id), "supplier_id": draft.supplier_id, "contract_id": draft.contract_id,
        "purpose": draft.purpose, "subject": draft.subject, "body": draft.body, "status": draft.status,
        "draft_source": draft.draft_source, "drafted_by": draft.drafted_by, "refs": draft.refs,
        "approval_reasons": draft.approval_reasons,
        "approvals": [{"role": role, "approver": PEOPLE[role],
                       "decision": decided[role].decision if role in decided else "pending",
                       "comment": decided[role].comment if role in decided else None}
                      for role in draft.required_roles],
        "created_at": draft.created_at, "sent_at": draft.sent_at,
    }


def decide(session: Session, draft_id: uuid.UUID, *, user_id: str, org_role: Optional[str], decision: str,
           comment: Optional[str] = None) -> ScCommunicationDraft:
    draft = repository.get_draft(session, draft_id)
    if draft is None:
        raise DecisionError(404, "Borrador no encontrado.")
    if decision not in ("approved", "rejected"):
        raise DecisionError(400, "La decisión debe ser approved o rejected.")
    if org_role not in draft.required_roles:
        raise DecisionError(403, "Tu rol no figura entre los aprobadores de este borrador.")
    if draft.status != DRAFT_PENDING:
        raise DecisionError(409, "El borrador ya no está pendiente de aprobación.")
    if any(a.role == org_role for a in repository.approvals_for(session, draft_id)):
        raise DecisionError(409, "Ese rol ya ha decidido sobre este borrador.")
    session.add(ScApproval(draft_id=draft_id, role=org_role, decision=decision, decided_by=human_actor(user_id),
                           comment=redact(comment)[:500] if comment else None))
    session.flush()
    approvals = repository.approvals_for(session, draft_id)
    if decision == "rejected":
        draft.status = DRAFT_REJECTED
    elif {a.role for a in approvals if a.decision == "approved"} >= set(draft.required_roles):
        draft.status = DRAFT_APPROVED
    session.add(draft)
    session.commit()
    log = ActionLog()
    log.record(human_actor(user_id), "approval_decision", decision, draft_id=str(draft_id), role=org_role,
               draft_status=draft.status)
    log.flush(session)
    session.refresh(draft)
    return draft


def send(session: Session, draft_id: uuid.UUID, *, user_id: str, org_role: Optional[str]) -> ScCommunicationDraft:
    draft = repository.get_draft(session, draft_id)
    if draft is None:
        raise DecisionError(404, "Borrador no encontrado.")
    if org_role != ROLE_CLINICAL_OPS:
        raise DecisionError(403, "Solo Operaciones Clínicas marca una comunicación como enviada.")
    approved = {a.role for a in repository.approvals_for(session, draft_id) if a.decision == "approved"}
    missing = [r for r in draft.required_roles if r not in approved]
    log = ActionLog()
    if draft.status != DRAFT_APPROVED or missing:
        log.record(human_actor(user_id), "send_communication", "blocked_missing_approval", draft_id=str(draft_id),
                   missing_roles=missing)
        log.flush(session)
        raise DecisionError(409, "Faltan aprobaciones: " + ", ".join(PEOPLE[r] for r in missing) if missing
                            else "El borrador no está aprobado.")
    if contains_sensitive(draft.subject) or contains_sensitive(draft.body):
        raise DecisionError(409, "El borrador contiene datos sensibles: no se envía.")
    draft.status, draft.sent_at = DRAFT_SENT, utcnow()
    session.add(draft)
    session.commit()
    log.record(human_actor(user_id), "send_communication", "sent", draft_id=str(draft_id))
    log.flush(session)
    session.refresh(draft)
    return draft
