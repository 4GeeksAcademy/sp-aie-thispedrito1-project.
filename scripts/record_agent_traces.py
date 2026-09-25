"""Graba un trace real por cada caso de evaluación del agente LangGraph.

Uso (desde la raíz del repo, con el venv de la API):

    # 1. Qdrant con la colección indexada (scripts/index_knowledge_base.py)
    # 2. Una TinyDB con las incidencias del CSV histórico, sin tocar la versionada:
    cp services/api/data/suppliers.db.json /tmp/agent-eval.db.json
    SUPPLIERS_DB_PATH=/tmp/agent-eval.db.json services/api/.venv/bin/python scripts/seed_incidents.py
    # 3. Supabase despierto (DATABASE_URL en services/api/.env) para el inventario
    SUPPLIERS_DB_PATH=/tmp/agent-eval.db.json services/api/.venv/bin/python scripts/record_agent_traces.py
    SUPPLIERS_DB_PATH=... services/api/.venv/bin/python scripts/record_agent_traces.py ticket-status off-topic  # solo esos casos

Ejecuta el MISMO grafo compilado que sirve POST /agent/query, sin dobles:
el modelo real decide las fuentes y las tools leen de los gestores reales.
Guarda cada trace en data/eval/agent-traces/<id>.json (versionado: es la
evidencia del PR); tests/pipelines/test_agent_evals.py los evalúa después sin
volver a llamar a nada. Hay que volver a grabar si cambian los casos, los
documentos, el prompt, el umbral, las tools o el grafo.

Casos con `"outage": "incidents"`: el gestor de incidencias se sustituye por
uno que no responde nunca (duerme más que el timeout), para ejercitar el
timeout real de 3 s de la tool y la ruta de fallback. No cambia ningún dato.

Las preguntas de los casos no contienen datos de pacientes; aun así el trace
solo guarda su huella, igual que en producción.
Códigos de salida: 0 todas las corridas completadas; 1 alguna falló.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import data.pipelines  # noqa: E402,F401  (añade services/api a sys.path)
from services.agent.graph import AgentNodes, build_agent_graph, compile_agent_graph  # noqa: E402
from services.agent.tools import incidents  # noqa: E402
from services.agent.tracing import AgentRunError, run_agent  # noqa: E402

CASES_PATH = ROOT_DIR / "data" / "eval" / "agent-eval-cases.json"
OUTAGE_SLEEP_S = incidents.TIMEOUT_S + 5


def _unresponsive_repository():
    time.sleep(OUTAGE_SLEEP_S)
    raise ConnectionError("incident manager did not answer")


def _agent_with_incident_outage():
    nodes = AgentNodes(
        incident_tool_fn=lambda payload: incidents.lookup_incident(payload, repository_factory=_unresponsive_repository)
    )
    return compile_agent_graph(build_agent_graph(nodes))


def _check_incident_manager_has_data() -> bool:
    from incident_repository import IncidentRepository

    if IncidentRepository().list():
        return True
    print(
        "El gestor de incidencias no tiene datos: siembra una copia con scripts/seed_incidents.py "
        "y pásala con SUPPLIERS_DB_PATH (ver el docstring).",
        file=sys.stderr,
    )
    return False


def main(selected: list) -> int:
    suite = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    trace_dir = ROOT_DIR / suite["trace_dir"]
    cases = [case for case in suite["cases"] if not selected or case["id"] in selected]
    if not _check_incident_manager_has_data():
        return 1

    agent = compile_agent_graph()
    outage_agent = _agent_with_incident_outage()
    failures = 0

    for case in cases:
        graph = outage_agent if case.get("outage") == "incidents" else agent
        try:
            result = run_agent(graph, case["question"], trace_id=f"eval-{case['id']}", trace_dir=trace_dir, file_stem=case["id"])
        except AgentRunError as exc:
            failures += 1
            print(f"[FALLO] {case['id']}: nodo {exc.node} ({type(exc.cause).__name__})", file=sys.stderr)
            continue
        trace = result.trace
        print(
            f"[OK] {case['id']}: {' > '.join(trace['node_sequence'])} -> {result.outcome} "
            f"(fuentes: {trace['sources_used'] or '-'}, {trace['duration_ms']:.0f} ms)"
        )

    print(f"Traces en {trace_dir.relative_to(ROOT_DIR)}/")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
