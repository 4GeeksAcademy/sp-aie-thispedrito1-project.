"""Grafo dedicado `rfp_intake` (LangGraph): orchestrator → workers → synthesizer.

    START → convert → phi_screen ─PHI──────────────→ discard → END
                          └→ classify ─no es RFP───→ discard
                                └→ orchestrate ─Send×N→ department_worker → synthesize → END

- Independiente del grafo del agente de soporte (services/agent/graph.py):
  el ticket lo exige y no comparten nodos ni estado.
- El grafo NO toca la base de datos ni el disco salvo para leer el PDF: solo
  calcula. `runner.py` guarda el resultado final en una transacción, así que
  un fallo a mitad no deja un ticket medio escrito.
- `phi_screen` va antes que cualquier agente: si hay PHI, ningún modelo ve
  el documento (ni siquiera el clasificador).
- Fan-out con `Send`: un worker por departamento, en paralelo. Cada uno
  recibe solo metadatos + sus extractos, no el estado entero. LangGraph
  espera a todos antes de `synthesize` (mismo superstep).
"""

from __future__ import annotations

import logging
import operator
from collections import deque
from pathlib import Path
from typing import Annotated, Any, Callable, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from data.pipelines.rfp_intake import agents
from data.pipelines.rfp_intake.conversion import Section, pdf_to_markdown, split_sections
from data.pipelines.rfp_intake.departments import required_departments, unmapped
from data.pipelines.rfp_intake.phi import screen
from data.pipelines.rfp_intake.readability import compute_readability

logger = logging.getLogger("healthcore.rfp_intake")

GRAPH_NAME = "rfp_intake"

OUTCOME_PHI = "discarded_phi"
OUTCOME_NOT_RFP = "discarded_not_rfp"
OUTCOME_COMPLETE = "complete"


class RfpGraphError(RuntimeError):
    """El grafo está mal construido (se detecta al compilar, no en una corrida)."""


class RfpIntakeState(TypedDict, total=False):
    ticket_id: str
    pdf_path: str
    markdown: str  # ya redactado tras phi_screen
    sections: List[Section]
    phi_categories: List[str]
    classification: Dict[str, Any]
    metadata: Dict[str, Any]
    excerpts: Dict[str, List[Section]]
    open_questions: List[str]
    readability: Dict[str, Any]
    departments: List[str]
    unmapped_departments: List[str]
    # Reducer: cada worker añade su resultado; sin él, el último pisaría al resto.
    worker_results: Annotated[List[Dict[str, Any]], operator.add]
    synthesis: Dict[str, Any]
    outcome: str


class WorkerInput(TypedDict):
    """Lo ÚNICO que recibe un worker (pregunta de diseño 2)."""
    department_id: str
    metadata: Dict[str, Any]
    excerpts: List[Section]


