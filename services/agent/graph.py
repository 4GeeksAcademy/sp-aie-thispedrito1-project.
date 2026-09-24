"""Grafo LangGraph del asistente de políticas (Agente de Soporte, Parte 1).

El mismo comportamiento que `data/pipelines/rag.py::query()`, pero con cada
paso como un nodo de responsabilidad única y las decisiones como aristas
condicionales explícitas:

    START → receive_question ─┬─ (pregunta vacía) ──────────→ reject_question → END
                              └─ retrieve ─┬─ (sin contexto) → no_information → END
                                           └─ generate ─────────────────────→ END

Contrato de nodos: `retrieve` llama SOLO a `rag.retrieve()` y `generate`
llama SOLO a `rag.generate_answer()` con el contexto que dejó `retrieve` en el
estado. Ningún nodo llama a `rag.query()`: eso recuperaría dos veces y
escondería el paso que este grafo existe para hacer visible.

El grafo se compila (y se valida su estructura) antes de cualquier ejecución:
`compile_agent_graph()` falla con `AgentGraphError` si hay un nodo sin
conexión o que no llega a END, cosas que `StateGraph.compile()` de LangGraph
0.6 deja pasar (comprobado).

Sin `from __future__ import annotations`: LangGraph resuelve las anotaciones
de `AgentState` en tiempo de ejecución y en Python 3.9 conviene no convertirlas
en cadenas (mismo criterio que `data/pipelines/pipeline.py` con Prefect).
"""

import logging
from collections import deque
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

logger = logging.getLogger(__name__)

GRAPH_NAME = "healthcore_support_agent"

# Nombres de nodo: son también los valores que devuelven las funciones de
# enrutamiento y los que aparecen en el trace. Constantes para no repetir
# cadenas sueltas entre el grafo, las aristas y los evals.
RECEIVE_QUESTION = "receive_question"
REJECT_QUESTION = "reject_question"
RETRIEVE = "retrieve"
NO_INFORMATION = "no_information"
GENERATE = "generate"

OUTCOME_ANSWERED = "answered"
OUTCOME_NO_INFORMATION = "no_information"
OUTCOME_INVALID_QUESTION = "invalid_question"

# Respuesta honesta cuando nada supera el umbral. Texto fijo a propósito: el
# ticket pide no forzar la generación sobre contexto vacío, y así la ruta es
# determinista, no gasta una llamada al modelo y no puede inventar nada.
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
    de pacientes."""

    question: str  # pregunta ya normalizada por receive_question
    context: List[Dict[str, Any]]  # chunks de retrieve() que superaron min_score
    answer: Optional[str]  # respuesta final (modelo o texto honesto fijo)
    outcome: Optional[str]  # answered | no_information | invalid_question


STATE_KEYS = frozenset(AgentState.__annotations__)


class AgentGraphError(RuntimeError):
    """Error estructural del grafo, detectado al compilar (nunca en una petición)."""


class AgentStateError(RuntimeError):
    """Un nodo intentó escribir una clave que no pertenece a `AgentState`."""


RetrieveFn = Callable[..., List[Dict[str, Any]]]
GenerateFn = Callable[[str, List[Dict[str, Any]]], str]


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


class AgentNodes:
    """Los nodos del grafo. Cada método recibe el estado y devuelve SOLO las
    claves que cambia. Las dependencias (retrieve, generación, umbral) se
    inyectan para poder probar cada nodo por separado sin red."""

    def __init__(
        self,
        *,
        retrieve_fn: Optional[RetrieveFn] = None,
        generate_fn: Optional[GenerateFn] = None,
        min_score_fn: Optional[Callable[[], float]] = None,
        k: Optional[int] = None,
    ) -> None:
        self.retrieve_fn = retrieve_fn or _default_retrieve
        self.generate_fn = generate_fn or _default_generate
        self.min_score_fn = min_score_fn or _default_min_score
        self.k = k if k is not None else _default_k()

    def receive_question(self, state: AgentState) -> Dict[str, Any]:
        """Normaliza la pregunta y deja el resto del estado limpio."""
        question = (state.get("question") or "").strip()
        return {"question": question, "context": [], "answer": None, "outcome": None}

    def reject_question(self, state: AgentState) -> Dict[str, Any]:
        """Pregunta vacía: se corta aquí, sin recuperar ni generar."""
        return {"answer": None, "outcome": OUTCOME_INVALID_QUESTION}

    def retrieve(self, state: AgentState) -> Dict[str, Any]:
        """Solo recuperación: `rag.retrieve()` con el umbral afinado del Hito 7."""
        chunks = self.retrieve_fn(state["question"], k=self.k, min_score=self.min_score_fn())
        return {"context": list(chunks)}

    def no_information(self, state: AgentState) -> Dict[str, Any]:
        """Ningún chunk superó el umbral: respuesta honesta, sin llamar al modelo."""
        return {"answer": NO_INFORMATION_ANSWER, "outcome": OUTCOME_NO_INFORMATION}

    def generate(self, state: AgentState) -> Dict[str, Any]:
        """Solo generación, sobre el contexto que ya dejó `retrieve` en el estado."""
        answer = self.generate_fn(state["question"], state["context"])
        return {"answer": answer, "outcome": OUTCOME_ANSWERED}


# --- Aristas condicionales ----------------------------------------------------


def route_after_receive(state: AgentState) -> str:
    """Pregunta vacía → error claro; cualquier otra → recuperación."""
    return RETRIEVE if state.get("question") else REJECT_QUESTION


def route_after_retrieve(state: AgentState) -> str:
    """Decide si hay base para generar o si hay que responder con honestidad.

    Devuelve GENERATE o NO_INFORMATION (los dos destinos declarados en
    `build_agent_graph`; cualquier otro valor hace fallar la corrida).

    No vuelve a mirar las puntuaciones: `retrieve()` ya descartó lo que no
    llega a `min_score`, y repetir el umbral aquí haría que cambiarlo
    exigiera tocar dos sitios."""
    return GENERATE if state.get("context") else NO_INFORMATION


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

    builder.add_node(RECEIVE_QUESTION, _checked(RECEIVE_QUESTION, nodes.receive_question))
    builder.add_node(REJECT_QUESTION, _checked(REJECT_QUESTION, nodes.reject_question))
    builder.add_node(RETRIEVE, _checked(RETRIEVE, nodes.retrieve))
    builder.add_node(NO_INFORMATION, _checked(NO_INFORMATION, nodes.no_information))
    builder.add_node(GENERATE, _checked(GENERATE, nodes.generate))

    builder.add_edge(START, RECEIVE_QUESTION)
    # path_map explícito: declara los destinos posibles de cada decisión, así
    # compile() puede comprobar que existen y get_graph() los dibuja.
    builder.add_conditional_edges(RECEIVE_QUESTION, route_after_receive, [RETRIEVE, REJECT_QUESTION])
    builder.add_conditional_edges(RETRIEVE, route_after_retrieve, [GENERATE, NO_INFORMATION])
    builder.add_edge(REJECT_QUESTION, END)
    builder.add_edge(NO_INFORMATION, END)
    builder.add_edge(GENERATE, END)
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
