"""Quién puede cerrar cada punto de aprobación, y cómo es una decisión válida.

BRIEFING: "El responsable directo aprueba la etapa de función. Claire aprueba
la de cumplimiento. Yo apruebo el cierre." + Personas marca los trámites.

La comprobación se hace con el directorio `onboarding_staff` (rol
organizativo + departamento), nunca con el rol de la plataforma: un admin del
backoffice que no es de Cumplimiento NO puede aprobar la etapa 4.

`authorize` es pura y se ejecuta dos veces: en la API (403 inmediato, la
decisión ni se guarda) y dentro del grafo antes de avanzar (una decisión que
llegue a la cola por otro camino tampoco pasa).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from services.onboarding import sensitive
from services.onboarding.catalog import (
    APPROVER_COMPLIANCE,
    APPROVER_LINE_MANAGER,
    APPROVER_PEOPLE_TEAM,
    APPROVER_VP_PEOPLE,
    ORG_ROLE_COMPLIANCE,
    ORG_ROLE_LINE_MANAGER,
    ORG_ROLE_PEOPLE_TEAM,
    ORG_ROLE_VP_PEOPLE,
    get_stage,
)

DECISION_APPROVE = "approve"
DECISION_REJECT = "reject"
DECISIONS = (DECISION_APPROVE, DECISION_REJECT)
MAX_COMMENT_LENGTH = 1000

REFUSAL_NOT_IN_DIRECTORY = "not_in_directory"
REFUSAL_SELF_APPROVAL = "self_approval"
REFUSAL_WRONG_ROLE = "wrong_role"
REFUSAL_WRONG_DEPARTMENT = "wrong_department"
REFUSAL_NOT_DIRECT_MANAGER = "not_direct_manager"
REFUSAL_NO_APPROVAL_HERE = "stage_has_no_human_approval"

# Texto para la API (sin datos de nadie).
REFUSAL_MESSAGES = {
    REFUSAL_NOT_IN_DIRECTORY: "Tu cuenta no figura en el directorio de aprobadores del onboarding.",
    REFUSAL_SELF_APPROVAL: "Nadie puede aprobar una etapa de su propia incorporación.",
    REFUSAL_WRONG_ROLE: "Tu rol no puede aprobar esta etapa.",
    REFUSAL_WRONG_DEPARTMENT: "Tu departamento no puede aprobar esta etapa.",
    REFUSAL_NOT_DIRECT_MANAGER: "Solo el responsable directo de esta persona puede aprobar la etapa de función.",
    REFUSAL_NO_APPROVAL_HERE: "Esta etapa no tiene aprobación humana.",
}


@dataclass(frozen=True)
class Approver:
    user_id: str
    org_role: str
    department: str

    def as_claims(self) -> Dict[str, str]:
        return asdict(self)

    @classmethod
    def from_claims(cls, claims: Optional[Mapping[str, Any]]) -> Optional["Approver"]:
        if not claims:
            return None
        try:
            return cls(str(claims["user_id"]), str(claims["org_role"]), str(claims["department"]))
        except KeyError:
            return None


@dataclass(frozen=True)
class HireRef:
    """Lo mínimo de la incorporación para decidir: nunca su nombre."""

    hire_user_id: str
    department: str
    manager_user_id: str


def authorize(stage_number: int, approver: Optional[Approver], hire: HireRef) -> Optional[str]:
    """None si `approver` puede cerrar el punto de aprobación de esta etapa;
    si no, el motivo (una de las constantes REFUSAL_*)."""
    rule = get_stage(stage_number).approver
    if rule is None:
        return REFUSAL_NO_APPROVAL_HERE
    if approver is None:
        return REFUSAL_NOT_IN_DIRECTORY
    if approver.user_id == hire.hire_user_id:
        return REFUSAL_SELF_APPROVAL

    if rule == APPROVER_PEOPLE_TEAM:
        if approver.org_role not in (ORG_ROLE_PEOPLE_TEAM, ORG_ROLE_VP_PEOPLE):
            return REFUSAL_WRONG_ROLE
        return None if approver.department == "people" else REFUSAL_WRONG_DEPARTMENT
    if rule == APPROVER_COMPLIANCE:
        if approver.org_role != ORG_ROLE_COMPLIANCE:
            return REFUSAL_WRONG_ROLE
        return None if approver.department == "compliance" else REFUSAL_WRONG_DEPARTMENT
    if rule == APPROVER_LINE_MANAGER:
        if approver.org_role != ORG_ROLE_LINE_MANAGER:
            return REFUSAL_WRONG_ROLE
        if approver.user_id != hire.manager_user_id:
            return REFUSAL_NOT_DIRECT_MANAGER
        return None if approver.department == hire.department else REFUSAL_WRONG_DEPARTMENT
    if rule == APPROVER_VP_PEOPLE:
        if approver.org_role != ORG_ROLE_VP_PEOPLE:
            return REFUSAL_WRONG_ROLE
        return None if approver.department == "people" else REFUSAL_WRONG_DEPARTMENT
    return REFUSAL_WRONG_ROLE  # pragma: no cover - regla nueva sin implementar: falla cerrado


class DecisionError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def validate_decision(payload: Mapping[str, Any]) -> Tuple[str, Optional[str]]:
    """(decisión, comentario). El rechazo exige comentario (la persona lo ve:
    BRIEFING "con un comentario visible"); un comentario con datos sensibles
    se rechaza, sin repetir su contenido."""
    unknown = set(payload) - {"decision", "comment", "stage"}
    if unknown:
        raise DecisionError(sorted(unknown)[0], "Campo no permitido.")
    decision = payload.get("decision")
    if decision not in DECISIONS:
        raise DecisionError("decision", "La decisión debe ser 'approve' o 'reject'.")
    comment = payload.get("comment")
    if comment is not None and not isinstance(comment, str):
        raise DecisionError("comment", "El comentario debe ser texto.")
    comment = (comment or "").strip() or None
    if decision == DECISION_REJECT and not comment:
        raise DecisionError("comment", "Un rechazo necesita un comentario para la persona incorporada.")
    if comment and len(comment) > MAX_COMMENT_LENGTH:
        raise DecisionError("comment", f"Máximo {MAX_COMMENT_LENGTH} caracteres.")
    if comment and sensitive.contains_sensitive(comment):
        raise DecisionError(
            "comment",
            "El comentario parece contener datos sensibles (identidad, banco, salario o verificación de antecedentes). Reescríbelo sin ellos.",
        )
    return decision, comment
