"""Catálogo de departamentos de la propuesta (BRIEFING del Hito 9, §2.1).

Es la única fuente de los `department_id`, sus responsables y lo que aporta
cada uno. El orquestador solo puede repartir trabajo entre estos tres; lo
que una RFP pida y no encaje aquí se registra como `unmapped_departments`
en vez de inventar un departamento nuevo (pregunta de diseño 1).

Las reglas que el BRIEFING declara sin excepción viven en código y no en un
prompt, para que no dependan de que el modelo las respete:
- `compliance` participa en todas las RFPs (BRIEFING §2.1).
- `revenue` también: es el dueño del ticket y toda propuesta se cotiza en la
  moneda del país (§5). La prueba con el modelo real lo demostró: con la RFP
  informal de Thames Valley el orquestador no lo pidió, y el BRIEFING dice
  que esa RFP activa los tres departamentos.
- El país del cliente fija el instrumento regulatorio y la moneda (§5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class Department:
    department_id: str
    name: str
    owner: str
    contributes: str


DEPARTMENTS: Dict[str, Department] = {
    "revenue": Department(
        "revenue",
        "Revenue Cycle",
        "Tom Callahan",
        "Términos financieros, moneda y estructura de pago. Dueño del ticket.",
    ),
    "clinical": Department(
        "clinical",
        "Clinical Operations",
        "Dr. Marcus Reid",
        "Viabilidad clínica: qué clínicas y qué capacidad de personal pueden cubrir el contrato.",
    ),
    "compliance": Department(
        "compliance",
        "Compliance and Data Governance",
        "Claire Whitfield",
        "Revisión regulatoria (HIPAA/UK GDPR), cláusulas de BAA o DPA según el país del cliente.",
    ),
}

DEPARTMENT_IDS = tuple(DEPARTMENTS)
ALWAYS_REQUIRED = ("revenue", "compliance")
COMPLIANCE = "compliance"

# §5 del BRIEFING: el país decide moneda e instrumento de protección de datos.
COUNTRY_RULES = {
    "US": {"currency": "USD", "data_agreement": "BAA", "regulation": "HIPAA"},
    "UK": {"currency": "GBP", "data_agreement": "DPA", "regulation": "UK GDPR"},
}


def country_rules(client_country: Optional[str]) -> Optional[dict]:
    """Moneda, instrumento y marco del país; None si el país no se sabe (no
    se asume EE. UU. por defecto: sería elegir moneda y contrato a ciegas)."""
    return COUNTRY_RULES.get(client_country or "")


def required_departments(requested: Iterable[str]) -> List[str]:
    """Departamentos conocidos que pidió el orquestador, más `revenue` y
    `compliance` siempre, en el orden del catálogo (estable para tests y UI).
    `clinical` lo decide el orquestador."""
    wanted = {department_id for department_id in requested if department_id in DEPARTMENTS}
    wanted.update(ALWAYS_REQUIRED)
    return [department_id for department_id in DEPARTMENT_IDS if department_id in wanted]


def unmapped(requested: Iterable[str]) -> List[str]:
    """Lo que el orquestador pidió y no es un departamento de HealthCore."""
    return sorted({item for item in requested if item and item not in DEPARTMENTS})
