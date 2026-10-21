"""(Re)lanza la Parte 2 (borradores + evaluación) de un ticket de RFP.

    services/api/.venv/bin/python scripts/generate_rfp_response.py <ticket_id> [--inline]

Útil para un ticket que quedó en `error` durante la generación o en
`analisis_completo` sin encadenar (Redis caído al terminar la Parte 1).
Vuelve el ticket a `analisis_completo` sin tocar el análisis de la Parte 1 y:
- por defecto, encola la tarea de Celery (necesita Redis + worker);
- con `--inline`, ejecuta el grafo en este mismo proceso (sin Celery).

Necesita DATABASE_URL (services/api/.env) y un handoff de la Parte 1.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "services" / "api"
for path in (str(ROOT_DIR), str(API_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from sqlmodel import Session  # noqa: E402

from data.pipelines.rfp_intake import repository  # noqa: E402
from data.pipelines.rfp_intake.response_runner import run_response  # noqa: E402


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print("Uso: generate_rfp_response.py <ticket_id> [--inline]", file=sys.stderr)
        return 1
    try:
        ticket_id = uuid.UUID(args[0])
    except ValueError:
        print(f"ticket_id no válido: {args[0]}", file=sys.stderr)
        return 1

    from database import get_inventory_engine

    try:
        engine = get_inventory_engine()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    with Session(engine) as session:
        ticket = repository.get_ticket(session, ticket_id)
        if ticket is None:
            print(f"No existe el ticket {ticket_id}", file=sys.stderr)
            return 1
        if not ticket.handoff_payload:
            print("El ticket no tiene handoff de la Parte 1: reprocésalo con reprocess_rfp.py", file=sys.stderr)
            return 1
        repository.reset_for_response(session, ticket)
        if "--inline" in argv:
            try:
                status = run_response(session, ticket_id)
            except Exception as exc:  # noqa: BLE001 — se refleja en el ticket
                repository.mark_error(session, ticket_id, f"Generación de borradores fallida ({type(exc).__name__}).")
                print(f"La generación falló: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1
            print(f"{ticket.rfp_id}: {status}")
            return 0

    from services.tasks.rfp_tasks import enqueue_rfp_response

    try:
        task_id = enqueue_rfp_response(ticket_id)
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo encolar (¿Redis arrancado?): {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"{ticket_id} encolado para generar borradores (task_id={task_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
