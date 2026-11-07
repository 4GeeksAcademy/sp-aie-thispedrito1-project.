"""Acceso a datos de roles, departamentos e informes internos (TinyDB).

Viven en la misma TinyDB que `users`, porque `User.role_id` y
`User.department_id` son claves foráneas hacia estas tablas (decisión ya
registrada: User/Profile viven solo en TinyDB).

El catálogo del BRIEFING se siembra solo y de forma perezosa
(`ensure_catalog`): cualquier TinyDB nueva —incluida la temporal de cada
test— tiene los 3 roles y los 6 departamentos sin pasos manuales.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from tinydb import Query

from access_models import (
    DEPARTMENTS_SEED,
    INCIDENT_CATEGORY_DEPARTMENT,
    LEGACY_ROLE_ALIASES,
    ROLES_SEED,
    capabilities_for_rank,
    department_id_for,
    role_id_for,
)
from database import get_db


class AccessRepository:
    ROLES_TABLE = "roles"
    DEPARTMENTS_TABLE = "departments"
    REPORTS_TABLE = "internal_reports"

    def __init__(self) -> None:
        db = get_db()
        self._roles = db.table(self.ROLES_TABLE)
        self._departments = db.table(self.DEPARTMENTS_TABLE)
        self._reports = db.table(self.REPORTS_TABLE)

    # --- Catálogo ---------------------------------------------------------

    def ensure_catalog(self) -> None:
        """Siembra roles y departamentos del BRIEFING si faltan (idempotente:
        ids deterministas, solo inserta los que no existen)."""
        item = Query()
        if len(self._roles) < len(ROLES_SEED):
            for role in ROLES_SEED:
                role_id = role_id_for(role["code"])
                if not self._roles.contains(item.id == role_id):
                    self._roles.insert({"id": role_id, **role})
        if len(self._departments) < len(DEPARTMENTS_SEED):
            for department in DEPARTMENTS_SEED:
                department_id = department_id_for(department["code"])
                if not self._departments.contains(item.id == department_id):
                    self._departments.insert({"id": department_id, **department})

    # --- Roles ------------------------------------------------------------

    def list_roles(self) -> list[dict[str, Any]]:
        self.ensure_catalog()
        roles = [self._role_entity(doc) for doc in self._roles.all()]
        return sorted(roles, key=lambda role: (role["rank"], role["code"]))

    def get_role(self, role_id: str) -> Optional[dict[str, Any]]:
        self.ensure_catalog()
        found = self._roles.get(Query().id == role_id)
        return self._role_entity(found) if found else None

    def get_role_by_code(self, code: str) -> Optional[dict[str, Any]]:
        self.ensure_catalog()
        found = self._roles.get(Query().code == code)
        return self._role_entity(found) if found else None

    def create_role(self, *, code: str, name: str, rank: int) -> dict[str, Any]:
        if self.get_role_by_code(code):
            raise ValueError("Role code already exists")
        record = {"id": str(uuid4()), "code": code, "name": name, "rank": rank}
        self._roles.insert(record)
        return self._role_entity(record)

    def update_role(self, role_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not self.get_role(role_id):
            return None
        if updates:
            self._roles.update(updates, Query().id == role_id)
        return self.get_role(role_id)

    @staticmethod
    def _role_entity(doc: Any) -> dict[str, Any]:
        role = dict(doc)
        role["capabilities"] = capabilities_for_rank(int(role["rank"]))
        return role

    # --- Departamentos ----------------------------------------------------

    def list_departments(self) -> list[dict[str, Any]]:
        self.ensure_catalog()
        return [dict(doc) for doc in self._departments.all()]

    def get_department(self, department_id: str) -> Optional[dict[str, Any]]:
        self.ensure_catalog()
        found = self._departments.get(Query().id == department_id)
        return dict(found) if found else None

    def get_department_by_code(self, code: str) -> Optional[dict[str, Any]]:
        self.ensure_catalog()
        found = self._departments.get(Query().code == code)
        return dict(found) if found else None

    def create_department(self, *, code: str, name: str) -> dict[str, Any]:
        if self.get_department_by_code(code):
            raise ValueError("Department code already exists")
        record = {"id": str(uuid4()), "code": code, "name": name}
        self._departments.insert(record)
        return record

    def update_department(self, department_id: str, *, name: str) -> Optional[dict[str, Any]]:
        if not self.get_department(department_id):
            return None
        self._departments.update({"name": name}, Query().id == department_id)
        return self.get_department(department_id)

    def department_code(self, department_id: Optional[str]) -> Optional[str]:
        if not department_id:
            return None
        found = self.get_department(department_id)
        return found["code"] if found else None

    def incident_department_id(self, incident: dict[str, Any]) -> Optional[str]:
        """Departamento de una incidencia: el suyo si lo tiene; si es anterior
        a este ticket, el que corresponde a su categoría."""
        if incident.get("department_id"):
            return str(incident["department_id"])
        code = INCIDENT_CATEGORY_DEPARTMENT.get(str(incident.get("category", "")))
        return department_id_for(code) if code else None

    # --- Resolución de una cuenta -----------------------------------------

    def resolve_user_access(self, user: dict[str, Any]) -> dict[str, Any]:
        """Copia del usuario con su rol y departamento resueltos.

        Rellena `role` (código), `role_id`, `role_rank`, `capabilities`,
        `department` (código) y `department_id`. Los dos ejes se resuelven
        por separado: ninguno se deriva del otro.

        Falla cerrado: un `role_id` que ya no existe o un rol antiguo
        desconocido dejan la cuenta sin rol (rango 0, sin capacidades), así
        que cualquier ruta protegida responde 403 en vez de adivinar.
        """
        resolved = dict(user)
        role: Optional[dict[str, Any]] = None
        if user.get("role_id"):
            role = self.get_role(str(user["role_id"]))
        elif user.get("role"):
            legacy_code = LEGACY_ROLE_ALIASES.get(str(user["role"]))
            role = self.get_role_by_code(legacy_code) if legacy_code else None

        resolved["role"] = role["code"] if role else None
        resolved["role_id"] = role["id"] if role else None
        resolved["role_rank"] = int(role["rank"]) if role else 0
        resolved["capabilities"] = role["capabilities"] if role else []

        department = self.get_department(str(user["department_id"])) if user.get("department_id") else None
        resolved["department"] = department["code"] if department else None
        resolved["department_id"] = department["id"] if department else None
        return resolved

    # --- Informes internos ------------------------------------------------

    def list_reports(self, department_id: Optional[str] = None) -> list[dict[str, Any]]:
        docs = self._reports.all()
        reports = [dict(doc) for doc in docs if department_id is None or doc.get("department_id") == department_id]
        return sorted(reports, key=lambda report: str(report.get("created_at", "")), reverse=True)

    def get_report(self, report_id: str) -> Optional[dict[str, Any]]:
        found = self._reports.get(Query().id == report_id)
        return dict(found) if found else None

    def create_report(self, *, department_id: str, title: str, body: str, created_by: str) -> dict[str, Any]:
        record = {
            "id": str(uuid4()),
            "department_id": department_id,
            "title": title,
            "body": body,
            "created_by": created_by,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "approved_by": None,
            "approved_at": None,
        }
        self._reports.insert(record)
        return record

    def update_report(self, report_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not self.get_report(report_id):
            return None
        if updates:
            self._reports.update(updates, Query().id == report_id)
        return self.get_report(report_id)

    def delete_report(self, report_id: str) -> bool:
        return bool(self._reports.remove(Query().id == report_id))

    def find_report_by_title(self, department_id: str, title: str) -> Optional[dict[str, Any]]:
        item = Query()
        found = self._reports.get((item.department_id == department_id) & (item.title == title))
        return dict(found) if found else None
