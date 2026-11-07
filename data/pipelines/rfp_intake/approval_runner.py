"""Ejecuta la Parte 3 para un ticket: arranca la aprobación o aplica las
decisiones humanas pendientes, y vuelca el estado del grafo a Supabase.

Entrada (contrato de docs/rfp-response.md): ticket `evaluacion_completa` o
`needs_human_review`, `next_stage = esperando_aprobación` y
`response_handoff` v1 (borrador + EvaluationResult por departamento). De la
Parte 1 se reutiliza el `handoff_payload` (workstreams y cifras permitidas)
para que una revisión vuelva al generador con la misma entrada.

`advance()` es el único punto de entrada y es idempotente:
- sin checkpoint → `start`: corre el grafo hasta las pausas;
- con checkpoint y decisiones guardadas por la API → `resume`: un
  `Command(resume={interrupt_id: decisión})` SOLO con los interrupts de los
  departamentos que decidieron; el resto sigue pausado;
- con una rama que falló a mitad (modelo caído durante una revisión) →
  `invoke(None)`, que reintenta solo esa tarea;
- nada que hacer → solo sincroniza.

Concurrencia: dos aprobaciones casi simultáneas encolan dos tareas sobre el
mismo hilo. Un advisory lock de Postgres por ticket las pone en fila; la
segunda ya no encuentra nada pendiente (la primera aplicó las dos).

Rechazo (decisión del usuario): la rama que rechaza termina y las que siguen
pausadas reciben `cancel` en la misma reanudación, así el grafo llega a
`arbitrate`, cierra sin documento y el ticket queda `descartado`.
"""

from __future__ import annotations

import logging
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from langgraph.types import Command
from sqlalchemy import text
from sqlmodel import Session

from data.pipelines.rfp_intake import repository
from data.pipelines.rfp_intake.agents import LlmCall, call_model
from data.pipelines.rfp_intake.approval_graph import (
    BRANCH_APPROVED,
    BRANCH_BLOCKED,
    BRANCH_CANCELLED,
    BRANCH_REJECTED,
    DEFAULT_MAX_ARBITRATION_ROUNDS,
    DEFAULT_MAX_REVISION_ROUNDS,
    INTERRUPT_ARBITRATION,
    INTERRUPT_SECTION,
    RESULT_ARBITRATION_LIMIT,
    RESULT_FINISHED,
    RESULT_REJECTED,
    compile_approval_graph,
)
from data.pipelines.rfp_intake.arbitration import REGULATORY_TRIGGERS
from data.pipelines.rfp_intake.checkpointer import thread_config, thread_id_for
from data.pipelines.rfp_intake.decisions import DecisionError, validate_arbitration_decision, validate_section_decision
from data.pipelines.rfp_intake.departments import DEPARTMENTS
from data.pipelines.rfp_intake.evaluators import allowed_figures
from data.pipelines.rfp_intake.generators import GeneratorInput
from data.pipelines.rfp_intake.models import (
    APPROVAL_APPROVED,
    APPROVAL_BLOCKED,
    APPROVAL_CANCELLED,
    APPROVAL_DECISION_RECORDED,
    APPROVAL_ENTRY_STATUSES,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    APPROVAL_REVISING,
    DECISION_CANCEL,
    DECISION_REJECT,
    DISCARD_ARBITRATION_LIMIT,
    DISCARD_REJECTED,
    NEXT_STAGE_APPROVAL,
    STATUS_AWAITING_APPROVAL,
    STATUS_DISCARDED,
    STATUS_ERROR,
    STATUS_FINISHED,
    RfpTicket,
    utcnow,
)
from data.pipelines.rfp_intake.response_runner import RESPONSE_HANDOFF_VERSION
from data.pipelines.rfp_intake.runner import HANDOFF_VERSION
from data.pipelines.rfp_intake.trace import entry, text_digest

logger = logging.getLogger("healthcore.rfp_intake")

