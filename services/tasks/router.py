"""GET /tasks/{task_id}: el cliente consulta cuándo quiera el estado de la
tarea que le devolvió un 202. Solo HTTP; la lectura vive en status.py."""

from __future__ import annotations

from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError

from security import get_current_user, require_admin
from services.tasks.schemas import TaskStatusResponse
from services.tasks.status import read_task_snapshot

router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(get_current_user)])


@router.get("/{task_id}", response_model=TaskStatusResponse)
def get_task_status(task_id: UUID, current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Solo admin, igual que POST /reporting/pipeline-runs, el único endpoint
    que hoy encola tareas: quien no puede lanzarlas tampoco las consulta.

    `task_id: UUID` rechaza con 422 cualquier id que Celery no pudo generar.
    Un UUID válido pero desconocido devuelve `pending`: Celery no guarda las
    tareas que nunca vio y no puede distinguirlas de una en cola."""
    require_admin(current_user)
    try:
        return read_task_snapshot(str(task_id))
    except (RedisError, OperationalError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Task backend unavailable. Try again later.") from exc
