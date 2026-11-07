from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session

from access_models import CAP_USERS_MANAGE, AdminUserCreate, UserAssignment
from audit_log import audit_event
from auth_repository import AuthRepository
from database import get_inventory_db
from models import UserListItem, UserPublic, UserRegistered, UserUpdate
from permissions import require_capability
from security import access_repo, ensure_self_or_admin, get_current_user, hash_password, resolve_access
from services.audit.writer import commit_record, human_actor, request_origin

router = APIRouter(prefix="/users", tags=["users"])
auth_repo = AuthRepository()

require_user_admin = require_capability(CAP_USERS_MANAGE)


def _field_errors(errors: list[dict[str, str]]) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"errors": errors})


def _validate_assignment_ids(role_id: str | None, department_id: str | None) -> None:
    errors = []
    if role_id is not None and not access_repo.get_role(role_id):
        errors.append({"field": "role_id", "message": "Unknown role."})
    if department_id is not None and not access_repo.get_department(department_id):
        errors.append({"field": "department_id", "message": "Unknown department."})
    if errors:
        raise _field_errors(errors)


def _audit(
    session: Session,
    request: Request,
    actor: dict[str, Any],
    action: str,
    target: dict[str, Any],
    **details: Any,
) -> None:
    """Registro de Auditoría: la entrada va al departamento de la cuenta
    AFECTADA, para que el Supervisor de ese departamento vea los cambios de
    su equipo. Nunca el email ni el nombre: solo ids y códigos."""
    commit_record(
        session,
        actor=human_actor(str(actor["id"]), request_origin(request)),
        action=action,
        resource_id=str(target["id"]),
        department_id=target.get("department_id"),
        details={"target_user": str(target["id"]), **details},
    )


def _list_item(user: dict[str, Any]) -> UserListItem:
    resolved = resolve_access(user)
    profile = auth_repo.get_profile_by_user_id(user["id"]) or {}
    return UserListItem.model_validate({**resolved, "name": profile.get("name")})


@router.post("", response_model=UserRegistered, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: AdminUserCreate,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user_admin),
    session: Session = Depends(get_inventory_db),
) -> UserRegistered:
    """Alta de cuentas: solo admin (BRIEFING de Roles y Permisos).

    Antes era un registro público que creaba rol `user`. Ahora rol y
    departamento son obligatorios y se eligen por separado. Sigue devolviendo
    solo la confirmación (ver docs/serialization-audit.md)."""
    _validate_assignment_ids(payload.role_id, payload.department_id)
    try:
        user, _profile = auth_repo.create_user(
            email=str(payload.email),
            hashed_password=hash_password(payload.password),
            role_id=payload.role_id,
            department_id=payload.department_id,
            profile_data={"name": payload.name, "phone": payload.phone, "address": payload.address},
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    resolved = resolve_access(user)
    _audit(
        session,
        request,
        current_user,
        "user_created",
        resolved,
        to_role=resolved["role"],
        to_department=resolved["department"],
    )
    return UserRegistered(id=user["id"], created_at=user["created_at"])


@router.get("", response_model=list[UserListItem])
def list_users(_: dict[str, Any] = Depends(require_user_admin)) -> list[UserListItem]:
    """Listado de cuentas para administrar accesos. Solo admin, sin emails."""
    return [_list_item(item) for item in auth_repo.list_users()]


@router.get("/{user_id}", response_model=UserPublic)
def get_user(user_id: str, current_user: dict[str, Any] = Depends(get_current_user)) -> UserPublic:
    # OWASP A01 (docs/security/owasp-top10-audit.md): antes cualquier
    # autenticado leía el email de cualquier cuenta. Mismo control que PUT.
    ensure_self_or_admin(user_id, current_user)
    found = auth_repo.get_user_by_id(user_id)
    if not found:
        raise HTTPException(status_code=404, detail="User not found")
    return UserPublic.model_validate(resolve_access(found))


@router.put("/{user_id}", response_model=UserPublic)
def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
    session: Session = Depends(get_inventory_db),
) -> UserPublic:
    ensure_self_or_admin(user_id, current_user)
    try:
        updated = auth_repo.update_user(user_id, email=str(payload.email) if payload.email else None)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if not updated:
        raise HTTPException(status_code=404, detail="User not found")

    resolved = resolve_access(updated)
    _audit(session, request, current_user, "user_updated", resolved, changed_fields=sorted(payload.model_dump(exclude_none=True)))
    return UserPublic.model_validate(resolved)


@router.patch("/{user_id}/assignment", response_model=UserListItem)
def assign_user(
    user_id: str,
    payload: UserAssignment,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user_admin),
    session: Session = Depends(get_inventory_db),
) -> UserListItem:
    """Cambia `role_id` y/o `department_id` de forma independiente.

    Lo que no llega en el cuerpo no se toca: cambiar el departamento de Tom
    no cambia su rol, y cambiar el rol de Marcus no cambia su departamento
    (prueba 4 del BRIEFING). Bajar al último admin se rechaza con 409: la
    plataforma se quedaría sin nadie capaz de administrarla."""
    if payload.role_id is None and payload.department_id is None:
        raise _field_errors([{"field": "assignment", "message": "Send role_id and/or department_id."}])
    _validate_assignment_ids(payload.role_id, payload.department_id)

    target = auth_repo.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    before = resolve_access(target)

    if payload.role_id is not None and CAP_USERS_MANAGE in before["capabilities"]:
        new_role = access_repo.get_role(payload.role_id)
        if new_role and CAP_USERS_MANAGE not in new_role["capabilities"]:
            admins = [u for u in auth_repo.list_users() if CAP_USERS_MANAGE in resolve_access(u)["capabilities"]]
            if len(admins) <= 1:
                raise HTTPException(status_code=409, detail="The platform must keep at least one admin.")

    updated = auth_repo.assign(user_id, role_id=payload.role_id, department_id=payload.department_id)
    after = resolve_access(updated or target)
    audit_event(
        "user_assignment_changed",
        actor_id=current_user.get("id"),
        target_user=user_id,
        from_role=before["role"],
        to_role=after["role"],
        from_department=before["department"],
        to_department=after["department"],
    )
    _audit(
        session,
        request,
        current_user,
        "user_assignment_changed",
        after,
        from_role=before["role"],
        to_role=after["role"],
        from_department=before["department"],
        to_department=after["department"],
    )
    return _list_item(updated or target)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user_admin),
    session: Session = Depends(get_inventory_db),
) -> Response:
    target = auth_repo.get_user_by_id(user_id)
    deleted = auth_repo.delete_user(user_id)
    if not deleted or not target:
        raise HTTPException(status_code=404, detail="User not found")
    resolved = resolve_access(target)
    _audit(
        session,
        request,
        current_user,
        "user_deleted",
        resolved,
        from_role=resolved["role"],
        from_department=resolved["department"],
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
