"""Nodo `plan_sources`: el modelo decide qué fuentes necesita la pregunta.

Function calling sobre el mismo modelo de generación (GENERATION_MODEL, proxy
de 4Geeks): se le ofrecen tres herramientas (políticas, incidencias,
inventario) y elige una o varias, con sus argumentos. El usuario nunca dice
qué fuente usar. Probado contra el proxy real: "¿estado del ticket 482?" →
incidencias; "¿cuánto cuesta un no-show?" → políticas; una pregunta mixta →
las dos en la misma respuesta.

Las incidencias se ofrecen al modelo como DOS funciones (`get_incident` por
número y `search_incidents` por filtros), no como una con todos los campos
opcionales: con esa forma el modelo real rellenaba todos los campos con
valores inventados (primer valor de cada enum) y el contrato rechazaba la
llamada. En la búsqueda cada filtro es obligatorio pero admite `null`, para
que el modelo tenga que decidir explícitamente "no lo menciona" (comprobado
contra el proxy; test de regresión en test_agent_tools.py). Ambas apuntan a
la misma tool y al mismo contrato `IncidentLookupInput`.

Nada de lo que propone el modelo se ejecuta sin validar: los argumentos pasan
por los contratos tipados de cada tool (`IncidentLookupInput`,
`InventoryLookupInput`) y una llamada inválida se descarta. Si no queda
ninguna, o el modelo falla o tarda más de PLANNER_TIMEOUT_S, el plan es la
base de conocimiento: el comportamiento de la Parte 1, que ante una pregunta
operativa acaba en la respuesta honesta de "no tengo información".

Orden de ejecución fijo (tools en vivo primero, RAG después), sea cual sea el
orden en que el modelo pidió las herramientas: el trace es comparable entre
corridas. La pregunta va al mismo proveedor que ya la recibía para generar;
nunca se escribe en los logs.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ValidationError

from services.agent.tools.incidents import ALLOWED_VALUES, IncidentLookupInput
from services.agent.tools.inventory import InventoryLookupInput

logger = logging.getLogger(__name__)

SOURCE_INCIDENTS = "incidents"
SOURCE_INVENTORY = "inventory"
SOURCE_KNOWLEDGE_BASE = "knowledge_base"
SOURCE_ORDER = (SOURCE_INCIDENTS, SOURCE_INVENTORY, SOURCE_KNOWLEDGE_BASE)

PLANNER_TIMEOUT_S = 10.0

Source = Literal["incidents", "inventory", "knowledge_base"]

# Nombre de la herramienta que ve el modelo → fuente y contrato de entrada.
MODEL_TOOLS: Dict[str, Dict[str, Any]] = {
    "search_knowledge_base": {"source": SOURCE_KNOWLEDGE_BASE, "input": None},
    "get_incident": {"source": SOURCE_INCIDENTS, "input": IncidentLookupInput},
    "search_incidents": {"source": SOURCE_INCIDENTS, "input": IncidentLookupInput},
    "check_inventory_stock": {"source": SOURCE_INVENTORY, "input": InventoryLookupInput},
}

PLANNER_PROMPT = """Eres el enrutador del asistente de los coordinadores de pacientes de HealthCore. \
No respondes a la pregunta: eliges qué herramientas hacen falta para responderla. Puedes elegir \
una o varias.
- search_knowledge_base: políticas y procedimientos internos (seguros aceptados, citas, \
cancelaciones y no-shows, derivaciones, documentación del paciente nuevo) y cualquier pregunta \
general o fuera de tema.
- get_incident: datos EN VIVO de UNA incidencia (también llamada ticket o caso) por su número.
- search_incidents: datos EN VIVO del gestor de incidencias para buscar o contar incidencias por \
estado, categoría, sede u origen. Pon null en todo filtro que la pregunta no mencione; nunca \
inventes un filtro.
- check_inventory_stock: stock EN VIVO de un insumo médico por su nombre o SKU.
Si la pregunta mezcla un dato en vivo y una política, elige ambas herramientas. Nunca uses \
las herramientas de incidencias ni check_inventory_stock para preguntas de políticas."""


def _tool_schemas() -> List[Dict[str, Any]]:
    filters = {
        field: {
            "type": ["string", "null"],
            "enum": [*values, None],
            "description": f"Filtro por {field}; null si la pregunta no lo menciona",
        }
        for field, values in ALLOWED_VALUES.items()
    }
    return [
        {
            "type": "function",
            "function": {
                "name": "search_knowledge_base",
                "description": "Busca en las políticas y procedimientos internos de HealthCore.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_incident",
                "description": "Consulta en vivo el estado de UNA incidencia por su número (solo lectura).",
                "parameters": {
                    "type": "object",
                    "properties": {"ticket_id": {"type": "integer", "minimum": 1, "description": "Número de la incidencia"}},
                    "required": ["ticket_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_incidents",
                "description": "Busca o cuenta en vivo incidencias por filtros (solo lectura). Al menos un filtro no nulo.",
                "parameters": {
                    "type": "object",
                    "properties": filters,
                    "required": list(filters),
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_inventory_stock",
                "description": "Consulta en vivo el stock total de un insumo médico (solo lectura).",
                "parameters": {
                    "type": "object",
                    "properties": {"product": {"type": "string", "description": "Nombre o SKU del insumo"}},
                    "required": ["product"],
                    "additionalProperties": False,
                },
            },
        },
    ]


class PlannedCall(BaseModel):
    source: Source
    args: Dict[str, Any] = {}


class Plan(BaseModel):
    calls: List[PlannedCall]
    status: Literal["model", "fallback"]
    error_type: Optional[str] = None
    rejected_tools: List[str] = []

    @property
    def sources(self) -> List[str]:
        return [call.source for call in self.calls]


FALLBACK_CALLS = [PlannedCall(source=SOURCE_KNOWLEDGE_BASE)]


def _validated_calls(tool_calls: Any) -> "tuple[List[PlannedCall], List[str]]":
    """Convierte las llamadas del modelo en PlannedCall validadas, una por fuente."""
    accepted: Dict[str, PlannedCall] = {}
    rejected: List[str] = []
    for tool_call in tool_calls or []:
        name = tool_call.function.name
        spec = MODEL_TOOLS.get(name)
        if spec is None:
            rejected.append(name)
            continue
        try:
            raw = json.loads(tool_call.function.arguments or "{}")
            args = spec["input"].model_validate(raw).model_dump(exclude_none=True) if spec["input"] else {}
        except (ValueError, ValidationError):  # JSON roto o contrato incumplido
            rejected.append(name)
            continue
        accepted.setdefault(spec["source"], PlannedCall(source=spec["source"], args=args))
    ordered = [accepted[source] for source in SOURCE_ORDER if source in accepted]
    return ordered, rejected


def plan_sources(question: str, *, client: Optional[Any] = None, model: Optional[str] = None) -> Plan:
    """Nunca lanza: ante cualquier fallo del modelo, plan de la Parte 1 (solo RAG)."""
    from data.pipelines import rag
    from data.process.rag import get_llm_client

    try:
        llm = client or get_llm_client()
        if hasattr(llm, "with_options"):
            llm = llm.with_options(timeout=PLANNER_TIMEOUT_S, max_retries=0)
        completion = llm.chat.completions.create(
            model=model or rag.get_generation_model(),
            messages=[{"role": "system", "content": PLANNER_PROMPT}, {"role": "user", "content": question}],
            tools=_tool_schemas(),
            tool_choice="required",
            temperature=0,
        )
        calls, rejected = _validated_calls(completion.choices[0].message.tool_calls)
    except Exception as exc:  # proveedor caído, timeout, configuración ausente
        logger.warning("Agent planner unavailable, falling back to knowledge base: %s", type(exc).__name__)
        return Plan(calls=list(FALLBACK_CALLS), status="fallback", error_type=type(exc).__name__)

    if not calls:
        logger.warning("Agent planner proposed no valid tool (rejected: %s)", rejected)
        return Plan(calls=list(FALLBACK_CALLS), status="fallback", error_type="NoValidToolCall", rejected_tools=rejected)
    return Plan(calls=calls, status="model", rejected_tools=rejected)
