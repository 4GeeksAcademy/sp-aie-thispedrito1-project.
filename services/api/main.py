from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.incidents_analysis import (  # noqa: E402
    analyze_rows,
    csv_results_content,
    load_csv_rows_from_bytes,
)
from routes.suppliers import router as suppliers_router  # noqa: E402

app = FastAPI(title="HealthCore Incidents API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(suppliers_router)

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
async def analyze_incidents(file: UploadFile = File(...)) -> Dict[str, Any]:
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
def export_last_results() -> Response:
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
