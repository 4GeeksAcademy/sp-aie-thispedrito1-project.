"""Alertas (BRIEFING, "Umbrales y alertas").

| Tipo                         | Cuándo                               | A quién          |
|------------------------------|--------------------------------------|------------------|
| price_increase               | > 4 % frente al mes anterior         | Dr. Marcus Reid  |
| expiry_90 / expiry_30        | 90 y 30 días antes del vencimiento   | Dr. Marcus Reid  |
| notice_deadline              | 15 días antes de la fecha de preaviso| Dr. Marcus Reid  |
| data_protection_missing      | inmediata, en cuanto se detecta      | Claire Whitfield |

Clave determinista por tipo + proveedor + fecha (o mes): un barrido repetido,
o una pregunta que vuelve a detectar lo mismo, no duplica avisos. Las alertas
las emite el supervisor (`raise_alert`), y su resumen se compone con los
hallazgos de los especialistas, con sus referencias.

Una alerta de subida lleva ya la evaluación automática: impacto anual y si el
contrato la permite (README, "Ante una subida detectada...").
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from sqlmodel import Session

from services.supplier_contracts import repository
from services.supplier_contracts.catalog import AGENT_SUPERVISOR, ROLE_CLINICAL_OPS, ROLE_COMPLIANCE
from services.supplier_contracts.models import ScAlert
from services.supplier_contracts.runtime import ActionLog, ContractsRequest, PricingRequest, SpecialistResult

RECIPIENTS = {
    "price_increase": ROLE_CLINICAL_OPS,
    "expiry_90": ROLE_CLINICAL_OPS,
    "expiry_30": ROLE_CLINICAL_OPS,
    "notice_deadline": ROLE_CLINICAL_OPS,
    "data_protection_missing": ROLE_COMPLIANCE,
}

TITLES = {
    "price_increase": "Subida de precio por encima del umbral",
    "expiry_90": "Contrato vence en 90 días o menos",
    "expiry_30": "Contrato vence en 30 días o menos",
    "notice_deadline": "Fecha límite de preaviso en 15 días o menos",
    "data_protection_missing": "Proveedor con datos de pacientes sin acuerdo de protección de datos",
}


def alert_key(alert: Dict[str, Any]) -> str:
    anchor = alert.get("due_date") or alert.get("month") or "open"
    return f"{alert['kind']}:{alert['supplier_id']}:{anchor}"


def _for_supplier(results: List[SpecialistResult], supplier_id: str, kind: str) -> List[SpecialistResult]:
    wanted = {
        "price_increase": {"detect_increases", "increase_impact", "increase_permission", "data_protection_status"},
        "data_protection_missing": {"data_protection_status"},
    }.get(kind, {"notice_deadline"})
    return [r for r in results if r.intent in wanted and (r.supplier_id in (supplier_id, None))]


def _details(kind: str, related: List[SpecialistResult], supplier_id: str) -> Dict[str, Any]:
    details: Dict[str, Any] = {}
    for result in related:
        for key in ("change_pct", "annual_impact", "annual_impact_usd", "permission", "notice_deadline",
                    "days_to_notice_deadline", "expiry_date", "days_to_expiry", "data_protection",
                    f"data_protection:{supplier_id}"):
            fact = result.fact(key)
            if fact is not None:
                details[key.split(":")[0]] = fact.value
    return details


def persist_alerts(session: Session, supervisor, log: ActionLog,
                   results: Optional[List[SpecialistResult]] = None) -> List[ScAlert]:
    """Convierte las alertas de los resultados en filas `sc_alerts` (idempotente)."""
    results = results if results is not None else supervisor.results
    created: List[ScAlert] = []
    seen = set()
    for result in results:
        for alert in result.alerts:
            key = alert_key(alert)
            if key in seen or alert["kind"] not in RECIPIENTS:
                continue
            seen.add(key)
            related = _for_supplier(results, alert["supplier_id"], alert["kind"])
            findings, refs = [], []
            for rel in related:
                for finding in rel.findings:
                    own = rel.supplier_id == alert["supplier_id"]
                    named = rel.supplier_id is None and _mentions(finding, alert["supplier_id"])
                    if (own or named) and finding not in findings:
                        findings.append(finding)
                refs += [r.label() for r in rel.refs() if r.label() not in refs]
            contract = repository.contract_for_supplier(session, alert["supplier_id"])
            row = ScAlert(
                alert_key=key,
                kind=alert["kind"],
                supplier_id=alert["supplier_id"],
                contract_id=contract.contract_id if contract else None,
                recipient_role=RECIPIENTS[alert["kind"]],
                due_date=date.fromisoformat(alert["due_date"]) if alert.get("due_date") else None,
                summary=f"{TITLES[alert['kind']]}. " + " ".join(findings),
                details=_details(alert["kind"], related, alert["supplier_id"]),
                refs=refs,
                raised_by=AGENT_SUPERVISOR,
            )
            is_new = supervisor.runtime.call("raise_alert", repository.add_alert_if_new, session, row,
                                             audit={"kind": alert["kind"], "supplier_id": alert["supplier_id"]})
            if is_new:
                created.append(row)
    return created


def _mentions(finding: str, supplier_id: str) -> bool:
    from services.supplier_contracts.catalog import SUPPLIERS_BY_ID

    spec = SUPPLIERS_BY_ID.get(supplier_id)
    return bool(spec and spec.name in finding)


def persist_result_alerts(session: Session, supervisor, log: ActionLog) -> List[ScAlert]:
    return persist_alerts(session, supervisor, log)


def run_alert_sweep(session: Session, today: Optional[date] = None) -> List[ScAlert]:
    """Barrido completo (lo que correría cada día). Cada comprobación es una
    petición del supervisor con su propio límite de consultas y request_id."""
    from services.supplier_contracts.supervisor import Supervisor

    today = today or date.today()
    created: List[ScAlert] = []

    log = ActionLog()
    scan = Supervisor(session, log, today=today)
    increases = scan.consult(PricingRequest(intent="detect_increases", supplier_id=None))
    log.flush(session)

    for alert in increases.alerts if increases else []:
        log = ActionLog()
        sup = Supervisor(session, log, today=today)
        sup.consult(PricingRequest(intent="detect_increases", supplier_id=alert["supplier_id"]))
        sup.assess_increase(alert["supplier_id"])
        created += persist_alerts(session, sup, log)
        log.flush(session)

    for supplier in repository.list_suppliers(session):
        if repository.contract_for_supplier(session, supplier.supplier_id) is None:
            continue
        log = ActionLog()
        sup = Supervisor(session, log, today=today)
        sup.consult(ContractsRequest(intent="notice_deadline", supplier_id=supplier.supplier_id))
        created += persist_alerts(session, sup, log)
        log.flush(session)

    log = ActionLog()
    sup = Supervisor(session, log, today=today)
    sup.consult(ContractsRequest(intent="data_protection_status", supplier_id=None))
    created += persist_alerts(session, sup, log)
    log.flush(session)
    return created
