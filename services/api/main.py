from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlmodel import Session, SQLModel

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from packages.shared.incidents_validation import (  # noqa: E402
    analyze_rows,
    csv_results_content,
    load_csv_rows_from_bytes,
)
from database import get_inventory_engine  # noqa: E402
import inventory_alerts  # noqa: E402
import inventory_models  # noqa: E402,F401  (registers ORM tables on SQLModel.metadata)
import telemetry_models  # noqa: E402,F401  (registers ORM tables on SQLModel.metadata)
import telemetry_service  # noqa: E402
from models import HealthStatus, IncidentAnalysisResponse  # noqa: E402
from routes.auth import router as auth_router  # noqa: E402
from routes.incidents import router as incidents_router  # noqa: E402
from routes.inventory import router as inventory_router  # noqa: E402
from routes.profiles import router as profiles_router  # noqa: E402
from routes.suppliers import router as suppliers_router  # noqa: E402
from routes.telemetry import router as telemetry_router  # noqa: E402
from routes.users import router as users_router  # noqa: E402
from security import get_current_user  # noqa: E402
from services.agent.router import router as agent_router  # noqa: E402
from services.agent.chat.router import router as agent_chat_router  # noqa: E402  (registra chat_sessions/chat_messages)
from services.agent.chat.service import CHAT as agent_chat_service  # noqa: E402
from services.knowledge.router import router as knowledge_router  # noqa: E402
from services.reporting.router import router as reporting_router  # noqa: E402
from services.rfp_intake.router import router as rfp_intake_router  # noqa: E402  (registra las tablas rfp_*)
from services.rfp_intake.events import install_shutdown_signal_hook, shutdown_hub as shutdown_rfp_event_hub  # noqa: E402
from services.tasks.router import router as tasks_router  # noqa: E402
import services.tasks.dead_letters  # noqa: E402,F401  (registers task_dead_letters on SQLModel.metadata)
from data.pipelines.monthly_clinic_supply_performance.models import ensure_reporting_schema  # noqa: E402
from data.pipelines.rfp_intake.models import ensure_rfp_response_columns  # noqa: E402

app = FastAPI(title="HealthCore Incidents API", version="1.0.0")

# Python's root logger defaults to WARNING with no handler attached, which
# would silently swallow the INFO-level timing logs below.
logging.basicConfig(level=logging.INFO, format="%(message)s")
# Prefect (disparo manual de POST /reporting/pipeline-runs) habla con su API
# efimera por httpx, que loguea cada peticion a INFO: cientos de lineas
# "HTTP Request: POST .../logs/" por corrida que taparian los logs de timing.
logging.getLogger("httpx").setLevel(logging.WARNING)

# OWASP A05 (docs/security/owasp-top10-audit.md): antes allow_origins=["*"]
# con allow_credentials=True, es decir, cualquier web podía llamar a la API
# desde el navegador de un usuario. Ahora, lista explícita por entorno. El
# backoffice va casi todo por su proxy /api-proxy (mismo origen); lo que llega
# directo desde el navegador es la telemetría (NEXT_PUBLIC_TELEMETRY_ENDPOINT).
# El WebSocket del chat no pasa por CORS: valida su token en el primer frame.
DEFAULT_CORS_ORIGINS = "http://localhost:3000,http://localhost:3001"


def parse_cors_origins(raw: str) -> list[str]:
    """`CORS_ALLOWED_ORIGINS` ("a,b,c") → lista de orígenes permitidos.

    (Learn by Doing delegado al asistente.) Un "*" para el arranque en vez de
    ignorarse: con allow_credentials=True reabriría la vulnerabilidad, y fallar
    al arrancar es lo que ya hace security.py si falta JWT_SECRET_KEY. La barra
    final se quita porque el navegador nunca la envía en la cabecera Origin.
    """
    origins = [origin.strip().rstrip("/") for origin in raw.split(",")]
    origins = [origin for origin in origins if origin]
    if "*" in origins:
        raise RuntimeError("CORS_ALLOWED_ORIGINS no admite '*': lista los orígenes del frontend")
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_cors_origins(os.getenv("CORS_ALLOWED_ORIGINS", DEFAULT_CORS_ORIGINS)),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(suppliers_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(profiles_router)
app.include_router(incidents_router)
app.include_router(inventory_router)
app.include_router(telemetry_router)
app.include_router(reporting_router)
app.include_router(tasks_router)
app.include_router(knowledge_router)
app.include_router(agent_router)
app.include_router(agent_chat_router)
app.include_router(rfp_intake_router)

timing_logger = logging.getLogger("api.timing")


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    """Logs latency per request so we have evidence (not guesses) of which
    endpoints are worth caching. See CACHING_REPORT.md for the readings.
    Also the source for api_latency_recorded — same reading, no second
    measurement pass."""
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    timing_logger.info(
        f"{request.method} {request.url.path} → {response.status_code} | {duration_ms:.1f}ms"
    )
    telemetry_service.emit_backend_event(
        event_type="api_latency_recorded",
        user_id=None,
        properties={
            "route_template": request.url.path,
            "method": request.method,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 1),
        },
    )
    return response


