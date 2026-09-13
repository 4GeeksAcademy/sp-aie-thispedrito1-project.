"""Dead Letter Queue: tareas que agotaron sus reintentos (Ticket #DEV-55).

No es una cola de Redis sino una tabla en Supabase (`task_dead_letters`):
el ticket pide que el fallo quede "registrado en base de datos", y una
tabla sobrevive a un reinicio de Redis y se puede consultar con SQL.

Registrada en el mismo SQLModel.metadata que inventario y telemetría, así
que el `create_all` del arranque de la API la crea. El worker no pasa por
ese arranque: `record_dead_letter` la crea también si falta (checkfirst).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import JSON, Column, DateTime, Text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, select

from services.tasks.redaction import describe_error

logger = logging.getLogger("healthcore.tasks")


class TaskDeadLetter(SQLModel, table=True):
    """Una fila por tarea que falló definitivamente."""

    __tablename__ = "task_dead_letters"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # Único: una tarea solo muere una vez. Si el aviso llegara duplicado
    # (reentrega del mensaje), la segunda inserción se ignora.
    task_id: str = Field(max_length=255, unique=True, index=True)
    task_name: str = Field(max_length=255)
    # Número de ejecución en la que falló por última vez (1 = sin reintentos).
    attempt: int
    error_type: str = Field(max_length=255)
    error_message: str = Field(sa_column=Column(Text, nullable=False))
    # Solo identificadores (run_id, mes): lo justo para relanzar a mano.
    task_kwargs: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    failed_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


def ensure_dead_letter_table(engine) -> None:
    TaskDeadLetter.__table__.create(engine, checkfirst=True)


def record_dead_letter(
    engine,
    *,
    task_id: str,
    task_name: str,
    attempt: int,
    error: BaseException,
    task_kwargs: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Inserta la tarea en la DLQ. Devuelve False si ya estaba registrada."""
    ensure_dead_letter_table(engine)
    with Session(engine) as session:
        session.add(
            TaskDeadLetter(
                task_id=task_id,
                task_name=task_name,
                attempt=attempt,
                error_type=type(error).__name__,
                error_message=describe_error(error),
                task_kwargs=dict(task_kwargs or {}),
                failed_at=now or datetime.now(timezone.utc),
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            logger.info("dead_letter_already_recorded task_id=%s", task_id)
            return False
    return True


def get_dead_letter(engine, task_id: str) -> Optional[TaskDeadLetter]:
    with Session(engine) as session:
        return session.exec(select(TaskDeadLetter).where(TaskDeadLetter.task_id == task_id)).first()
