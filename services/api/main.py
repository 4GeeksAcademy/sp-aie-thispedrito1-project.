from __future__ import annotations

import logging
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
import inventory_models  # noqa: E402,F401  (registers ORM tables on SQLModel.metadata)
import inventory_repository  # noqa: E402
import telemetry_service  # noqa: E402
from routes.auth import router as auth_router  # noqa: E402
from routes.incidents import router as incidents_router  # noqa: E402
from routes.inventory import router as inventory_router  # noqa: E402
from routes.profiles import router as profiles_router  # noqa: E402
from routes.suppliers import router as suppliers_router  # noqa: E402
from routes.telemetry import router as telemetry_router  # noqa: E402
from routes.users import router as users_router  # noqa: E402
from security import get_current_user  # noqa: E402

app = FastAPI(title="HealthCore Incidents API", version="1.0.0")

# Python's root logger defaults to WARNING with no handler attached, which
# would silently swallow the INFO-level timing logs below.
logging.basicConfig(level=logging.INFO, format="%(message)s")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(suppliers_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(profiles_router)
app.include_router(incidents_router)
app.include_router(inventory_router)
app.include_router(telemetry_router)

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
    """Create the inventory tables in Supabase if DATABASE_URL is configured.

    Left non-fatal on purpose: the rest of the API (auth, suppliers, incidents)
    must keep working locally even before Supabase is wired up.
    """
    try:
        SQLModel.metadata.create_all(get_inventory_engine())
    except Exception as exc:  # missing DATABASE_URL, or Supabase unreachable
        print(f"[inventory] skipping schema init: {exc}", file=sys.stderr)


@app.on_event("startup")
def flag_expiring_supplies() -> None:
    """Stand-in for the 'daily job' the telemetry plan describes for
    supply_expiry_flagged — this project has no scheduler/cron
    infrastructure, so running the same check once at API startup is enough
    to prove the event fires end-to-end without building one. Non-fatal for
    the same reason as init_inventory_schema above: Supabase may not be
    configured locally."""
    try:
        with Session(get_inventory_engine()) as session:
            for supply, stock, days_until_expiry in inventory_repository.list_supplies_expiring_within(
                session, days=30
            ):
                telemetry_service.emit_backend_event(
                    event_type="supply_expiry_flagged",
                    user_id=None,
                    properties={
                        "clinic_id": None,
                        "country": supply.country,
                        "product_id": supply.id,
                        "product_category": supply.category,
                        "expiry_date": supply.expiry_date.isoformat(),
                        "days_until_expiry": days_until_expiry,
                        "quantity_at_risk": stock,
                    },
                )
    except Exception as exc:  # missing DATABASE_URL, or Supabase unreachable
        print(f"[telemetry] skipping expiry check: {exc}", file=sys.stderr)


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Never leak stack traces or internal details to API clients.
    error_id = telemetry_service.emit_api_error(request)
    return JSONResponse(status_code=500, content={"detail": "Internal server error", "error_id": error_id})

_last_analysis_lock = Lock()
_last_analysis: Dict[str, Any] | None = None
WEB_INDEX_PATH = ROOT_DIR / "uis" / "web" / "index.html"


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def web_home() -> Response:
    if WEB_INDEX_PATH.exists():
        return FileResponse(WEB_INDEX_PATH)
    raise HTTPException(status_code=404, detail="Web UI not found")


@app.post("/api/incidents/analyze")
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


@app.get("/api/incidents/results/export")
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