@app.on_event("startup")
def init_inventory_schema() -> None:
    """Create every table registered on SQLModel.metadata in Supabase if
    DATABASE_URL is configured — inventory's tables and, since the
    telemetry storage feature, telemetry_events too (same shared metadata,
    same engine).

    Left non-fatal on purpose: the rest of the API (auth, suppliers, incidents)
    must keep working locally even before Supabase is wired up.

    The business pipeline's reporting.* tables are registered on the same
    metadata (services/reporting imports their models), but Postgres needs
    the `reporting` schema to exist first — and the KPI table is created
    with the CONTEXT's literal DDL — so ensure_reporting_schema runs before
    the generic create_all.
    """
    try:
        ensure_reporting_schema(get_inventory_engine())
        SQLModel.metadata.create_all(get_inventory_engine())
        # rfp_* already existed before Part 2 of the RFP milestone added columns.
        ensure_rfp_response_columns(get_inventory_engine())
    except Exception as exc:  # missing DATABASE_URL, or Supabase unreachable
        print(f"[inventory] skipping schema init: {exc}", file=sys.stderr)


@app.on_event("startup")
def flag_expiring_supplies() -> None:
    """Stand-in for the 'daily job' the telemetry plan describes for
    supply_expiry_flagged — this project has no scheduler/cron
    infrastructure. Safe to run on every startup now: inventory_alerts
    emits (and persists) each lot per clinic only once, via a deterministic
    eventId, so restarts no longer multiply the event. Non-fatal for the
    same reason as init_inventory_schema above: Supabase may not be
    configured locally."""
    try:
        with Session(get_inventory_engine()) as session:
            inventory_alerts.flag_expiring_supplies(session)
    except Exception as exc:  # missing DATABASE_URL, or Supabase unreachable
        print(f"[telemetry] skipping expiry check: {exc}", file=sys.stderr)


@app.on_event("startup")
def hook_rfp_streams_into_shutdown_signals() -> None:
    """Sin esto, una pestaña con /rfps abierta impide que la API se apague
    (ver install_shutdown_signal_hook)."""
    install_shutdown_signal_hook()


@app.on_event("shutdown")
async def close_rfp_event_stream() -> None:
    """Cierra los streams SSE abiertos y la suscripción a Redis del hub."""
    await shutdown_rfp_event_hub()


@app.on_event("shutdown")
def stop_agent_chat_generations() -> None:
    """Que ninguna respuesta del chat siga pidiendo tokens al proveedor tras
    apagar la API (uvicorn ya cerró los WebSocket con 1012; los navegadores
    reconectan con su backoff)."""
    agent_chat_service.stop_all()


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Never leak stack traces or internal details to API clients.
    error_id = telemetry_service.emit_api_error(request)
    return JSONResponse(status_code=500, content={"detail": "Internal server error", "error_id": error_id})

_last_analysis_lock = Lock()
_last_analysis: Dict[str, Any] | None = None
WEB_INDEX_PATH = ROOT_DIR / "uis" / "web" / "index.html"


@app.get("/api/health", response_model=HealthStatus)
def health() -> HealthStatus:
    return HealthStatus(status="ok")


# response_class, no response_model: esta ruta devuelve el HTML estatico de
# uis/web, no JSON. Declararlo hace que /docs lo muestre como text/html en
# vez de prometer un objeto que nunca llega.
@app.get(
    "/",
    response_class=FileResponse,
    responses={200: {"content": {"text/html": {}}, "description": "UI estatica de uis/web"}},
)
def web_home() -> Response:
    if WEB_INDEX_PATH.exists():
        return FileResponse(WEB_INDEX_PATH)
    raise HTTPException(status_code=404, detail="Web UI not found")


@app.post("/api/incidents/analyze", response_model=IncidentAnalysisResponse)
async def analyze_incidents(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    _ = current_user
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file name")

    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        rows = load_csv_rows_from_bytes(payload)
        results = analyze_rows(rows)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    response = {
        "source_file": file.filename,
        "analyzed_at": datetime.utcnow().isoformat() + "Z",
        "summary": results,
    }

    with _last_analysis_lock:
        global _last_analysis
        _last_analysis = response

    return response


# Descarga de CSV: response_class en lugar de response_model, por el mismo
# motivo que GET /. El Content-Type real es text/csv.
@app.get(
    "/api/incidents/results/export",
    response_class=Response,
    responses={
        200: {
            "content": {"text/csv": {}},
            "description": "Descarga CSV del ultimo analisis ejecutado",
        }
    },
)
def export_last_results(current_user: Dict[str, Any] = Depends(get_current_user)) -> Response:
    _ = current_user
    with _last_analysis_lock:
        if _last_analysis is None:
            raise HTTPException(status_code=404, detail="No analysis available yet")
        results = _last_analysis["summary"]

    csv_content = csv_results_content(results)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=results.csv",
            "Cache-Control": "no-store",
        },
    )