BRANCH_TO_APPROVAL = {
    BRANCH_APPROVED: APPROVAL_APPROVED,
    BRANCH_REJECTED: APPROVAL_REJECTED,
    BRANCH_BLOCKED: APPROVAL_BLOCKED,
    BRANCH_CANCELLED: APPROVAL_CANCELLED,
}
RESULT_TO_DISCARD = {RESULT_REJECTED: DISCARD_REJECTED, RESULT_ARBITRATION_LIMIT: DISCARD_ARBITRATION_LIMIT}


class ApprovalHandoffError(ValueError):
    """El ticket no trae un handoff de la Parte 2 que esta versión entienda."""


class ApprovalStateError(RuntimeError):
    """La decisión no tiene a qué aplicarse (ticket o sección no esperan
    nada). `not_found` distingue 404 de 409 en la API."""

    def __init__(self, message: str, *, not_found: bool = False) -> None:
        super().__init__(message)
        self.not_found = not_found


# --------------------------------------------------------------------------
# Registrar decisiones humanas (API y simulación E2E usan el MISMO camino)
# --------------------------------------------------------------------------


def submit_section_decision(
    session: Session,
    ticket_id: uuid.UUID,
    department_id: str,
    raw: Dict[str, Any],
    *,
    decided_by: str,
    draft_digest: Optional[str] = None,
) -> Dict[str, Any]:
    """Valida contra la petición viva y la deja en `pending_decision`. No
    reanuda el grafo: eso lo hace `advance` (worker o simulación)."""
    ticket = repository.get_ticket(session, ticket_id)
    if ticket is None:
        raise ApprovalStateError("RFP ticket not found.", not_found=True)
    if ticket.status != STATUS_AWAITING_APPROVAL:
        raise ApprovalStateError("The RFP is not waiting for approvals.")
    section = repository.sections_by_department(session, ticket_id).get(department_id)
    if section is None:
        raise ApprovalStateError("Department section not found.", not_found=True)
    if section.approval_status != APPROVAL_PENDING or not section.approval_request:
        raise ApprovalStateError("This section is not waiting for a decision.")
    decision = validate_section_decision(raw, section.approval_request)  # DecisionError → 400
    repository.record_pending_decision(session, section, {
        **decision,
        "decided_by": decided_by,
        "decided_at": utcnow().isoformat(),
        "draft_digest": draft_digest or section.approval_request.get("draft_digest"),
    })
    return decision


def submit_arbitration_decision(
    session: Session, ticket_id: uuid.UUID, raw: Dict[str, Any], *, decided_by: str
) -> Dict[str, Any]:
    ticket = repository.get_ticket(session, ticket_id)
    if ticket is None:
        raise ApprovalStateError("RFP ticket not found.", not_found=True)
    arbitration = ticket.arbitration or {}
    if ticket.status != STATUS_AWAITING_APPROVAL or not arbitration.get("pending") or arbitration.get("pending_decision"):
        raise ApprovalStateError("No arbitration is waiting for a decision.")
    decision = validate_arbitration_decision(raw, arbitration["request"])
    repository.record_pending_arbitration(
        session, ticket, {**decision, "decided_by": decided_by, "decided_at": utcnow().isoformat()}
    )
    return decision


# --------------------------------------------------------------------------
# Trazabilidad → rfp_trace_events
# --------------------------------------------------------------------------


class TraceStore:
    """`on_trace` del grafo → filas de `rfp_trace_events`. Llega desde los
    hilos de cada rama: sesión propia por evento y un candado, igual que el
    progreso de la Parte 2."""

    def __init__(self, engine: Any, ticket_id: uuid.UUID) -> None:
        self._engine = engine
        self._ticket_id = ticket_id
        self._lock = threading.Lock()

    def __call__(self, item: Dict[str, Any]) -> None:
        self.write([item])

    def write(self, items: List[Dict[str, Any]]) -> None:
        try:
            with self._lock, Session(self._engine) as session:
                repository.insert_trace_events(session, self._ticket_id, items)
        except Exception as exc:  # noqa: BLE001 — el trace no tumba la aprobación
            logger.warning("rfp_trace_write_failed ticket=%s error_type=%s", self._ticket_id, type(exc).__name__)


