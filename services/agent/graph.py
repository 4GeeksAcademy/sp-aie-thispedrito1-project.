"""Grafo LangGraph del asistente de soporte (Agente de Soporte, Partes 1 y 2).

Parte 1: el RAG del Hito 7 con cada paso como nodo de responsabilidad única.
Parte 2: tools de datos operativos en vivo (incidencias, inventario) y un
nodo `plan_sources` en el que el modelo decide qué fuentes necesita la
pregunta, sin que el usuario lo diga:

    START → receive_question ─┬─ (vacía) → reject_question → END
                              └─ plan_sources
                                   │  route_next_source, tras cada fuente:
                                   ├→ lookup_incident ────────┐  siguiente fuente del plan,
                                   ├→ check_inventory_stock ──┤  o tool_fallback si una tool falló,
                                   └→ retrieve ───────────────┘  o generate / no_information al acabar
                     tool_fallback → END · no_information → END · generate → END

Contrato de nodos: `retrieve` llama SOLO a `rag.retrieve()`, `generate` SOLO
a `rag.generate_answer()` (con los chunks del RAG y/o los datos en vivo como
contexto), y cada tool solo a su gestor. Ningún nodo llama a `rag.query()`.

El grafo se compila (y se valida su estructura) antes de cualquier ejecución:
`compile_agent_graph()` falla con `AgentGraphError` si hay un nodo sin
conexión o que no llega a END, cosas que `StateGraph.compile()` de LangGraph
0.6 deja pasar (comprobado).

Sin `from __future__ import annotations`: LangGraph resuelve las anotaciones
de `AgentState` en tiempo de ejecución y en Python 3.9 conviene no convertirlas
en cadenas (mismo criterio que `data/pipelines/pipeline.py` con Prefect).
"""

import logging
import operator
from collections import deque
from typing import Annotated, Any, Callable, Dict, Iterable, List, Optional, Set, Tuple, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from services.agent.evidence import fallback_message, tool_evidence
from services.agent.planner import SOURCE_INCIDENTS, SOURCE_INVENTORY, SOURCE_KNOWLEDGE_BASE, Plan
from services.agent.tools.base import ToolResult
from services.agent.tools.incidents import IncidentLookupInput
from services.agent.tools.inventory import InventoryLookupInput

logger = logging.getLogger(__name__)

GRAPH_NAME = "healthcore_support_agent"

# Nombres de nodo: son también los valores que devuelven las funciones de
# enrutamiento y los que aparecen en el trace.
RECEIVE_QUESTION = "receive_question"
REJECT_QUESTION = "reject_question"
PLAN_SOURCES = "plan_sources"
LOOKUP_INCIDENT = "lookup_incident"
CHECK_INVENTORY_STOCK = "check_inventory_stock"
RETRIEVE = "retrieve"
TOOL_FALLBACK = "tool_fallback"
NO_INFORMATION = "no_information"
GENERATE = "generate"

# Qué nodo consulta cada fuente del plan (y, al revés, qué fuente usó cada nodo).
SOURCE_NODES = {
    SOURCE_INCIDENTS: LOOKUP_INCIDENT,
    SOURCE_INVENTORY: CHECK_INVENTORY_STOCK,
    SOURCE_KNOWLEDGE_BASE: RETRIEVE,
}
NODE_SOURCES = {node: source for source, node in SOURCE_NODES.items()}

OUTCOME_ANSWERED = "answered"
OUTCOME_NO_INFORMATION = "no_information"
OUTCOME_INVALID_QUESTION = "invalid_question"
OUTCOME_TOOL_FALLBACK = "tool_fallback"

# Respuesta honesta cuando la base de conocimiento no tiene nada sobre el
# umbral. Texto fijo a propósito: sin llamada al modelo, no puede inventar.
# (POST /knowledge/query conserva su comportamiento: allí responde el modelo.)
NO_INFORMATION_ANSWER = (
    "No tengo información suficiente en la base de conocimiento para responder a eso. "
    "Consúltalo con el responsable correspondiente antes de confirmar nada al paciente."
)


