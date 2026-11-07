"""Triggers de conflicto y árbitro fijo (BRIEFING del Hito 9, §7).

El arbitraje es un nodo dedicado del grafo, disparado por contradicciones
que se ven en ESTADO ESTRUCTURADO, nunca por agentes negociando:

| trigger                  | señal estructurada                                    | árbitro                      |
|--------------------------|-------------------------------------------------------|------------------------------|
| `phi-detected`           | `contains_phi` del evaluador de cumplimiento          | Claire Whitfield (regla)     |
| `baa-dpa-mismatch`       | violaciones `baa-us` / `dpa-uk` del mismo evaluador   | Claire Whitfield (regla)     |
| `capacity-vs-population` | `confirmed_capacity` (número que escribe Clinical al  | Tom Callahan (humano), tras  |
|                          | aprobar) < `covered_population` de los metadatos      | confirmar Compliance sin PHI |

Los dos primeros se resuelven con la regla del BRIEFING ("Compliance siempre
gana"): `request_changes` forzado en las secciones afectadas, con el motivo
por escrito. El tercero lo decide una persona (Tom), así que el nodo se
interrumpe y le ofrece las dos salidas del BRIEFING: reducir la población
cubierta o añadir sedes. Ningún LLM vota en ninguno de los tres.

Prioridad: los triggers regulatorios van primero y, mientras haya uno, el de
capacidad ni se evalúa (el BRIEFING lo condiciona a que Compliance confirme
que no hay PHI).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from data.pipelines.rfp_intake.departments import COMPLIANCE, DEPARTMENTS
from data.pipelines.rfp_intake.evaluators import RULE_BAA_US, RULE_DPA_UK, RULE_NO_PHI, evaluate_compliance

TRIGGER_PHI = "phi-detected"
TRIGGER_BAA_DPA = "baa-dpa-mismatch"
TRIGGER_CAPACITY = "capacity-vs-population"

ARBITER_RULE = "rule"
ARBITER_HUMAN = "human"

# Árbitro fijo de cada trigger (BRIEFING §7). Nunca un modelo.
ARBITERS: Dict[str, Dict[str, str]] = {
    TRIGGER_PHI: {"name": DEPARTMENTS[COMPLIANCE].owner, "department_id": COMPLIANCE, "kind": ARBITER_RULE},
    TRIGGER_BAA_DPA: {"name": DEPARTMENTS[COMPLIANCE].owner, "department_id": COMPLIANCE, "kind": ARBITER_RULE},
    TRIGGER_CAPACITY: {"name": DEPARTMENTS["revenue"].owner, "department_id": "revenue", "kind": ARBITER_HUMAN},
}
REGULATORY_TRIGGERS = (TRIGGER_PHI, TRIGGER_BAA_DPA)

# Estados de rama que el arbitraje lee (los fija approval_graph).
BRANCH_APPROVED = "approved"


@dataclass
class Conflict:
    trigger_id: str
    departments: List[str]
    arbiter: str
    arbiter_kind: str
    # Solo datos estructurados (categorías, rule_ids, cifras); nunca texto
    # del borrador. Los mensajes de violación los escribe nuestro código.
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def regulatory_signals(draft: str, *, department_id: str, client_country: Optional[str]) -> List[Dict[str, Any]]:
    """Señales `phi-detected` / `baa-dpa-mismatch` de UN borrador.

    Reutiliza el evaluador de cumplimiento de la Parte 2 (misma definición
    de "falta el BAA" en todo el flujo) y se queda solo con sus reglas
    regulatorias: una cifra no confirmada es asunto del humano que aprueba,
    no un conflicto entre departamentos."""
    result = evaluate_compliance(
        draft, department_id=department_id, client_country=client_country, figures_allowed=set()
    )
    signals: List[Dict[str, Any]] = []
    if result.contains_phi:
        phi = next(v for v in result.violations if v.rule_id == RULE_NO_PHI)
        signals.append({"trigger_id": TRIGGER_PHI, "rule_ids": [RULE_NO_PHI], "messages": [phi.message]})
    mismatch = [v for v in result.violations if v.rule_id in (RULE_BAA_US, RULE_DPA_UK)]
    if mismatch:
        signals.append({
            "trigger_id": TRIGGER_BAA_DPA,
            "rule_ids": sorted({v.rule_id for v in mismatch}),
            "messages": [v.message for v in mismatch],
        })
    return signals


def capacity_shortfall(outcomes: Dict[str, Dict[str, Any]], context: Dict[str, Any]) -> bool:
    """¿La capacidad que confirmó Clinical no cubre la población del contrato?

    `outcomes[dept]` trae `status` ("approved", "rejected", "blocked"...) y,
    en Clinical, `confirmed_capacity` (int o None). `context` trae
    `covered_population` (int o None si la RFP no lo dice).
    """
    # Implementado por el asistente (Learn by Doing delegado por el usuario).
    # 1. BRIEFING §7: Tom arbitra "tras confirmar Compliance que no hay PHI".
    #    Sin la aprobación de Compliance no se evalúa la capacidad.
    if (outcomes.get(COMPLIANCE) or {}).get("status") != BRANCH_APPROVED:
        return False
    # 2. Sin población en la RFP no hay con qué comparar: nunca se inventa.
    population = context.get("covered_population")
    # 3. Solo cuenta una capacidad CONFIRMADA por Clinical al aprobar (una
    #    rama bloqueada o una RFP sin Clinical no aportan dato).
    clinical = outcomes.get("clinical") or {}
    capacity = clinical.get("confirmed_capacity") if clinical.get("status") == BRANCH_APPROVED else None
    if population is None or capacity is None:
        return False
    # 4. Cubrir exactamente la población basta; el conflicto es quedarse corto.
    return capacity < population


def detect_conflicts(outcomes: Dict[str, Dict[str, Any]], context: Dict[str, Any]) -> List[Conflict]:
    """Conflictos del BRIEFING §7 sobre el estado de todas las ramas.

    Además de las señales que cada rama levantó antes de pedir aprobación,
    vuelve a pasar las reglas sobre los borradores APROBADOS: es la última
    puerta antes del synthesizer ("phi-detected: el synthesizer no debe
    correr")."""
    by_trigger: Dict[str, Conflict] = {}
    for department_id, outcome in outcomes.items():
        signals = list(outcome.get("signals") or [])
        if outcome.get("status") == BRANCH_APPROVED:
            signals += regulatory_signals(
                outcome.get("draft") or "", department_id=department_id, client_country=context.get("client_country")
            )
        for signal in signals:
            trigger_id = signal["trigger_id"]
            arbiter = ARBITERS[trigger_id]
            conflict = by_trigger.setdefault(
                trigger_id, Conflict(trigger_id, [], arbiter["name"], arbiter["kind"], {"rule_ids": []})
            )
            if department_id not in conflict.departments:
                conflict.departments.append(department_id)
            conflict.details["rule_ids"] = sorted(set(conflict.details["rule_ids"]) | set(signal["rule_ids"]))
            conflict.details.setdefault("messages", {}).setdefault(department_id, [])
            for message in signal["messages"]:
                if message not in conflict.details["messages"][department_id]:
                    conflict.details["messages"][department_id].append(message)

    regulatory = [by_trigger[t] for t in REGULATORY_TRIGGERS if t in by_trigger]
    if regulatory:
        return regulatory  # Compliance primero; capacidad, cuando no quede nada regulatorio

    if capacity_shortfall(outcomes, context):
        arbiter = ARBITERS[TRIGGER_CAPACITY]
        return [Conflict(
            TRIGGER_CAPACITY,
            ["revenue", "clinical"],
            arbiter["name"],
            arbiter["kind"],
            {
                "covered_population": context.get("covered_population"),
                "covered_population_unit": context.get("covered_population_unit"),
                "confirmed_capacity": outcomes["clinical"].get("confirmed_capacity"),
            },
        )]
    return []


def rule_resolution(conflicts: List[Conflict]) -> Dict[str, str]:
    """Regla fija de Claire Whitfield para los triggers regulatorios:
    `request_changes` forzado en cada sección afectada, con el motivo. El
    texto lo construye el código a partir de las violaciones (que ya nombran
    categorías, nunca el fragmento con PHI)."""
    forced: Dict[str, List[str]] = {}
    for conflict in conflicts:
        prefix = "datos de paciente" if conflict.trigger_id == TRIGGER_PHI else "cláusula de protección de datos"
        for department_id in conflict.departments:
            lines = forced.setdefault(department_id, [])
            for message in conflict.details.get("messages", {}).get(department_id, []):
                lines.append(f"[{conflict.trigger_id}] {conflict.arbiter} (Compliance) exige corregir la {prefix}: {message}")
    return {department_id: "\n".join(lines) for department_id, lines in forced.items()}


def capacity_request(conflict: Conflict, *, round_number: int, max_rounds: int) -> Dict[str, Any]:
    """Lo que ve Tom Callahan en el punto de arbitraje (payload del interrupt)."""
    from data.pipelines.rfp_intake.decisions import ARBITRATION_ADD_SITES, ARBITRATION_REDUCE_POPULATION

    return {
        "kind": "arbitration",
        "trigger_id": conflict.trigger_id,
        "arbiter": conflict.arbiter,
        "arbiter_department_id": ARBITERS[conflict.trigger_id]["department_id"],
        "departments": conflict.departments,
        "covered_population": conflict.details["covered_population"],
        "covered_population_unit": conflict.details["covered_population_unit"],
        "confirmed_capacity": conflict.details["confirmed_capacity"],
        "options": [ARBITRATION_REDUCE_POPULATION, ARBITRATION_ADD_SITES],
        "round": round_number,
        "max_rounds": max_rounds,
        "compliance_cleared": True,  # solo se llega aquí sin triggers regulatorios
    }


def capacity_resolution(decision: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    """Decisión de Tom → secciones a revisar + cambios de contexto (BRIEFING:
    "reducir población cubierta o añadir sedes; forzar revisión en revenue
    y/o clinical")."""
    from data.pipelines.rfp_intake.decisions import ARBITRATION_REDUCE_POPULATION

    unit = request.get("covered_population_unit") or ""
    capacity = request["confirmed_capacity"]
    if decision["option"] == ARBITRATION_REDUCE_POPULATION:
        new_population = decision["new_population"]
        message = (
            f"[{TRIGGER_CAPACITY}] {request['arbiter']} (Revenue Cycle) fija la población cubierta en "
            f"{new_population} {unit} (antes {request['covered_population']}; capacidad confirmada por "
            f"Clinical Operations: {capacity}). Ajusta la sección a esa cifra."
        ).replace("  ", " ")
        return {
            "forced": {"revenue": message, "clinical": message},
            "context": {"covered_population": new_population},
            "extra_figures": [str(new_population), str(capacity)],
        }
    message = (
        f"[{TRIGGER_CAPACITY}] {request['arbiter']} (Revenue Cycle) decide añadir sedes para cubrir a "
        f"{request['covered_population']} {unit} (capacidad confirmada hoy: {capacity}). "
        f"Indicación: {decision['note']}"
    ).replace("  ", " ")
    return {"forced": {"clinical": message}, "context": {}, "extra_figures": [str(capacity)]}
