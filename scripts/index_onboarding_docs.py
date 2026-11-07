"""Indexa el material oficial del onboarding en Qdrant (`healthcore_onboarding`).

    docker compose up -d qdrant
    services/api/.venv/bin/python scripts/index_onboarding_docs.py

Necesita LLM_BASE_URL, LLM_API_KEY y EMBEDDING_MODEL en services/api/.env
(los mismos del RAG del Hito 7). Recrea la colección entera; los vectores se
calculan antes de tocarla.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
for path in (str(ROOT_DIR), str(ROOT_DIR / "services" / "api")):
    if path not in sys.path:
        sys.path.insert(0, path)


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / "services" / "api" / ".env")
    from data.pipelines.onboarding_docs import setup

    try:
        summary = setup()
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo indexar ({type(exc).__name__}: revisa Qdrant y las variables del modelo de embeddings)", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