class AgentState(TypedDict, total=False):
    """Estado mínimo que viaja entre nodos.

    Sin historial de conversación: cada consulta del coordinador es
    independiente (la pantalla "Asistente" no tiene hilo) y arrastrarlo solo
    ampliaría lo que se guarda en cada checkpoint, incluidos posibles datos
    de pacientes. `completed_sources` y `tool_results` acumulan (reducer
    `operator.add`): cada nodo aporta solo lo suyo, y el trace muestra
    exactamente qué produjo cada paso."""

    question: str  # pregunta ya normalizada por receive_question
    plan: List[Dict[str, Any]]  # [{"source", "args"}] en orden de ejecución
    plan_status: Optional[str]  # model | fallback (el modelo no pudo decidir → solo RAG)
    completed_sources: Annotated[List[str], operator.add]
    tool_results: Annotated[List[Dict[str, Any]], operator.add]  # ToolResult serializados
    context: List[Dict[str, Any]]  # chunks de retrieve() que superaron min_score
    answer: Optional[str]
    outcome: Optional[str]  # answered | no_information | invalid_question | tool_fallback


STATE_KEYS = frozenset(AgentState.__annotations__)


class AgentGraphError(RuntimeError):
    """Error estructural del grafo, detectado al compilar (nunca en una petición)."""


class AgentStateError(RuntimeError):
    """Un nodo intentó escribir una clave que no pertenece a `AgentState`."""


RetrieveFn = Callable[..., List[Dict[str, Any]]]
GenerateFn = Callable[[str, List[Dict[str, Any]]], str]
PlannerFn = Callable[[str], Plan]
IncidentToolFn = Callable[[IncidentLookupInput], ToolResult]
InventoryToolFn = Callable[[InventoryLookupInput], ToolResult]


# Dependencias por defecto: se resuelven en cada llamada (no al importar) para
# que los tests puedan sustituir la función del módulo con monkeypatch.


def _default_retrieve(question: str, *, k: int, min_score: float) -> List[Dict[str, Any]]:
    from data.pipelines import rag

    return rag.retrieve(question, k=k, min_score=min_score)


def _default_generate(question: str, context: List[Dict[str, Any]]) -> str:
    from data.pipelines import rag

    return rag.generate_answer(question, context)


def _default_min_score() -> float:
    from data.pipelines import rag

    return rag.get_min_score()


def _default_k() -> int:
    from data.pipelines import rag

    return rag.DEFAULT_K


def _default_planner(question: str) -> Plan:
    from services.agent import planner

    return planner.plan_sources(question)


def _default_incident_tool(payload: IncidentLookupInput) -> ToolResult:
    from services.agent.tools import incidents

    return incidents.lookup_incident(payload)


def _default_inventory_tool(payload: InventoryLookupInput) -> ToolResult:
    from services.agent.tools import inventory

    return inventory.check_inventory_stock(payload)


def _planned_args(state: AgentState, source: str) -> Dict[str, Any]:
    for call in state.get("plan") or []:
        if call["source"] == source:
            return call["args"]
    raise AgentStateError(f"El plan no incluye la fuente '{source}'")