# --------------------------------------------------------------------------
# Lectura del estado del grafo
# --------------------------------------------------------------------------


@dataclass
class ApprovalView:
    exists: bool
    values: Dict[str, Any]
    # departamento → (interrupt_id, petición); solo pausas VIVAS
    pending: Dict[str, Tuple[str, Dict[str, Any]]] = field(default_factory=dict)
    arbitration: Optional[Tuple[str, Dict[str, Any]]] = None
    finished: bool = False
    failed: bool = False


def read_view(graph: Any, config: Dict[str, Any], *, trust_errors: bool = False) -> ApprovalView:
    """Gotchas de LangGraph 0.6 (comprobados): tras reanudar una rama,
    `snapshot.interrupts` sigue listando su pausa ya resuelta, y una tarea
    que falló y se reintentó con éxito CONSERVA su `error` aunque ya esté
    pausada otra vez. Una pausa está viva si su tarea no tiene `result`; el
    `error` solo cuenta si el último `advance` falló (`trust_errors`, el
    ticket quedó en `error`): entonces su pausa es la ya consumida."""
    snapshot = graph.get_state(config)
    view = ApprovalView(exists=bool(snapshot.values), values=snapshot.values or {})
    for task in snapshot.tasks:
        if trust_errors and task.error is not None:
            view.failed = True
            continue
        if task.result is not None:
            continue
        for pending in task.interrupts:
            value = pending.value
            if value.get("kind") == INTERRUPT_SECTION:
                view.pending[value["department_id"]] = (pending.id, value)
            elif value.get("kind") == INTERRUPT_ARBITRATION:
                view.arbitration = (pending.id, value)
    view.finished = view.exists and not snapshot.next
    return view


# --------------------------------------------------------------------------
# Entrada del grafo a partir de los handoffs de las Partes 1 y 2
# --------------------------------------------------------------------------


def build_approval_input(
    ticket: RfpTicket,
    *,
    max_revision_rounds: int = DEFAULT_MAX_REVISION_ROUNDS,
    max_arbitration_rounds: int = DEFAULT_MAX_ARBITRATION_ROUNDS,
) -> Dict[str, Any]:
    intake, response = ticket.handoff_payload or {}, ticket.response_handoff or {}
    if intake.get("handoff_version") != HANDOFF_VERSION or response.get("handoff_version") != RESPONSE_HANDOFF_VERSION:
        raise ApprovalHandoffError("Handoffs de las Partes 1/2 ausentes o de una versión no soportada")
    if not response.get("sections"):
        raise ApprovalHandoffError("El handoff de la Parte 2 no trae secciones")

    metadata = intake["metadata"]
    thread_id = thread_id_for(ticket.ticket_id)
    context = {
        "rfp_id": ticket.rfp_id,
        "client_name": metadata.get("client_name"),
        "client_country": metadata.get("client_country"),
        "currency": metadata.get("currency"),
        "program_type": metadata.get("program_type") or [],
        "covered_population": metadata.get("covered_population"),
        "covered_population_unit": metadata.get("covered_population_unit"),
    }
    workstreams = {w["department_id"]: w for w in intake.get("workstreams", [])}
    tasks: Dict[str, Dict[str, Any]] = {}
    for section in response["sections"]:
        department_id = section["department_id"]
        workstream = workstreams.get(department_id, {"key_aspects": [], "open_questions": []})
        generator_input = GeneratorInput(
            department_id=department_id,
            metadata=metadata,
            key_aspects=workstream.get("key_aspects", []),
            open_questions=workstream.get("open_questions", []),
            summary=intake.get("summary"),
        ).model_dump()
        tasks[department_id] = {
            "thread_id": thread_id,
            "department_id": department_id,
            "context": context,
            "generator_input": generator_input,
            "figures_allowed": sorted(allowed_figures(metadata, workstream, intake.get("summary"))),
            "draft": section["draft_content"],
            "evaluation": section["evaluation_result"],
            "provisional": bool(section.get("provisional")),
            "forced_feedback": None,
            "arbitration_round": 0,
            "revision_round": 0,
            "max_revision_rounds": max_revision_rounds,
            "cycle": 0,
        }
    return {
        "ticket_id": str(ticket.ticket_id),
        "thread_id": thread_id,
        "context": context,
        "tasks": tasks,
        "max_revision_rounds": max_revision_rounds,
        "max_arbitration_rounds": max_arbitration_rounds,
        "arbitration_round": 0,
        "outcomes": {},
        "trace": [],
    }


