"""Un turno de conversación con memoria (Ticket #MEM-092).

Único sitio que junta el grafo con la memoria. Orden fijo de cada turno:

1. Si el usuario tiene una propuesta pendiente, su mensaje se clasifica
   PRIMERO contra ella (`decision.classify_decision`) y se resuelve con
   `decision.resolve_decision`: guardar, guardar editada, rechazar o
   descartar. Toda resolución queda en `agent_memory_proposals`.
2. Lo que quede por responder (el mensaje entero si cambió de tema, o la
   pregunta que venía detrás del "sí") pasa por el grafo, con los recuerdos
   aprobados que devuelve `store.recall()` como entrada.
3. Si el grafo trae una propuesta, pasa por el validador de PHI (texto
   propuesto + mensaje original). Limpia: se registra como pendiente y se
   pregunta al usuario al final de la respuesta. Con PHI: se registra como
   bloqueada (sin texto) y se explica al usuario por qué no se puede recordar.

Nunca se escribe memoria en el paso 3: solo en el 1, y solo desde una
propuesta pendiente del propio usuario con una decisión clasificada.

Si Supabase no responde, el agente contesta sin memoria (ni lee, ni propone):
la memoria es un complemento, no puede tumbar la consulta.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy.exc import SQLAlchemyError

from services.agent.memory import phi_guard
from services.agent.memory.consolidation import PhiInMemoryError
from services.agent.memory.decision import DecisionClassification, Resolution, classify_decision, resolve_decision
from services.agent.memory.models import (
    PROPOSAL_APPROVED,
    PROPOSAL_APPROVED_EDITED,
    PROPOSAL_BLOCKED_PHI,
    PROPOSAL_DISCARDED,
    PROPOSAL_REJECTED,
    AgentMemoryProposal,
)
from services.agent.memory.store import MemoryStore, PendingProposalExists, as_evidence
from services.agent.tracing import run_agent

logger = logging.getLogger(__name__)

OUTCOME_MEMORY_DECISION = "memory_decision"

# Estados de memoria que ve el cliente en la respuesta.
MEMORY_PROPOSED = "proposed"
MEMORY_SAVED = "saved"
MEMORY_REJECTED = "rejected"
MEMORY_DISCARDED = "discarded"
MEMORY_BLOCKED_PHI = "blocked_phi"
MEMORY_NOT_MEMORABLE = "not_memorable"

PROPOSAL_QUESTION = (
    "¿Quieres que recuerde esto para la próxima vez?\n«{content}»\n"
    "Responde sí o no, o dime cómo quieres que lo guarde."
)
NOT_MEMORABLE_MESSAGE = (
    "Esto no lo guardo en memoria: solo recuerdo cambios operativos de las sedes, patrones de "
    "incidentes conocidos y preferencias de presentación del staff. Los datos puntuales se "
    "consultan en vivo en su sistema."
)

ClassifyFn = Callable[[str, str], DecisionClassification]
RunFn = Callable[..., Any]


@dataclass
class MemoryEvent:
    """Lo que pasó con la memoria en este turno, para el cliente."""

    status: str
    message: str  # texto que se añade a la respuesta
    proposal_id: Optional[str] = None
    content: Optional[str] = None  # solo textos ya validados sin PHI


@dataclass
class TurnResult:
    answer: str
    outcome: Optional[str]
    trace_id: Optional[str]
    resolved: Optional[MemoryEvent] = None  # decisión sobre la propuesta que estaba pendiente
    offered: Optional[MemoryEvent] = None  # propuesta nueva (o su bloqueo) de este turno


def sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _question_after_decision(classification: DecisionClassification, message: str) -> Optional[str]:
    """Qué queda por responder tras resolver la propuesta."""
    if classification.label == "unrelated":
        return message
    remaining = (classification.remaining_message or "").strip()
    return remaining or None


def _resolve_pending(
    store: MemoryStore,
    pending: AgentMemoryProposal,
    classification: DecisionClassification,
    *,
    user_id: str,
    message_sha: str,
) -> MemoryEvent:
    resolution = resolve_decision(classification)
    common = {
        "label": classification.label,
        "confidence": classification.confidence,
        "decision_message_sha256": message_sha,
    }

    if resolution in (Resolution.SAVE, Resolution.SAVE_EDITED):
        edited = resolution is Resolution.SAVE_EDITED
        content = (classification.edited_content if edited else pending.proposed_content) or ""
        try:
            result = store.commit(pending, content, approved_by=user_id)
        except PhiInMemoryError as exc:
            store.rollback()
            store.resolve(
                pending,
                status=PROPOSAL_BLOCKED_PHI,
                note="La versión a guardar contenía PHI",
                phi_categories=exc.verdict.categories,
                **common,
            )
            return MemoryEvent(MEMORY_BLOCKED_PHI, phi_guard.refusal_message(exc.verdict), str(pending.id))
        store.resolve(
            pending,
            status=PROPOSAL_APPROVED_EDITED if edited else PROPOSAL_APPROVED,
            final_content=result.memory.content,
            memory_id=result.memory.id,
            note=f"consolidation={result.action}",
            **common,
        )
        return MemoryEvent(
            MEMORY_SAVED,
            f"Hecho: lo he guardado en mi memoria. «{result.memory.content}»",
            str(pending.id),
            result.memory.content,
        )

    if resolution is Resolution.REJECT:
        store.resolve(pending, status=PROPOSAL_REJECTED, **common)
        return MemoryEvent(MEMORY_REJECTED, "De acuerdo, no lo guardo.", str(pending.id))

    note = "Cambio de tema" if classification.label == "unrelated" else "Respuesta ambigua o sin confianza suficiente"
    store.resolve(pending, status=PROPOSAL_DISCARDED, note=note, **common)
    text = (
        "He descartado la nota que te propuse antes: no la guardo porque no me respondiste sí o no."
        if classification.label == "unrelated"
        else "No he guardado la nota que te propuse: tu respuesta no fue un sí o un no claro. "
        "Si quieres que la recuerde, vuelve a contármelo."
    )
    return MemoryEvent(MEMORY_DISCARDED, text, str(pending.id))


def _offer_proposal(
    store: MemoryStore, run: Any, *, user_id: str, question: str, message_sha: str
) -> Optional[MemoryEvent]:
    """Valida la propuesta del grafo y, si procede, la deja pendiente.
    Devuelve el texto a añadir a la respuesta (o None si no hay nada).

    El mensaje del usuario se valida SIEMPRE, sin depender de que el modelo
    haya propuesto algo o marcado `user_requested_memory`: en la prueba real,
    ante "El paciente Johnson canceló su cita de mañana, apúntalo." el modelo
    no propuso ni marcó nada y respondió "anoto la cancelación". Un requisito
    de cumplimiento no puede depender de que el modelo rellene bien un campo."""
    proposal = run.memory_proposal
    message_verdict = phi_guard.scan(question)
    if message_verdict.contains_phi:
        asked = bool(proposal) or run.user_requested_memory
        verdict = phi_guard.scan(question, (proposal or {}).get("content") or "")
        store.record_blocked(
            user_id=user_id,
            kind=(proposal or {}).get("kind"),
            clinic=(proposal or {}).get("clinic"),
            phi_categories=verdict.categories,
            origin_message_sha256=message_sha,
            origin_trace_id=run.trace_id,
            note=(
                "Bloqueada por el validador de PHI antes de mostrarse"
                if asked
                else "Mensaje con PHI excluido de la memoria (sin propuesta del modelo)"
            ),
        )
        text = phi_guard.refusal_message(verdict) if asked else phi_guard.exclusion_notice(verdict)
        return MemoryEvent(MEMORY_BLOCKED_PHI, text)

    if proposal:
        verdict = phi_guard.scan(proposal.get("content") or "", question)
        if verdict.contains_phi:
            store.record_blocked(
                user_id=user_id,
                kind=proposal.get("kind"),
                clinic=proposal.get("clinic"),
                phi_categories=verdict.categories,
                origin_message_sha256=message_sha,
                origin_trace_id=run.trace_id,
            )
            return MemoryEvent(MEMORY_BLOCKED_PHI, phi_guard.refusal_message(verdict))
        try:
            pending = store.propose(
                user_id=user_id,
                kind=proposal["kind"],
                clinic=proposal.get("clinic"),
                content=proposal["content"],
                reason=proposal.get("reason"),
                origin_message_sha256=message_sha,
                origin_trace_id=run.trace_id,
            )
        except PendingProposalExists:
            # Otra petición simultánea dejó una pendiente: una sola a la vez.
            logger.info("Memory proposal skipped: another one is already pending")
            return None
        return MemoryEvent(
            MEMORY_PROPOSED,
            PROPOSAL_QUESTION.format(content=pending.proposed_content),
            str(pending.id),
            pending.proposed_content,
        )

    if run.user_requested_memory:
        # Pidió recordar algo que no es memorizable: se explica por qué.
        return MemoryEvent(MEMORY_NOT_MEMORABLE, NOT_MEMORABLE_MESSAGE)
    return None


def handle_turn(
    agent: Any,
    message: str,
    *,
    user_id: str,
    store: Optional[MemoryStore],
    classify_fn: ClassifyFn = classify_decision,
    run_fn: RunFn = run_agent,
) -> TurnResult:
    message = (message or "").strip()
    message_sha = sha256(message)
    resolved: Optional[MemoryEvent] = None
    question: Optional[str] = message

    if store is not None:
        try:
            pending = store.get_pending(user_id)
            if pending is not None:
                classification = classify_fn(pending.proposed_content or "", message)
                resolved = _resolve_pending(store, pending, classification, user_id=user_id, message_sha=message_sha)
                question = _question_after_decision(classification, message)
        except SQLAlchemyError as exc:
            logger.warning("Agent memory unavailable, answering without it: %s", type(exc).__name__)
            store.rollback()
            store = None

    if not question:  # el mensaje solo resolvía la propuesta pendiente
        return TurnResult(resolved.message, OUTCOME_MEMORY_DECISION, None, resolved=resolved)

    memories = []
    if store is not None:
        try:
            memories = as_evidence(store.recall(user_id))
        except SQLAlchemyError as exc:
            logger.warning("Agent memory unavailable, answering without it: %s", type(exc).__name__)
            store.rollback()
            store = None

    run = run_fn(agent, question, memories=memories)
    offered: Optional[MemoryEvent] = None
    if store is not None and run.answer:
        try:
            offered = _offer_proposal(store, run, user_id=user_id, question=question, message_sha=message_sha)
        except SQLAlchemyError as exc:
            logger.warning("Agent memory proposal could not be stored: %s", type(exc).__name__)
            store.rollback()

    parts = [resolved.message if resolved else None, run.answer, offered.message if offered else None]
    return TurnResult(
        answer="\n\n".join(part for part in parts if part),
        outcome=run.outcome,
        trace_id=run.trace_id,
        resolved=resolved,
        offered=offered,
    )
