"""El orquestador: un único agente LangGraph con un grafo explícito de etapas.

Los nodos se GENERAN a partir de `catalog.STAGES`, así que el grafo compilado
tiene literalmente las 7 etapas del BRIEFING como nodos con nombre
(`s2_company__present`, `s4_compliance__approval`, ...). `draw_mermaid()`
sobre él produce el diagrama del PR.

Por etapa:
- 1 (trámites): `__paperwork` se pausa hasta que Personas marca contrato,
  nómina y verificación de antecedentes como recibidos/completada. Solo tres
  booleanos: el resultado de la verificación no tiene dónde entrar.
- 2-6 (contenido): `__present` → `__check` (pausa: respuestas) → `__grade`
  → aprobado: `__approval` si la etapa la tiene (4 y 5) o la etapa siguiente;
  suspenso: `__reinforce` (vuelve a presentar SOLO las secciones falladas) →
  `__check` con el otro juego de preguntas. Al segundo suspenso de la ronda,
  aviso a Claire (etapa 4) o al responsable directo (resto). Nunca se salta.
- 7 (accesos y cierre): `__access` emite las solicitudes de los sistemas del
  departamento → `__approval` de Diane → `onboarding_complete`.
- Rechazo en cualquier aprobación → vuelta al inicio de esa misma etapa con el
  comentario visible para la persona.

Reglas que conviene no romper:
- El grafo NO toca la base de datos (el checkpointer serializa el estado y un
  nodo puede reejecutarse). Lo que tiene efectos fuera (auditoría, avisos,
  solicitudes de acceso) se añade a `effects` con un id determinista y lo
  persiste `runner.sync`, que ignora los ids ya guardados.
- Lo anterior a un `interrupt()` debe ser puro: LangGraph reejecuta el nodo
  entero al reanudar.
- Cada decisión humana se vuelve a autorizar aquí (`approvals.authorize`),
  aunque la API ya la validó.
- En el estado solo hay el perfil necesario para elegir material (idioma,
  marco, puesto, departamento) e ids: ni nombre ni datos sensibles.
"""

from __future__ import annotations

import operator
from typing import Any, Callable, Dict, List, Optional, Tuple

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import Annotated, TypedDict

from data.pipelines.onboarding_docs import HireProfile
from services.onboarding import approvals, checks
from services.onboarding.catalog import (
    ACTOR_AGENT,
    ACTOR_HUMAN,
    AGENT_ACTOR_ID,
    APPROVER_COMPLIANCE,
    APPROVER_LINE_MANAGER,
    APPROVER_PEOPLE_TEAM,
    APPROVER_VP_PEOPLE,
    COMPLIANCE_STAGE,
    FAILED_ATTEMPTS_BEFORE_NOTICE,
    KIND_ADMIN,
    KIND_CLOSURE,
    KIND_CONTENT,
    RECIPIENT_COMPLIANCE,
    RECIPIENT_HIRE,
    RECIPIENT_MANAGER,
    RECIPIENT_PEOPLE_TEAM,
    RECIPIENT_VP_PEOPLE,
    STAGES,
    Stage,
    department_systems,
)
from services.onboarding.permissions import (
    CAP_GRADE_CHECK,
    CAP_ISSUE_DEPARTMENT_ACCESS_REQUEST,
    CAP_PRESENT_CONTENT,
    CAP_SEND_NOTIFICATION,
    require_agent_capability,
)

PAPERWORK_FIELDS = ("contract_received", "payroll_docs_received", "background_check_completed")

WAIT_PAPERWORK = "paperwork"
WAIT_CHECK = "check"
WAIT_APPROVAL = "approval"

COMPLETE_NODE = "onboarding_complete"


class OnboardingState(TypedDict, total=False):
    hire_id: str
    profile: Dict[str, str]
    hire_ref: Dict[str, str]
    stage: int
    attempts: Dict[str, int]  # intento abierto por etapa ("4" → 2)
    failed_attempts: Dict[str, int]  # suspensos de la ronda actual por etapa
    rounds: Dict[str, int]  # veces que se ha (re)empezado cada etapa
    pending_answers: Optional[Dict[str, Any]]  # check → grade
    last_result: Optional[Dict[str, Any]]  # última nota corregida (con su etapa)
    reinforce_sections: Optional[List[str]]
    rejection: Optional[Dict[str, Any]]
    paperwork: Dict[str, bool]
    approvals: Dict[str, Dict[str, Any]]
    completed_stages: List[int]
    done: bool
    effects: Annotated[List[Dict[str, Any]], operator.add]


