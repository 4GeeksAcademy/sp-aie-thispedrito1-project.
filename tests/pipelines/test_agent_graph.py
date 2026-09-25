"""Tests de estructura del agente LangGraph: nodos, aristas, compilación,
checkpointing y trace, por el camino del RAG (Parte 1). Las tools y el
enrutamiento entre fuentes (Parte 2) están en test_agent_tools.py.

    services/api/.venv/bin/python -m pytest tests/pipelines/test_agent_graph.py

Sin red: `retrieve`, la generación y el planificador son dobles que
registran sus llamadas (el planificador elige siempre la base de conocimiento).
Los evals sobre corridas reales están en test_agent_evals.py.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START

from data.pipelines import rag
from services.agent import graph as agent_graph
from services.agent import planner
from services.agent.graph import (
    CHECK_INVENTORY_STOCK,
    GENERATE,
    LOOKUP_INCIDENT,
    PLAN_SOURCES,
    TOOL_FALLBACK,
    NO_INFORMATION,
    NO_INFORMATION_ANSWER,
    RECEIVE_QUESTION,
    REJECT_QUESTION,
    RETRIEVE,
    AgentGraphError,
    AgentNodes,
    AgentStateError,
    build_agent_graph,
    compile_agent_graph,
    route_next_source,
)
from services.agent.planner import Plan, PlannedCall
from services.agent.tracing import AgentRunError, fingerprint, run_agent

QUESTION = "¿Cuánto se cobra por un no-show a un paciente de pago privado en Texas?"
CHUNK = {
    "company": "healthcore",
    "source_document": "appointment-policy",
    "section": "Política de cancelación",
    "language": "es",
    "chunk_index": 1,
    "text": "Cancelar con menos de 24 horas o no presentarse (no-show): cargo de 50 USD.",
    "score": 0.71,
}


class Doubles:
    """Dobles de retrieve() y generate_answer() que registran cada llamada."""

    def __init__(self, chunks: List[Dict[str, Any]], answer: str = "Se cobra un cargo de 50 USD.") -> None:
        self.chunks = chunks
        self.answer = answer
        self.retrieve_calls: List[Dict[str, Any]] = []
        self.generate_calls: List[Dict[str, Any]] = []

    def retrieve(self, question: str, *, k: int, min_score: float) -> List[Dict[str, Any]]:
        self.retrieve_calls.append({"question": question, "k": k, "min_score": min_score})
        return [dict(chunk) for chunk in self.chunks]

    def generate(self, question: str, context: List[Dict[str, Any]]) -> str:
        self.generate_calls.append({"question": question, "context": context})
        return self.answer

    def plan(self, question: str) -> Plan:
        return Plan(calls=[PlannedCall(source="knowledge_base")], status="model")

    def nodes(self) -> AgentNodes:
        return AgentNodes(
            retrieve_fn=self.retrieve,
            generate_fn=self.generate,
            min_score_fn=lambda: 0.38,
            k=5,
            planner_fn=self.plan,
        )


@pytest.fixture()
def no_monolithic_query(monkeypatch):
    """Si algún nodo llamara a rag.query() (retrieve + generación juntos), el test revienta."""

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Ningún nodo debe llamar a rag.query()")

    monkeypatch.setattr(rag, "query", forbidden)


def compiled_with(doubles: Doubles):
    return compile_agent_graph(build_agent_graph(doubles.nodes()))


def read_trace(result) -> Dict[str, Any]:
    return json.loads(result.trace_path.read_text(encoding="utf-8"))


# --- Compilación ------------------------------------------------------------


def test_graph_compiles_with_conditional_edges_after_every_decision():
    compiled = compiled_with(Doubles([CHUNK]))
    drawable = compiled.get_graph()

    assert set(drawable.nodes) == {
        START, END, RECEIVE_QUESTION, REJECT_QUESTION, PLAN_SOURCES, LOOKUP_INCIDENT,
        CHECK_INVENTORY_STOCK, RETRIEVE, TOOL_FALLBACK, NO_INFORMATION, GENERATE,
    }
    conditional = {(e.source, e.target) for e in drawable.edges if e.conditional}
    assert conditional == {
        (RECEIVE_QUESTION, PLAN_SOURCES),
        (RECEIVE_QUESTION, REJECT_QUESTION),
        (PLAN_SOURCES, LOOKUP_INCIDENT),
        (PLAN_SOURCES, CHECK_INVENTORY_STOCK),
        (PLAN_SOURCES, RETRIEVE),
        (LOOKUP_INCIDENT, CHECK_INVENTORY_STOCK),
        (LOOKUP_INCIDENT, RETRIEVE),
        (LOOKUP_INCIDENT, TOOL_FALLBACK),
        (LOOKUP_INCIDENT, GENERATE),
        (CHECK_INVENTORY_STOCK, RETRIEVE),
        (CHECK_INVENTORY_STOCK, TOOL_FALLBACK),
        (CHECK_INVENTORY_STOCK, GENERATE),
        (RETRIEVE, GENERATE),
        (RETRIEVE, NO_INFORMATION),
    }


def test_compile_fails_clearly_on_an_edge_to_an_unknown_node():
    builder = build_agent_graph(Doubles([CHUNK]).nodes())
    builder.add_edge(GENERATE, "notify_supervisor")

    with pytest.raises(AgentGraphError, match="no compila.*notify_supervisor"):
        compile_agent_graph(builder)


def test_compile_fails_clearly_on_an_orphan_node():
    """LangGraph 0.6 compila un nodo sin aristas de entrada; nuestra validación no."""
    builder = build_agent_graph(Doubles([CHUNK]).nodes())
    builder.add_node("escalate", lambda state: {})
    builder.add_edge("escalate", END)

    with pytest.raises(AgentGraphError, match="sin conexión desde START.*escalate"):
        compile_agent_graph(builder)


def test_compile_fails_clearly_on_a_node_that_never_reaches_end():
    builder = build_agent_graph(Doubles([CHUNK]).nodes())
    builder.add_node("dead_end", lambda state: {})
    builder.add_edge(REJECT_QUESTION, "dead_end")

    with pytest.raises(AgentGraphError, match="nunca llegan a END.*dead_end"):
        compile_agent_graph(builder)


# --- Aristas condicionales --------------------------------------------------


KB_PLAN = [{"source": "knowledge_base", "args": {}}]


def test_after_retrieval_generates_only_with_context():
    done = {"question": QUESTION, "plan": KB_PLAN, "completed_sources": ["knowledge_base"]}
    assert route_next_source({**done, "context": [CHUNK]}) == GENERATE
    assert route_next_source({**done, "context": []}) == NO_INFORMATION


def test_after_retrieval_treats_missing_context_as_no_information():
    assert route_next_source({"question": QUESTION, "plan": KB_PLAN, "completed_sources": ["knowledge_base"]}) == NO_INFORMATION


# --- Recorridos completos ---------------------------------------------------


def test_answered_path_retrieves_once_and_generates_from_that_context(tmp_path, no_monolithic_query):
    doubles = Doubles([CHUNK])
    result = run_agent(compiled_with(doubles), f"  {QUESTION}  ", trace_dir=tmp_path)

    assert result.outcome == "answered"
    assert result.answer == "Se cobra un cargo de 50 USD."
    assert result.trace["node_sequence"] == [RECEIVE_QUESTION, PLAN_SOURCES, RETRIEVE, GENERATE]
    assert result.trace["sources_used"] == ["knowledge_base"]
    # Una sola recuperación, con la pregunta normalizada y el umbral afinado.
    assert doubles.retrieve_calls == [{"question": QUESTION, "k": 5, "min_score": 0.38}]
    # La generación recibe exactamente lo que recuperó el nodo retrieve.
    assert doubles.generate_calls == [{"question": QUESTION, "context": [CHUNK]}]


def test_blank_question_is_rejected_before_retrieval(tmp_path, no_monolithic_query):
    doubles = Doubles([CHUNK])
    result = run_agent(compiled_with(doubles), "   ", trace_dir=tmp_path)

    assert result.outcome == "invalid_question"
    assert result.answer is None
    assert result.trace["node_sequence"] == [RECEIVE_QUESTION, REJECT_QUESTION]
    assert result.trace["sources_used"] == []
    assert doubles.retrieve_calls == []
    assert doubles.generate_calls == []


def test_empty_retrieval_answers_honestly_without_calling_the_model(tmp_path, no_monolithic_query):
    doubles = Doubles([])
    result = run_agent(compiled_with(doubles), "¿Cuál es la receta de la paella?", trace_dir=tmp_path)

    assert result.outcome == "no_information"
    assert result.answer == NO_INFORMATION_ANSWER
    assert result.trace["node_sequence"] == [RECEIVE_QUESTION, PLAN_SOURCES, RETRIEVE, NO_INFORMATION]
    assert doubles.generate_calls == []


def test_a_node_writing_outside_the_state_fails_with_its_name(tmp_path):
    class LeakyNodes(AgentNodes):
        def retrieve(self, state):
            return {"context": [], "patient_name": "no debería existir"}

    doubles = Doubles([])
    leaky = LeakyNodes(
        retrieve_fn=doubles.retrieve, generate_fn=doubles.generate, min_score_fn=lambda: 0.38, k=5, planner_fn=doubles.plan
    )

    with pytest.raises(AgentRunError) as excinfo:
        run_agent(compile_agent_graph(build_agent_graph(leaky)), QUESTION, trace_dir=tmp_path)

    assert excinfo.value.node == RETRIEVE
    assert isinstance(excinfo.value.cause, AgentStateError)


# --- Checkpointing ----------------------------------------------------------


def test_each_transition_leaves_a_checkpoint_listed_in_the_trace(tmp_path):
    compiled = compiled_with(Doubles([CHUNK]))
    result = run_agent(compiled, QUESTION, trace_dir=tmp_path, keep_checkpoints=True)

    config = {"configurable": {"thread_id": result.trace_id}}
    history_ids = [s.config["configurable"]["checkpoint_id"] for s in compiled.get_state_history(config)]
    trace_ids = [c["checkpoint_id"] for c in result.trace["checkpoints"]]

    assert trace_ids == list(reversed(history_ids))
    # Entrada + START + un checkpoint por nodo ejecutado; el último ya no tiene siguiente paso.
    assert len(trace_ids) == len(result.trace["steps"]) + 2
    assert result.trace["checkpoints"][-1]["next"] == []
    assert compiled.get_state(config).values["answer"] == result.answer


def test_checkpoints_are_released_by_default(tmp_path):
    compiled = compiled_with(Doubles([CHUNK]))
    result = run_agent(compiled, QUESTION, trace_dir=tmp_path)

    config = {"configurable": {"thread_id": result.trace_id}}
    assert list(compiled.get_state_history(config)) == []
    assert result.trace["checkpoints"], "los ids quedan en el trace aunque el checkpointer se vacíe"


def test_a_run_paused_before_generation_resumes_from_its_checkpoint():
    """Reanudar: la corrida se detiene tras retrieve y continúa sin volver a recuperar."""
    doubles = Doubles([CHUNK])
    paused = build_agent_graph(doubles.nodes()).compile(checkpointer=InMemorySaver(), interrupt_before=[GENERATE])
    config = {"configurable": {"thread_id": "resume-demo"}}

    paused.invoke({"question": QUESTION}, config)
    snapshot = paused.get_state(config)
    assert snapshot.next == (GENERATE,)
    assert snapshot.values["context"] == [CHUNK]
    assert doubles.generate_calls == []

    final = paused.invoke(None, config)
    assert final["answer"] == "Se cobra un cargo de 50 USD."
    assert len(doubles.retrieve_calls) == 1


# --- Trace ------------------------------------------------------------------


def test_trace_is_persisted_with_node_outputs_and_without_the_question_text(tmp_path):
    result = run_agent(compiled_with(Doubles([CHUNK])), QUESTION, trace_dir=tmp_path)
    raw = result.trace_path.read_text(encoding="utf-8")
    trace = read_trace(result)

    assert result.trace_path == tmp_path / f"{result.trace_id}.json"
    assert trace["status"] == "completed"
    assert [step["node"] for step in trace["steps"]] == [RECEIVE_QUESTION, PLAN_SOURCES, RETRIEVE, GENERATE]
    assert [step["index"] for step in trace["steps"]] == [1, 2, 3, 4]
    assert trace["input"]["question"] == fingerprint(QUESTION)
    assert trace["plan"] == KB_PLAN and trace["plan_status"] == "model"
    assert trace["steps"][2]["output"]["context"] == [
        {"source_document": "appointment-policy", "section": "Política de cancelación", "chunk_index": 1, "score": 0.71}
    ]
    assert trace["answer"] == "Se cobra un cargo de 50 USD."
    # Privacidad: ni la pregunta ni el texto de los chunks llegan al archivo.
    assert QUESTION not in raw
    assert CHUNK["text"] not in raw


def test_a_failing_node_still_leaves_a_failed_trace_without_the_raw_error(tmp_path):
    class ProviderDown(Doubles):
        def generate(self, question, context):
            raise RuntimeError("upstream rejected request for ana@example.com")

    doubles = ProviderDown([CHUNK])
    with pytest.raises(AgentRunError) as excinfo:
        run_agent(compiled_with(doubles), QUESTION, trace_dir=tmp_path, trace_id="failed-run")

    assert excinfo.value.node == GENERATE
    raw = (tmp_path / "failed-run.json").read_text(encoding="utf-8")
    trace = json.loads(raw)
    assert trace["status"] == "failed"
    assert trace["error"] == {"node": GENERATE, "type": "RuntimeError"}
    assert trace["node_sequence"] == [RECEIVE_QUESTION, PLAN_SOURCES, RETRIEVE]
    assert "ana@example.com" not in raw


def test_trace_write_failure_does_not_lose_the_answer(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    result = run_agent(compiled_with(Doubles([CHUNK])), QUESTION, trace_dir=blocker)

    assert result.answer == "Se cobra un cargo de 50 USD."
    assert result.trace_path is None


def test_default_nodes_delegate_to_the_rag_pipeline(monkeypatch, tmp_path, no_monolithic_query):
    """Sin dobles inyectados, los nodos llaman a data/pipelines/rag.py (no a una copia)."""
    calls: List[str] = []
    monkeypatch.setattr(rag, "retrieve", lambda q, *, k, min_score: calls.append("retrieve") or [CHUNK])
    monkeypatch.setattr(rag, "generate_answer", lambda q, c: calls.append("generate_answer") or "ok")
    monkeypatch.setattr(rag, "get_min_score", lambda: 0.38)
    monkeypatch.setattr(planner, "plan_sources", lambda q: Plan(calls=[PlannedCall(source="knowledge_base")], status="model"))

    result = run_agent(compile_agent_graph(), QUESTION, trace_dir=tmp_path)

    assert calls == ["retrieve", "generate_answer"]
    assert result.answer == "ok"
    assert agent_graph.AgentNodes().k == rag.DEFAULT_K
