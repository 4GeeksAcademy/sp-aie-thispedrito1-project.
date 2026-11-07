"""Reanudación tras reinicio del servicio (criterio de evaluación del README).

"Una prueba demuestra que, si el servicio se reinicia a mitad de una
incorporación, el agente la retoma exactamente en la etapa en que estaba."

Dos pruebas:
- entre dos PROCESOS reales: el primero muere con SIGKILL en la etapa 3 y el
  segundo, nuevo, retoma desde los mismos archivos (base + checkpoints);
- dentro del proceso: se cierra el checkpointer y se abre otro (nuevo grafo
  compilado, nueva conexión), como tras reiniciar el worker.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
from pathlib import Path

from conftest import new_hire
from services.onboarding import runner
from services.onboarding.graph import WAIT_CHECK

HELPER = Path(__file__).with_name("resume_helper.py")


def _run(phase: str, db: Path, ckpt: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HELPER), phase, str(db), str(ckpt), str(out)], capture_output=True, text=True, timeout=120)


def test_new_process_resumes_exactly_where_a_killed_one_stopped(tmp_path: Path):
    db, ckpt, out = tmp_path / "app.sqlite", tmp_path / "checkpoints.sqlite", tmp_path / "out.json"

    before = _run("before", db, ckpt, out)
    assert before.returncode == -signal.SIGKILL, before.stderr[-2000:]  # murió de verdad, a mitad
    killed = json.loads(out.read_text())
    assert killed["waiting_for"] == {"kind": WAIT_CHECK, "stage": 3, "attempt": 1, "set_id": "operations:en:0"}
    assert killed["completed"] == [1, 2]

    after = _run("after", db, ckpt, out)
    assert after.returncode == 0, after.stderr[-2000:]
    resumed = json.loads(out.read_text())
    assert resumed["pid"] != killed["pid"]
    # Mismo punto exacto: etapa 3, intento 1, mismo juego de preguntas.
    assert resumed["resumed_at"]["waiting_for"] == killed["waiting_for"]
    assert resumed["resumed_at"]["graph_next"] == ["s3_operations__check"]
    assert resumed["resumed_at"]["completed"] == [1, 2]
    # Y continúa: la etapa 3 se cierra y espera la 4.
    assert resumed["after_answer"]["completed"] == [1, 2, 3]
    assert resumed["after_answer"]["waiting_for"]["stage"] == 4
    # Sin repetir comprobaciones ya superadas: la de la etapa 2 sigue siendo una sola.
    stage2 = [k for k in resumed["graded_after"] if ":s2:" in k]
    assert len(stage2) == 1
    assert set(resumed["graded_before"]) < set(resumed["graded_after"])


def test_reopening_the_checkpointer_resumes_the_same_stage(session, checkpoint_path):
    hire = new_hire(session)
    with runner.open_saver(database_url="", sqlite_path=checkpoint_path) as saver:
        from conftest import Driver

        hire = Driver(session, saver).to_stage(hire, 4)
        hire = Driver(session, saver).check(hire, 4, correct=False)  # intento 1 fallido
    expected = dict(hire.waiting_for)
    assert expected["stage"] == 4 and expected["attempt"] == 2

    # "Reinicio": nada del objeto anterior sobrevive salvo los archivos.
    with runner.open_saver(database_url="", sqlite_path=checkpoint_path) as saver:
        graph = runner.compile_onboarding_graph(saver)
        snapshot = graph.get_state(runner.thread_config(hire.hire_id))
        waiting = runner._waiting_for(snapshot)
        assert waiting == expected
        assert snapshot.values["failed_attempts"]["4"] == 1
        from conftest import Driver

        hire = Driver(session, saver).check(hire, 4)
        assert hire.waiting_for == {"kind": "approval", "stage": 4, "approver": "compliance"}
