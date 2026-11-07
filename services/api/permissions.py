"""Verificación centralizada de permisos (BRIEFING de Roles y Permisos).

Un único sitio decide el acceso, en dos pasos y en este orden:

1. **Rol** — `require_capability(cap)` es una dependencia de FastAPI. Si el
   rango del rol no alcanza la capacidad, 403 `insufficient_role` antes de
   ejecutar la ruta. Nunca un 200 con datos vacíos.
2. **Departamento** — `ensure_in_scope(user, department_id)` para un registro
   concreto (403 `department_out_of_scope`) y `department_scope(user)` para
   filtrar listados (alcance, no denegación).

Las rutas no comparan roles ni departamentos a mano: llaman a esto.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import Depends, HTTPException, status

from access_models import CAP_DEPARTMENTS_ALL, CAPABILITY_MIN_RANK, RANK_ROLE_CODE
from security import get_current_user

User = dict[str, Any]


def has_capability(user: User, capability: str) -> bool:
    return capability in user.get("capabilities", ())


def forbidden_role(user: User, capability: str) -> HTTPException:
    required_rank = CAPABILITY_MIN_RANK[capability]
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "insufficient_role",
            "message": f"Your role does not allow '{capability}'.",
            "capability": capability,
            "required_role": RANK_ROLE_CODE.get(required_rank),
            "current_role": user.get("role"),
        },
    )


def forbidden_department() -> HTTPException:
    # Sin el departamento del registro en el cuerpo: decir "es de
    # revenue_cycle" ya sería filtrar información a quien no la puede ver.
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "department_out_of_scope",
            "message": "This record belongs to a department outside your scope.",
        },
    )


def no_department() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "department_required",
            "message": "Your account has no department assigned.",
        },
    )


def require_capability(capability: str) -> Callable[..., User]:
    """Dependencia: `user = Depends(require_capability(CAP_RECORDS_UPDATE))`.

    Devuelve el usuario ya resuelto para que la ruta no tenga que pedirlo
    otra vez. Se valida la capacidad al declararla: un nombre mal escrito
    revienta al arrancar, no deja la ruta abierta."""
    if capability not in CAPABILITY_MIN_RANK:
        raise ValueError(f"Unknown capability: {capability}")

    def dependency(user: User = Depends(get_current_user)) -> User:
        if not has_capability(user, capability):
            raise forbidden_role(user, capability)
        return user

    dependency.__name__ = f"require_{capability.replace(':', '_')}"
    return dependency


def can_access_department(user: User, department_id: Optional[str]) -> bool:
    """¿Puede `user` ver o tocar un registro del departamento `department_id`?

    Learn by Doing delegado al asistente (2026-11-07). Decide SOLO el eje de
    departamento: el rol ya lo comprobó `require_capability` antes.

    - La visibilidad cruzada es una capacidad de rol (Admin), no un
      departamento vacío: un admin sigue teniendo el suyo y ve todos.
    - Un registro sin departamento no es "de todos": solo lo ve quien tiene
      visibilidad cruzada. Si no, un dato mal etiquetado sería público.
    - Una cuenta sin departamento no ve nada departamental (falla cerrado).
    """
    if has_capability(user, CAP_DEPARTMENTS_ALL):
        return True
    own = user.get("department_id")
    if not own or not department_id:
        return False
    return own == department_id


def ensure_in_scope(user: User, department_id: Optional[str]) -> None:
    if not can_access_department(user, department_id):
        raise forbidden_department()


def department_scope(user: User) -> Optional[str]:
    """Departamento por el que filtrar un listado; None = todos (Admin).

    Una cuenta sin departamento y sin visibilidad cruzada recibe un 403
    explícito: devolverle una lista vacía sería la "denegación silenciosa"
    que el BRIEFING prohíbe."""
    if has_capability(user, CAP_DEPARTMENTS_ALL):
        return None
    if not user.get("department_id"):
        raise no_department()
    return str(user["department_id"])


def own_department(user: User) -> str:
    """Departamento con el que se etiqueta un registro nuevo."""
    if not user.get("department_id"):
        raise no_department()
    return str(user["department_id"])
