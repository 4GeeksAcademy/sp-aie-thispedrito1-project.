"""Evals del agente LangGraph sobre traces grabados (Agente de Soporte, Partes 1 y 2).

    services/api/.venv/bin/python -m pytest tests/pipelines/test_agent_evals.py -v

No ejecutan el agente: leen los traces que dejó una corrida real por caso
(`scripts/record_agent_traces.py`, contra Qdrant, el modelo y los gestores
de incidencias e inventario de verdad) y comprueban sobre ellos el
enrutamiento entre RAG y tools, el orden de los nodos, el fallback y el
anclaje de la respuesta en la base de conocimiento o en el dato en vivo. Así el resultado no cambia
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


def status_pattern(label: str) -> str:
    """El estado en cualquier concordancia: "resuelta" (la incidencia) o
    "resuelto" (el ticket). "en curso" no tiene género. Otro estado no encaja."""
    stem = label[:-1] if label.endswith("a") else label
    suffix = "[ao]" if label.endswith("a") else ""
    return rf"\b{re.escape(stem)}{suffix}s?\b"


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


def tool_results(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [result for step in trace["steps"] for result in step["output"].get("tool_results", [])]


def test_suite_has_at_least_three_cases_and_one_grounding_case():
    assert len(CASES) >= 3
    assert any(case["expect"].get("answer_contains_all") and case["expect"].get("retrieved_documents_include") for case in CASES)


def test_suite_covers_routing_to_a_tool_to_the_rag_and_the_fallback():
    """Parte 2: al menos un caso solo-tool, uno solo-RAG y uno de fallback."""
    sources = [tuple(case["expect"].get("sources_used", [])) for case in CASES]
    assert ("incidents",) in sources
    assert ("knowledge_base",) in sources
    assert any(case["expect"]["outcome"] == "tool_fallback" for case in CASES)


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
    # Qué fuentes (RAG, tools o ambos) y en qué orden, tal como lo resume el trace.
    assert trace["sources_used"] == expect["sources_used"]
    for before, after in expect.get("must_run_before", []):
        assert before in sequence and after in sequence
        assert sequence.index(before) < sequence.index(after), f"'{before}' debe ejecutarse antes que '{after}'"
    for node in expect.get("must_not_run", []):
        assert node not in sequence, f"'{node}' no debía ejecutarse"


def test_generation_only_runs_on_evidence_from_that_same_run(case_and_trace):
    """Si hubo generación, fue sobre chunks recuperados o datos en vivo correctos
    de esa misma corrida; el fallback solo aparece si una tool no dio su dato."""
    _, trace = case_and_trace
    outputs = {step["node"]: step["output"] for step in trace["steps"]}
    results = tool_results(trace)
    if "generate" in outputs:
        chunks = outputs.get("retrieve", {}).get("context", [])
        assert chunks or any(r["status"] == "ok" for r in results), "generate no puede ejecutarse sin evidencia"
        assert all(r["status"] == "ok" for r in results)
    if "no_information" in outputs:
        assert outputs["retrieve"]["context"] == []
    if "tool_fallback" in outputs:
        assert any(r["status"] != "ok" for r in results)


def test_tool_calls_are_typed_and_bounded_by_their_timeout(case_and_trace):
    case, trace = case_and_trace
    for result in tool_results(trace):
        assert result["status"] in {"ok", "not_found", "unavailable"}
        assert isinstance(result["timeout_s"], float) and result["timeout_s"] > 0
        # Margen para el arranque del hilo: nunca muy por encima del timeout.
        assert result["duration_ms"] <= result["timeout_s"] * 1000 + 500
    expected_error = case["expect"].get("tool_error_type")
    if expected_error:
        assert [r["error_type"] for r in tool_results(trace)] == [expected_error]


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


def test_answer_is_grounded_in_the_live_data_of_that_run(case_and_trace):
    """Anclaje en el dato en vivo: la respuesta repite el estado o el total que
    devolvió la tool en ESA corrida, no uno recordado ni inventado."""
    case, trace = case_and_trace
    expect = case["expect"]
    answer = normalize(trace["answer"] or "")
    incident_data = [r["data"] for r in tool_results(trace) if r["tool"] == "lookup_incident" and r["status"] == "ok"]
    if expect.get("answer_mentions_live_status"):
        labels = {normalize(i["status_label_es"]) for data in incident_data for i in data["incidents"]}
        assert labels and all(re.search(status_pattern(label), answer) for label in labels), f"sin el estado en vivo {labels}: {trace['answer']!r}"
    if expect.get("answer_mentions_live_count"):
        totals = {str(data["total"]) for data in incident_data}
        assert totals and all(re.search(rf"\b{total}\b", answer) for total in totals), f"sin el total {totals}: {trace['answer']!r}"
    stock = [i for r in tool_results(trace) if r["tool"] == "check_inventory_stock" and r["status"] == "ok" for i in r["data"]["items"]]
    if expect.get("answer_mentions_live_stock"):
        assert stock and all(re.search(rf"\b{i['current_stock']}\b", answer) for i in stock), f"sin el stock en vivo: {trace['answer']!r}"
