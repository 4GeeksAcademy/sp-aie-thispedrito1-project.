"""Solicitudes de acceso a sistemas: el único punto por el que se emiten.

BRIEFING, "Restricción específica": cualquier intento de solicitar acceso a
un sistema con información de pacientes antes de que la etapa 4 esté
aprobada debe rechazarse, quedar registrado como evento de seguridad y
notificarse a Claire Whitfield.

Lo usan la API (la persona pide un acceso) y `runner.sync` (el agente emite
los accesos del departamento en la etapa 7). El agente no tiene ningún atajo:
pasa por la misma comprobación que una persona.

La fuente de "etapa 4 aprobada" es `hire.compliance_approved_at`, que solo
escribe `runner.sync` cuando el grafo registra la aprobación de alguien con
rol de Cumplimiento.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from services.audit import catalog as audit_catalog
from services.audit.writer import Actor, human_actor, process_actor, record as audit_record
from services.onboarding import repository
from services.onboarding.catalog import (
    ACTOR_AGENT,
    ACTOR_HUMAN,
    COMPLIANCE_STAGE,
    RECIPIENT_COMPLIANCE,
    RECIPIENT_HIRE,
    SYSTEMS,
)
from services.onboarding.models import ACCESS_BLOCKED, ACCESS_REQUESTED, OnboardingAccessRequest, OnboardingHire


class UnknownSystemError(ValueError):
    pass


class AccessBlockedError(PermissionError):
    """La solicitud se registró como bloqueada (y como evento de seguridad)."""

    def __init__(self, request: OnboardingAccessRequest) -> None:
        super().__init__("La etapa de Cumplimiento debe estar aprobada antes de pedir acceso a un sistema con información de pacientes.")
        self.request = request


def compliance_approved(hire: OnboardingHire) -> bool:
    return hire.compliance_approved_at is not None


def _audit_actor(actor_type: str, actor_user_id: Optional[str]) -> Actor:
    """El agente de incorporaciones es un PROCESO; una persona del equipo de
    Personas es humana. Sin petición HTTP aquí: el origen de la persona es el
    servicio, no su IP."""
    if actor_type == ACTOR_HUMAN and actor_user_id:
        return human_actor(actor_user_id, "service:onboarding")
    return process_actor(audit_catalog.PROCESS_ONBOARDING_AGENT, on_behalf_of=actor_user_id)


def issue_access_request(
    session: Session,
    hire: OnboardingHire,
    system_key: str,
    *,
    requested_by_type: str,
    actor_user_id: Optional[str] = None,
) -> OnboardingAccessRequest:
    """Emite (o reutiliza) la solicitud. Lanza `AccessBlockedError` si el
    sistema tiene datos de pacientes y la etapa 4 no está aprobada; antes de
    lanzar, deja guardados la fila bloqueada, el evento de seguridad y los
    avisos (commit incluido: el rechazo debe quedar aunque la petición falle)."""
    system = SYSTEMS.get(system_key)
    if system is None:
        raise UnknownSystemError(system_key)

    existing = session.exec(
        select(OnboardingAccessRequest).where(
            OnboardingAccessRequest.hire_id == hire.hire_id,
            OnboardingAccessRequest.system_key == system_key,
            OnboardingAccessRequest.status == ACCESS_REQUESTED,
        )
    ).first()
    if existing is not None:
        return existing

    actor_type = ACTOR_HUMAN if requested_by_type == ACTOR_HUMAN else ACTOR_AGENT
    if system.contains_patient_data and not compliance_approved(hire):
        blocked_count = len(
            session.exec(
                select(OnboardingAccessRequest.id).where(
                    OnboardingAccessRequest.hire_id == hire.hire_id,
                    OnboardingAccessRequest.status == ACCESS_BLOCKED,
                )
            ).all()
        )
        request = OnboardingAccessRequest(
            hire_id=hire.hire_id,
            system_key=system_key,
            contains_patient_data=True,
            status=ACCESS_BLOCKED,
            requested_by_type=actor_type,
        )
        session.add(request)
        key = f"{hire.hire_id}:access:{system_key}:blocked:{blocked_count + 1}"
        repository.record_audit(
            session,
            event_key=key,
            hire_id=hire.hire_id,
            stage=hire.current_stage,
            action="patient_data_access_blocked",
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            outcome=f"stage_{COMPLIANCE_STAGE}_not_approved",
            security_event=True,
        )
        repository.record_notification(
            session, hire, key=key, stage=hire.current_stage, kind="security_event",
            recipient_role=RECIPIENT_COMPLIANCE, params={"system": system_key},
        )
        repository.record_notification(
            session, hire, key=key, stage=hire.current_stage, kind="access_blocked",
            recipient_role=RECIPIENT_HIRE, params={"system": system_key},
        )
        session.flush()
        audit_record(
            session,
            actor=_audit_actor(actor_type, actor_user_id),
            action="access_request_blocked",
            resource_id=str(request.id),
            department_id=audit_catalog.department_id("people_workforce"),
            outcome=audit_catalog.OUTCOME_BLOCKED,
            details={"system_key": system_key, "hire_ref": str(hire.hire_id)},
        )
        session.commit()
        session.refresh(request)
        raise AccessBlockedError(request)

    request = OnboardingAccessRequest(
        hire_id=hire.hire_id,
        system_key=system_key,
        contains_patient_data=system.contains_patient_data,
        status=ACCESS_REQUESTED,
        requested_by_type=actor_type,
    )
    session.add(request)
    repository.record_audit(
        session,
        event_key=f"{hire.hire_id}:access:{system_key}:requested",
        hire_id=hire.hire_id,
        stage=hire.current_stage,
        action="access_requested",
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        outcome=system_key,
    )
    session.flush()
    audit_record(
        session,
        actor=_audit_actor(actor_type, actor_user_id),
        action="access_request_issued",
        resource_id=str(request.id),
        department_id=audit_catalog.department_id("people_workforce"),
        details={"system_key": system_key, "hire_ref": str(hire.hire_id)},
    )
    return request
