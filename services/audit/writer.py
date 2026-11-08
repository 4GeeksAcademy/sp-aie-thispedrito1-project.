"""La única forma de escribir en el registro de auditoría: `record()`.

No hace commit: la entrada entra en la transacción de quien llama. Así una
modificación de datos y su entrada de auditoría se guardan juntas o no se
guarda ninguna (si falla el commit, no queda un cambio sin rastro).

Concurrencia: el siguiente eslabón depende del último, así que dos escrituras
no pueden calcularlo a la vez. En Postgres, `pg_advisory_xact_lock` serializa
hasta el commit (también entre la API y el worker de Celery, que son
procesos distintos). En SQLite (tests) basta un candado de proceso. Si aun
así dos escrituras compitieran, el índice único de `prev_hash` rechaza la
segunda en vez de bifurcar la cadena.
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from sqlalchemy import text
from sqlmodel import Session, select

from services.agent.memory import phi_guard
from services.audit import catalog
from services.audit.chain import GENESIS_HASH, canonical_json, compute_entry_hash
from services.audit.models import AuditLogEntry

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

_CHAIN_LOCK = threading.Lock()


def format_timestamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)


# --- Actores ---------------------------------------------------------------------


@dataclass(frozen=True)
class Actor:
    type: str
    id: Optional[str]
    origin: str
    on_behalf_of: Optional[str] = None


def human_actor(user_id: Optional[str], origin: str) -> Actor:
    return Actor(type=catalog.ACTOR_HUMAN, id=user_id, origin=origin)


def anonymous_actor(origin: str) -> Actor:
    return Actor(type=catalog.ACTOR_ANONYMOUS, id=None, origin=origin)


def process_actor(name: str, *, on_behalf_of: Optional[str] = None) -> Actor:
    """Un proceso o agente. Origen = su nombre + pid (README: "identificador de
    proceso"). Sin nombre de máquina: en un portátil suele llevar el nombre de
    su dueño."""
    return Actor(
        type=catalog.ACTOR_PROCESS,
        id=name,
        origin=f"process:{name}:pid{os.getpid()}",
        on_behalf_of=on_behalf_of,
    )


def request_origin(request: Any) -> str:
    """IP de quien hizo la petición.

    Detrás del proxy de Next.js (`/api-proxy`) el par TCP es 127.0.0.1; en ese
    caso, y SOLO si el par es un proxy de confianza, se usa el último salto de
    `X-Forwarded-For` (el que añadió nuestro proxy). Un cliente directo no puede
    falsear su IP mandando esa cabecera."""
    client = getattr(request, "client", None)
    peer = client.host if client else None
    trusted = {item.strip() for item in os.getenv("AUDIT_TRUSTED_PROXIES", "127.0.0.1,::1").split(",") if item.strip()}
    if peer in trusted:
        forwarded = request.headers.get("x-forwarded-for", "")
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return f"ip:{hops[-1]}"[:80]
    return f"ip:{peer or 'unknown'}"[:80]


# --- Validación ----------------------------------------------------------------------


def _validate_details(details: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(details) - catalog.ALLOWED_DETAIL_KEYS
    if unknown:
        raise ValueError(f"Detail keys not allowed in the audit log: {sorted(unknown)}")
    clean: dict[str, Any] = {}
    scannable: list[str] = []
    for key, value in details.items():
        if value is None or isinstance(value, (bool, int)):
            clean[key] = value
            continue
        if isinstance(value, str):
            items, clean[key] = [value], value
        elif isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
            if len(value) > catalog.MAX_DETAIL_LIST:
                raise ValueError(f"Detail '{key}' has too many items.")
            items, clean[key] = list(value), list(value)
        else:
            raise ValueError(f"Detail '{key}' has an unsupported type.")
        if any(len(item) > catalog.MAX_DETAIL_STRING for item in items):
            raise ValueError(f"Detail '{key}' is too long for the audit log.")
        pattern = catalog.OPAQUE_DETAIL_FORMATS.get(key)
        if pattern is not None:
            if not all(pattern.match(item) for item in items):
                raise ValueError(f"Detail '{key}' must be an opaque identifier.")
        else:
            scannable.extend(items)
    # Segunda capa (BRIEFING §4): aunque todo sean códigos, nada con forma de
    # PHI entra en el registro. Se informa la categoría, nunca el texto.
    verdict = phi_guard.scan(*scannable) if scannable else None
    if verdict is not None and verdict.contains_phi:
        raise ValueError(f"Audit details look like patient data: {', '.join(verdict.categories)}")
    return clean


def _validate(actor: Actor, action: str, patient_ref: Optional[str], context: Optional[str], outcome: str) -> str:
    if action not in catalog.ACTIONS:
        raise ValueError(f"Unknown audit action: {action}")
    if actor.type not in catalog.ACTOR_TYPES:
        raise ValueError(f"Unknown actor type: {actor.type}")
    if actor.type == catalog.ACTOR_PROCESS and actor.id not in catalog.PROCESS_ACTORS:
        raise ValueError(f"Unknown process actor: {actor.id}")
    if actor.type == catalog.ACTOR_HUMAN and not actor.id:
        raise ValueError("A human actor needs an account id.")
    if outcome not in catalog.OUTCOMES:
        raise ValueError(f"Unknown outcome: {outcome}")
    if context is not None and context not in catalog.ACCESS_PURPOSES:
        raise ValueError(f"Unknown access purpose: {context}")
    if patient_ref is not None and not catalog.PATIENT_REF_PATTERN.match(patient_ref):
        raise ValueError("patient_ref must be an opaque reference (PT-XXXXXXXX).")
    if action in catalog.PATIENT_ACTIONS and patient_ref is None:
        raise ValueError(f"Action {action} needs a patient_ref.")
    return catalog.ACTIONS[action]


# --- Escritura -------------------------------------------------------------------------


def _last_entry(session: Session) -> Optional[AuditLogEntry]:
    return session.exec(select(AuditLogEntry).order_by(AuditLogEntry.sequence.desc()).limit(1)).first()


def record(
    session: Session,
    *,
    actor: Actor,
    action: str,
    resource_id: Optional[str] = None,
    patient_ref: Optional[str] = None,
    department_id: Optional[str] = None,
    context: Optional[str] = None,
    outcome: str = catalog.OUTCOME_SUCCESS,
    details: Optional[Mapping[str, Any]] = None,
    occurred_at: Optional[datetime] = None,
) -> AuditLogEntry:
    """Añade una entrada al final de la cadena (sin commit).

    `occurred_at` solo lo usan la semilla y los tests para construir un
    histórico; nunca puede estar en el futuro. En la aplicación es siempre
    "ahora"."""
    resource_type = _validate(actor, action, patient_ref, context, outcome)
    clean_details = _validate_details(details or {})
    now = datetime.now(timezone.utc)
    moment = occurred_at or now
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if moment > now:
        raise ValueError("An audit entry cannot be dated in the future.")

    with _CHAIN_LOCK:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": catalog.AUDIT_CHAIN_LOCK_KEY})
        last = _last_entry(session)
        entry = AuditLogEntry(
            sequence=(last.sequence + 1) if last else 1,
            entry_id=str(uuid.uuid4()),
            occurred_at=format_timestamp(moment),
            actor_type=actor.type,
            actor_id=actor.id,
            on_behalf_of=actor.on_behalf_of,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            patient_ref=patient_ref,
            department_id=department_id,
            context=context,
            origin=actor.origin,
            outcome=outcome,
            details=canonical_json(clean_details),
            prev_hash=last.entry_hash if last else GENESIS_HASH,
            entry_hash="",
        )
        entry.entry_hash = compute_entry_hash(entry)
        session.add(entry)
        session.flush()
    return entry


def commit_record(session: Session, **kwargs: Any) -> AuditLogEntry:
    """`record` + commit, para acciones cuyo dato vive FUERA de Supabase
    (usuarios, roles y departamentos están en TinyDB): no comparten
    transacción, así que la entrada se confirma justo después del cambio."""
    entry = record(session, **kwargs)
    session.commit()
    return entry
