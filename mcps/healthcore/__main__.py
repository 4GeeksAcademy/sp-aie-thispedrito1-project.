"""Arranque: `services/api/.venv/bin/python -m mcps.healthcore` desde la raíz del repo."""

from __future__ import annotations

import logging
import sys

import uvicorn

from mcps.healthcore.config import McpConfigError, load_settings
from mcps.healthcore.server import create_app


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Cada petición del SDK MCP genera líneas `HTTP Request` de httpx: ruido.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = load_settings()
        app = create_app(settings)
    except McpConfigError as exc:
        print(f"Configuración incompleta: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # p. ej. Logto inalcanzable al descubrir el emisor
        print(f"No se pudo iniciar el servidor MCP ({type(exc).__name__}). ¿Es correcto MCP_OAUTH_ISSUER?", file=sys.stderr)
        return 1
    # proxy_headers: detrás del reenvío de Codespaces, respeta X-Forwarded-*.
    uvicorn.run(app, host=settings.host, port=settings.port, proxy_headers=True, forwarded_allow_ips="*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
