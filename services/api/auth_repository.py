from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from tinydb import Query
from tinydb.operations import delete

from access_models import department_id_for, role_id_for
from database import get_db
from models import UserRole


class AuthRepository:
    USERS_TABLE = "users"
    PROFILES_TABLE = "profiles"
    RESETS_TABLE = "password_resets"

    def __init__(self) -> None:
        self._db = get_db()
        self._users = self._db.table(self.USERS_TABLE)
        self._profiles = self._db.table(self.PROFILES_TABLE)
        self._resets = self._db.table(self.RESETS_TABLE)

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    def list_users(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._users.all()]

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        user = Query()
        found = self._users.get(user.id == user_id)
        return dict(found) if found else None

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        user = Query()
        found = self._users.get(user.email == self._normalize_email(email))
        return dict(found) if found else None

    def create_user(
        self,
        email: str,
        hashed_password: str,
        role: UserRole = UserRole.employee,
        is_active: bool = True,
        profile_data: dict[str, Any] | None = None,
        department: str | None = None,
        role_id: str | None = None,
        department_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Crea la cuenta con `role_id` y `department_id` como claves
        separadas (BRIEFING: ninguna se deriva de la otra).

        `role`/`department` son códigos de conveniencia para scripts y tests;
        `role_id`/`department_id` explícitos (POST /users de un admin) tienen
        prioridad. Sin departamento la cuenta no ve datos departamentales."""
        normalized_email = self._normalize_email(email)
        if self.get_user_by_email(normalized_email):
            raise ValueError("Email already registered")

        user_id = str(uuid4())
        user_record = {
            "id": user_id,
            "email": normalized_email,
            "hashed_password": hashed_password,
            "is_active": is_active,
            "role_id": role_id or role_id_for(UserRole(role).value),
            "department_id": department_id or (department_id_for(department) if department else None),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._users.insert(user_record)

        profile_payload = profile_data or {}
        profile_record = {
            "id": str(uuid4()),
            "user_id": user_id,
            "name": profile_payload.get("name"),
            "phone": profile_payload.get("phone"),
            "address": profile_payload.get("address"),
        }

        try:
            self._profiles.insert(profile_record)
        except Exception as exc:
            user = Query()
            self._users.remove(user.id == user_id)
            raise exc

        return user_record, profile_record

    def update_user(
        self,
        user_id: str,
        *,
        email: str | None = None,
    ) -> dict[str, Any] | None:
        user = Query()
        current = self._users.get(user.id == user_id)
        if not current:
            return None

        updates: dict[str, Any] = {}
        if email is not None:
            normalized_email = self._normalize_email(email)
            existing = self.get_user_by_email(normalized_email)
            if existing and existing["id"] != user_id:
                raise ValueError("Email already registered")
            updates["email"] = normalized_email

        if updates:
            self._users.update(updates, user.id == user_id)

        updated = self._users.get(user.id == user_id)
        return dict(updated) if updated else None

    def assign(
        self, user_id: str, *, role_id: str | None = None, department_id: str | None = None
    ) -> dict[str, Any] | None:
        """Cambia rol y/o departamento. Solo escribe lo que llega: cambiar uno
        deja el otro intacto. Al fijar `role_id` se retira el `role` antiguo
        (texto) para que no quede un segundo origen de verdad."""
        user = Query()
        current = self._users.get(user.id == user_id)
        if not current:
            return None
        updates: dict[str, Any] = {}
        if role_id is not None:
            updates["role_id"] = role_id
        if department_id is not None:
            updates["department_id"] = department_id
        if updates:
            self._users.update(updates, user.id == user_id)
        # update() mezcla campos, no borra: el texto antiguo se quita aparte.
        if role_id is not None and "role" in current:
            self._users.update(delete("role"), user.id == user_id)
        found = self._users.get(user.id == user_id)
        return dict(found) if found else None

    def delete_user(self, user_id: str) -> bool:
        user = Query()
        if not self._users.contains(user.id == user_id):
            return False

        profile = Query()
        self._profiles.remove(profile.user_id == user_id)
        self._users.remove(user.id == user_id)
        return True

    def set_password(self, user_id: str, hashed_password: str) -> dict[str, Any] | None:
        user = Query()
        if not self._users.contains(user.id == user_id):
            return None
        self._users.update({"hashed_password": hashed_password}, user.id == user_id)
        updated = self._users.get(user.id == user_id)
        return dict(updated) if updated else None

    def is_reset_token_used(self, jti: str) -> bool:
        reset = Query()
        return self._resets.contains(reset.jti == jti)

    def consume_reset_token(self, jti: str) -> None:
        self._resets.insert({"jti": jti, "used_at": datetime.now(timezone.utc).isoformat()})

    def get_profile_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        profile = Query()
        found = self._profiles.get(profile.user_id == user_id)
        return dict(found) if found else None

    def update_profile(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        profile = Query()
        current = self._profiles.get(profile.user_id == user_id)
        if not current:
            return None

        updates = {key: value for key, value in payload.items() if value is not None}
        if updates:
            self._profiles.update(updates, profile.user_id == user_id)

        updated = self._profiles.get(profile.user_id == user_id)
        return dict(updated) if updated else None