# --------------------------------------------------------------------------
# Advance = start | resume | retry
# --------------------------------------------------------------------------

_local_locks: Dict[str, threading.Lock] = {}
_local_locks_guard = threading.Lock()


@contextmanager
def ticket_lock(engine: Any, ticket_id: uuid.UUID) -> Iterator[None]:
    """Un solo `advance` a la vez por ticket. En Postgres, advisory lock de
    transacción (el pooler de Supabase en modo transacción no conserva los de
    sesión) en una conexión propia que se mantiene abierta mientras dure."""
    key = f"rfp-approval:{ticket_id}"
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
            yield
        return
    with _local_locks_guard:
        lock = _local_locks.setdefault(key, threading.Lock())
    with lock:
        yield


def advance(
    session: Session,
    ticket_id: uuid.UUID,
    *,
    checkpointer: Any,
    llm: LlmCall = call_model,
    max_revision_rounds: int = DEFAULT_MAX_REVISION_ROUNDS,
    max_arbitration_rounds: int = DEFAULT_MAX_ARBITRATION_ROUNDS,
) -> str:
    """Devuelve el estado del ticket tras avanzar lo que se pueda."""
    engine = session.get_bind()
    with ticket_lock(engine, ticket_id):
        ticket = repository.get_ticket(session, ticket_id)
        if ticket is None:
            raise LookupError(f"Ticket {ticket_id} no existe")
        session.refresh(ticket)
        traces = TraceStore(engine, ticket_id)
        graph = compile_approval_graph(checkpointer=checkpointer, llm=llm, on_trace=traces)
        config = thread_config(ticket_id)
        view = read_view(graph, config, trust_errors=ticket.status == STATUS_ERROR)

        if view.exists and ticket.approval_thread_id is None:
            # El ticket se reprocesó (Parte 1 o 2): el checkpoint es de borradores viejos.
            checkpointer.delete_thread(thread_id_for(ticket_id))
            view = read_view(graph, config)
        try:
            action = _step(session, ticket, graph, config, view, max_revision_rounds, max_arbitration_rounds)
        except Exception as exc:
            # El ticket dice la verdad ya (no espera a Celery) y el próximo
            # `advance` sabe que el error de la tarea es real y la reintenta.
            session.rollback()
            repository.mark_error(
                session, ticket_id, f"La aprobación falló a mitad ({type(exc).__name__}). Relanzar el ticket para reintentarla."
            )
            raise
        if action is None:
            return ticket.status

        final_view = read_view(graph, config)  # tras un invoke con éxito, ningún error es actual
        sync(session, ticket, final_view, traces)
        logger.info(
            "rfp_approval_advanced ticket=%s action=%s status=%s pending=%s",
            ticket_id, action, ticket.status, ",".join(sorted(final_view.pending)) or "-",
        )
        return ticket.status


