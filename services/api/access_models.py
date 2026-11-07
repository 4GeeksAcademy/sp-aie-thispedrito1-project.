"""Roles y departamentos de HealthCore (BRIEFING de Roles y Permisos).

Dos ejes independientes, nunca una sola tabla de permisos:

- **Rol** (`roles`): qué puede HACER una persona. Jerarquía estricta por
  `rank`: employee (1) < supervisor (2) < admin (3). Las capacidades salen del
  rango (`CAPABILITY_MIN_RANK`), así que un rol nuevo creado por un admin con
  rango 2 tiene exactamente las capacidades de Supervisor.
- **Departamento** (`departments`): qué datos le COMPETEN. No da acciones.

Este módulo es la fuente única de los valores del BRIEFING (semilla literal)
y de los contratos de la API. Los ids son `uuid5` deterministas a partir del
código: la misma semilla da los mismos ids en cualquier TinyDB, y sembrar dos
veces no duplica nada.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

_NAMESPACE = uuid.UUID("6c1f3a52-8f4e-4c55-9d0f-3c3f0b6f2a10")

# --- Semilla literal del BRIEFING -------------------------------------------

ROLES_SEED = [
    {"code": "employee", "name": "Employee", "rank": 1},
    {"code": "supervisor", "name": "Supervisor", "rank": 2},
    {"code": "admin", "name": "Admin", "rank": 3},
]

DEPARTMENTS_SEED = [
    {"code": "clinical_operations", "name": "Clinical Operations"},
    {"code": "patient_experience", "name": "Patient Experience and Access"},
    {"code": "revenue_cycle", "name": "Revenue Cycle and Billing"},
    {"code": "compliance", "name": "Compliance and Data Governance"},
    {"code": "people_workforce", "name": "People and Workforce"},
    {"code": "technology", "name": "Technology"},
]

BASE_ROLE_CODES = frozenset(role["code"] for role in ROLES_SEED)
MIN_RANK = 1
MAX_RANK = 3

# Cuentas creadas antes de este ticket guardaban el rol como texto en
# `users.role` ("user"/"manager"/"admin"). Se traducen al leerlas; nunca se
# reescriben en bloque (el archivo de datos no cambia por un despliegue).
LEGACY_ROLE_ALIASES = {"user": "employee", "manager": "supervisor", "admin": "admin"}

# --- Capacidades: rango mínimo de cada una ----------------------------------
# Tabla explícita y documentada de la jerarquía (la que va en el PR).

CAP_RECORDS_READ = "records:read"
CAP_RECORDS_CREATE = "records:create"
CAP_RECORDS_UPDATE = "records:update"
CAP_RECORDS_APPROVE = "records:approve"
CAP_RECORDS_DELETE = "records:delete"
CAP_USERS_MANAGE = "users:manage"
CAP_ACCESS_MANAGE = "access:manage"  # roles y departamentos
CAP_DEPARTMENTS_ALL = "departments:read_all"  # visibilidad cruzada

CAPABILITY_MIN_RANK: dict[str, int] = {
    CAP_RECORDS_READ: 1,
    CAP_RECORDS_CREATE: 1,
    CAP_RECORDS_UPDATE: 2,
    CAP_RECORDS_APPROVE: 2,
    CAP_RECORDS_DELETE: 3,
    CAP_USERS_MANAGE: 3,
    CAP_ACCESS_MANAGE: 3,
    CAP_DEPARTMENTS_ALL: 3,
}

# El rol base que exige cada rango, para el cuerpo del 403 ("required_role").
RANK_ROLE_CODE = {role["rank"]: role["code"] for role in ROLES_SEED}

# Incidencias anteriores a este ticket no tienen `department_id`: se le
# asigna al leerlas según su categoría (decisión del usuario, 2026-11-07).
INCIDENT_CATEGORY_DEPARTMENT = {
    "patient_experience": "patient_experience",
    "billing_error": "revenue_cycle",
    "other": "clinical_operations",
}


def role_id_for(code: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"role:{code}"))


def department_id_for(code: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"department:{code}"))


def capabilities_for_rank(rank: int) -> list[str]:
    return [cap for cap, min_rank in CAPABILITY_MIN_RANK.items() if rank >= min_rank]


# --- Contratos de la API ----------------------------------------------------

CODE_PATTERN = r"^[a-z][a-z0-9_]{1,39}$"


class RoleRead(BaseModel):
    id: str
    code: str
    name: str
    rank: int
    capabilities: list[str]


class RoleCreate(BaseModel):
    code: str = Field(pattern=CODE_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    rank: int = Field(ge=MIN_RANK, le=MAX_RANK)


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    rank: Optional[int] = Field(default=None, ge=MIN_RANK, le=MAX_RANK)


class DepartmentRead(BaseModel):
    id: str
    code: str
    name: str


class DepartmentCreate(BaseModel):
    code: str = Field(pattern=CODE_PATTERN)
    name: str = Field(min_length=1, max_length=120)


class DepartmentUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AccessSummary(BaseModel):
    """Rol y departamento resueltos de una cuenta (GET /auth/me, listados)."""

    role: Optional[str]
    role_id: Optional[str]
    role_rank: int
    department: Optional[str]
    department_id: Optional[str]
    capabilities: list[str]


class UserAssignment(BaseModel):
    """PATCH /users/{id}/assignment: cada eje por separado. Lo que no viene
    no se toca (regla de independencia del BRIEFING)."""

    role_id: Optional[str] = None
    department_id: Optional[str] = None


class AdminUserCreate(BaseModel):
    """POST /users (solo admin). Rol y departamento obligatorios: toda cuenta
    pertenece a un departamento, también la de un admin."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    name: Optional[str] = Field(default=None, max_length=160)
    phone: Optional[str] = Field(default=None, max_length=40)
    address: Optional[str] = Field(default=None, max_length=300)
    role_id: str
    department_id: str


# --- Informes internos (colección canónica con alcance departamental) -------


class InternalReportCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)
    # Solo un admin puede elegirlo; para el resto es siempre el suyo.
    department_id: Optional[str] = None


class InternalReportUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    body: Optional[str] = Field(default=None, min_length=1, max_length=5000)


class InternalReportRead(BaseModel):
    id: str
    department_id: str
    department: Optional[str]
    title: str
    body: str
    created_by: str
    created_at: datetime
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
