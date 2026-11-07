"""Quién puede hacer qué en el onboarding, incluido el propio agente.

El README pide que el agente opere "con una identidad propia y permisos más
acotados que los de cualquier persona del equipo". Aquí esa frase es una
matriz comprobable: `AGENT_CAPABILITIES` no contiene ninguna capacidad de
aprobación, ni de lectura de incorporaciones ajenas, ni de alta de personas,
y `tests/onboarding/test_onboarding_security.py` verifica que para cada rol
humano hay algo que la persona puede hacer y el agente no.

Las capacidades del agente son las que necesita su trabajo y nada más:
presentar material, corregir comprobaciones, responder dudas con el material
oficial, avisar/escalar y emitir las solicitudes de acceso de la etapa 7
(solo a través de `access.issue_access_request`, que aplica el bloqueo de la
etapa 4 igual que a una persona).
"""

from __future__ import annotations

from typing import Dict, FrozenSet

from services.onboarding.catalog import (
    ORG_ROLE_COMPLIANCE,
    ORG_ROLE_LINE_MANAGER,
    ORG_ROLE_PEOPLE_TEAM,
    ORG_ROLE_VP_PEOPLE,
)

CAP_PRESENT_CONTENT = "present_content"
CAP_GRADE_CHECK = "grade_check"
CAP_ANSWER_FROM_OFFICIAL_MATERIAL = "answer_from_official_material"
CAP_SEND_NOTIFICATION = "send_notification"
CAP_ISSUE_DEPARTMENT_ACCESS_REQUEST = "issue_department_access_request"

CAP_VIEW_ALL_ONBOARDINGS = "view_all_onboardings"
CAP_VIEW_REPORTS_ONBOARDINGS = "view_reports_onboardings"
CAP_VIEW_OWN_ONBOARDING = "view_own_onboarding"
CAP_CREATE_ONBOARDING = "create_onboarding"
CAP_MARK_PAPERWORK = "mark_paperwork"
CAP_APPROVE_COMPLIANCE = "approve_compliance_stage"
CAP_APPROVE_ROLE = "approve_role_stage"
CAP_APPROVE_CLOSURE = "approve_closure"
CAP_SUBMIT_CHECK = "submit_check"
CAP_ASK_QUESTION = "ask_question"
CAP_REQUEST_OWN_ACCESS = "request_own_access"
CAP_VIEW_AUDIT = "view_audit"

ACTOR_KIND_HIRE = "hire"

AGENT_CAPABILITIES: FrozenSet[str] = frozenset(
    {
        CAP_PRESENT_CONTENT,
        CAP_GRADE_CHECK,
        CAP_ANSWER_FROM_OFFICIAL_MATERIAL,
        CAP_SEND_NOTIFICATION,
        CAP_ISSUE_DEPARTMENT_ACCESS_REQUEST,
    }
)

HUMAN_CAPABILITIES: Dict[str, FrozenSet[str]] = {
    ACTOR_KIND_HIRE: frozenset({CAP_VIEW_OWN_ONBOARDING, CAP_SUBMIT_CHECK, CAP_ASK_QUESTION, CAP_REQUEST_OWN_ACCESS}),
    ORG_ROLE_PEOPLE_TEAM: frozenset({CAP_VIEW_ALL_ONBOARDINGS, CAP_CREATE_ONBOARDING, CAP_MARK_PAPERWORK, CAP_VIEW_AUDIT}),
    ORG_ROLE_VP_PEOPLE: frozenset(
        {CAP_VIEW_ALL_ONBOARDINGS, CAP_CREATE_ONBOARDING, CAP_MARK_PAPERWORK, CAP_APPROVE_CLOSURE, CAP_VIEW_AUDIT}
    ),
    ORG_ROLE_COMPLIANCE: frozenset({CAP_VIEW_ALL_ONBOARDINGS, CAP_APPROVE_COMPLIANCE, CAP_VIEW_AUDIT}),
    ORG_ROLE_LINE_MANAGER: frozenset({CAP_VIEW_REPORTS_ONBOARDINGS, CAP_APPROVE_ROLE}),
}

# Lo que ninguna identidad automatizada debe tener jamás.
HUMAN_ONLY_CAPABILITIES: FrozenSet[str] = frozenset(
    {
        CAP_VIEW_ALL_ONBOARDINGS,
        CAP_VIEW_REPORTS_ONBOARDINGS,
        CAP_CREATE_ONBOARDING,
        CAP_MARK_PAPERWORK,
        CAP_APPROVE_COMPLIANCE,
        CAP_APPROVE_ROLE,
        CAP_APPROVE_CLOSURE,
        CAP_VIEW_AUDIT,
        CAP_REQUEST_OWN_ACCESS,
    }
)


def agent_can(capability: str) -> bool:
    return capability in AGENT_CAPABILITIES


def require_agent_capability(capability: str) -> None:
    """Puerta explícita en cada acción del agente: si alguien añade una acción
    nueva sin darle la capacidad, revienta en el primer test."""
    if not agent_can(capability):
        raise PermissionError(f"La identidad del agente no tiene la capacidad {capability!r}")
