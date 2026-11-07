"""Administración de roles y departamentos (solo Admin).

`GET/POST /roles`, `GET/PUT /roles/{id}`, `GET/POST /departments`,
`GET/PUT /departments/{id}` (matriz de acceso del BRIEFING). Todo el router
exige la capacidad `access:manage`: un Empleado o un Supervisor reciben 403
aunque llamen directamente a la API.

No hay DELETE: el BRIEFING no lo pide, y borrar un rol o un departamento
dejaría cuentas e informes apuntando a algo que no existe.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from access_models import (
    BASE_ROLE_CODES,
    CAP_ACCESS_MANAGE,
    DepartmentCreate,
    DepartmentRead,
    DepartmentUpdate,
    RoleCreate,
    RoleRead,
    RoleUpdate,
)
from audit_log import audit_event
from database import get_inventory_db
from permissions import require_capability
from security import access_repo
from services.audit.writer import commit_record, human_actor, request_origin

require_access_admin = require_capability(CAP_ACCESS_MANAGE)

router = APIRouter(tags=["access"], dependencies=[Depends(require_access_admin)])


def _audit(session: Session, request: Request, user: dict[str, Any], action: str, resource_id: str, **details: Any) -> None:
    """Registro de Auditoría: cambios de roles y departamentos. Sin
    `department_id`: solo los ve un Admin (afectan a toda la plataforma)."""
    commit_record(
        session,
        actor=human_actor(str(user["id"]), request_origin(request)),
        action=action,
        resource_id=resource_id,
        details=details,
    )


@router.get("/roles", response_model=list[RoleRead])
def list_roles() -> list[RoleRead]:
    return [RoleRead.model_validate(role) for role in access_repo.list_roles()]


@router.post("/roles", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    request: Request,
    user: dict[str, Any] = Depends(require_access_admin),
    session: Session = Depends(get_inventory_db),
) -> RoleRead:
    """Un rol nuevo hereda las capacidades de su rango (1, 2 o 3)."""
    try:
        role = access_repo.create_role(code=payload.code, name=payload.name, rank=payload.rank)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_event("role_created", actor_id=user.get("id"), resource="role", resource_id=role["id"], to_role=role["code"])
    _audit(session, request, user, "role_created", role["id"], role_code=role["code"])
    return RoleRead.model_validate(role)


@router.get("/roles/{role_id}", response_model=RoleRead)
def get_role(role_id: str) -> RoleRead:
    role = access_repo.get_role(role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    return RoleRead.model_validate(role)


@router.put("/roles/{role_id}", response_model=RoleRead)
def update_role(
    role_id: str,
    payload: RoleUpdate,
    request: Request,
    user: dict[str, Any] = Depends(require_access_admin),
    session: Session = Depends(get_inventory_db),
) -> RoleRead:
    role = access_repo.get_role(role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    if payload.rank is not None and role["code"] in BASE_ROLE_CODES and payload.rank != role["rank"]:
        # La jerarquía employee < supervisor < admin es el contrato del BRIEFING.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The rank of a base role cannot change.")
    updates = payload.model_dump(exclude_none=True)
    updated = access_repo.update_role(role_id, updates)
    audit_event("role_updated", actor_id=user.get("id"), resource="role", resource_id=role_id, to_role=role["code"])
    _audit(session, request, user, "role_updated", role_id, role_code=role["code"], changed_fields=sorted(updates))
    return RoleRead.model_validate(updated)


@router.get("/departments", response_model=list[DepartmentRead])
def list_departments() -> list[DepartmentRead]:
    return [DepartmentRead.model_validate(item) for item in access_repo.list_departments()]


@router.post("/departments", response_model=DepartmentRead, status_code=status.HTTP_201_CREATED)
def create_department(
    payload: DepartmentCreate,
    request: Request,
    user: dict[str, Any] = Depends(require_access_admin),
    session: Session = Depends(get_inventory_db),
) -> DepartmentRead:
    try:
        department = access_repo.create_department(code=payload.code, name=payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_event(
        "department_created",
        actor_id=user.get("id"),
        resource="department",
        resource_id=department["id"],
        to_department=department["code"],
    )
    _audit(session, request, user, "department_created", department["id"], department_code=department["code"])
    return DepartmentRead.model_validate(department)


@router.get("/departments/{department_id}", response_model=DepartmentRead)
def get_department(department_id: str) -> DepartmentRead:
    department = access_repo.get_department(department_id)
    if not department:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    return DepartmentRead.model_validate(department)


@router.put("/departments/{department_id}", response_model=DepartmentRead)
def update_department(
    department_id: str,
    payload: DepartmentUpdate,
    request: Request,
    user: dict[str, Any] = Depends(require_access_admin),
    session: Session = Depends(get_inventory_db),
) -> DepartmentRead:
    updated = access_repo.update_department(department_id, name=payload.name)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    audit_event(
        "department_updated",
        actor_id=user.get("id"),
        resource="department",
        resource_id=department_id,
        to_department=updated["code"],
    )
    _audit(session, request, user, "department_updated", department_id, department_code=updated["code"], changed_fields=["name"])
    return DepartmentRead.model_validate(updated)
