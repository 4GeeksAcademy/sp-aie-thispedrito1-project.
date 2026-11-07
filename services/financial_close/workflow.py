"""Ciclo de vida del cierre: ingesta → informe → revisión → (nueva versión | distribución).

BRIEFING, "Revisión y distribución" y "Restricción específica":
- Cada registro con datos de paciente → aviso a Cumplimiento (Claire
  Whitfield) con SOLO su `entry_id` (clave determinista: sin duplicados).
- El informe nace `pending_review` y se avisa a Tom Callahan. Nada se
  distribuye sin su aprobación.
- Solo el rol `revenue_cycle_director` decide. Rechazar exige comentarios
  (sin datos de pacientes ni sensibles); el rechazo cierra esa versión y
  `revise` genera la siguiente con esos comentarios. Las cifras se recalculan
  de los mismos datos: no cambian.
- Aprobar distribuye en la misma transacción, solo a los destinatarios del
  BRIEFING (`catalog.DISTRIBUTION_ROLES`), tras revalidar que el texto
  saliente no contiene datos sensibles.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session

from services.audit import catalog as audit_catalog
from services.audit.writer import process_actor, record as audit_record
from services.financial_close import ingestion, report, repository
from services.financial_close.agent import CloseAgent
from services.financial_close.catalog import (
    AGENT_ACTOR,
    DISTRIBUTION_ROLES,
    PATIENT_DATA_REVIEWER_ROLE,
    PEOPLE,
    REPORT_LANGUAGES,
    REVIEWER_ROLE,
)
from services.financial_close.models import (
    REPORT_APPROVED,
    REPORT_DISTRIBUTED,
    REPORT_PENDING,
    REPORT_REJECTED,
    TRIGGER_REVISION,
    FcDistribution,
    FcNotification,
    FcReport,
    FcReviewDecision,
    utcnow,
)
from services.financial_close.runtime import ActionLog, human_actor
from services.financial_close.sensitive import screen

MAX_COMMENT_LENGTH = 2000


class DecisionError(Exception):
    def __init__(self, status: int, field: str, message: str) -> None:
        super().__init__(message)
        self.status, self.field, self.message = status, field, message


# --- Ingesta ---------------------------------------------------------------------------


def ingest_period(session: Session, data_dir: Path, period: date, *, log: Optional[ActionLog] = None) -> int:
    """Ingiere las fuentes del periodo y avisa a Cumplimiento. Devuelve los avisos nuevos."""
    log = log or ActionLog()
    dataset = ingestion.read_dataset(data_dir, period)
    repository.replace_period(session, dataset)
    created = 0
    for record in dataset.ledger:
        if "patient_data" not in record.sensitive_categories:
            continue
        notification = FcNotification(
            notification_key=f"patient-record:{period.isoformat()}:{record.entry_id}",
            recipient_role=PATIENT_DATA_REVIEWER_ROLE, kind="patient_data_in_source", period=period,
            entry_id=record.entry_id,
            summary=(f"Ledger record {record.entry_id} ({period:%Y-%m}) may contain patient data. Its description "
                     f"was withheld from the financial close and from the model; please review it at the source."),
        )
        if repository.add_notification(session, notification):
            created += 1
    session.commit()
    log.record(AGENT_ACTOR, "ingest_period", "ok", period=period.isoformat(), ledger=len(dataset.ledger),
               doubtful=sum(1 for r in dataset.ledger if not r.is_ok), missing_files=dataset.missing_files or None,
               compliance_notifications=created)
    log.flush(session)
    return created


# --- Cierre ----------------------------------------------------------------------------


def _build_report(session: Session, analysis, *, period: date, trigger: str, request_id: str,
                  supersedes: Optional[uuid.UUID]) -> FcReport:
    figures = report.build_figures(analysis)
    version = repository.next_version(session, period)
    texts = {lang: report.render(analysis, figures, lang=lang, version=version, status=REPORT_PENDING)
             for lang in REPORT_LANGUAGES}
    return FcReport(
        period=period, version=version, status=REPORT_PENDING, trigger=trigger, supersedes_id=supersedes,
        data_fingerprint=analysis.dataset.fingerprint, figures_sha256=report.figures_sha256(figures),
        narrative_source=analysis.narrative.source, figures=figures, quality=analysis.quality,
        trace={"narrative_sources": analysis.narrative.sources,
               "plan": {d.comparison.key: d.plan_source for d in analysis.deviations}},
        report_en=texts["en"], report_es=texts["es"], request_id=request_id)


def _notify_reviewer(session: Session, row: FcReport) -> None:
    repository.add_notification(session, FcNotification(
        notification_key=f"review:{row.id}", recipient_role=REVIEWER_ROLE, kind="report_pending_review",
        period=row.period, report_id=row.id,
        summary=f"Financial close {row.period:%Y-%m} v{row.version} is ready for your review."))


def run_close(session: Session, engine, period: date, *, trigger: str, comments: Optional[str] = None,
              supersedes: Optional[uuid.UUID] = None, client: Any = None, use_model: bool = True,
              log: Optional[ActionLog] = None) -> FcReport:
    """Ejecuta el agente sobre el periodo (lectura de solo lectura) y guarda la versión nueva."""
    log = log or ActionLog()
    agent = CloseAgent(log, client=client, use_model=use_model)
    analysis = agent.analyze(engine, period, comments=comments)
    row = _build_report(session, analysis, period=period, trigger=trigger, request_id=log.request_id,
                        supersedes=supersedes)
    session.add(row)
    session.flush()
    _notify_reviewer(session, row)
    # Registro de Auditoría: el agente de cierre lee el ledger (que puede
    # traer un registro con datos de un paciente, ver sensitive.py) y produce
    # un informe. Actor = PROCESO, en la misma transacción que el informe.
    audit_record(
        session,
        actor=process_actor(audit_catalog.PROCESS_FINANCIAL_CLOSE_AGENT),
        action="financial_report_prepared",
        resource_id=str(row.id),
        department_id=audit_catalog.department_id("revenue_cycle"),
        details={
            "period": period.isoformat(),
            "version": row.version,
            "trigger": trigger,
            "figures_sha256": row.figures_sha256,
        },
    )
    session.commit()
    session.refresh(row)
    log.record(AGENT_ACTOR, "prepare_report", "ok", report_id=str(row.id), period=period.isoformat(),
               version=row.version, trigger=trigger, figures_sha256=row.figures_sha256,
               narrative_source=row.narrative_source, sent_to=PEOPLE[REVIEWER_ROLE])
    log.flush(session)
    return row


def revise(session: Session, engine, report_id: uuid.UUID, *, client: Any = None, use_model: bool = True,
           log: Optional[ActionLog] = None) -> FcReport:
    """Nueva versión de un informe rechazado, con los comentarios del rechazo."""
    previous = repository.get_report(session, report_id)
    if previous is None:
        raise LookupError("report_not_found")
    if previous.status != REPORT_REJECTED:
        raise DecisionError(409, "status", "Solo se revisa un informe rechazado.")
    existing = [r for r in repository.list_reports(session, limit=500) if r.supersedes_id == previous.id]
    if existing:  # idempotente: un reintento de la tarea no crea dos versiones
        return existing[0]
    rejection = [d for d in repository.decisions_for(session, report_id) if d.decision == "reject"][-1]
    return run_close(session, engine, previous.period, trigger=TRIGGER_REVISION, comments=rejection.comments,
                     supersedes=previous.id, client=client, use_model=use_model, log=log)


# --- Revisión ----------------------------------------------------------------------------


def validate_comments(comments: Any, *, required: bool) -> Optional[str]:
    if comments is None or (isinstance(comments, str) and not comments.strip()):
        if required:
            raise DecisionError(400, "comments", "Rechazar exige comentarios para la nueva versión.")
        return None
    if not isinstance(comments, str):
        raise DecisionError(400, "comments", "Los comentarios deben ser texto.")
    text = comments.strip()
    if len(text) > MAX_COMMENT_LENGTH:
        raise DecisionError(400, "comments", f"Máximo {MAX_COMMENT_LENGTH} caracteres.")
    if screen(text).is_sensitive:
        # No se repite el texto: podría contener el propio dato.
        raise DecisionError(400, "comments", "Los comentarios no pueden contener datos de pacientes, salarios "
                                             "individuales ni datos bancarios.")
    return text


def decide(session: Session, report_id: uuid.UUID, *, user_id: str, org_role: Optional[str], decision: str,
           comments: Any = None, log: Optional[ActionLog] = None) -> FcReport:
    log = log or ActionLog()
    actor = human_actor(user_id)
    if org_role != REVIEWER_ROLE:
        log.record(actor, "review_decision", "denied", report_id=str(report_id))
        log.flush(session)
        raise DecisionError(403, "role", f"Solo {PEOPLE[REVIEWER_ROLE]} (Revenue Cycle) revisa el cierre.")
    if decision not in ("approve", "reject"):
        raise DecisionError(400, "decision", "La decisión debe ser 'approve' o 'reject'.")
    row = repository.get_report(session, report_id)
    if row is None:
        raise DecisionError(404, "report_id", "Informe no encontrado.")
    if row.status != REPORT_PENDING:
        raise DecisionError(409, "status", "Este informe ya tiene una decisión.")
    text = validate_comments(comments, required=decision == "reject")

    session.add(FcReviewDecision(report_id=row.id, decision=decision, comments=text, decided_by=actor))
    row.decided_at = utcnow()
    if decision == "reject":
        row.status = REPORT_REJECTED
        session.add(row)
        session.commit()
        log.record(actor, "review_decision", "rejected", report_id=str(row.id), version=row.version)
        log.flush(session)
        return row

    for lang, body in (("en", row.report_en), ("es", row.report_es)):
        if screen(body).is_sensitive:
            session.rollback()
            log.record(AGENT_ACTOR, "distribute", "blocked", report_id=str(row.id), language=lang,
                       reason="sensitive_data")
            log.flush(session)
            raise DecisionError(409, "report", "El informe contiene datos sensibles y no se puede distribuir.")
    row.status = REPORT_APPROVED
    session.add(row)
    session.flush()
    log.record(actor, "review_decision", "approved", report_id=str(row.id), version=row.version)
    distribute(session, row, log=log)
    session.commit()
    log.flush(session)
    return row


def distribute(session: Session, row: FcReport, *, log: ActionLog) -> None:
    """Bandeja interna: una entrega por destinatario del BRIEFING. Exige aprobación."""
    if row.status != REPORT_APPROVED:
        raise DecisionError(409, "status", "Solo se distribuye un informe aprobado.")
    for role in DISTRIBUTION_ROLES:
        session.add(FcDistribution(report_id=row.id, recipient_role=role, recipient_name=PEOPLE[role]))
    row.status = REPORT_DISTRIBUTED
    row.distributed_at = utcnow()
    for lang, body in (("en", row.report_en), ("es", row.report_es)):
        setattr(row, f"report_{lang}", body.replace(_status_marker(lang, REPORT_PENDING),
                                                    _status_marker(lang, REPORT_DISTRIBUTED))
                .replace(_draft_banner(lang), ""))
    session.add(row)
    log.record(AGENT_ACTOR, "distribute", "ok", report_id=str(row.id), recipients=len(DISTRIBUTION_ROLES))


def _status_marker(lang: str, status: str) -> str:
    return f"{'status' if lang == 'en' else 'estado'}: {report.STATUS_LABELS[lang][status]}"


def _draft_banner(lang: str) -> str:
    return f"> {report._L[lang]['draft'].format(reviewer=PEOPLE[REVIEWER_ROLE])}\n\n"
