"""Disparador programado del cierre: lo lanza el servicio `scheduler` CADA HORA.

Solo actúa en la hora 06:00 de Austin del tercer día hábil del mes
(`schedule.is_close_due`); el resto de horas sale sin hacer nada. Si ya hay un
informe programado para ese periodo, la tarea no crea otro (idempotente).

    services/api/.venv/bin/python scripts/financial_close_scheduled.py [--now 2026-11-04T12:00:00+00:00] [--inline]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "services" / "api"
for path in (str(ROOT_DIR), str(API_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--now", type=datetime.fromisoformat, default=None, help="Instante con zona (pruebas).")
    parser.add_argument("--inline", action="store_true", help="Ejecutar aquí en vez de encolar en Celery.")
    args = parser.parse_args()

    from services.financial_close import schedule

    now = args.now or datetime.now(timezone.utc)
    if not schedule.is_close_due(now):
        print(f"No toca: próxima ejecución {schedule.next_run(now).isoformat()}")
        return 0
    period = schedule.period_to_close(now)
    from dotenv import load_dotenv

    load_dotenv(API_DIR / ".env")
    try:
        if args.inline:
            from services.tasks.financial_close_tasks import execute_close

            print(execute_close(period, "scheduled"))
        else:
            from services.tasks.financial_close_tasks import enqueue_close

            print(f"Cierre {period:%Y-%m} encolado: {enqueue_close(period, 'scheduled')}")
    except Exception as exc:  # noqa: BLE001
        print(f"Error lanzando el cierre programado: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
