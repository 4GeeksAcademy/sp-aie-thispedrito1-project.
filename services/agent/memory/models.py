"""Tablas de la memoria del agente (Ticket #MEM-092), en Supabase.

Dos tablas, ninguna en Qdrant: la colección `healthcore_knowledge` del RAG
sigue siendo de solo lectura y este módulo no la importa (hay test que lo
comprueba). Registradas en el mismo `SQLModel.metadata` que inventario y
telemetría, así que el `create_all` del arranque de la API las crea.

- `agent_memories`: lo que el agente recuerda. Nada se borra: cada cambio
  (sustituida, caducada, expulsada por tope, retirada por PHI) es un cambio
  de `status` con fecha, para que siempre se pueda reconstruir qué sabía el
  agente en un momento dado y quién lo autorizó.
- `agent_memory_proposals`: el registro de auditoría. Una fila por propuesta,
  aprobada o no. Guarda el texto propuesto (ya validado sin PHI) y la
  decisión, pero de los mensajes del usuario solo su SHA-256: el mensaje
  crudo podría contener datos de pacientes. Si la propuesta se bloqueó por
  PHI, tampoco se guarda el texto propuesto, solo las categorías detectadas.

"Una sola propuesta pendiente por usuario" lo garantiza la base de datos con
un índice único parcial, no solo una consulta previa (mismo patrón que el lock
de `job_runs`): dos peticiones simultáneas no pueden dejar dos pendientes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import JSON, Column, DateTime, Index, Text, text
from sqlmodel import Field, SQLModel

# Tipos de hecho memorizables: exactamente las tres familias del CONTEXT.
KIND_CLINIC_OPERATIONS = "clinic_operations"  # horarios, protocolos locales, excepciones US/UK
KIND_INCIDENT_PATTERN = "incident_pattern"  # patrones de incidentes, sin datos de paciente
KIND_STAFF_PREFERENCE = "staff_preference"  # cómo quiere un miembro del staff la información
MEMORY_KINDS = (KIND_CLINIC_OPERATIONS, KIND_INCIDENT_PATTERN, KIND_STAFF_PREFERENCE)
# Los hechos operativos se comparten con todo el staff (es el objetivo del
# ticket: que otra persona no repita la corrección); las preferencias no.
SHARED_KINDS = (KIND_CLINIC_OPERATIONS, KIND_INCIDENT_PATTERN)

# agent_memories.status
MEMORY_ACTIVE = "active"
MEMORY_SUPERSEDED = "superseded"  # una corrección posterior la sustituyó
MEMORY_EXPIRED = "expired"  # sin confirmar durante MEMORY_TTL
MEMORY_EVICTED = "evicted"  # expulsada por el tope de su ámbito
MEMORY_QUARANTINED = "quarantined"  # la re-verificación de PHI la marcó
MEMORY_REVOKED = "revoked"  # retirada por un admin

# agent_memory_proposals.status
PROPOSAL_PENDING = "pending"
PROPOSAL_APPROVED = "approved"
PROPOSAL_APPROVED_EDITED = "approved_edited"
PROPOSAL_REJECTED = "rejected"
PROPOSAL_DISCARDED = "discarded"  # ambigua, cambio de tema o clasificador caído
PROPOSAL_EXPIRED = "expired"  # nadie respondió a tiempo
PROPOSAL_BLOCKED_PHI = "blocked_phi"  # nunca se mostró: parecía PHI


def _utc_column(nullable: bool = False) -> Column:
    return Column(DateTime(timezone=True), nullable=nullable)


class AgentMemory(SQLModel, table=True):
    __tablename__ = "agent_memories"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    kind: str = Field(max_length=32, index=True)
    # Sede a la que se refiere (texto libre corto, p. ej. "Manchester"); None = toda la red.
    clinic: Optional[str] = Field(default=None, max_length=64)
    content: str = Field(sa_column=Column(Text, nullable=False))
    # Solo para staff_preference: de quién es la preferencia. None = compartida.
    owner_user_id: Optional[str] = Field(default=None, max_length=64, index=True)
    status: str = Field(default=MEMORY_ACTIVE, max_length=16, index=True)
    approved_by_user_id: str = Field(max_length=64)
    source_proposal_id: uuid.UUID
    superseded_by: Optional[uuid.UUID] = None
    created_at: datetime = Field(sa_column=_utc_column())
    # Última vez que alguien volvió a aprobar este mismo hecho: la caducidad
    # cuenta desde aquí, no desde la creación.
    last_confirmed_at: datetime = Field(sa_column=_utc_column())
    status_changed_at: datetime = Field(sa_column=_utc_column())


class AgentMemoryProposal(SQLModel, table=True):
    __tablename__ = "agent_memory_proposals"
    __table_args__ = (
        Index(
            "uq_agent_memory_proposals_one_pending_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: str = Field(max_length=64, index=True)
    status: str = Field(default=PROPOSAL_PENDING, max_length=16, index=True)
    kind: Optional[str] = Field(default=None, max_length=32)
    clinic: Optional[str] = Field(default=None, max_length=64)
    # None si se bloqueó por PHI: el texto no se guarda ni en auditoría.
    proposed_content: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    reason: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    # Lo que finalmente se guardó (igual al propuesto, o la edición del usuario).
    final_content: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    phi_categories: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    origin_message_sha256: str = Field(max_length=64)
    origin_trace_id: Optional[str] = Field(default=None, max_length=64)
    created_at: datetime = Field(sa_column=_utc_column())
    # Decisión del usuario, clasificada (ver decision.py).
    decision_label: Optional[str] = Field(default=None, max_length=16)
    decision_confidence: Optional[float] = None
    decision_message_sha256: Optional[str] = Field(default=None, max_length=64)
    decision_note: Optional[str] = Field(default=None, max_length=255)
    resolved_at: Optional[datetime] = Field(default=None, sa_column=_utc_column(nullable=True))
    memory_id: Optional[uuid.UUID] = None


def ensure_memory_tables(engine) -> None:
    """Para scripts que no pasan por el arranque de la API."""
    AgentMemory.__table__.create(engine, checkfirst=True)
    AgentMemoryProposal.__table__.create(engine, checkfirst=True)
