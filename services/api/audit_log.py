"""Registro de auditoría de acciones de negocio (clase NIST, funciones Protect y Detect).

Una línea JSON por acción en el logger `healthcore.audit`: QUÉ pasó, sobre qué
id, quién (seudónimo) y cuándo. Nunca el contenido: el BRIEFING de
ciberseguridad §1 pide auditar "que ocurrió una acción, no el contenido
clínico". Por eso no se aceptan campos de texto libre (título, descripción,
mensaje): solo los nombres de `ALLOWED_FIELDS`, y un campo fuera de lista es
un error de programación que revienta en el test, no un dato que se cuela.

El actor va como seudónimo (SHA-256 truncado del id interno), el mismo
esquema que el monitor de guardarraíles: permite agrupar acciones de una
misma cuenta sin escribir su id ni su email.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("healthcore.audit")

ALLOWED_FIELDS = frozenset({"incident_id", "from_status", "to_status", "confirmed", "role"})


def actor_pseudonym(user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:12]


def audit_event(event: str, *, actor_id: Optional[str], **fields: Any) -> None:
    unknown = set(fields) - ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"Campos no permitidos en auditoría: {sorted(unknown)}")
    logger.info(
        json.dumps(
            {
                "event": event,
                "at": datetime.now(timezone.utc).isoformat(),
                "actor": actor_pseudonym(actor_id),
                **fields,
            },
            ensure_ascii=False,
        )
    )
