"""Pipelines de datos de HealthCore. Punto de entrada: data/pipelines/pipeline.py.

El codigo de los pipelines reutiliza la conexion y los modelos de la API
(`database`, `telemetry_models`), que viven como modulos planos en
services/api/. La API ya tiene esa carpeta en sys.path (se arranca desde
ahi), pero `python data/pipelines/pipeline.py` no: se anade aqui una sola
vez para que cualquier punto de entrada funcione igual.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
API_DIR = ROOT_DIR / "services" / "api"

for _path in (str(ROOT_DIR), str(API_DIR)):
    if _path not in sys.path:
        sys.path.append(_path)