def _step(
    session: Session,
    ticket: RfpTicket,
    graph: Any,
    config: Dict[str, Any],
    view: ApprovalView,
    max_revision_rounds: int,
    max_arbitration_rounds: int,
) -> Optional[str]:
    """start | resume | retry | sync. None = nada que hacer con este ticket."""
    if not view.exists:
        if ticket.status not in APPROVAL_ENTRY_STATUSES or ticket.next_stage != NEXT_STAGE_APPROVAL:
            logger.info("rfp_approval_skipped ticket=%s status=%s", ticket.ticket_id, ticket.status)
            return None
        payload = build_approval_input(
            ticket, max_revision_rounds=max_revision_rounds, max_arbitration_rounds=max_arbitration_rounds
        )
        repository.start_approval(session, ticket, payload["thread_id"])
        graph.invoke(payload, config)
        return "start"
    if ticket.status in (STATUS_FINISHED, STATUS_DISCARDED):
        return None
    resume = collect_resume(session, ticket, view)
    if resume:
        graph.invoke(Command(resume=resume), config)
        return f"resume:{len(resume)}"
    if view.failed:
        graph.invoke(None, config)  # reintenta solo la tarea que falló
        return "retry"
    return "sync"


def collect_resume(session: Session, ticket: RfpTicket, view: ApprovalView) -> Dict[str, Any]:
    """Decisiones guardadas por la API → mapa `{interrupt_id: valor}`.

    Cada una se valida OTRA VEZ contra la petición viva del grafo: entre la
    API y el worker pudo cambiar (otra ronda, un borrador nuevo). Las que ya
    no encajan se descartan con un motivo visible, sin tocar el grafo."""
    resume: Dict[str, Any] = {}
    rejected_by: Optional[str] = None
    for department_id, section in repository.sections_by_department(session, ticket.ticket_id).items():
        payload = section.pending_decision
        if not payload:
            continue
        live = view.pending.get(department_id)
        if live is None:
            repository.clear_pending_decision(session, section, error="La sección ya no esperaba una decisión.")
            continue
        interrupt_id, request = live
        if payload.get("draft_digest") and payload["draft_digest"] != request["draft_digest"]:
            repository.clear_pending_decision(
                session, section, error="El borrador cambió mientras decidías: revisa la versión nueva."
            )
            continue
        try:
            normalized = validate_section_decision(payload, request)
        except DecisionError as exc:
            repository.clear_pending_decision(session, section, error=str(exc))
            continue
        decision = {**normalized, "decided_by": payload.get("decided_by"), "decided_at": payload.get("decided_at")}
        resume[interrupt_id] = decision
        repository.mark_decision_applying(session, section, decision)
        if normalized["decision"] == DECISION_REJECT:
            rejected_by = department_id

    if rejected_by:
        for department_id, (interrupt_id, _) in view.pending.items():
            if interrupt_id not in resume:
                resume[interrupt_id] = {
                    "decision": DECISION_CANCEL, "reason": f"rejected_by:{rejected_by}", "decided_at": utcnow().isoformat(),
                }

    pending_arbitration = (ticket.arbitration or {}).get("pending_decision")
    if pending_arbitration:
        arbitration = dict(ticket.arbitration)
        arbitration.pop("pending_decision")
        if view.arbitration is None:
            arbitration["last_error"] = "El arbitraje ya no estaba pendiente."
        else:
            interrupt_id, request = view.arbitration
            try:
                normalized = validate_arbitration_decision(pending_arbitration, request)
                resume[interrupt_id] = {
                    **normalized,
                    "decided_by": pending_arbitration.get("decided_by"),
                    "decided_at": pending_arbitration.get("decided_at"),
                }
                arbitration["applying"] = True
            except DecisionError as exc:
                arbitration["last_error"] = str(exc)
        ticket.arbitration = arbitration
        session.add(ticket)
    session.commit()
    return resume


