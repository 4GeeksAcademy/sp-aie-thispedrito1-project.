"""Consolidación y limpieza de la memoria del agente (Ticket #MEM-092).

La memoria no puede crecer sin límite. Cada escritura aprobada pasa por
`consolidate()`, que en una sola transacción:

1. Re-verifica que el texto final no contenga PHI (el CONTEXT exige hacerlo
   aquí también, no solo al proponer). Si la contiene, no escribe nada.
2. Deduplica: un hecho casi idéntico a uno activo (similitud >= DUPLICATE_THRESHOLD)
   no crea fila; renueva `last_confirmed_at` del existente.
3. Sustituye: un hecho parecido de la misma sede y tipo (>= SUPERSEDE_THRESHOLD)
   se interpreta como corrección; el anterior pasa a `superseded`, con
   `superseded_by` apuntando al nuevo. Así una corrección no convive con el
   dato que corrige.
4. Aplica el tope por ámbito y retira por PHI lo ya guardado (`sweep`).

La caducidad (`expire_stale`) corre también al leer: nada caducado llega al
modelo aunque no haya habido escrituras.

La similitud es Jaccard sobre palabras significativas, deliberadamente simple
(sin embeddings): con decenas de entradas por ámbito es suficiente, se puede
razonar a mano y no envía la memoria a un proveedor externo.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlmodel import Session, col, select

from services.agent.memory import phi_guard
from services.agent.memory.models import (
    KIND_STAFF_PREFERENCE,
    MEMORY_ACTIVE,
    MEMORY_EVICTED,
    MEMORY_EXPIRED,
    MEMORY_QUARANTINED,
    MEMORY_SUPERSEDED,
    SHARED_KINDS,
    AgentMemory,
    AgentMemoryProposal,
)

MAX_CONTENT_CHARS = 500
DUPLICATE_THRESHOLD = 0.85
SUPERSEDE_THRESHOLD = 0.5
# Tope por ámbito: hechos compartidos de toda la red y preferencias por persona.
MAX_SHARED_MEMORIES = 50
MAX_PREFERENCES_PER_USER = 10
# Un hecho operativo que nadie vuelve a confirmar en ~6 meses se considera
# viejo: los horarios y protocolos de clínica cambian por trimestres.
MEMORY_TTL = timedelta(days=180)

_STOPWORDS = frozenset(
    "a al ante con de del el en es la las lo los o para por que se su sus un una uno y "
    "ahora ya the of to and in on for is are at by".split()
)


class PhiInMemoryError(ValueError):
    """El texto a consolidar contiene PHI: no se escribe nada."""

    def __init__(self, verdict: phi_guard.PhiVerdict) -> None:
        super().__init__(f"PHI detected: {list(verdict.categories)}")
        self.verdict = verdict


@dataclass(frozen=True)
class ConsolidationResult:
    memory: AgentMemory
    action: str  # created | duplicate | superseded
    superseded_id: Optional[str] = None


def normalize_content(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:MAX_CONTENT_CHARS]


def _tokens(text: str) -> frozenset:
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    return frozenset(word for word in re.findall(r"[a-z0-9]+", plain) if word not in _STOPWORDS and len(word) > 1)


def similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _same_clinic(a: Optional[str], b: Optional[str]) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def _scope_filter(statement, kind: str, owner_user_id: Optional[str]):
    statement = statement.where(AgentMemory.kind == kind, AgentMemory.status == MEMORY_ACTIVE)
    if kind == KIND_STAFF_PREFERENCE:
        return statement.where(AgentMemory.owner_user_id == owner_user_id)
    return statement


def _set_status(memory: AgentMemory, status: str, now: datetime) -> None:
    memory.status = status
    memory.status_changed_at = now


def consolidate(
    session: Session,
    proposal: AgentMemoryProposal,
    content: str,
    *,
    approved_by: str,
    now: datetime,
) -> ConsolidationResult:
    """Escribe (o fusiona) un hecho aprobado. No hace commit: lo hace quien
    llama, junto con la resolución de la propuesta, en la misma transacción."""
    content = normalize_content(content)
    verdict = phi_guard.scan(content)
    if verdict.contains_phi:
        raise PhiInMemoryError(verdict)

    kind = proposal.kind
    owner = approved_by if kind == KIND_STAFF_PREFERENCE else None
    candidates = [
        memory
        for memory in session.exec(_scope_filter(select(AgentMemory), kind, owner)).all()
        if _same_clinic(memory.clinic, proposal.clinic)
    ]
    best: Optional[AgentMemory] = None
    best_score = 0.0
    for memory in candidates:
        score = similarity(memory.content, content)
        if score > best_score:
            best, best_score = memory, score

    if best is not None and best_score >= DUPLICATE_THRESHOLD:
        best.last_confirmed_at = now
        session.add(best)
        return ConsolidationResult(memory=best, action="duplicate")

    memory = AgentMemory(
        kind=kind,
        clinic=proposal.clinic,
        content=content,
        owner_user_id=owner,
        approved_by_user_id=approved_by,
        source_proposal_id=proposal.id,
        created_at=now,
        last_confirmed_at=now,
        status_changed_at=now,
    )
    session.add(memory)
    superseded_id = None
    if best is not None and best_score >= SUPERSEDE_THRESHOLD:
        _set_status(best, MEMORY_SUPERSEDED, now)
        best.superseded_by = memory.id
        session.add(best)
        superseded_id = str(best.id)
    session.flush()
    sweep(session, now=now)
    return ConsolidationResult(
        memory=memory, action="superseded" if superseded_id else "created", superseded_id=superseded_id
    )


def expire_stale(session: Session, *, now: datetime) -> int:
    """Caduca lo que nadie ha vuelto a confirmar en MEMORY_TTL."""
    cutoff = now - MEMORY_TTL
    stale = session.exec(
        select(AgentMemory).where(AgentMemory.status == MEMORY_ACTIVE, col(AgentMemory.last_confirmed_at) < cutoff)
    ).all()
    for memory in stale:
        _set_status(memory, MEMORY_EXPIRED, now)
        session.add(memory)
    return len(stale)


def as_utc(value: datetime) -> datetime:
    """SQLite devuelve las fechas sin zona (Postgres sí la conserva): se
    tratan siempre como UTC para poder compararlas con `now`."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _evict_over_cap(session: Session, memories: List[AgentMemory], cap: int, now: datetime) -> int:
    # Primero las más recientemente confirmadas; lo que sobra por detrás sale.
    ordered = sorted(memories, key=lambda m: (as_utc(m.last_confirmed_at), as_utc(m.created_at)), reverse=True)
    for memory in ordered[cap:]:
        _set_status(memory, MEMORY_EVICTED, now)
        session.add(memory)
    return max(0, len(ordered) - cap)


def sweep(session: Session, *, now: datetime) -> dict:
    """Limpieza completa: caducidad, re-verificación de PHI y topes."""
    expired = expire_stale(session, now=now)
    active = list(session.exec(select(AgentMemory).where(AgentMemory.status == MEMORY_ACTIVE)).all())

    quarantined = 0
    for memory in active:
        if phi_guard.scan(memory.content).contains_phi:
            _set_status(memory, MEMORY_QUARANTINED, now)
            session.add(memory)
            quarantined += 1
    active = [memory for memory in active if memory.status == MEMORY_ACTIVE]

    evicted = _evict_over_cap(session, [m for m in active if m.kind in SHARED_KINDS], MAX_SHARED_MEMORIES, now)
    owners = {m.owner_user_id for m in active if m.kind == KIND_STAFF_PREFERENCE}
    for owner in owners:
        preferences = [m for m in active if m.kind == KIND_STAFF_PREFERENCE and m.owner_user_id == owner]
        evicted += _evict_over_cap(session, preferences, MAX_PREFERENCES_PER_USER, now)
    return {"expired": expired, "quarantined": quarantined, "evicted": evicted}
