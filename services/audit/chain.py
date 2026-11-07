"""Encadenamiento por hash: la evidencia de alteración (README, "Modelo").

Cada entrada guarda `prev_hash` (la huella de la anterior) y `entry_hash`
(SHA-256 de su propio contenido canónico, que INCLUYE `prev_hash`). Alterar
una entrada pasada deja una de estas huellas a la vista:

- se cambia un campo y no su hash → `tampered_entry` en esa misma entrada;
- se cambia un campo y se recalcula su hash → su sucesor ya no apunta a él:
  `broken_link` en la entrada siguiente;
- se borra una entrada del medio → falta su número: `missing_entry`.

Límite conocido (documentado): borrar las ÚLTIMAS entradas no rompe nada
visible desde dentro de la cadena. Se detecta comparando con una huella de
cabeza (`head_hash` + `checked`) anotada fuera de la base de datos; el
visor muestra ambas para que Compliance pueda guardarlas.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional

GENESIS_HASH = "0" * 64

# Campos que firma el hash, en este orden fijo. `entry_hash` no (es el resultado).
HASHED_FIELDS = (
    "sequence",
    "entry_id",
    "occurred_at",
    "actor_type",
    "actor_id",
    "on_behalf_of",
    "action",
    "resource_type",
    "resource_id",
    "patient_ref",
    "department_id",
    "context",
    "origin",
    "outcome",
    "details",
    "prev_hash",
)

BROKEN_TAMPERED = "tampered_entry"
BROKEN_LINK = "broken_link"
BROKEN_MISSING = "missing_entry"


def canonical_json(value: Any) -> str:
    """JSON determinista: mismas claves → mismo texto, en cualquier máquina."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_entry_hash(entry: Any) -> str:
    """Huella de una entrada (objeto con los atributos de `HASHED_FIELDS`)."""
    payload = {name: getattr(entry, name) for name in HASHED_FIELDS}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChainVerdict:
    ok: bool
    checked: int
    head_sequence: Optional[int]
    head_hash: str
    broken_at: Optional[int] = None
    reason: Optional[str] = None


def verify_chain(entries: Iterable[Any]) -> ChainVerdict:
    """Recorre las entradas EN ORDEN de `sequence` y para en la primera rotura.

    Recalcula cada huella en vez de fiarse de la guardada: es la única forma
    de detectar una entrada cuyo contenido cambió."""
    expected_sequence = 1
    expected_prev = GENESIS_HASH
    checked = 0
    head_sequence: Optional[int] = None

    for entry in entries:
        def broken(reason: str) -> ChainVerdict:
            return ChainVerdict(
                ok=False,
                checked=checked,
                head_sequence=head_sequence,
                head_hash=expected_prev,
                broken_at=entry.sequence,
                reason=reason,
            )

        if entry.sequence != expected_sequence:
            return broken(BROKEN_MISSING)
        if compute_entry_hash(entry) != entry.entry_hash:
            return broken(BROKEN_TAMPERED)
        if entry.prev_hash != expected_prev:
            return broken(BROKEN_LINK)

        checked += 1
        head_sequence = entry.sequence
        expected_prev = entry.entry_hash
        expected_sequence += 1

    return ChainVerdict(ok=True, checked=checked, head_sequence=head_sequence, head_hash=expected_prev)
