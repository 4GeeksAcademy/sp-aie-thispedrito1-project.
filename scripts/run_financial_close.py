"""Ejecuta el cierre financiero a mano.

    services/api/.venv/bin/python scripts/run_financial_close.py                 # encola en Celery (último mes cerrado)
    services/api/.venv/bin/python scripts/run_financial_close.py --month 2026-10 --inline   # en este proceso, contra Supabase
    services/api/.venv/bin/python scripts/run_financial_close.py --offline --out docs/financial-close/examples \
        [--incomplete] [--load-date 2026-11-07]                                   # ejemplo sin red: SQLite + plantillas

`--offline` genera los CSV en un directorio temporal, los ingiere en una
SQLite en memoria y escribe report_<lang>.md + figures.json: es como se
produjeron los ejemplos del PR (deterministas).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "services" / "api"
for path in (str(ROOT_DIR), str(API_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)


def run_offline(out: Path, load_date: date, incomplete: bool) -> int:
    from sqlmodel import Session, SQLModel, create_engine
    from sqlmodel.pool import StaticPool

    from services.financial_close import seed_data, workflow
    from services.financial_close.models import FC_TABLES

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine, tables=FC_TABLES)
    with tempfile.TemporaryDirectory() as tmp, Session(engine) as session:
        month = seed_data.generate_all(Path(tmp), load_date, incomplete=incomplete)
        workflow.ingest_period(session, Path(tmp), month)
        row = workflow.run_close(session, engine, month, trigger="manual", use_model=False)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report_en.md").write_text(row.report_en, encoding="utf-8")
        (out / "report_es.md").write_text(row.report_es, encoding="utf-8")
        (out / "figures.json").write_text(json.dumps(row.figures, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Informe {month:%Y-%m} v{row.version} → {out} (figures_sha256 {row.figures_sha256[:16]}…)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--month", help="YYYY-MM (por defecto, el último mes cerrado en Austin)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inline", action="store_true")
    mode.add_argument("--offline", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT_DIR / "docs" / "financial-close" / "examples" / "complete")
    parser.add_argument("--incomplete", action="store_true")
    parser.add_argument("--load-date", type=date.fromisoformat, default=date(2026, 11, 7))
    args = parser.parse_args()

    if args.offline:
        return run_offline(args.out, args.load_date, args.incomplete)

    from dotenv import load_dotenv

    load_dotenv(API_DIR / ".env")
    from services.financial_close import schedule

    period = (date.fromisoformat(f"{args.month}-01") if args.month
              else schedule.period_to_close(datetime.now(timezone.utc)))
    try:
        if args.inline:
            from services.tasks.financial_close_tasks import execute_close

            print(json.dumps(execute_close(period, "manual")))
        else:
            from services.tasks.financial_close_tasks import enqueue_close

            print(f"Encolado: task_id={enqueue_close(period, 'manual')} periodo={period:%Y-%m}")
    except Exception as exc:  # noqa: BLE001
        print(f"Error ejecutando el cierre: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
