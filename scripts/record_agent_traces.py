"""Graba un trace real por cada caso de evaluación del agente LangGraph.

Uso (desde la raíz del repo, con el venv de la API, Qdrant levantado, la
colección indexada con scripts/index_knowledge_base.py y las variables
LLM_* / *_MODEL en services/api/.env):

    services/api/.venv/bin/python scripts/record_agent_traces.py

Ejecuta el MISMO grafo compilado que sirve POST /agent/query, sin dobles,
para cada caso de data/eval/agent-eval-cases.json, y deja su trace en
data/eval/agent-traces/<id>.json (versionado: es la evidencia del PR).
Después, tests/pipelines/test_agent_evals.py evalúa esos traces sin volver a
llamar al modelo. Hay que volver a grabar si cambian los casos, los
documentos, el prompt, el umbral o el grafo.

Las preguntas de los casos no contienen datos de pacientes; aun así el trace
solo guarda su huella, igual que en producción.
Códigos de salida: 0 todas las corridas completadas; 1 alguna falló.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import data.pipelines  # noqa: E402,F401  (añade services/api a sys.path)
from services.agent.graph import compile_agent_graph  # noqa: E402
from services.agent.tracing import AgentRunError, run_agent  # noqa: E402

CASES_PATH = ROOT_DIR / "data" / "eval" / "agent-eval-cases.json"


def main() -> int:
    suite = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    trace_dir = ROOT_DIR / suite["trace_dir"]
    agent = compile_agent_graph()
    failures = 0

    for case in suite["cases"]:
        try:
            result = run_agent(
                agent,
                case["question"],
                trace_id=f"eval-{case['id']}",
                trace_dir=trace_dir,
                file_stem=case["id"],
            )
        except AgentRunError as exc:
            failures += 1
            print(f"[FALLO] {case['id']}: nodo {exc.node} ({type(exc.cause).__name__})", file=sys.stderr)
            continue
        route = " > ".join(result.trace["node_sequence"])
        print(f"[OK] {case['id']}: {route} -> {result.outcome} ({result.trace['duration_ms']:.0f} ms)")

    print(f"Traces en {trace_dir.relative_to(ROOT_DIR)}/")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