# --- Ayudas -----------------------------------------------------------------


def node_name(stage: Stage, step: str) -> str:
    return f"s{stage.number}_{stage.key}__{step}"


def entry_node(stage: Stage) -> str:
    if stage.kind == KIND_ADMIN:
        return node_name(stage, "paperwork")
    if stage.kind == KIND_CONTENT:
        return node_name(stage, "present")
    return node_name(stage, "access")


def _profile(state: OnboardingState) -> HireProfile:
    return HireProfile(**state["profile"])


def _hire_ref(state: OnboardingState) -> approvals.HireRef:
    return approvals.HireRef(**state["hire_ref"])


def _key(state: OnboardingState, stage: int, *parts: Any) -> str:
    round_no = state.get("rounds", {}).get(str(stage), 1)
    return ":".join([state["hire_id"], f"s{stage}", f"r{round_no}", *[str(p) for p in parts]])


def audit(state: OnboardingState, stage: int, action: str, *key_parts: Any, outcome: Optional[str] = None,
          actor: Optional[approvals.Approver] = None, security_event: bool = False) -> Dict[str, Any]:
    """Intención de auditoría. Sin actor humano → la acción es del agente."""
    return {
        "type": "audit",
        "id": _key(state, stage, "audit", action, *key_parts),
        "stage": stage,
        "action": action,
        "actor_type": ACTOR_HUMAN if actor else ACTOR_AGENT,
        "actor_user_id": actor.user_id if actor else None,
        "actor_ref": None if actor else AGENT_ACTOR_ID,
        "outcome": outcome,
        "security_event": security_event,
    }


def notify(state: OnboardingState, stage: int, kind: str, recipient: str, *key_parts: Any, **params: Any) -> Dict[str, Any]:
    require_agent_capability(CAP_SEND_NOTIFICATION)
    return {
        "type": "notification",
        "id": _key(state, stage, "notify", kind, recipient, *key_parts),
        "stage": stage,
        "kind": kind,
        "recipient": recipient,
        "params": params,
    }


def _next_stage_node(stage: Stage) -> str:
    index = STAGES.index(stage)
    return entry_node(STAGES[index + 1]) if index + 1 < len(STAGES) else COMPLETE_NODE


def _finish_stage(state: OnboardingState, stage: Stage) -> Dict[str, Any]:
    completed = sorted(set(state.get("completed_stages", [])) | {stage.number})
    return {
        "completed_stages": completed,
        "rejection": None,
        "reinforce_sections": None,
        "effects": [audit(state, stage.number, "stage_completed")],
    }


def _restart_stage(state: OnboardingState, stage: Stage, comment: str, approver: approvals.Approver) -> Command:
    """Rechazo: vuelve al inicio de la etapa con el comentario visible."""
    rounds = dict(state.get("rounds", {}))
    previous_round = rounds.get(str(stage.number), 1)
    effects = [
        audit(state, stage.number, "approval_rejected", actor=approver),
        notify(state, stage.number, "stage_rejected", RECIPIENT_HIRE, stage_number=stage.number, comment=comment),
    ]
    rounds[str(stage.number)] = previous_round + 1
    return Command(
        goto=entry_node(stage),
        update={
            "rounds": rounds,
            "rejection": {"stage": stage.number, "comment": comment, "round": previous_round},
            "effects": effects,
        },
    )


# A quién se avisa de que tiene una aprobación pendiente.
APPROVAL_RECIPIENTS = {
    APPROVER_PEOPLE_TEAM: RECIPIENT_PEOPLE_TEAM,
    APPROVER_COMPLIANCE: RECIPIENT_COMPLIANCE,
    APPROVER_LINE_MANAGER: RECIPIENT_MANAGER,
    APPROVER_VP_PEOPLE: RECIPIENT_VP_PEOPLE,
}