class AgentNodes:
    """Los nodos del grafo. Cada método recibe el estado y devuelve SOLO las
    claves que cambia. Las dependencias se inyectan para poder probar cada
    nodo por separado sin red."""

    def __init__(
        self,
        *,
        retrieve_fn: Optional[RetrieveFn] = None,
        generate_fn: Optional[GenerateFn] = None,
        min_score_fn: Optional[Callable[[], float]] = None,
        k: Optional[int] = None,
        planner_fn: Optional[PlannerFn] = None,
        incident_tool_fn: Optional[IncidentToolFn] = None,
        inventory_tool_fn: Optional[InventoryToolFn] = None,
    ) -> None:
        self.retrieve_fn = retrieve_fn or _default_retrieve
        self.generate_fn = generate_fn or _default_generate
        self.min_score_fn = min_score_fn or _default_min_score
        self.k = k if k is not None else _default_k()
        self.planner_fn = planner_fn or _default_planner
        self.incident_tool_fn = incident_tool_fn or _default_incident_tool
        self.inventory_tool_fn = inventory_tool_fn or _default_inventory_tool

    def receive_question(self, state: AgentState) -> Dict[str, Any]:
        """Normaliza la pregunta y deja el resto del estado limpio."""
        question = (state.get("question") or "").strip()
        return {"question": question, "plan": [], "plan_status": None, "context": [], "answer": None, "outcome": None}

    def reject_question(self, state: AgentState) -> Dict[str, Any]:
        """Pregunta vacía: se corta aquí, sin planificar, consultar ni generar."""
        return {"answer": None, "outcome": OUTCOME_INVALID_QUESTION}

    def plan_sources(self, state: AgentState) -> Dict[str, Any]:
        """El modelo elige las fuentes (ver planner.py). Nunca falla: ante un
        problema del modelo, el plan es solo la base de conocimiento."""
        plan = self.planner_fn(state["question"])
        return {"plan": [call.model_dump() for call in plan.calls], "plan_status": plan.status}

    def lookup_incident(self, state: AgentState) -> Dict[str, Any]:
        """Solo la tool de incidencias, con los argumentos ya validados del plan."""
        payload = IncidentLookupInput.model_validate(_planned_args(state, SOURCE_INCIDENTS))
        result = self.incident_tool_fn(payload)
        return {"tool_results": [result.model_dump(mode="json")], "completed_sources": [SOURCE_INCIDENTS]}

    def check_inventory_stock(self, state: AgentState) -> Dict[str, Any]:
        """Solo la tool de inventario, con los argumentos ya validados del plan."""
        payload = InventoryLookupInput.model_validate(_planned_args(state, SOURCE_INVENTORY))
        result = self.inventory_tool_fn(payload)
        return {"tool_results": [result.model_dump(mode="json")], "completed_sources": [SOURCE_INVENTORY]}

    def retrieve(self, state: AgentState) -> Dict[str, Any]:
        """Solo recuperación: `rag.retrieve()` con el umbral afinado del Hito 7."""
        chunks = self.retrieve_fn(state["question"], k=self.k, min_score=self.min_score_fn())
        return {"context": list(chunks), "completed_sources": [SOURCE_KNOWLEDGE_BASE]}

    def tool_fallback(self, state: AgentState) -> Dict[str, Any]:
        """Una tool no pudo dar el dato: respuesta honesta fija, sin modelo."""
        failed = next(result for result in state.get("tool_results") or [] if result["status"] != "ok")
        return {"answer": fallback_message(failed), "outcome": OUTCOME_TOOL_FALLBACK}

    def no_information(self, state: AgentState) -> Dict[str, Any]:
        """Ninguna fuente aportó nada: respuesta honesta, sin llamar al modelo."""
        return {"answer": NO_INFORMATION_ANSWER, "outcome": OUTCOME_NO_INFORMATION}

    def generate(self, state: AgentState) -> Dict[str, Any]:
        """Solo generación, sobre los datos en vivo y los chunks ya recuperados."""
        evidence = tool_evidence(state.get("tool_results") or []) + list(state.get("context") or [])
        answer = self.generate_fn(state["question"], evidence)
        return {"answer": answer, "outcome": OUTCOME_ANSWERED}


# --- Aristas condicionales ----------------------------------------------------


def route_after_receive(state: AgentState) -> str:
    """Pregunta vacía → error claro; cualquier otra → planificar fuentes."""
    return PLAN_SOURCES if state.get("question") else REJECT_QUESTION


def pending_sources(state: AgentState) -> List[str]:
    done = set(state.get("completed_sources") or [])
    return [call["source"] for call in state.get("plan") or [] if call["source"] not in done]


def route_next_source(state: AgentState) -> str:
    """Tras planificar y tras cada fuente: ¿a qué nodo se va ahora?

    Devuelve el nodo de la siguiente fuente pendiente del plan, o uno de los
    tres finales: TOOL_FALLBACK, NO_INFORMATION o GENERATE."""
    tool_results = state.get("tool_results") or []
    has_evidence = bool(state.get("context")) or any(result["status"] == "ok" for result in tool_results)
    # Cortocircuito: si una tool no pudo dar su dato, la respuesta será el
    # fallback honesto pase lo que pase; consultar el resto sería trabajo tirado.
    if any(result["status"] != "ok" for result in tool_results):
        return TOOL_FALLBACK
    pending = pending_sources(state)
    if pending:
        return SOURCE_NODES[pending[0]]
    return GENERATE if has_evidence else NO_INFORMATION


# --- Construcción, validación y compilación ----------------------------------


def _checked(name: str, fn: Callable[[AgentState], Dict[str, Any]]) -> Callable[[AgentState], Dict[str, Any]]:
    """Envuelve un nodo para que escribir fuera de `AgentState` sea un error
    con nombre, no una clave que LangGraph descarta en silencio (comprobado:
    LangGraph 0.6 ignora las claves desconocidas sin avisar)."""

    def node(state: AgentState) -> Dict[str, Any]:
        update = fn(state)
        unknown = set(update) - STATE_KEYS
        if unknown:
            raise AgentStateError(f"El nodo '{name}' escribió claves fuera de AgentState: {sorted(unknown)}")
        return update

    node.__name__ = name
    return node


