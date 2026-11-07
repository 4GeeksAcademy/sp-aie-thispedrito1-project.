"""Acceso a datos de proveedores y contratos (única capa que toca la sesión).

`replace_dataset` sustituye proveedores, precios y contratos en una sola
transacción (recargar no duplica). Alertas, borradores, aprobaciones,
autorizaciones de Cumplimiento y el registro de acciones NUNCA se borran:
son la historia auditable.
"""

from __future__ import annotations

import uuid
from typing import Dict, Iterable, List, Optional

from sqlmodel import Session, delete, select

from services.supplier_contracts.ingestion import Dataset
from services.supplier_contracts.models import (
    ScAgentAction,
    ScAlert,
    ScApproval,
    ScCommunicationDraft,
    ScComplianceClearance,
    ScContract,
    ScContractTerm,
    ScIngestionRun,
    ScPriceRecord,
    ScStaff,
    ScSupplier,
)


def replace_dataset(session: Session, dataset: Dataset) -> ScIngestionRun:
    for table in (ScContractTerm, ScContract, ScPriceRecord, ScSupplier):
        session.exec(delete(table))
    for row in dataset.suppliers:
        session.add(ScSupplier(**{k: row[k] for k in (
            "supplier_id", "name", "country", "category", "currency", "processes_patient_data", "source_file", "source_row")}))
    for row in dataset.prices:
        session.add(ScPriceRecord(**row))
    for contract in dataset.contracts:
        session.add(ScContract(contract_id=contract.contract_id, supplier_id=contract.supplier_id,
                               source_file=contract.source_file, sha256=contract.sha256, pages=contract.pages))
        for term in contract.terms.values():
            session.add(ScContractTerm(contract_id=contract.contract_id, **term.__dict__))
    run = ScIngestionRun(
        load_date=dataset.load_date,
        files=dataset.files,
        rejected_rows=[r.__dict__ for r in dataset.rejected],
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


# --- Lecturas ------------------------------------------------------------------


def latest_run(session: Session) -> Optional[ScIngestionRun]:
    return session.exec(select(ScIngestionRun).order_by(ScIngestionRun.created_at.desc())).first()


def list_suppliers(session: Session) -> List[ScSupplier]:
    return list(session.exec(select(ScSupplier).order_by(ScSupplier.supplier_id)))


def get_supplier(session: Session, supplier_id: str) -> Optional[ScSupplier]:
    return session.get(ScSupplier, supplier_id)


def price_history(session: Session, supplier_id: str) -> List[ScPriceRecord]:
    return list(session.exec(
        select(ScPriceRecord).where(ScPriceRecord.supplier_id == supplier_id).order_by(ScPriceRecord.month)))


def all_prices(session: Session) -> List[ScPriceRecord]:
    return list(session.exec(select(ScPriceRecord).order_by(ScPriceRecord.supplier_id, ScPriceRecord.month)))


def contract_for_supplier(session: Session, supplier_id: str) -> Optional[ScContract]:
    return session.exec(select(ScContract).where(ScContract.supplier_id == supplier_id)).first()


def contract_terms(session: Session, contract_id: str) -> Dict[str, ScContractTerm]:
    rows = session.exec(select(ScContractTerm).where(ScContractTerm.contract_id == contract_id))
    return {row.term: row for row in rows}


def has_clearance(session: Session, supplier_id: str) -> bool:
    return session.exec(
        select(ScComplianceClearance).where(ScComplianceClearance.supplier_id == supplier_id)).first() is not None


def add_clearance(session: Session, supplier_id: str, cleared_by: str) -> ScComplianceClearance:
    clearance = ScComplianceClearance(supplier_id=supplier_id, cleared_by=cleared_by)
    session.add(clearance)
    session.commit()
    session.refresh(clearance)
    return clearance


# --- Personas -------------------------------------------------------------------


def get_staff(session: Session, user_id: Optional[str]) -> Optional[ScStaff]:
    return session.get(ScStaff, user_id) if user_id else None


def upsert_staff(session: Session, user_id: str, display_name: str, org_role: str) -> None:
    existing = session.get(ScStaff, user_id)
    if existing:
        existing.display_name, existing.org_role = display_name, org_role
    else:
        session.add(ScStaff(user_id=user_id, display_name=display_name, org_role=org_role))
    session.commit()


# --- Alertas --------------------------------------------------------------------


def add_alert_if_new(session: Session, alert: ScAlert) -> bool:
    """Idempotente por `alert_key`: True si la alerta es nueva."""
    if session.get(ScAlert, alert.alert_key) is not None:
        return False
    session.add(alert)
    session.commit()
    return True


def list_alerts(session: Session, recipient_role: Optional[str] = None) -> List[ScAlert]:
    query = select(ScAlert).order_by(ScAlert.created_at.desc())
    if recipient_role:
        query = query.where(ScAlert.recipient_role == recipient_role)
    return list(session.exec(query))


# --- Borradores y aprobaciones ---------------------------------------------------


def add_draft(session: Session, draft: ScCommunicationDraft) -> ScCommunicationDraft:
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


def get_draft(session: Session, draft_id: uuid.UUID) -> Optional[ScCommunicationDraft]:
    return session.get(ScCommunicationDraft, draft_id)


def list_drafts(session: Session) -> List[ScCommunicationDraft]:
    return list(session.exec(select(ScCommunicationDraft).order_by(ScCommunicationDraft.created_at.desc())))


def approvals_for(session: Session, draft_id: uuid.UUID) -> List[ScApproval]:
    return list(session.exec(select(ScApproval).where(ScApproval.draft_id == draft_id).order_by(ScApproval.id)))


# --- Registro de acciones --------------------------------------------------------


def add_actions(session: Session, actions: Iterable[ScAgentAction]) -> None:
    for action in actions:
        session.add(action)
    session.commit()


def list_actions(session: Session, request_id: Optional[str] = None, limit: int = 200) -> List[ScAgentAction]:
    query = select(ScAgentAction).order_by(ScAgentAction.id.desc()).limit(limit)
    if request_id:
        query = query.where(ScAgentAction.request_id == request_id)
    return list(session.exec(query))