def approval_requested(state: OnboardingState, stage: Stage) -> Dict[str, Any]:
    attempt = state.get("attempts", {}).get(str(stage.number), 1)
    return notify(state, stage.number, "approval_requested", APPROVAL_RECIPIENTS[stage.approver], attempt, stage_number=stage.number)


def _decision_from(value: Any) -> Tuple[Optional[approvals.Approver], Dict[str, Any]]:
    if not isinstance(value, dict):
        return None, {}
    return approvals.Approver.from_claims(value.get("actor")), value


# --- Nodos ------------------------------------------------------------------


def make_paperwork_node(stage: Stage) -> Callable[[OnboardingState], Command]:
    me = node_name(stage, "paperwork")

    def paperwork(state: OnboardingState) -> Command:
        current = {field: bool(state.get("paperwork", {}).get(field)) for field in PAPERWORK_FIELDS}
        value = interrupt({"kind": WAIT_PAPERWORK, "stage": stage.number, "received": current})
        approver, payload = _decision_from(value)
        refusal = approvals.authorize(stage.number, approver, _hire_ref(state))
        attempt = len(state.get("effects", []))  # distingue intentos sucesivos en la clave
        if refusal:
            return Command(
                goto=me,
                update={"effects": [audit(state, stage.number, "paperwork_refused", attempt, outcome=refusal, actor=approver, security_event=approver is not None)]},
            )
        marks = payload.get("marks", {})
        merged = {field: current[field] or bool(marks.get(field)) for field in PAPERWORK_FIELDS}
        effects = [audit(state, stage.number, "paperwork_marked", attempt, actor=approver)]
        if not all(merged.values()):
            return Command(goto=me, update={"paperwork": merged, "effects": effects})
        finish = _finish_stage(state, stage)
        finish["effects"] = effects + finish["effects"]
        return Command(goto=_next_stage_node(stage), update={**finish, "paperwork": merged})

    return paperwork


def make_present_node(stage: Stage) -> Callable[[OnboardingState], Command]:
    def present(state: OnboardingState) -> Command:
        require_agent_capability(CAP_PRESENT_CONTENT)
        attempts = dict(state.get("attempts", {}))
        failed = dict(state.get("failed_attempts", {}))
        attempts[str(stage.number)] = 1
        failed[str(stage.number)] = 0
        return Command(
            goto=node_name(stage, "check"),
            update={
                "stage": stage.number,
                "attempts": attempts,
                "failed_attempts": failed,
                "pending_answers": None,
                "reinforce_sections": None,
                "effects": [audit(state, stage.number, "content_presented")],
            },
        )

    return present


def make_check_node(stage: Stage) -> Callable[[OnboardingState], Command]:
    me = node_name(stage, "check")

    def check(state: OnboardingState) -> Command:
        attempt = state.get("attempts", {}).get(str(stage.number), 1)
        set_id, _ = checks.question_set(stage.number, _profile(state), attempt)
        value = interrupt({"kind": WAIT_CHECK, "stage": stage.number, "attempt": attempt, "set_id": set_id})
        if not isinstance(value, dict) or value.get("stage") != stage.number or value.get("attempt") != attempt:
            # Respuestas de otro intento (o basura): se ignoran y se sigue esperando.
            return Command(goto=me)
        return Command(goto=node_name(stage, "grade"), update={"pending_answers": {"answers": value.get("answers", {}), "attempt": attempt}})

    return check


