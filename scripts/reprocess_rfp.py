"""Reprocesa un ticket de RFP (p. ej. uno en `error`), sin pasar por HTTP.

    services/api/.venv/bin/python scripts/reprocess_rfp.py <ticket_id> [--inline]

Vuelve el ticket a `analizando`, borra el resultado anterior y:
- por defecto, encola la tarea de Celery (necesita Redis + worker);
- con `--inline`, ejecuta el grafo en este mismo proceso (smoke run sin
  Celery: útil para depurar con el modelo real).

Necesita DATABASE_URL (services/api/.env) y el PDF original en data/raw/rfp/.
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
from data.pipelines.rfp_intake.runner import _resolve, run_intake  # noqa: E402


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print("Uso: reprocess_rfp.py <ticket_id> [--inline]", file=sys.stderr)
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
        if not _resolve(ticket.raw_pdf_path).exists():
            print(f"Falta el PDF {ticket.raw_pdf_path}: no se puede reprocesar", file=sys.stderr)
            return 1
        repository.reset_for_reprocess(session, ticket)
        if "--inline" in argv:
            try:
                status = run_intake(session, ticket_id)
            except Exception as exc:  # noqa: BLE001 — se refleja en el ticket
                repository.mark_error(session, ticket_id, f"Reproceso fallido ({type(exc).__name__}).")
                print(f"El reproceso falló: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1
            print(f"{ticket.rfp_id}: {status}")
            return 0

    from services.tasks.rfp_tasks import enqueue_rfp_ticket

    try:
        task_id = enqueue_rfp_ticket(ticket_id)
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo encolar (¿Redis arrancado?): {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"{ticket_id} encolado de nuevo (task_id={task_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
