"""(Re)lanza la Parte 3 (aprobación humana y cierre) de un ticket de RFP.

    services/api/.venv/bin/python scripts/advance_rfp_approval.py <ticket_id> [--inline]

`advance` es idempotente: arranca la aprobación si el ticket acaba de salir
de la Parte 2, aplica las decisiones que la API dejó guardadas o reintenta
una revisión que falló a mitad. Útil para un ticket que quedó en `error`
durante la aprobación o sin encadenar (Redis caído al terminar la Parte 2).
- por defecto, encola la tarea de Celery (necesita Redis + worker);
- con `--inline`, lo ejecuta en este proceso (sin Celery).

Necesita DATABASE_URL de Postgres (services/api/.env): ahí vive el checkpointer.
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


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print("Uso: advance_rfp_approval.py <ticket_id> [--inline]", file=sys.stderr)
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
        if not ticket.response_handoff:
            print("El ticket no tiene borradores de la Parte 2: relánzala con generate_rfp_response.py", file=sys.stderr)
            return 1
        if "--inline" in argv:
            from data.pipelines.rfp_intake.approval_runner import advance
            from data.pipelines.rfp_intake.checkpointer import open_checkpointer

            try:
                with open_checkpointer() as checkpointer:
                    status = advance(session, ticket_id, checkpointer=checkpointer)
            except Exception as exc:  # noqa: BLE001 — se refleja en el ticket
                repository.mark_error(session, ticket_id, f"La aprobación falló ({type(exc).__name__}). Relanzar el ticket.")
                print(f"La aprobación falló: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1
            print(f"{ticket.rfp_id}: {status}")
            return 0

    from services.tasks.rfp_tasks import enqueue_rfp_approval

    try:
        task_id = enqueue_rfp_approval(ticket_id)
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo encolar (¿Redis arrancado?): {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"{ticket_id} encolado para avanzar la aprobación (task_id={task_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
