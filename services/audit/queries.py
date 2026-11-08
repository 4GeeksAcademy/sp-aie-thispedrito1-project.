"""Consultas de solo lectura sobre el registro de auditoría.

Paginación por cursor (`before_sequence`), no por OFFSET: con OFFSET la
página 500 obliga a la base de datos a recorrer y descartar 25.000 filas, y
una entrada nueva desplazaría las páginas mientras alguien las lee. Con el
cursor, "dame 50 anteriores a la #812" cuesta lo mismo en cualquier página.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional, Sequence

from sqlalchemy import func
from sqlmodel import Session, select

from services.audit.models import AuditLogEntry
from services.audit.writer import format_timestamp

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
VERIFY_BATCH = 500


@dataclass(frozen=True)
class AuditFilters:
    actor_type: Optional[str] = None
    actor_id: Optional[str] = None
    action: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    patient_ref: Optional[str] = None
    date_from: Optional[datetime] = None  # inclusivo
    date_to: Optional[datetime] = None  # exclusivo
    # Alcance: None = todos los departamentos (Admin). Lo pone el router a
    # partir de `permissions.department_scope`, nunca el cliente.
    department_id: Optional[str] = None
    # Alcance alternativo: solo estas refs (empleado, "mis pacientes").
    patient_refs: Optional[Sequence[str]] = None


@dataclass(frozen=True)
class AuditPage:
    items: list[AuditLogEntry]
    next_cursor: Optional[int]
    total: int


def _conditions(filters: AuditFilters) -> list:
    conditions = []
    if filters.actor_type:
        conditions.append(AuditLogEntry.actor_type == filters.actor_type)
    if filters.actor_id:
        conditions.append(AuditLogEntry.actor_id == filters.actor_id)
    if filters.action:
        conditions.append(AuditLogEntry.action == filters.action)
    if filters.resource_type:
        conditions.append(AuditLogEntry.resource_type == filters.resource_type)
    if filters.resource_id:
        conditions.append(AuditLogEntry.resource_id == filters.resource_id)
    if filters.patient_ref:
        conditions.append(AuditLogEntry.patient_ref == filters.patient_ref)
    if filters.date_from:
        conditions.append(AuditLogEntry.occurred_at >= format_timestamp(filters.date_from))
    if filters.date_to:
        conditions.append(AuditLogEntry.occurred_at < format_timestamp(filters.date_to))
    if filters.department_id is not None:
        conditions.append(AuditLogEntry.department_id == filters.department_id)
    if filters.patient_refs is not None:
        # Lista vacía = no ve nada (falla cerrado), nunca "sin filtro".
        conditions.append(AuditLogEntry.patient_ref.in_(list(filters.patient_refs) or ["__none__"]))
    return conditions


def page(
    session: Session,
    filters: AuditFilters,
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    before_sequence: Optional[int] = None,
) -> AuditPage:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    conditions = _conditions(filters)
    total = session.exec(select(func.count()).select_from(AuditLogEntry).where(*conditions)).one()

    statement = select(AuditLogEntry).where(*conditions)
    if before_sequence is not None:
        statement = statement.where(AuditLogEntry.sequence < before_sequence)
    # Una de más para saber si hay página siguiente sin otra consulta.
    rows = list(session.exec(statement.order_by(AuditLogEntry.sequence.desc()).limit(limit + 1)).all())
    has_more = len(rows) > limit
    items = rows[:limit]
    return AuditPage(items=items, next_cursor=items[-1].sequence if has_more and items else None, total=total)


def actors(session: Session, *, department_id: Optional[str]) -> list[tuple[str, Optional[str], int]]:
    """Actores distintos dentro del alcance, con su número de entradas."""
    conditions = _conditions(AuditFilters(department_id=department_id))
    statement = (
        select(AuditLogEntry.actor_type, AuditLogEntry.actor_id, func.count())
        .where(*conditions)
        .group_by(AuditLogEntry.actor_type, AuditLogEntry.actor_id)
        .order_by(func.count().desc())
    )
    return [(row[0], row[1], int(row[2])) for row in session.exec(statement).all()]


def iter_chain(session: Session, *, batch: int = VERIFY_BATCH) -> Iterator[AuditLogEntry]:
    """Toda la cadena en orden, por lotes (no carga la tabla entera en memoria)."""
    after = 0
    while True:
        rows = session.exec(
            select(AuditLogEntry)
            .where(AuditLogEntry.sequence > after)
            .order_by(AuditLogEntry.sequence)
            .limit(batch)
        ).all()
        if not rows:
            return
        yield from rows
        after = rows[-1].sequence