def build_agent_graph(nodes: Optional[AgentNodes] = None) -> StateGraph:
    """Declara nodos y aristas. No compila: eso es `compile_agent_graph()`."""
    nodes = nodes or AgentNodes()
    builder = StateGraph(AgentState)

    for name in (
        RECEIVE_QUESTION,
        REJECT_QUESTION,
        PLAN_SOURCES,
        LOOKUP_INCIDENT,
        CHECK_INVENTORY_STOCK,
        RETRIEVE,
        TOOL_FALLBACK,
        NO_INFORMATION,
        GENERATE,
    ):
        builder.add_node(name, _checked(name, getattr(nodes, name)))

    builder.add_edge(START, RECEIVE_QUESTION)
    builder.add_conditional_edges(RECEIVE_QUESTION, route_after_receive, [PLAN_SOURCES, REJECT_QUESTION])
    # Una sola función de enrutamiento para todas las fuentes; el path_map de
    # cada nodo declara solo los destinos posibles desde ahí (el plan siempre
    # sigue el orden incidencias → inventario → RAG), así el dibujo del grafo
    # es fiel y un destino imposible haría fallar la corrida.
    builder.add_conditional_edges(PLAN_SOURCES, route_next_source, [LOOKUP_INCIDENT, CHECK_INVENTORY_STOCK, RETRIEVE])
    builder.add_conditional_edges(
        LOOKUP_INCIDENT, route_next_source, [CHECK_INVENTORY_STOCK, RETRIEVE, TOOL_FALLBACK, GENERATE]
    )
    builder.add_conditional_edges(CHECK_INVENTORY_STOCK, route_next_source, [RETRIEVE, TOOL_FALLBACK, GENERATE])
    builder.add_conditional_edges(RETRIEVE, route_next_source, [NO_INFORMATION, GENERATE])
    for final in (REJECT_QUESTION, TOOL_FALLBACK, NO_INFORMATION, GENERATE):
        builder.add_edge(final, END)
    return builder


def _reachable(start: str, edges: Iterable[Tuple[str, str]]) -> Set[str]:
    adjacency: Dict[str, List[str]] = {}
    for source, target in edges:
        adjacency.setdefault(source, []).append(target)
    seen = {start}
    pending = deque([start])
    while pending:
        for target in adjacency.get(pending.popleft(), []):
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return seen


def _declared_edges(builder: StateGraph) -> List[Tuple[str, str]]:
    """Aristas tal como se declararon, fijas y condicionales.

    Se valida sobre el constructor y no sobre `compiled.get_graph()`: el
    dibujo añade por su cuenta una arista a END en cualquier nodo sin salida
    (comprobado en LangGraph 0.6), así que un callejón sin salida no se vería."""
    edges = list(builder.edges)
    for source, branches in builder.branches.items():
        for name, branch in branches.items():
            if not branch.ends:
                raise AgentGraphError(
                    f"La arista condicional '{name}' de '{source}' no declara sus destinos (path_map)"
                )
            edges.extend((source, target) for target in branch.ends.values())
    return edges


def validate_structure(builder: StateGraph) -> None:
    """Todo nodo debe ser alcanzable desde START y tener una salida declarada
    que acabe llegando a END: un nodo sin salida terminaría la corrida en
    silencio, sin que nadie lo haya decidido."""
    node_ids = set(builder.nodes)
    edges = _declared_edges(builder)

    unreachable = node_ids - _reachable(START, edges)
    if unreachable:
        raise AgentGraphError(f"Nodos sin conexión desde START: {sorted(unreachable)}")

    reversed_edges = [(target, source) for source, target in edges]
    dead_ends = node_ids - _reachable(END, reversed_edges)
    if dead_ends:
        raise AgentGraphError(f"Nodos que nunca llegan a END: {sorted(dead_ends)}")


def compile_agent_graph(builder: Optional[StateGraph] = None, *, checkpointer: Optional[Any] = None) -> Any:
    """Compila y valida. Se llama al importar el router (arranque de la API),
    así que un grafo roto impide arrancar en vez de fallar en una petición.

    Checkpointer en memoria por defecto: guarda una foto del estado tras cada
    nodo (inspeccionable y reanudable con el mismo `thread_id`). Sin servidor
    ni base de datos nueva; el trace JSON es el registro duradero."""
    builder = builder or build_agent_graph()
    try:
        validate_structure(builder)
        compiled = builder.compile(checkpointer=checkpointer or InMemorySaver(), name=GRAPH_NAME)
    except ValueError as exc:
        raise AgentGraphError(f"El grafo del agente no compila: {exc}") from exc
    logger.info("Agent graph '%s' compiled: %d nodes", GRAPH_NAME, len(compiled.get_graph().nodes))
    return compiled
