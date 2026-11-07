"""Acceso a las tablas del onboarding. Único módulo que las escribe.

Idempotencia: auditoría y avisos llevan una clave determinista (`event_key`,
`dedupe_key`) y aquí se ignora una clave ya guardada. Así `runner.sync` puede
volver a recorrer todos los efectos del grafo sin duplicar nada, y el barrido
de plazos puede ejecutarse cada hora.

Auditoría: además de la fila, una línea JSON en el logger
`healthcore.onboarding.audit` con una lista cerrada de campos. La persona
humana aparece como seudónimo (SHA-256 truncado, el mismo de audit_log.py);
el agente, con su id `onboarding-orchestrator` y `actor_type=agent`.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlmodel import Session, select

from audit_log import actor_pseudonym
from services.onboarding import notifications
from services.onboarding.catalog import (
    ACTOR_AGENT,
    ACTOR_HUMAN,
    AGENT_ACTOR_ID,
    ORG_ROLE_COMPLIANCE,
    ORG_ROLE_PEOPLE_TEAM,
    ORG_ROLE_VP_PEOPLE,
    RECIPIENT_COMPLIANCE,
    RECIPIENT_HIRE,
    RECIPIENT_MANAGER,
    RECIPIENT_PEOPLE_TEAM,
    RECIPIENT_VP_PEOPLE,
)
from services.onboarding.models import (
    HIRE_ACTIVE,
    OnboardingAccessRequest,
    OnboardingAuditEvent,
    OnboardingHire,
    OnboardingNotification,
    OnboardingQuestion,
    OnboardingStaff,
)

audit_logger = logging.getLogger("healthcore.onboarding.audit")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Directorio --------------------------------------------------------------


def get_staff(session: Session, user_id: Optional[str]) -> Optional[OnboardingStaff]:
    if not user_id:
        return None
    return session.get(OnboardingStaff, user_id)


def staff_with_role(session: Session, org_role: str) -> List[OnboardingStaff]:
    return list(session.exec(select(OnboardingStaff).where(OnboardingStaff.org_role == org_role)))


def recipients_for(session: Session, hire: OnboardingHire, recipient_role: str) -> List[str]:
    """Ids de usuario a los que va un aviso dirigido a un rol."""
    if recipient_role == RECIPIENT_HIRE:
        return [hire.hire_user_id]
    if recipient_role == RECIPIENT_MANAGER:
        return [hire.manager_user_id]
    if recipient_role == RECIPIENT_COMPLIANCE:
        return [s.user_id for s in staff_with_role(session, ORG_ROLE_COMPLIANCE)]
    if recipient_role == RECIPIENT_VP_PEOPLE:
        return [s.user_id for s in staff_with_role(session, ORG_ROLE_VP_PEOPLE)]
    if recipient_role == RECIPIENT_PEOPLE_TEAM:
        team = [s.user_id for s in staff_with_role(session, ORG_ROLE_PEOPLE_TEAM)]
        return team or [s.user_id for s in staff_with_role(session, ORG_ROLE_VP_PEOPLE)]
    raise ValueError(f"Destinatario desconocido: {recipient_role}")


# --- Incorporaciones ---------------------------------------------------------


def get_hire(session: Session, hire_id: uuid.UUID) -> Optional[OnboardingHire]:
    return session.get(OnboardingHire, hire_id)


def get_hire_by_user(session: Session, user_id: str) -> Optional[OnboardingHire]:
    return session.exec(select(OnboardingHire).where(OnboardingHire.hire_user_id == user_id)).first()


def list_hires(session: Session, *, manager_user_id: Optional[str] = None) -> List[OnboardingHire]:
    statement = select(OnboardingHire).order_by(OnboardingHire.start_date, OnboardingHire.created_at)
    if manager_user_id is not None:
        statement = statement.where(OnboardingHire.manager_user_id == manager_user_id)
    return list(session.exec(statement))


def active_hires(session: Session) -> List[OnboardingHire]:
    return list(session.exec(select(OnboardingHire).where(OnboardingHire.status == HIRE_ACTIVE)))


# --- Auditoría ---------------------------------------------------------------


def _existing(session: Session, column: Any, keys: Iterable[str]) -> set:
    keys = list(keys)
    if not keys:
        return set()
    return set(session.exec(select(column).where(column.in_(keys))))


def record_audit(
    session: Session,
    *,
    event_key: str,
    hire_id: uuid.UUID,
    stage: Optional[int],
    action: str,
    actor_type: str,
    actor_user_id: Optional[str] = None,
    outcome: Optional[str] = None,
    security_event: bool = False,
) -> bool:
    """Guarda y loguea una acción si su clave es nueva. Devuelve si era nueva."""
    if _existing(session, OnboardingAuditEvent.event_key, [event_key]):
        return False
    actor_ref = AGENT_ACTOR_ID if actor_type == ACTOR_AGENT else (actor_pseudonym(actor_user_id) or "unknown")
    event = OnboardingAuditEvent(
        event_key=event_key,
        hire_id=hire_id,
        stage=stage,
        action=action,
        actor_type=actor_type,
        actor_ref=actor_ref,
        outcome=outcome,
        security_event=security_event,
    )
    session.add(event)
    audit_logger.info(
        json.dumps(
            {
                "event": f"onboarding.{action}",
                "at": utcnow().isoformat(),
                "actor_type": actor_type,
                "actor": actor_ref,
                "automated": actor_type == ACTOR_AGENT,
                "onboarding_id": str(hire_id),
                "stage": stage,
                "outcome": outcome,
                "security_event": security_event,
            }
        )
    )
    return True


def list_audit(session: Session, hire_id: uuid.UUID) -> List[OnboardingAuditEvent]:
    statement = select(OnboardingAuditEvent).where(OnboardingAuditEvent.hire_id == hire_id).order_by(OnboardingAuditEvent.id)
    return list(session.exec(statement))


# --- Avisos ------------------------------------------------------------------


def record_notification(
    session: Session,
    hire: OnboardingHire,
    *,
    key: str,
    stage: Optional[int],
    kind: str,
    recipient_role: str,
    params: Dict[str, Any],
) -> int:
    """Un aviso por destinatario. Devuelve cuántos son nuevos."""
    recipients = recipients_for(session, hire, recipient_role)
    keys = {user_id: f"{key}:{user_id}" for user_id in recipients}
    existing = _existing(session, OnboardingNotification.dedupe_key, keys.values())
    subject, body = notifications.render(
        kind, recipient_role, hire_language=hire.language, hire_name=hire.full_name, params=params
    )
    created = 0
    for user_id, dedupe_key in keys.items():
        if dedupe_key in existing:
            continue
        session.add(
            OnboardingNotification(
                dedupe_key=dedupe_key,
                hire_id=hire.hire_id,
                stage=stage,
                kind=kind,
                recipient_role=recipient_role,
                recipient_user_id=user_id,
                subject=subject,
                body=body,
                sender=AGENT_ACTOR_ID,
            )
        )
        created += 1
    return created


def notifications_for_user(session: Session, user_id: str, limit: int = 50) -> List[OnboardingNotification]:
    statement = (
        select(OnboardingNotification)
        .where(OnboardingNotification.recipient_user_id == user_id)
        .order_by(OnboardingNotification.id.desc())  # type: ignore[union-attr]
        .limit(limit)
    )
    return list(session.exec(statement))


def notifications_for_hire(session: Session, hire_id: uuid.UUID) -> List[OnboardingNotification]:
    statement = select(OnboardingNotification).where(OnboardingNotification.hire_id == hire_id).order_by(OnboardingNotification.id)
    return list(session.exec(statement))


# --- Dudas y accesos -----------------------------------------------------------


def questions_for_hire(session: Session, hire_id: uuid.UUID) -> List[OnboardingQuestion]:
    statement = select(OnboardingQuestion).where(OnboardingQuestion.hire_id == hire_id).order_by(OnboardingQuestion.created_at)
    return list(session.exec(statement))


def access_requests_for_hire(session: Session, hire_id: uuid.UUID) -> List[OnboardingAccessRequest]:
    statement = select(OnboardingAccessRequest).where(OnboardingAccessRequest.hire_id == hire_id).order_by(OnboardingAccessRequest.id)
    return list(session.exec(statement))


def existing_keys(session: Session, keys: Sequence[str]) -> set:
    """Claves de auditoría ya guardadas (para que sync compruebe en bloque)."""
    return _existing(session, OnboardingAuditEvent.event_key, keys)
