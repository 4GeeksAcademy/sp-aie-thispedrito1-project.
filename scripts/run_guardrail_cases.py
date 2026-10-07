"""Recorre data/eval/guardrail-cases.json contra el agente y resume el harness
(Ticket #SEC-114). Es la evidencia del PR y el "comando" de resumen del README.

    services/api/.venv/bin/python scripts/run_guardrail_cases.py          # sin red: solo guardarraíles
    services/api/.venv/bin/python scripts/run_guardrail_cases.py --live   # agente real (modelo + Qdrant)

Sin `--live`, los mensajes que el harness deja pasar no se envían a ningún
modelo: se marcan como "→ grafo". Con `--live` se ejecuta el mismo
`handle_turn` que usa POST /agent/query (sin memoria: store=None, para no
escribir en Supabase), con el grafo real. Las incidencias necesitan la API en
8000 y el servidor MCP; si no están, esa pregunta cae a su fallback honesto.

Escribe `docs/agent/guardrails-evidence/cases.json` (con `--live`) o solo
imprime (sin `--live`). Nunca escribe en logs el texto de los mensajes: el
script imprime los casos porque son de prueba y no contienen PHI real.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "services" / "api"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from services.agent.guardrails.monitor import MONITOR  # noqa: E402
from services.agent.memory.conversation import handle_turn  # noqa: E402

CASES_PATH = ROOT / "data" / "eval" / "guardrail-cases.json"
EVIDENCE_PATH = ROOT / "docs" / "agent" / "guardrails-evidence" / "cases.json"


def _offline_run(agent, question, *, memories=None):
    return SimpleNamespace(
        answer="→ grafo (RAG / tools / memoria)",
        outcome="allowed",
        trace_id=None,
        memory_proposal=None,
        user_requested_memory=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="ejecutar el agente real con el modelo")
    args = parser.parse_args()

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    if args.live:
        from services.agent.graph import compile_agent_graph

        agent, run_fn, extra = compile_agent_graph(), None, {}
    else:
        agent, run_fn = None, _offline_run
        extra = {"general_fn": lambda q, mode: SimpleNamespace(answer=f"→ modo general ({mode})", output_event=None)}

    MONITOR.reset()
    results, failures = [], 0
    for case in cases:
        user = f"seq-{case['sequence']}" if case.get("sequence") else f"user-{case['id']}"
        kwargs = dict(extra)
        if run_fn is not None:
            kwargs["run_fn"] = run_fn
        try:
            result = handle_turn(agent, case["message"], user_id=user, store=None, **kwargs)
        except Exception as exc:  # proveedor caído en --live: se registra y se sigue
            node = getattr(exc, "node", None)
            print(f"[ERROR] {case['id']}: {type(exc).__name__}" + (f" en el nodo {node}" if node else ""), file=sys.stderr)
            failures += 1
            continue
        action = result.guardrail.action if result.guardrail else "allow"
        # La regulación fuera de la KB pasa la entrada (allow) y el grafo la
        # manda después al modo general: para el caso, la entrada fue "allow".
        input_action = "allow" if result.guardrail and result.guardrail.guard == "regulation_general" else action
        ok = input_action == case["expect"]["action"]
        failures += not ok
        results.append(
            {
                "id": case["id"],
                "message": case["message"],
                "expected_action": case["expect"]["action"],
                "action": action,
                "guard": result.guardrail.guard if result.guardrail else None,
                "failure_type": result.guardrail.failure_type if result.guardrail else None,
                "outcome": result.outcome,
                "answer": result.answer,
                "ok": ok,
            }
        )
        print(f"{'OK ' if ok else 'MAL'} {case['id']:<32} {action:<8} {results[-1]['guard'] or '-'}")
        if args.live:
            print("    " + result.answer.replace("\n", "\n    "))

    summary = MONITOR.summary()
    print("\nResumen de activaciones (igual que GET /agent/guardrails/summary):")
    print(json.dumps({k: summary[k] for k in ("total", "by_guard", "by_failure_type", "by_action")}, ensure_ascii=False, indent=2))

    if args.live:
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_PATH.write_text(
            json.dumps({"cases": results, "summary": summary}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nEvidencia escrita en {EVIDENCE_PATH.relative_to(ROOT)}")
    if failures:
        print(f"\n{failures} caso(s) no dieron la acción esperada", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
