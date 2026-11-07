"""Grafo de generación y evaluación de respuestas (Hito 9, Parte 2).

    START ─Send×N─→ draft_section ─→ END        (un subgrafo por departamento, en paralelo)

    draft_section = subgrafo `rfp_section`:
        generate ─┬→ evaluate_readability ─┐
                  ├→ evaluate_relevance   ─┼→ judge ─(suspende y quedan intentos)→ generate
                  └→ evaluate_compliance  ─┘       └─(aprueba / agotado / no arreglable)→ END

- Assignment orchestrator = el fan-out con `Send`: un subgrafo por
  workstream del handoff de la Parte 1. Cada departamento itera a su ritmo:
  un generador que necesita tres vueltas no retrasa a los que aprueban a la
  primera (salvo al final, cuando el runner recoge los tres).
- Los tres evaluadores corren en el mismo superstep y cada uno escribe SU
  clave del estado (`readability`, `relevance`, `compliance`); solo `judge`
  las combina. Nunca dos nodos escriben la misma clave a la vez (pregunta de
  diseño 2), así que no hace falta reducer ni bloqueo.
- Límite de iteraciones: `max_iterations` generaciones por sección (1 + 2
  correcciones por defecto). Al agotarlo se conserva el último borrador y su
  EvaluationResult y la sección sale `needs_human_review`; el ticket sigue.
- Como el grafo de la Parte 1, no toca la base de datos: avisa del progreso
  con `on_progress` y el runner decide qué escribir.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Callable, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from data.pipelines.rfp_intake import evaluators
from data.pipelines.rfp_intake.agents import LlmCall, call_model
from data.pipelines.rfp_intake.generators import GENERATORS, GeneratorInput
from data.pipelines.rfp_intake.graph import validate_structure
from data.pipelines.rfp_intake.models import SECTION_NEEDS_REVIEW, SECTION_PASSED

GRAPH_NAME = "rfp_response"
SECTION_GRAPH_NAME = "rfp_section"
# 1 borrador + 2 correcciones. Con deepseek (5-20 s por llamada) y 2 llamadas
# por vuelta, una sección que agota el límite tarda ~1-2 min.
DEFAULT_MAX_ITERATIONS = 3

EVENT_DRAFTING = "drafting"
EVENT_EVALUATING = "evaluating"
EVENT_EVALUATED = "evaluated"

# (department_id, evento, datos) → None. Se llama desde hilos en paralelo.
Progress = Callable[[str, str, Dict[str, Any]], None]


def _no_progress(_department_id: str, _event: str, _data: Dict[str, Any]) -> None:
    return None


class SectionState(TypedDict, total=False):
    section_id: str
    generator_input: Dict[str, Any]
    figures_allowed: List[str]
    max_iterations: int
    iteration: int
    draft: str
    # Una clave por evaluador: escrituras paralelas sin conflicto.
    readability: Dict[str, Any]
    relevance: Dict[str, Any]
    compliance: Dict[str, Any]
    evaluation: Dict[str, Any]
    history: Annotated[List[Dict[str, Any]], operator.add]


class SectionTask(TypedDict):
    """Lo que el orquestador manda a cada subgrafo."""
    section_id: str
    generator_input: Dict[str, Any]
    figures_allowed: List[str]


class ResponseState(TypedDict, total=False):
    ticket_id: str
    handoff: Dict[str, Any]
    max_iterations: int
    # Reducer: cada subgrafo añade su sección; sin él, la última pisaría al resto.
    sections: Annotated[List[Dict[str, Any]], operator.add]


def build_section_graph(*, llm: LlmCall = call_model, on_progress: Progress = _no_progress) -> StateGraph:
    def generate(state: SectionState) -> Dict[str, Any]:
        payload = GeneratorInput.model_validate(state["generator_input"])
        iteration = state.get("iteration", 0) + 1
        on_progress(payload.department_id, EVENT_DRAFTING, {"iteration": iteration})
        previous = state.get("evaluation")
        draft = GENERATORS[payload.department_id].generate(
            payload,
            previous_draft=state.get("draft") if previous else None,
            feedback=previous["feedback_for_generator"] if previous else None,
            llm=llm,
        )
        on_progress(payload.department_id, EVENT_EVALUATING, {"iteration": iteration, "draft": draft})
        return {"draft": draft, "iteration": iteration}

    # Cada evaluador recibe solo su parte del estado (pregunta de diseño 1).
    def evaluate_readability(state: SectionState) -> Dict[str, Any]:
        return {"readability": evaluators.evaluate_readability(state["draft"]).model_dump()}

    def evaluate_relevance(state: SectionState) -> Dict[str, Any]:
        aspects = state["generator_input"].get("key_aspects", [])
        return {"relevance": evaluators.evaluate_relevance(state["draft"], aspects, llm=llm).model_dump()}

    def evaluate_compliance(state: SectionState) -> Dict[str, Any]:
        payload = state["generator_input"]
        result = evaluators.evaluate_compliance(
            state["draft"],
            department_id=payload["department_id"],
            client_country=payload["metadata"].get("client_country"),
            figures_allowed=set(state["figures_allowed"]),
        )
        return {"compliance": result.model_dump()}

    def judge(state: SectionState) -> Dict[str, Any]:
        department_id = state["generator_input"]["department_id"]
        result = evaluators.combine(
            section_id=state["section_id"],
            department_id=department_id,
            iteration=state["iteration"],
            readability=evaluators.ReadabilityResult.model_validate(state["readability"]),
            relevance=evaluators.RelevanceResult.model_validate(state["relevance"]),
            compliance=evaluators.ComplianceResult.model_validate(state["compliance"]),
        )
        record = result.to_record()
        on_progress(department_id, EVENT_EVALUATED, {"iteration": state["iteration"], "draft": state["draft"], "evaluation": record})
        return {"evaluation": record, "history": [record]}

    builder = StateGraph(SectionState)
    builder.add_node("generate", generate)
    builder.add_node("evaluate_readability", evaluate_readability)
    builder.add_node("evaluate_relevance", evaluate_relevance)
    builder.add_node("evaluate_compliance", evaluate_compliance)
    builder.add_node("judge", judge)

    builder.add_edge(START, "generate")
    for evaluator in ("evaluate_readability", "evaluate_relevance", "evaluate_compliance"):
        builder.add_edge("generate", evaluator)
        builder.add_edge(evaluator, "judge")
    builder.add_conditional_edges("judge", route_after_judge, {"generate": "generate", END: END})
    return builder


def route_after_judge(state: SectionState) -> str:
    evaluation = state["evaluation"]
    if evaluation["overall_pass"] or not evaluation["retryable"]:
        return END
    if state["iteration"] >= state.get("max_iterations", DEFAULT_MAX_ITERATIONS):
        return END  # límite agotado: el último borrador va a revisión humana
    return "generate"


def section_status(evaluation: Dict[str, Any]) -> str:
    return SECTION_PASSED if evaluation["overall_pass"] else SECTION_NEEDS_REVIEW


def build_response_graph(
    *,
    llm: LlmCall = call_model,
    on_progress: Progress = _no_progress,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> StateGraph:
    section_graph = compile_graph(build_section_graph(llm=llm, on_progress=on_progress), SECTION_GRAPH_NAME)

    def draft_section(task: SectionTask) -> Dict[str, Any]:
        final = section_graph.invoke({**task, "max_iterations": max_iterations, "iteration": 0, "history": []})
        evaluation = final["evaluation"]
        return {
            "sections": [{
                "department_id": task["generator_input"]["department_id"],
                "draft_content": final["draft"],
                "iterations": final["iteration"],
                "evaluation": evaluation,
                "history": final["history"],
                "status": section_status(evaluation),
            }]
        }

    builder = StateGraph(ResponseState)
    builder.add_node("draft_section", draft_section)
    builder.add_conditional_edges(START, assign_sections, ["draft_section"])
    builder.add_edge("draft_section", END)
    return builder


def assign_sections(state: ResponseState) -> List[Send]:
    """Assignment orchestrator: un subgrafo por workstream del handoff."""
    handoff = state["handoff"]
    metadata = handoff["metadata"]
    tasks = []
    for workstream in handoff["workstreams"]:
        department_id = workstream["department_id"]
        if department_id not in GENERATORS:
            continue
        payload = GeneratorInput(
            department_id=department_id,
            metadata=metadata,
            key_aspects=workstream.get("key_aspects", []),
            open_questions=workstream.get("open_questions", []),
            summary=handoff.get("summary"),
        )
        tasks.append(Send("draft_section", SectionTask(
            section_id=f"{handoff['ticket_id']}:{department_id}",
            generator_input=payload.model_dump(),
            figures_allowed=sorted(evaluators.allowed_figures(metadata, workstream, handoff.get("summary"))),
        )))
    return tasks


def compile_graph(builder: StateGraph, name: str) -> Any:
    validate_structure(builder)
    return builder.compile(name=name)


def compile_response_graph(
    *, llm: LlmCall = call_model, on_progress: Progress = _no_progress, max_iterations: int = DEFAULT_MAX_ITERATIONS
) -> Any:
    return compile_graph(build_response_graph(llm=llm, on_progress=on_progress, max_iterations=max_iterations), GRAPH_NAME)

