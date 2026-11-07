"""Proceso auxiliar de la prueba de reinicio (no es un test).

    python tests/onboarding/resume_helper.py before <db> <checkpoints> <out.json>
    python tests/onboarding/resume_helper.py after  <db> <checkpoints> <out.json>

`before`: crea la incorporación, la lleva hasta la comprobación de la etapa 3,
escribe su id y lo que espera en <out.json> y se mata con SIGKILL: sin
`finally`, sin cerrar conexiones, como un `kill -9` o un contenedor caído.

`after`: un proceso NUEVO abre los mismos archivos, lee dónde estaba, aplica
las respuestas de la etapa 3 y deja el resultado en <out.json>.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import conftest  # noqa: E402  (añade la raíz y services/api al path)
from sqlmodel import Session, create_engine  # noqa: E402

from services.onboarding import repository, runner  # noqa: E402


def main() -> None:
    phase, db_path, checkpoint_path, out_path = sys.argv[1:5]
    engine = create_engine(f"sqlite:///{db_path}")
    out = Path(out_path)
    if phase == "before":
        conftest.create_tables(engine)
        session = Session(engine)
        conftest.seed_staff(session)
        hire = conftest.new_hire(session)
        with runner.open_saver(database_url="", sqlite_path=Path(checkpoint_path)) as saver:
            driver = conftest.Driver(session, saver)
            hire = driver.to_stage(hire, 3)
            out.write_text(json.dumps({"hire_id": str(hire.hire_id), "pid": os.getpid(), "waiting_for": hire.waiting_for, "completed": hire.completed_stages}))
            os.kill(os.getpid(), signal.SIGKILL)  # muerte abrupta a mitad de la etapa 3
        return

    hire_id = uuid.UUID(json.loads(out.read_text())["hire_id"])
    with Session(engine) as session, runner.open_saver(database_url="", sqlite_path=Path(checkpoint_path)) as saver:
        hire = repository.get_hire(session, hire_id)
        snapshot = runner.compile_onboarding_graph(saver).get_state(runner.thread_config(hire_id))
        graded_before = sorted(e.event_key for e in repository.list_audit(session, hire_id) if e.action == "check_graded")
        resumed_at = {"waiting_for": dict(hire.waiting_for), "graph_next": list(snapshot.next), "completed": list(hire.completed_stages)}
        driver = conftest.Driver(session, saver)
        hire = driver.check(hire, 3)
        graded_after = sorted(e.event_key for e in repository.list_audit(session, hire_id) if e.action == "check_graded")
        out.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "resumed_at": resumed_at,
                    "after_answer": {"waiting_for": hire.waiting_for, "completed": hire.completed_stages},
                    "graded_before": graded_before,
                    "graded_after": graded_after,
                }
            )
        )


if __name__ == "__main__":
    main()
