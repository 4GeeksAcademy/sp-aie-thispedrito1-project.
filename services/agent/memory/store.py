"""Interfaz explícita de lectura/escritura de la memoria del agente.

Único punto que toca `agent_memories` y `agent_memory_proposals`. Nada del
agente guarda estado añadiéndolo al system prompt: el grafo recibe lo que
devuelve `recall()` como una fuente más (con forma de chunk) y la escritura
solo ocurre por `commit()`, que exige una propuesta pendiente del mismo
usuario y pasa por la consolidación.

Cada método que cambia algo NO hace commit por su cuenta salvo que se indique;
`MemoryStore.commit_transaction()` lo hace al final del turno, para que la
resolución de la propuesta y la escritura del recuerdo sean atómicas.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Sequence

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from services.agent.memory import consolidation
from services.agent.memory.models import (
    KIND_STAFF_PREFERENCE,
    MEMORY_ACTIVE,
    MEMORY_REVOKED,
    PROPOSAL_BLOCKED_PHI,
    PROPOSAL_EXPIRED,
    PROPOSAL_PENDING,
    SHARED_KINDS,
    AgentMemory,
    AgentMemoryProposal,
)

# Registro de auditoría en logs: una línea JSON por decisión, sin textos.
audit_logger = logging.getLogger("healthcore.agent.memory.audit")

RECALL_LIMIT = 20
# Una propuesta a la que nadie responde en este tiempo se descarta: si el
# usuario vuelve mañana, "sí" ya no puede aprobar algo de ayer por accidente.
PENDING_TTL = timedelta(minutes=30)


class PendingProposalExists(RuntimeError):
    """El índice único parcial rechazó una segunda propuesta pendiente."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStore:
    def __init__(self, session: Session, *, now_fn: Callable[[], datetime] = utcnow) -> None:
        self.session = session
        self.now_fn = now_fn

    # --- Lectura -------------------------------------------------------------

    def recall(self, user_id: str, *, limit: int = RECALL_LIMIT) -> List[AgentMemory]:
        """Recuerdos activos visibles para el usuario: los hechos compartidos
        de la red y sus propias preferencias. Caduca antes de leer."""
        consolidation.expire_stale(self.session, now=self.now_fn())
        statement = (
            select(AgentMemory)
            .where(AgentMemory.status == MEMORY_ACTIVE)
            .where(
                or_(
                    col(AgentMemory.kind).in_(SHARED_KINDS),
                    (AgentMemory.kind == KIND_STAFF_PREFERENCE) & (AgentMemory.owner_user_id == user_id),
                )
            )
            .order_by(col(AgentMemory.last_confirmed_at).desc())
            .limit(limit)
        )
        return list(self.session.exec(statement).all())

    def get_pending(self, user_id: str) -> Optional[AgentMemoryProposal]:
        """La propuesta pendiente del usuario, si sigue viva. Una caducada se
        cierra aquí como `expired` (nunca como aprobada)."""
        now = self.now_fn()
        pending = self.session.exec(
            select(AgentMemoryProposal).where(
                AgentMemoryProposal.user_id == user_id, AgentMemoryProposal.status == PROPOSAL_PENDING
            )
        ).first()
        if pending is None:
            return None
        if consolidation.as_utc(pending.created_at) < now - PENDING_TTL:
            self.resolve(pending, status=PROPOSAL_EXPIRED, note="Sin respuesta en el plazo")
            return None
        return pending

    def audit(self, *, user_id: Optional[str] = None, limit: int = 100) -> List[AgentMemoryProposal]:
        statement = select(AgentMemoryProposal).order_by(col(AgentMemoryProposal.created_at).desc()).limit(limit)
        if user_id is not None:
            statement = statement.where(AgentMemoryProposal.user_id == user_id)
        return list(self.session.exec(statement).all())

    # --- Escritura -----------------------------------------------------------

    def propose(
        self,
        *,
        user_id: str,
        kind: str,
        clinic: Optional[str],
        content: str,
        reason: Optional[str],
        origin_message_sha256: str,
        origin_trace_id: Optional[str],
    ) -> AgentMemoryProposal:
        """Registra una propuesta pendiente (no escribe memoria). Hace commit
        propio: si ya hay otra pendiente, la base de datos la rechaza."""
        proposal = AgentMemoryProposal(
            user_id=user_id,
            kind=kind,
            clinic=clinic,
            proposed_content=consolidation.normalize_content(content),
            reason=reason,
            origin_message_sha256=origin_message_sha256,
            origin_trace_id=origin_trace_id,
            created_at=self.now_fn(),
        )
        self.session.add(proposal)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise PendingProposalExists("Ya hay una propuesta de memoria pendiente para este usuario") from exc
        self.session.refresh(proposal)
        self._audit_log(proposal)
        return proposal

    def record_blocked(
        self,
        *,
        user_id: str,
        kind: Optional[str],
        clinic: Optional[str],
        phi_categories: Sequence[str],
        origin_message_sha256: str,
        origin_trace_id: Optional[str],
        note: str = "Bloqueada por el validador de PHI antes de mostrarse",
    ) -> AgentMemoryProposal:
        """Deja rastro de una propuesta que nunca se mostró por parecer PHI
        (o de un mensaje con PHI excluido de la memoria). Sin el texto: solo
        las categorías detectadas."""
        now = self.now_fn()
        proposal = AgentMemoryProposal(
            user_id=user_id,
            status=PROPOSAL_BLOCKED_PHI,
            kind=kind,
            clinic=clinic,
            proposed_content=None,
            phi_categories=list(phi_categories),
            origin_message_sha256=origin_message_sha256,
            origin_trace_id=origin_trace_id,
            created_at=now,
            resolved_at=now,
            decision_note=note,
        )
        self.session.add(proposal)
        self.session.commit()
        self._audit_log(proposal)
        return proposal

    def resolve(
        self,
        proposal: AgentMemoryProposal,
        *,
        status: str,
        label: Optional[str] = None,
        confidence: Optional[float] = None,
        decision_message_sha256: Optional[str] = None,
        note: Optional[str] = None,
        final_content: Optional[str] = None,
        memory_id: Optional[uuid.UUID] = None,
        phi_categories: Sequence[str] = (),
    ) -> AgentMemoryProposal:
        """Cierra una propuesta con la decisión. Siempre deja rastro, sea cual sea."""
        proposal.status = status
        proposal.decision_label = label
        proposal.decision_confidence = confidence
        proposal.decision_message_sha256 = decision_message_sha256
        proposal.decision_note = note
        proposal.final_content = final_content
        proposal.memory_id = memory_id
        if phi_categories:
            proposal.phi_categories = list(phi_categories)
        proposal.resolved_at = self.now_fn()
        self.session.add(proposal)
        self.session.commit()
        self._audit_log(proposal)
        return proposal

    def commit(
        self, proposal: AgentMemoryProposal, content: str, *, approved_by: str
    ) -> consolidation.ConsolidationResult:
        """Escribe un recuerdo aprobado, consolidando. Solo una propuesta
        PENDIENTE del mismo usuario puede escribir: nunca hay escritura sin
        una decisión registrada detrás. No hace commit (lo hace `resolve`)."""
        if proposal.status != PROPOSAL_PENDING or proposal.user_id != approved_by:
            raise PermissionError("Solo una propuesta pendiente del propio usuario puede escribir memoria")
        return consolidation.consolidate(self.session, proposal, content, approved_by=approved_by, now=self.now_fn())

    def revoke(self, memory_id: uuid.UUID) -> Optional[AgentMemory]:
        memory = self.session.get(AgentMemory, memory_id)
        if memory is None or memory.status != MEMORY_ACTIVE:
            return None
        memory.status = MEMORY_REVOKED
        memory.status_changed_at = self.now_fn()
        self.session.add(memory)
        self.session.commit()
        audit_logger.info(json.dumps({"event": "memory_revoked", "memory_id": str(memory_id)}))
        return memory

    def rollback(self) -> None:
        self.session.rollback()

    @staticmethod
    def _audit_log(proposal: AgentMemoryProposal) -> None:
        audit_logger.info(
            json.dumps(
                {
                    "event": "memory_proposal",
                    "proposal_id": str(proposal.id),
                    "user_id": proposal.user_id,
                    "status": proposal.status,
                    "kind": proposal.kind,
                    "decision_label": proposal.decision_label,
                    "decision_confidence": proposal.decision_confidence,
                    "phi_categories": list(proposal.phi_categories or []),
                    "memory_id": str(proposal.memory_id) if proposal.memory_id else None,
                }
            )
        )


def as_evidence(memories: Sequence[AgentMemory]) -> List[dict]:
    """Recuerdos → contexto con forma de chunk para el grafo (y para
    `rag.format_context`). Se presentan como notas del staff, no como política."""
    return [
        {
            "source_document": "agent-memory",
            "section": f"Nota aprobada por el staff ({memory.kind}"
            + (f", {memory.clinic}" if memory.clinic else "")
            + f", confirmada {consolidation.as_utc(memory.last_confirmed_at).date().isoformat()})",
            "text": memory.content,
            "memory_id": str(memory.id),
            "memory_kind": memory.kind,
            "clinic": memory.clinic,
        }
        for memory in memories
    ]
