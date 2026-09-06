"""Pipeline de análisis técnico/operacional sobre telemetry_events.

Cada función responde una pregunta sobre la salud del *sistema* (volumen,
tasa de error, rendimiento) — nunca una pregunta de negocio. Todas siguen el
mismo orden, documentado en el README de la clase:

    cargar (SQL) -> refinar (Pandas) -> convertir tipos -> agrupar -> agregar

Son puras: mismos start_date/end_date + mismos datos en la tabla ->
mismo resultado. No mutan nada ni tienen efectos secundarios; el llamador
(routes/telemetry.py) decide cuándo llamarlas y con qué caché.

Vive en services/telemetry/ (fuera de services/api/) porque así lo pide la
ruta que evalúa la rúbrica de la clase — el resto de los módulos de
telemetría (telemetry_models.py, telemetry_service.py, telemetry_repository.py)
son archivos planos dentro de services/api/, pero éste es la excepción
deliberada. Para poder importar `database`/`telemetry_models` de todas
formas, depende de que services/api/ esté en sys.path cuando el proceso
arranca — cierto tanto en dev/prod (uvicorn se lanza con cwd=services/api)
como en los tests (conftest.py inserta esa carpeta en sys.path antes de
importar `main`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from sqlmodel import Session, select

from telemetry_models import TelemetryEventRecord


def _load_events(
    session: Session,
    *,
    start_date: datetime,
    end_date: datetime,
    event_types: list[str] | None = None,
) -> pd.DataFrame:
    """Paso de carga (SQL), compartido por las funciones de métrica de abajo.

    El rango de fechas siempre se aplica en la consulta — nunca se trae la
    tabla completa. event_types es opcional: las métricas de volumen/tasa de
    error necesitan ver todos los tipos para calcular el total; las que
    responden sobre un tipo puntual (ej. web vitals) lo pasan para que el
    filtro también baje a SQL, no se aplique después en Pandas.
    """
    query = select(TelemetryEventRecord).where(
        TelemetryEventRecord.timestamp >= start_date,
        TelemetryEventRecord.timestamp < end_date,
    )
    if event_types is not None:
        query = query.where(TelemetryEventRecord.event_type.in_(event_types))

    rows = session.exec(query).all()
    return pd.DataFrame(
        [
            {
                "timestamp": row.timestamp,
                "event_type": row.event_type,
                "level": row.level,
                "value": row.value,
                "tags": row.tags,
            }
            for row in rows
        ]
    )


def events_per_day(
    session: Session, *, start_date: datetime, end_date: datetime
) -> list[dict[str, Any]]:
    """Volumen de eventos por día y por tipo: ¿qué está pasando en el
    sistema, y con qué frecuencia? La pregunta operacional más básica."""
    df = _load_events(session, start_date=start_date, end_date=end_date)
    if df.empty:
        return []

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["date"] = df["timestamp"].dt.date.astype(str)

    result = df.groupby(["date", "event_type"]).size().reset_index(name="count")
    return result.to_dict(orient="records")


def error_rate_by_day(
    session: Session, *, start_date: datetime, end_date: datetime
) -> list[dict[str, Any]]:
    """Qué porcentaje de los eventos de cada día son errores (columna
    `level`, ya calculada por telemetry_service.derive_level al guardar cada
    evento) sobre el total de eventos de ese día — salud relativa, no un
    conteo absoluto sin contexto."""
    df = _load_events(session, start_date=start_date, end_date=end_date)
    if df.empty:
        return []

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["date"] = df["timestamp"].dt.date.astype(str)
    df["is_error"] = df["level"] == "error"

    daily = df.groupby("date").agg(
        total_events=("event_type", "count"),
        error_events=("is_error", "sum"),
    )
    daily["error_rate"] = daily["error_events"] / daily["total_events"]
    return daily.reset_index().to_dict(orient="records")


def web_vital_latency_by_day(
    session: Session, *, start_date: datetime, end_date: datetime
) -> list[dict[str, Any]]:
    """Promedio diario de cada métrica de Web Vitals (LCP, INP, CLS, etc.),
    como proxy de rendimiento percibido. No usamos api_latency_recorded
    (duration_ms real de cada request de backend) porque ese evento lo
    emite el backend sin pasar por POST /telemetry/events y hoy no se
    persiste (ver techContext.md) — web_vital_recorded sí llega a la tabla,
    porque el frontend lo manda a través de TelemetryService."""
    df = _load_events(
        session,
        start_date=start_date,
        end_date=end_date,
        event_types=["web_vital_recorded"],
    )
    if df.empty:
        return []

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["date"] = df["timestamp"].dt.date.astype(str)
    df["metric_name"] = df["tags"].apply(lambda tags: tags.get("metric_name"))
    df = df.dropna(subset=["metric_name", "value"])
    if df.empty:
        return []

    result = (
        df.groupby(["date", "metric_name"])["value"]
        .mean()
        .reset_index(name="avg_value")
    )
    return result.to_dict(orient="records")


def auth_failure_rate(
    session: Session, *, start_date: datetime, end_date: datetime
) -> list[dict[str, Any]]:
    """Actividad adicional: tasa diaria de fallos de login, login_failed
    sobre (login_failed + login_succeeded). Requiere que routes/auth.py
    persista ambos eventos — antes de esta clase solo se logueaban (ver
    telemetry_service.emit_backend_event)."""
    df = _load_events(
        session,
        start_date=start_date,
        end_date=end_date,
        event_types=["login_failed", "login_succeeded"],
    )
    if df.empty:
        return []

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["date"] = df["timestamp"].dt.date.astype(str)
    df["is_failure"] = df["event_type"] == "login_failed"

    daily = df.groupby("date").agg(
        total_attempts=("event_type", "count"),
        failed_attempts=("is_failure", "sum"),
    )
    daily["failure_rate"] = daily["failed_attempts"] / daily["total_attempts"]
    return daily.reset_index().to_dict(orient="records")
