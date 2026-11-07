"""Persistencia de la autenticación federada (TinyDB, junto a `users`).

Los usuarios viven en TinyDB (decisión de techContext), así que el vínculo
también: una clave foránea entre bases distintas no se podría garantizar.

- `federated_identities`: el vínculo usuario ↔ identidad Microsoft (BRIEFING
  §5). Cero, uno o varios por usuario (uno por proveedor). Sin tokens.
- `oauth_states`: peticiones de autorización pendientes. Se guarda la
  HUELLA del `state` (SHA-256), no el valor: quien lea la base no puede
  completar un flujo ajeno. Un solo uso, con caducidad.
- `federated_audit_events`: traza consultable de los eventos del BRIEFING §7.

TinyDB no tiene índices únicos: la unicidad de (provider, provider_user_id)
se garantiza comprobando y escribiendo bajo `_LINK_LOCK`, en un solo
proceso. Con varios workers de uvicorn haría falta una base con índice único
(mismo residual que documenta `LockedJSONStorage`).
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from tinydb import Query

from database import get_db

_LINK_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()


class IdentityAlreadyLinked(Exception):
    """La identidad externa ya pertenece a otra cuenta."""


class ProviderAlreadyLinked(Exception):
    """La cuenta ya tiene otra identidad de ese proveedor."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def state_fingerprint(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


class FederatedRepository:
    LINKS_TABLE = "federated_identities"
    STATES_TABLE = "oauth_states"
    AUDIT_TABLE = "federated_audit_events"

    def __init__(self) -> None:
        db = get_db()
        self._links = db.table(self.LINKS_TABLE)
        self._states = db.table(self.STATES_TABLE)
        self._audit = db.table(self.AUDIT_TABLE)

    # --- Vínculos -------------------------------------------------------------

    def find_link(self, provider: str, provider_user_id: str) -> Optional[dict[str, Any]]:
        link = Query()
        found = self._links.get((link.provider == provider) & (link.provider_user_id == provider_user_id))
        return dict(found) if found else None

    def links_for_user(self, user_id: str) -> list[dict[str, Any]]:
        link = Query()
        rows = [dict(item) for item in self._links.search(link.user_id == user_id)]
        return sorted(rows, key=lambda row: row["linked_at"])

    def create_link(
        self,
        *,
        user_id: str,
        provider: str,
        provider_user_id: str,
        email_at_link_time: Optional[str],
        provisioned_by_it: bool,
    ) -> tuple[dict[str, Any], bool]:
        """(vínculo, creado). Idempotente si ya era de esta misma cuenta."""
        with _LINK_LOCK:
            existing = self.find_link(provider, provider_user_id)
            if existing:
                if existing["user_id"] != user_id:
                    raise IdentityAlreadyLinked()
                return existing, False
            if any(row["provider"] == provider for row in self.links_for_user(user_id)):
                raise ProviderAlreadyLinked()
            record = {
                "id": str(uuid4()),
                "user_id": user_id,
                "provider": provider,
                "provider_user_id": provider_user_id,
                "email_at_link_time": email_at_link_time,
                "linked_at": _now().isoformat(),
                "provisioned_by_it": provisioned_by_it,
            }
            self._links.insert(record)
            return record, True

    def delete_link(self, user_id: str, provider: str) -> Optional[dict[str, Any]]:
        link = Query()
        with _LINK_LOCK:
            found = self._links.get((link.user_id == user_id) & (link.provider == provider))
            if not found:
                return None
            self._links.remove((link.user_id == user_id) & (link.provider == provider))
            return dict(found)

    # --- state de OAuth -------------------------------------------------------

    def save_state(
        self,
        *,
        state: str,
        purpose: str,
        provider: str,
        user_id: Optional[str],
        code_verifier: str,
        nonce: str,
        redirect_uri: str,
        ttl_seconds: int,
    ) -> None:
        now = _now()
        with _STATE_LOCK:
            row = Query()
            # Limpieza perezosa: los states caducados no se acumulan.
            self._states.remove(row.expires_at < now.isoformat())
            self._states.insert(
                {
                    "state_hash": state_fingerprint(state),
                    "purpose": purpose,
                    "provider": provider,
                    "user_id": user_id,
                    "code_verifier": code_verifier,
                    "nonce": nonce,
                    "redirect_uri": redirect_uri,
                    "created_at": now.isoformat(),
                    "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
                }
            )

    def consume_state(self, state: str) -> Optional[dict[str, Any]]:
        """Devuelve y BORRA el state (un solo uso), o None si no existe o caducó."""
        if not state:
            return None
        row = Query()
        fingerprint = state_fingerprint(state)
        with _STATE_LOCK:
            found = self._states.get(row.state_hash == fingerprint)
            if not found:
                return None
            self._states.remove(row.state_hash == fingerprint)
        record = dict(found)
        if record["expires_at"] < _now().isoformat():
            return None
        return record

    # --- Auditoría --------------------------------------------------------------

    def record_event(
        self,
        event: str,
        *,
        user_id: Optional[str],
        provider: str,
        provider_user_id: Optional[str],
        reason: Optional[str] = None,
    ) -> dict[str, Any]:
        record = {
            "id": str(uuid4()),
            "event": event,
            "at": _now().isoformat(),
            "user_id": user_id,
            "provider": provider,
            "provider_user_id": provider_user_id,
            "reason": reason,
        }
        self._audit.insert(record)
        return record

    def list_events(self, *, event: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
        rows = [dict(item) for item in self._audit.all()]
        if event:
            rows = [row for row in rows if row["event"] == event]
        rows.sort(key=lambda row: row["at"], reverse=True)
        return rows[:limit]
