"""Métricas de legibilidad del Markdown como estimador de coste.

No es una nota literaria: un grado Flesch-Kincaid alto y muchas palabras por
frase anticipan más tokens y más trabajo de extracción por departamento. Se
guarda junto a los metadatos para que Revenue Cycle vea, antes de la Parte 2,
cuánto va a costar procesar cada RFP.

Dos límites reales de `py-readability-metrics`:
- Usa el tokenizador `punkt_tab` de NLTK. Si falta, se descarga UNA vez
  dentro del venv (`sys.prefix/nltk_data`, ruta que NLTK ya consulta), nunca
  en la carpeta personal del usuario.
- Exige al menos 100 palabras. Un correo corto que sí es RFP no debe tumbar
  el pipeline: las métricas quedan en None con `readability_note`.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MIN_WORDS = 100
# Aproximación habitual para texto en inglés: ~4 caracteres por token. Basta
# para comparar RFPs entre sí; el coste exacto depende del tokenizador.
CHARS_PER_TOKEN = 4

_NLTK_DIR = Path(sys.prefix) / "nltk_data"


def _ensure_punkt() -> None:
    import nltk

    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        logger.info("Descargando el tokenizador punkt_tab de NLTK en %s", _NLTK_DIR)
        nltk.download("punkt_tab", download_dir=str(_NLTK_DIR), quiet=True)
        if str(_NLTK_DIR) not in nltk.data.path:
            nltk.data.path.append(str(_NLTK_DIR))


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 2)


def compute_readability(markdown: str) -> Dict[str, Any]:
    words = markdown.split()
    metrics: Dict[str, Any] = {
        "word_count": len(words),
        "sentence_count": None,
        "estimated_tokens": max(1, len(markdown) // CHARS_PER_TOKEN) if markdown else 0,
        "flesch_kincaid_grade": None,
        "gunning_fog": None,
        "flesch_reading_ease": None,
        "readability_note": None,
    }
    if len(words) < MIN_WORDS:
        metrics["readability_note"] = f"Texto demasiado corto para las métricas (mínimo {MIN_WORDS} palabras)"
        return metrics
    try:
        _ensure_punkt()
        from readability import Readability

        analysis = Readability(markdown)
        metrics["sentence_count"] = analysis.statistics().get("num_sentences")
        metrics["flesch_kincaid_grade"] = _round(analysis.flesch_kincaid().score)
        metrics["gunning_fog"] = _round(analysis.gunning_fog().score)
        metrics["flesch_reading_ease"] = _round(analysis.flesch().score)
    except Exception as exc:  # sin red para descargar punkt, texto raro, etc.
        logger.warning("Readability metrics unavailable: %s", type(exc).__name__)
        metrics["readability_note"] = "Métricas de legibilidad no disponibles"
    return metrics
