"""Acceso a datos del cierre financiero (patrón repositorio sobre SQLModel)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Iterable, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select

from services.financial_close.ingestion import BudgetRecord, LedgerRecord, OperationRecord, PeriodDataset
from services.financial_close.models import (
    FcAgentAction,
    FcBudgetLine,
    FcDistribution,
    FcIngestion,
    FcLedgerEntry,
    FcNotification,
    FcOperationMetric,
    FcReport,
    FcReviewDecision,
    FcStaff,
)

# --- Fuentes ---------------------------------------------------------------------


def replace_period(session: Session, dataset: PeriodDataset) -> None:
    """Sustituye las fuentes del periodo en una sola transacción (idempotente)."""
    period = dataset.month
    for model in (FcLedgerEntry, FcBudgetLine, FcOperationMetric):
        session.exec(delete(model).where(model.period == period))
    existing = session.get(FcIngestion, period)
    if existing:
        session.delete(existing)
        session.flush()
    for r in dataset.ledger:
        session.add(FcLedgerEntry(
            period=period, entry_id=r.entry_id, entry_date=r.entry_date, country=r.country, payer=r.payer,
            category=r.category, currency=r.currency, amount=r.amount, source_file=r.source_file,
            source_row=r.source_row, source_ref=r.source_ref, description=r.description,
            description_sha256=r.description_sha256, status=r.status, issues=list(r.issues) or None,
            sensitive_categories=list(r.sensitive_categories) or None, quantity=r.quantity, unit_price=r.unit_price))
    for b in dataset.budget:
        session.add(FcBudgetLine(period=period, country=b.country, payer=b.payer, category=b.category,
                                 currency=b.currency, amount=b.amount, source_row=b.source_row, status=b.status,
                                 issues=list(b.issues) or None))
    for o in dataset.operations:
        session.add(FcOperationMetric(period=period, country=o.country, payer=o.payer, metric=o.metric, value=o.value,
                                      source_row=o.source_row, status=o.status, issues=list(o.issues) or None))
    session.add(FcIngestion(period=period, fingerprint=dataset.fingerprint,
                            missing_files=list(dataset.missing_files) or None))
    session.commit()


def load_period(session: Session, period: date) -> PeriodDataset:
    """Reconstruye el periodo desde la base de datos con el formato de la ingesta.

    Sin ingesta registrada, las tres fuentes se declaran ausentes (el informe
    lo dice en vez de parecer completo)."""
    ingestion = session.get(FcIngestion, period)
    if ingestion is None:
        return PeriodDataset(month=period, missing_files=["ledger.csv", "budget.csv", "operations.csv"],
                             fingerprint="no-ingestion")
    ledger = [
        LedgerRecord(
            entry_id=e.entry_id, entry_date=e.entry_date, country=e.country, payer=e.payer, category=e.category,
            currency=e.currency, amount=e.amount, source_ref=e.source_ref, source_row=e.source_row,
            description=e.description, description_sha256=e.description_sha256, status=e.status,
            issues=tuple(e.issues or ()), sensitive_categories=tuple(e.sensitive_categories or ()),
            quantity=e.quantity, unit_price=e.unit_price, source_file=e.source_file)
        for e in session.exec(select(FcLedgerEntry).where(FcLedgerEntry.period == period)
                              .order_by(FcLedgerEntry.source_row))
    ]
    budget = [
        BudgetRecord(country=b.country, payer=b.payer, category=b.category, currency=b.currency, amount=b.amount,
                     source_row=b.source_row, status=b.status, issues=tuple(b.issues or ()))
        for b in session.exec(select(FcBudgetLine).where(FcBudgetLine.period == period)
                              .order_by(FcBudgetLine.source_row))
    ]
    operations = [
        OperationRecord(country=o.country, payer=o.payer, metric=o.metric, value=o.value, source_row=o.source_row,
                        status=o.status, issues=tuple(o.issues or ()))
        for o in session.exec(select(FcOperationMetric).where(FcOperationMetric.period == period)
                              .order_by(FcOperationMetric.source_row))
    ]
    return PeriodDataset(month=period, ledger=ledger, budget=budget, operations=operations,
                         missing_files=list(ingestion.missing_files or []), fingerprint=ingestion.fingerprint)


def ledger_entries(session: Session, period: date, entry_ids: Iterable[str]) -> List[FcLedgerEntry]:
    ids = list(entry_ids)
    if not ids:
        return []
    return list(session.exec(select(FcLedgerEntry).where(FcLedgerEntry.period == period,
                                                         FcLedgerEntry.entry_id.in_(ids))
                             .order_by(FcLedgerEntry.source_row)))


# --- Personas ---------------------------------------------------------------------


def get_staff(session: Session, user_id: Optional[str]) -> Optional[FcStaff]:
    return session.get(FcStaff, user_id) if user_id else None


def upsert_staff(session: Session, user_id: str, display_name: str, org_role: str) -> None:
    existing = session.get(FcStaff, user_id)
    if existing:
        existing.display_name, existing.org_role = display_name, org_role
    else:
        session.add(FcStaff(user_id=user_id, display_name=display_name, org_role=org_role))
    session.commit()


# --- Informes ---------------------------------------------------------------------


def get_report(session: Session, report_id: uuid.UUID) -> Optional[FcReport]:
    return session.get(FcReport, report_id)


def list_reports(session: Session, limit: int = 50) -> List[FcReport]:
    return list(session.exec(select(FcReport).order_by(FcReport.period.desc(), FcReport.version.desc()).limit(limit)))


def next_version(session: Session, period: date) -> int:
    versions = session.exec(select(FcReport.version).where(FcReport.period == period)).all()
    return (max(versions) if versions else 0) + 1


def has_scheduled_report(session: Session, period: date) -> bool:
    return session.exec(select(FcReport.id).where(FcReport.period == period,
                                                  FcReport.trigger == "scheduled")).first() is not None


def decisions_for(session: Session, report_id: uuid.UUID) -> List[FcReviewDecision]:
    return list(session.exec(select(FcReviewDecision).where(FcReviewDecision.report_id == report_id)
                             .order_by(FcReviewDecision.id)))


def distributions_for(session: Session, report_id: uuid.UUID) -> List[FcDistribution]:
    return list(session.exec(select(FcDistribution).where(FcDistribution.report_id == report_id)
                             .order_by(FcDistribution.id)))


def distributions_for_role(session: Session, role: str) -> List[FcDistribution]:
    return list(session.exec(select(FcDistribution).where(FcDistribution.recipient_role == role)
                             .order_by(FcDistribution.id.desc())))


# --- Avisos y registro -----------------------------------------------------------


def add_notification(session: Session, notification: FcNotification) -> bool:
    """Inserta un aviso si su clave no existe. True si es nuevo."""
    exists = session.exec(select(FcNotification.id).where(
        FcNotification.notification_key == notification.notification_key)).first()
    if exists is not None:
        return False
    session.add(notification)
    try:
        session.flush()
    except IntegrityError:  # carrera con otro proceso: el aviso ya existe
        session.rollback()
        return False
    return True


def notifications_for_role(session: Session, role: str) -> List[FcNotification]:
    return list(session.exec(select(FcNotification).where(FcNotification.recipient_role == role)
                             .order_by(FcNotification.id.desc())))


def add_actions(session: Session, actions: Iterable[FcAgentAction]) -> None:
    for action in actions:
        session.add(action)
    session.commit()


def list_actions(session: Session, request_id: Optional[str] = None, limit: int = 300) -> List[FcAgentAction]:
    query = select(FcAgentAction).order_by(FcAgentAction.id.desc()).limit(limit)
    if request_id:
        query = query.where(FcAgentAction.request_id == request_id)
    return list(session.exec(query))
