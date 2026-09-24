"""Evals del agente LangGraph sobre traces grabados (Agente de Soporte, Parte 1).

    services/api/.venv/bin/python -m pytest tests/pipelines/test_agent_evals.py -v

No ejecutan el agente: leen los traces que dejó una corrida real por caso
(`scripts/record_agent_traces.py`, contra Qdrant y el modelo de verdad) y
comprueban sobre ellos el recorrido, el orden de los nodos y el anclaje de la
respuesta en la base de conocimiento del CONTEXT. Así el resultado no cambia
de una ejecución a otra ni cuesta llamadas al modelo.

Cada trace se valida primero contra su caso (huella SHA-256 de la pregunta):
si alguien cambia la pregunta en agent-eval-cases.json y no vuelve a grabar,
el eval falla en vez de juzgar un trace de otra pregunta.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest

from services.agent.tracing import TRACE_SCHEMA_VERSION, fingerprint

ROOT = Path(__file__).resolve().parents[2]
CASES_FILE = ROOT / "data" / "eval" / "agent-eval-cases.json"
SUITE = json.loads(CASES_FILE.read_text(encoding="utf-8"))
TRACE_DIR = ROOT / SUITE["trace_dir"]
CASES: List[Dict[str, Any]] = SUITE["cases"]
CASE_IDS = [case["id"] for case in CASES]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def load_trace(case: Dict[str, Any]) -> Dict[str, Any]:
    path = TRACE_DIR / f"{case['id']}.json"
    if not path.exists():
        pytest.fail(f"Falta {path.relative_to(ROOT)}: ejecuta scripts/record_agent_traces.py")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(params=CASES, ids=CASE_IDS)
def case_and_trace(request):
    return request.param, load_trace(request.param)


def test_suite_has_at_least_three_cases_and_one_grounding_case():
    assert len(CASES) >= 3
    assert any(case["expect"].get("answer_contains_all") and case["expect"].get("retrieved_documents_include") for case in CASES)


def test_trace_belongs_to_its_case_and_completed(case_and_trace):
    case, trace = case_and_trace
    assert trace["schema_version"] == TRACE_SCHEMA_VERSION
    assert trace["input"]["question"] == fingerprint(case["question"]), "trace de otra pregunta: vuelve a grabar"
    assert trace["status"] == "completed"
    assert trace["error"] is None


def test_trace_is_consistent_with_its_checkpoints(case_and_trace):
    """Un checkpoint por transición: entrada + START + uno por nodo ejecutado."""
    _, trace = case_and_trace
    assert [step["node"] for step in trace["steps"]] == trace["node_sequence"]
    assert len(trace["checkpoints"]) == len(trace["steps"]) + 2
    assert trace["checkpoints"][-1]["next"] == []
    steps = [checkpoint["step"] for checkpoint in trace["checkpoints"]]
    assert steps == sorted(steps)


def test_route_through_the_graph(case_and_trace):
    case, trace = case_and_trace
    expect = case["expect"]
    sequence = trace["node_sequence"]

    assert sequence == expect["node_sequence"]
    assert trace["outcome"] == expect["outcome"]
    for before, after in expect.get("must_run_before", []):
        assert before in sequence and after in sequence
        assert sequence.index(before) < sequence.index(after), f"'{before}' debe ejecutarse antes que '{after}'"
    for node in expect.get("must_not_run", []):
        assert node not in sequence, f"'{node}' no debía ejecutarse"


def test_generation_uses_exactly_what_retrieve_returned(case_and_trace):
    """Si hubo generación, fue sobre contexto no vacío recuperado en esa misma corrida."""
    _, trace = case_and_trace
    outputs = {step["node"]: step["output"] for step in trace["steps"]}
    if "generate" in outputs:
        assert outputs["retrieve"]["context"], "generate no puede ejecutarse sobre contexto vacío"
    if "no_information" in outputs:
        assert outputs["retrieve"]["context"] == []


def test_answer_is_grounded_in_the_knowledge_base(case_and_trace):
    case, trace = case_and_trace
    expect = case["expect"]
    answer = normalize(trace["answer"] or "")
    retrieved = {
        chunk["source_document"]
        for step in trace["steps"]
        if step["node"] == "retrieve"
        for chunk in step["output"]["context"]
    }

    for document in expect.get("retrieved_documents_include", []):
        assert document in retrieved, f"no se recuperó '{document}' (recuperados: {sorted(retrieved)})"
    for fragment in expect.get("answer_contains_all", []):
        assert normalize(fragment) in answer, f"la respuesta no contiene '{fragment}': {trace['answer']!r}"
    options = expect.get("answer_contains_any", [])
    if options:
        assert any(normalize(option) in answer for option in options), f"la respuesta no contiene ninguno de {options}: {trace['answer']!r}"
