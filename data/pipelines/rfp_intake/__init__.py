"""Recepción y enrutamiento de RFPs (Hito 9, Parte 1). Ver docs/rfp-intake.md.

Grafo dedicado `rfp_intake` (graph.py): conversión a Markdown, filtro de PHI,
clasificador, orquestador, un worker por departamento y synthesizer. Los
routers de services/ solo disparan (tarea de Celery) y consultan.
"""

import data.pipelines  # noqa: F401  (añade la raíz y services/api a sys.path)