def build_rfp_intake_graph(
    *,
    llm: agents.LlmCall = agents.call_model,
    converter: Callable[[Path], str] = pdf_to_markdown,
) -> StateGraph:
    def convert(state: RfpIntakeState) -> Dict[str, Any]:
        return {"markdown": converter(Path(state["pdf_path"]))}

    def phi_screen(state: RfpIntakeState) -> Dict[str, Any]:
        verdict = screen(state["markdown"])
        if verdict.contains_phi:
            logger.warning("rfp_phi_detected ticket=%s categories=%s", state["ticket_id"], ",".join(verdict.categories))
        return {
            "markdown": verdict.redacted_markdown,
            "sections": split_sections(verdict.redacted_markdown),
            "phi_categories": list(verdict.categories),
        }

    def classify(state: RfpIntakeState) -> Dict[str, Any]:
        result = agents.classify(state["sections"], llm=llm)
        return {"classification": result.model_dump() | {"accepted": result.accepted}}

    def orchestrate(state: RfpIntakeState) -> Dict[str, Any]:
        plan = agents.orchestrate(state["sections"], llm=llm)
        requested = plan["metadata"]["departments_requested"]
        return {
            "metadata": plan["metadata"],
            "excerpts": plan["excerpts"],
            "open_questions": plan["open_questions"],
            "readability": compute_readability(state["markdown"]),
            "departments": required_departments(requested),
            "unmapped_departments": unmapped(requested),
        }

    def department_worker(payload: WorkerInput) -> Dict[str, Any]:
        result = agents.run_worker(payload["department_id"], payload["metadata"], payload["excerpts"], llm=llm)
        return {"worker_results": [result]}

    def synthesize(state: RfpIntakeState) -> Dict[str, Any]:
        synthesis = agents.synthesize(state["metadata"], state["worker_results"], llm=llm)
        return {"synthesis": synthesis, "outcome": OUTCOME_COMPLETE}

    def discard(state: RfpIntakeState) -> Dict[str, Any]:
        return {"outcome": OUTCOME_PHI if state.get("phi_categories") else OUTCOME_NOT_RFP}

    builder = StateGraph(RfpIntakeState)
    builder.add_node("convert", convert)
    builder.add_node("phi_screen", phi_screen)
    builder.add_node("classify", classify)
    builder.add_node("orchestrate", orchestrate)
    builder.add_node("department_worker", department_worker)
    builder.add_node("synthesize", synthesize)
    builder.add_node("discard", discard)

    builder.add_edge(START, "convert")
    builder.add_edge("convert", "phi_screen")
    builder.add_conditional_edges("phi_screen", route_after_phi, {"classify": "classify", "discard": "discard"})
    builder.add_conditional_edges(
        "classify", route_after_classify, {"orchestrate": "orchestrate", "discard": "discard"}
    )
    builder.add_conditional_edges("orchestrate", fan_out_to_workers, ["department_worker"])
    builder.add_edge("department_worker", "synthesize")
    builder.add_edge("synthesize", END)
    builder.add_edge("discard", END)
    return builder


def route_after_phi(state: RfpIntakeState) -> str:
    # Redactar y parar (decisión del usuario): con PHI no corre ningún agente.
    return "discard" if state.get("phi_categories") else "classify"


def route_after_classify(state: RfpIntakeState) -> str:
    return "orchestrate" if state["classification"]["accepted"] else "discard"


def fan_out_to_workers(state: RfpIntakeState) -> List[Send]:
    return [
        Send(
            "department_worker",
            WorkerInput(
                department_id=department_id,
                metadata=state["metadata"],
                excerpts=state["excerpts"].get(department_id, []),
            ),
        )
        for department_id in state["departments"]
    ]


def validate_structure(builder: StateGraph) -> None:
    """Todo nodo alcanzable desde START y con camino a END (LangGraph 0.6 no
    lo comprueba al compilar; ver la sección del agente en CLAUDE.md)."""
    edges = list(builder.edges)
    for source, branches in builder.branches.items():
        for name, branch in branches.items():
            if not branch.ends:
                raise RfpGraphError(f"La arista condicional '{name}' de '{source}' no declara sus destinos")
            edges.extend((source, target) for target in branch.ends.values())

    def reachable(start: str, pairs: List[tuple]) -> set:
        seen, queue = {start}, deque([start])
        while queue:
            node = queue.popleft()
            for source, target in pairs:
                if source == node and target not in seen:
                    seen.add(target)
                    queue.append(target)
        return seen

    nodes = set(builder.nodes)
    if nodes - reachable(START, edges):
        raise RfpGraphError(f"Nodos sin conexión desde START: {sorted(nodes - reachable(START, edges))}")
    backwards = [(target, source) for source, target in edges]
    if nodes - reachable(END, backwards):
        raise RfpGraphError(f"Nodos que nunca llegan a END: {sorted(nodes - reachable(END, backwards))}")


def compile_rfp_intake_graph(builder: Optional[StateGraph] = None) -> Any:
    builder = builder or build_rfp_intake_graph()
    validate_structure(builder)
    return builder.compile(name=GRAPH_NAME)
