"""Indexa la base de conocimiento RAG de HealthCore en Qdrant (setup()).

Uso (desde la raíz del repo, con el venv de la API y Qdrant levantado):

    docker compose up -d qdrant
    services/api/.venv/bin/python scripts/index_knowledge_base.py

Lee docs/company-knowledge-base/, trocea los 4 documentos del CONTEXT,
genera los vectores con EMBEDDING_MODEL y (re)crea la colección
`healthcore_knowledge`. Idempotente: volver a ejecutarlo no duplica puntos.

Códigos de salida: 0 correcto; 1 configuración, documentos o servicios fallidos.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data.process.rag import setup  # noqa: E402


def main() -> int:
    try:
        summary = setup()
    except Exception as exc:  # noqa: BLE001 - cualquier fallo es un error de CLI
        print(f"Error al indexar la base de conocimiento: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