def make_grade_node(stage: Stage) -> Callable[[OnboardingState], Command]:
    def grade(state: OnboardingState) -> Command:
        require_agent_capability(CAP_GRADE_CHECK)
        pending = state.get("pending_answers") or {}
        attempt = pending.get("attempt", 1)
        try:
            result = checks.grade(stage.number, _profile(state), attempt, pending.get("answers", {}))
        except checks.CheckAnswersError:
            return Command(goto=node_name(stage, "check"), update={"pending_answers": None})
        summary = {**result.as_dict(), "attempt": attempt, "stage": stage.number}
        effects = [audit(state, stage.number, "check_graded", attempt, outcome="passed" if result.passed else "failed")]
        if result.passed:
            update = {"last_result": summary, "pending_answers": None, "reinforce_sections": None, "effects": effects}
            if stage.approver:
                effects.append(approval_requested(state, stage))
                return Command(goto=node_name(stage, "approval"), update=update)
            finish = _finish_stage(state, stage)
            finish["effects"] = effects + finish["effects"]
            return Command(goto=_next_stage_node(stage), update={**finish, "last_result": summary, "pending_answers": None})

        failed = dict(state.get("failed_attempts", {}))
        failed[str(stage.number)] = failed.get(str(stage.number), 0) + 1
        if failed[str(stage.number)] == FAILED_ATTEMPTS_BEFORE_NOTICE:
            recipient = RECIPIENT_COMPLIANCE if stage.number == COMPLIANCE_STAGE else RECIPIENT_MANAGER
            effects.append(notify(state, stage.number, "check_failed_twice", recipient, stage_number=stage.number))
        return Command(
            goto=node_name(stage, "reinforce"),
            update={"last_result": summary, "pending_answers": None, "failed_attempts": failed, "reinforce_sections": list(result.failed_sections), "effects": effects},
        )

    return grade


def make_reinforce_node(stage: Stage) -> Callable[[OnboardingState], Command]:
    def reinforce(state: OnboardingState) -> Command:
        require_agent_capability(CAP_PRESENT_CONTENT)
        attempts = dict(state.get("attempts", {}))
        previous = attempts.get(str(stage.number), 1)
        attempts[str(stage.number)] = previous + 1
        return Command(
            goto=node_name(stage, "check"),
            update={"attempts": attempts, "effects": [audit(state, stage.number, "content_reinforced", previous)]},
        )

    return reinforce


def make_approval_node(stage: Stage) -> Callable[[OnboardingState], Command]:
    me = node_name(stage, "approval")

    def approval(state: OnboardingState) -> Command:
        value = interrupt({"kind": WAIT_APPROVAL, "stage": stage.number, "approver": stage.approver})
        approver, payload = _decision_from(value)
        refusal = approvals.authorize(stage.number, approver, _hire_ref(state))
        seq = len(state.get("effects", []))
        if refusal:
            return Command(
                goto=me,
                update={"effects": [audit(state, stage.number, "approval_refused", seq, outcome=refusal, actor=approver, security_event=approver is not None)]},
            )
        try:
            decision, comment = approvals.validate_decision({k: payload.get(k) for k in ("decision", "comment") if k in payload})
        except approvals.DecisionError:
            return Command(goto=me)
        if decision == approvals.DECISION_REJECT:
            return _restart_stage(state, stage, comment or "", approver)
        recorded = dict(state.get("approvals", {}))
        recorded[str(stage.number)] = {"org_role": approver.org_role, "department": approver.department, "user_id": approver.user_id}
        finish = _finish_stage(state, stage)
        finish["effects"] = [audit(state, stage.number, "approval_granted", actor=approver)] + finish["effects"]
        return Command(goto=_next_stage_node(stage), update={**finish, "approvals": recorded})

    return approval


def make_access_node(stage: Stage) -> Callable[[OnboardingState], Command]:
    def access(state: OnboardingState) -> Command:
        require_agent_capability(CAP_ISSUE_DEPARTMENT_ACCESS_REQUEST)
        effects: List[Dict[str, Any]] = []
        compliance_ok = str(COMPLIANCE_STAGE) in state.get("approvals", {})
        for system in department_systems(state["profile"]["department"]):
            # Defensa en el propio grafo: en la etapa 7 la 4 ya está aprobada,
            # pero si algún día cambia el orden, aquí no se emite nada con PHI.
            if system.contains_patient_data and not compliance_ok:
                continue
            effects.append({"type": "access_request", "id": _key(state, stage.number, "access", system.key), "stage": stage.number, "system": system.key})
        effects.append(audit(state, stage.number, "access_requests_issued", outcome=str(len(effects))))
        effects.append(approval_requested(state, stage))
        return Command(goto=node_name(stage, "approval"), update={"stage": stage.number, "effects": effects})

    return access


