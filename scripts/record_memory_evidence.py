"""Graba la evidencia real de la memoria del agente (Ticket #MEM-092).

    services/api/.venv/bin/python scripts/record_memory_evidence.py

Desde la raíz, con Qdrant levantado (`docker compose up -d qdrant`), Supabase
despierto y `LLM_*`/`GENERATION_MODEL` válidos en services/api/.env (o en el
entorno: una variable del entorno gana a la del .env).

Arranca la API real (uvicorn, puerto 8010) con una TinyDB TEMPORAL y dos
coordinadores + un admin creados por el repositorio, con tokens generados
directamente (sin login: no escribe `login_succeeded` en Supabase). Las
conversaciones van por HTTP a `POST /agent/query` contra el modelo real y la
memoria/auditoría reales en Supabase. Escribe una línea por turno en
`docs/agent/memory-evidence/turns.json`, más la memoria visible y la
auditoría final. Los mensajes del guion son ficticios y sin datos reales
(el de PHI es el ejemplo literal del CONTEXT, que debe bloquearse).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services" / "api"
OUT_DIR = ROOT / "docs" / "agent" / "memory-evidence"
PORT = 8010
BASE = f"http://127.0.0.1:{PORT}"

# (quién, mensaje, qué demuestra)
SCRIPT = [
    ("ana", "En la clínica de Manchester el proceso de referidos internos ahora pasa primero por el coordinador antes que por el especialista — cambió el trimestre pasado.", "ciclo A · propuesta"),
    ("ana", "Sí, guárdalo por favor.", "ciclo A · aprobación → escritura"),
    ("luis", "¿Cómo funcionan ahora los referidos internos en la clínica de Manchester?", "ciclo A · otro coordinador lo recibe"),
    ("ana", "Esa alerta de no-show elevado en la clínica de Austin fue porque hubo un cierre de carretera esa semana, no un problema real del programa de recordatorios.", "ciclo B · propuesta"),
    ("ana", "No, no hace falta que lo recuerdes.", "ciclo B · rechazo → sin escritura"),
    ("luis", "¿Por qué hubo una alerta de no-show elevado en Austin?", "ciclo B · la memoria no cambió"),
    ("ana", "El paciente Johnson canceló su cita de mañana, apúntalo.", "PHI · rechazo explícito"),
    ("luis", "¿Cuál es la tasa de no-show de esta semana?", "sin propuesta · consulta puntual"),
    ("luis", "Gracias, con eso resuelvo mi reporte.", "sin propuesta · cierre"),
    ("ana", "El reporte semanal para Diane Foster debe incluir vacantes por rol, no solo por clínica — eso lo pidió hace dos semanas.", "cambio de tema · propuesta"),
    ("ana", "¿Cuánto se cobra por un no-show a un paciente de pago privado en Texas?", "cambio de tema · descarte + respuesta"),
]


def _setup_users(db_path: Path) -> dict:
    env = {**os.environ, "SUPPLIERS_DB_PATH": str(db_path)}
    code = """
import json, sys
sys.path[:0] = ['.', 'services/api']
from auth_repository import AuthRepository
from models import UserRole
from security import create_access_token, hash_password
repo = AuthRepository()
tokens = {}
for key, email, role in [('ana', 'ana.coord@healthcore.test', UserRole.user),
                         ('luis', 'luis.coord@healthcore.test', UserRole.user),
                         ('admin', 'admin@healthcore.test', UserRole.admin)]:
    user, _profile = repo.create_user(email=email, hashed_password=hash_password('Evidence-Only-123'), role=role,
                            profile_data={'name': key})
    tokens[key] = {'token': create_access_token(user['id'], role.value), 'user_id': user['id']}
print(json.dumps(tokens))
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="memory-evidence-"))
    db_path = tmp / "users.db.json"
    users = _setup_users(db_path)
    env = {**os.environ, "SUPPLIERS_DB_PATH": str(db_path), "AGENT_TRACE_DIR": str(tmp / "traces")}
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(PORT)],
        cwd=API_DIR, env=env, stdout=open(tmp / "api.log", "w"), stderr=subprocess.STDOUT,
    )
    try:
        client = httpx.Client(base_url=BASE, timeout=120)
        for _ in range(60):
            try:
                client.get("/api/health").raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(1)
        headers = {key: {"Authorization": f"Bearer {value['token']}"} for key, value in users.items()}

        turns = []
        for index, (who, message, purpose) in enumerate(SCRIPT, start=1):
            started = time.perf_counter()
            response = client.post("/agent/query", json={"question": message}, headers=headers[who])
            elapsed = round(time.perf_counter() - started, 1)
            body = response.json()
            turns.append({"turn": index, "user": who, "purpose": purpose, "message": message,
                          "status_code": response.status_code, "seconds": elapsed, "response": body})
            memory = body.get("memory", {}) if isinstance(body, dict) else {}
            print(f"[{index:02d}] {who:5} {purpose:42} → {response.status_code} "
                  f"resolved={(memory.get('resolved') or {}).get('status')} offered={(memory.get('offered') or {}).get('status')} "
                  f"outcome={body.get('outcome') if isinstance(body, dict) else '-'} ({elapsed}s)")

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "turns.json").write_text(json.dumps(turns, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        visible = {who: client.get("/agent/memory", headers=headers[who]).json() for who in ("ana", "luis")}
        (OUT_DIR / "memory-visible.json").write_text(json.dumps(visible, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        audit = client.get("/agent/memory/audit", headers=headers["admin"]).json()
        run_users = {value["user_id"] for value in users.values()}
        audit = [row for row in audit if row["user_id"] in run_users]  # solo esta corrida
        (OUT_DIR / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Evidencia en {OUT_DIR.relative_to(ROOT)} ({len(audit)} filas de auditoría)")
        return 0
    finally:
        api.terminate()
        api.wait(timeout=20)


if __name__ == "__main__":
    raise SystemExit(main())
