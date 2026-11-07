"""Ruta E2E reproducible del Hito 9: una RFP por las tres partes, con
aprobaciones humanas SIMULADAS (resume programático), sin clicks en la UI.

    # Sin red: SQLite en memoria, checkpointer en memoria y modelo guionizado
    services/api/.venv/bin/python scripts/run_rfp_e2e.py --offline

    # Real: Supabase + checkpointer Postgres + modelo del proxy de 4Geeks
    services/api/.venv/bin/python scripts/run_rfp_e2e.py --live <ruta/al/rfp.pdf> [--scenario approve-all|parallel]

Mismo recorrido en los dos modos, con los runners que usa el worker:
Parte 1 `run_intake` → Parte 2 `run_response` → Parte 3 `advance` +
decisiones registradas por el mismo camino que la API
(`submit_section_decision` / `submit_arbitration_decision`). Imprime el
estado tras cada paso, el documento final y el trace por agente.

Escenarios (tests/pipelines/rfp_fixtures.py):
- `parallel` (por defecto en --offline): Compliance aprueba con Revenue y
  Clinical pausados; Clinical pide cambios; confirma capacidad para 500 de
  800; Tom Callahan arbitra (`capacity-vs-population`) y reduce la población;
  Revenue y Clinical aprueban la versión revisada → documento final.
- `approve-all` (por defecto en --live): cada responsable aprueba por
  separado, sin revisiones (no añade llamadas al modelo en la Parte 3).

El modo --live deja un ticket real en Supabase (visible en /rfps) y gasta
llamadas al modelo: ~15-20 en las Partes 1 y 2.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "services" / "api"
FIXTURES_DIR = ROOT_DIR / "tests" / "pipelines"
for path in (str(ROOT_DIR), str(API_DIR), str(FIXTURES_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from sqlmodel import Session  # noqa: E402

from data.pipelines.rfp_intake import repository  # noqa: E402

STARTED = time.monotonic()


def log(line: str) -> None:
    print(f"[{time.monotonic() - STARTED:6.1f}s] {line}", flush=True)


@contextmanager
def offline_environment() -> Iterator[tuple]:
    import os

    from langgraph.checkpoint.memory import InMemorySaver
    from sqlmodel import create_engine
    from sqlmodel.pool import StaticPool

    from rfp_fixtures import CLINICAL_500, CLINICAL_OK, CLINICAL_REVISED, REVENUE_500, REVENUE_OK, ScriptedLlm, create_rfp_tables

    raw_dir = Path(tempfile.mkdtemp(prefix="rfp-e2e-"))
    os.environ["RFP_RAW_DIR"] = str(raw_dir)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_rfp_tables(engine)
    llm = ScriptedLlm(drafts={"revenue": [REVENUE_OK, REVENUE_500], "clinical": [CLINICAL_OK, CLINICAL_REVISED, CLINICAL_500]})
    try:
        yield engine, InMemorySaver(), llm, raw_dir
    finally:
        shutil.rmtree(raw_dir, ignore_errors=True)


@contextmanager
def live_environment() -> Iterator[tuple]:
    from data.pipelines.rfp_intake.agents import call_model
    from data.pipelines.rfp_intake.checkpointer import open_checkpointer
    from data.pipelines.rfp_intake.runner import raw_dir
    from database import get_inventory_engine

    with open_checkpointer() as saver:
        yield get_inventory_engine(), saver, call_model, raw_dir()


def run(mode: str, pdf: Path, scenario_name: str) -> int:
    from data.pipelines.rfp_intake.approval_runner import advance
    from data.pipelines.rfp_intake.models import STATUS_COMPLETE, STATUS_FINISHED
    from data.pipelines.rfp_intake.response_runner import run_response
    from data.pipelines.rfp_intake.runner import run_intake
    from rfp_fixtures import run_approve_all_scenario, run_parallel_scenario

    scenario: Callable[..., Any] = run_parallel_scenario if scenario_name == "parallel" else run_approve_all_scenario
    environment = offline_environment() if mode == "offline" else live_environment()
    with environment as (engine, saver, llm, raw_dir), Session(engine) as session:
        ticket_id = uuid.uuid4()
        target = Path(raw_dir) / f"{ticket_id}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        if pdf.exists():
            shutil.copyfile(pdf, target)
        else:
            target.write_bytes(b"%PDF-1.4 e2e-offline")
        ticket = repository.create_ticket(session, ticket_id=ticket_id, raw_pdf_path=str(target), created_by="e2e-script")
        log(f"Ticket {ticket.rfp_id} ({ticket_id}) creado: {ticket.status}")

        graph = None
        if mode == "offline":
            from data.pipelines.rfp_intake.conversion import clean_markdown
            from data.pipelines.rfp_intake.graph import build_rfp_intake_graph, compile_rfp_intake_graph
            from rfp_fixtures import MERIDIAN

            graph = compile_rfp_intake_graph(build_rfp_intake_graph(llm=llm, converter=lambda _p: clean_markdown(MERIDIAN)))
        status = run_intake(session, ticket_id, graph=graph)
        log(f"Parte 1 (recepción) → {status}")
        if status != STATUS_COMPLETE:
            log("La RFP no llegó a analisis_completo: fin del recorrido.")
            return 1
        status = run_response(session, ticket_id, llm=llm)
        log(f"Parte 2 (borradores + evaluación) → {status}")

        steps = scenario(
            session, ticket_id, lambda: advance(session, ticket_id, checkpointer=saver, llm=llm), log=lambda line: log(f"Parte 3 · {line}")
        )
        if steps[-1]["status"] != STATUS_FINISHED:
            log(f"La Parte 3 terminó en {steps[-1]['status']}")
            return 1

        document = repository.get_final_document(session, ticket_id)
        print("\n" + "=" * 30 + " DOCUMENTO FINAL " + "=" * 30)
        print(document.content_markdown)
        print("=" * 30 + " TRACE (qué agente hizo qué) " + "=" * 30)
        for event in repository.list_trace_events(session, ticket_id):
            print(f"{event.timestamp:%H:%M:%S.%f}"[:-3] + f"  P{event.part}  {event.node:<26} {event.department_id or '':<11} {event.agent}")
        log(f"Terminado: {ticket.rfp_id} → {STATUS_FINISHED}")
    return 0


def main(argv: list[str]) -> int:
    if "--offline" in argv:
        mode, pdf = "offline", Path("meridian-offline.pdf")
    elif "--live" in argv:
        rest = [a for a in argv if not a.startswith("--") and a not in ("parallel", "approve-all")]
        if not rest or not Path(rest[0]).is_file():
            print("Uso: run_rfp_e2e.py --live <ruta/al/rfp.pdf> [--scenario approve-all|parallel]", file=sys.stderr)
            return 1
        mode, pdf = "live", Path(rest[0])
    else:
        print("Uso: run_rfp_e2e.py --offline | --live <ruta/al/rfp.pdf> [--scenario approve-all|parallel]", file=sys.stderr)
        return 1
    scenario = "parallel" if mode == "offline" else "approve-all"
    if "--scenario" in argv:
        index = argv.index("--scenario")
        if index + 1 >= len(argv) or argv[index + 1] not in ("parallel", "approve-all"):
            print("--scenario debe ser approve-all o parallel", file=sys.stderr)
            return 1
        scenario = argv[index + 1]
    try:
        return run(mode, pdf, scenario)
    except Exception as exc:  # noqa: BLE001 — CLI: error legible y código ≠ 0
        print(f"El recorrido E2E falló: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