def onboarding_complete(state: OnboardingState) -> Dict[str, Any]:
    last = STAGES[-1].number
    return {
        "done": True,
        "effects": [
            audit(state, last, "onboarding_completed"),
            notify(state, last, "onboarding_completed", RECIPIENT_HIRE),
            notify(state, last, "onboarding_completed", RECIPIENT_PEOPLE_TEAM),
        ],
    }


def start(state: OnboardingState) -> Command:
    first = STAGES[0]
    return Command(
        goto=entry_node(first),
        update={
            "stage": first.number,
            "attempts": {},
            "failed_attempts": {},
            "rounds": {},
            "paperwork": {},
            "approvals": {},
            "completed_stages": [],
            "done": False,
            "effects": [
                audit(state, first.number, "onboarding_started"),
                notify(state, first.number, "onboarding_started", RECIPIENT_HIRE),
                notify(state, first.number, "approval_requested", RECIPIENT_PEOPLE_TEAM, 1, stage_number=first.number),
            ],
        },
    )


# --- Construcción -----------------------------------------------------------


def build_graph() -> StateGraph:
    builder = StateGraph(OnboardingState)
    builder.add_node("start", start, destinations=(entry_node(STAGES[0]),))
    builder.add_edge(START, "start")
    for stage in STAGES:
        nxt = _next_stage_node(stage)
        if stage.kind == KIND_ADMIN:
            me = node_name(stage, "paperwork")
            builder.add_node(me, make_paperwork_node(stage), destinations=(me, nxt))
        elif stage.kind == KIND_CONTENT:
            present, check_n = node_name(stage, "present"), node_name(stage, "check")
            grade_n, reinforce_n = node_name(stage, "grade"), node_name(stage, "reinforce")
            builder.add_node(present, make_present_node(stage), destinations=(check_n,))
            builder.add_node(check_n, make_check_node(stage), destinations=(check_n, grade_n))
            after_pass = (node_name(stage, "approval"),) if stage.approver else (nxt,)
            builder.add_node(grade_n, make_grade_node(stage), destinations=(check_n, reinforce_n, *after_pass))
            builder.add_node(reinforce_n, make_reinforce_node(stage), destinations=(check_n,))
            if stage.approver:
                approval_n = node_name(stage, "approval")
                builder.add_node(approval_n, make_approval_node(stage), destinations=(approval_n, present, nxt))
        elif stage.kind == KIND_CLOSURE:
            access_n, approval_n = node_name(stage, "access"), node_name(stage, "approval")
            builder.add_node(access_n, make_access_node(stage), destinations=(approval_n,))
            builder.add_node(approval_n, make_approval_node(stage), destinations=(approval_n, access_n, nxt))
    builder.add_node(COMPLETE_NODE, onboarding_complete)
    builder.add_edge(COMPLETE_NODE, END)
    return builder


def compile_onboarding_graph(checkpointer: Any):
    """Siempre con checkpointer: sin él no hay pausas ni reanudación."""
    if checkpointer is None:
        raise ValueError("El orquestador necesita un checkpointer")
    return build_graph().compile(checkpointer=checkpointer)


def initial_state(*, hire_id: str, profile: HireProfile, hire_ref: approvals.HireRef) -> OnboardingState:
    return {
        "hire_id": hire_id,
        "profile": {
            "language": profile.language,
            "framework": profile.framework,
            "role_variant": profile.role_variant,
            "department": profile.department,
        },
        "hire_ref": {
            "hire_user_id": hire_ref.hire_user_id,
            "department": hire_ref.department,
            "manager_user_id": hire_ref.manager_user_id,
        },
        "effects": [],
    }


def stage_nodes() -> Dict[int, List[str]]:
    """Nodos de cada etapa (para el test que comprueba que el grafo cubre la tabla)."""
    graph = build_graph()
    by_stage: Dict[int, List[str]] = {}
    for name in graph.nodes:
        if name.startswith("s") and "__" in name:
            by_stage.setdefault(int(name[1:].split("_", 1)[0]), []).append(name)
    return by_stage
