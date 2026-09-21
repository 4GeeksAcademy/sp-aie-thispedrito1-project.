"""Configuracion de los tests unitarios del pipeline de negocio.

Se ejecutan desde la raiz del monorepo con el venv de la API:

    services/api/.venv/bin/python -m pytest tests/pipelines/test_pipeline.py

Pytest, lanzado asi, solo pone tests/pipelines/ en sys.path; se anade la
raiz para poder importar el paquete `data` (data/pipelines/__init__.py
anade a su vez services/api, de donde salen `database` y los modelos).

Estos tests no abren ninguna conexion: ejecutan la funcion de cada task
(`task.fn`) con DataFrames en memoria. Importar pipeline.py no conecta con
Supabase (el engine se crea en la primera consulta) ni arranca Prefect.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