def sync(session: Session, ticket: RfpTicket, view: ApprovalView, traces: TraceStore) -> None:
    """Estado del grafo → filas (una transacción). También registra en el
    trace cada pausa viva ("esperando a X"), para medir el tiempo de
    aprobación por departamento (KPI del BRIEFING §3)."""
    values = view.values
    outcomes = values.get("outcomes") or {}
    thread_id = thread_id_for(ticket.ticket_id)
    waiting_events = []
    for department_id, section in repository.sections_by_department(session, ticket.ticket_id).items():
        if department_id in view.pending:
            interrupt_id, request = view.pending[department_id]
            section.approval_status = APPROVAL_DECISION_RECORDED if section.pending_decision else APPROVAL_PENDING
            last_error = (section.approval_request or {}).get("last_error")
            same_request = (section.approval_request or {}).get("interrupt_id") == interrupt_id
            section.approval_request = {**request, "interrupt_id": interrupt_id, "last_error": last_error if same_request else None}
            section.approval_round = request["revision_round"]
            if section.draft_content != request["draft"]:
                section.draft_content = request["draft"]
                section.drafted_at = utcnow()
            waiting_events.append(entry(
                key=(thread_id, "awaiting_approval", department_id, interrupt_id), part=3, node="interrupt",
                agent=f"human:{request['approver']}", department_id=department_id,
                input={"draft": text_digest(request["draft"]), "revision_round": request["revision_round"]},
                output={"status": "waiting", "can_request_changes": request["can_request_changes"]},
            ))
        elif department_id in outcomes and not (view.failed and section.approval_status == APPROVAL_REVISING):
            # (Si la revisión falló a mitad, `outcomes` aún guarda el resultado
            # de la ronda anterior: la sección sigue "en revisión".)
            outcome = outcomes[department_id]
            section.approval_status = BRANCH_TO_APPROVAL[outcome["status"]]
            section.approval_request = None
            section.draft_content = outcome["draft"]
            section.evaluation_results = outcome.get("evaluation") or section.evaluation_results
            section.approval_round = outcome["revision_round"]
            if outcome["status"] in (BRANCH_APPROVED, BRANCH_REJECTED):
                section.approver = DEPARTMENTS[department_id].owner
                section.decided_by = outcome.get("decided_by")
            if outcome["status"] == BRANCH_APPROVED:
                section.approved_at = _parse(outcome.get("decided_at"))
                section.confirmed_capacity = outcome.get("confirmed_capacity")
        session.add(section)

    record = values.get("arbitration")
    arbitration = {k: v for k, v in (ticket.arbitration or {}).items() if k in ("pending_decision", "last_error")}
    if record:
        arbitration["last"] = record
    arbitration["round"] = values.get("arbitration_round", 0)
    arbitration["max_rounds"] = values.get("max_arbitration_rounds")
    arbitration["pending"] = view.arbitration is not None
    arbitration["request"] = view.arbitration[1] if view.arbitration else None
    if view.arbitration:
        request = view.arbitration[1]
        waiting_events.append(entry(
            key=(thread_id, "awaiting_arbitration", view.arbitration[0]), part=3, node="interrupt",
            agent=f"human:{request['arbiter']}", input={"trigger_id": request["trigger_id"], "round": request["round"]},
            output={"status": "waiting"},
        ))
    ticket.arbitration = arbitration
    regulatory = any(
        c["trigger_id"] in REGULATORY_TRIGGERS for c in (record or {}).get("conflicts", [])
    )

    if view.finished:
        result = values.get("result")
        if result == RESULT_FINISHED:
            repository.save_final_document(session, ticket, values["final_document"])
        else:
            repository.close_without_document(
                session, ticket, reason=RESULT_TO_DISCARD.get(result, DISCARD_REJECTED), requires_compliance_review=regulatory
            )
    else:
        ticket.status = STATUS_AWAITING_APPROVAL
        ticket.requires_compliance_review = ticket.requires_compliance_review or regulatory
    ticket.error_message = None  # sincronizar con éxito saca al ticket de un `error` anterior
    ticket.updated_at = utcnow()
    session.add(ticket)
    session.commit()
    traces.write(list(values.get("trace") or []) + waiting_events)


def _parse(value: Optional[str]):
    from datetime import datetime

    return datetime.fromisoformat(value) if value else None
