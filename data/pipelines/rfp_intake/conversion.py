"""PDF → Markdown (MarkItDown) y troceado en bloques numerados.

Ningún agente lee el PDF: todos trabajan sobre el Markdown que sale de aquí,
más barato en tokens y sin el ruido del formato. La limpieza corrige lo que
se vio con los PDF reales del BRIEFING:

- Las viñetas salen como `(cid:127)` (glifo sin nombre en la fuente del PDF).
- Las líneas de un párrafo llegan partidas donde el PDF hacía salto visual.
- Las tablas pierden su forma (criterios por un lado, pesos por otro). No se
  intenta reconstruirlas: el worker tiene prohibido emparejar cifras que no
  estén juntas en su extracto (ver agents.py).

`split_sections` parte el Markdown por párrafos. Cada bloque lleva un índice
estable: el orquestador reparte índices, no texto, y cada worker recibe solo
los bloques de su departamento.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

# Tope de seguridad del Markdown que entra en el flujo. Una RFP real de
# decenas de páginas cabe de sobra; por encima se recorta y se avisa, en vez
# de mandar al modelo un documento que dispare el coste sin control.
MAX_MARKDOWN_CHARS = 60_000

_CID_BULLET = re.compile(r"^[ \t]*\(cid:\d+\)[ \t]*", re.MULTILINE)
_CID_ANY = re.compile(r"\(cid:\d+\)")
_BLANK_LINES = re.compile(r"\n\s*\n+")


class ConversionError(RuntimeError):
    """El PDF no se pudo convertir o no contiene texto (p. ej. un escaneo)."""


@dataclass(frozen=True)
class Section:
    index: int
    text: str


def pdf_to_markdown(pdf_path: Path) -> str:
    from markitdown import MarkItDown  # import diferido: carga onnxruntime/magika

    try:
        result = MarkItDown(enable_plugins=False).convert(str(pdf_path))
    except Exception as exc:  # PDF corrupto o cifrado
        raise ConversionError(f"MarkItDown no pudo convertir el PDF ({type(exc).__name__})") from exc
    markdown = clean_markdown(result.text_content or "")
    if not markdown:
        raise ConversionError("El PDF no contiene texto extraíble (¿es un escaneo?)")
    return markdown


def clean_markdown(text: str) -> str:
    text = _CID_BULLET.sub("- ", text)
    text = _CID_ANY.sub("", text)
    paragraphs = []
    for block in _BLANK_LINES.split(text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines:
            # Un párrafo (o una viñeta) partido en varias líneas vuelve a ser uno.
            paragraphs.append(" ".join(lines))
    cleaned = "\n\n".join(paragraphs)
    return cleaned[:MAX_MARKDOWN_CHARS]


def split_sections(markdown: str) -> List[Section]:
    return [
        Section(index=index, text=block.strip())
        for index, block in enumerate(_BLANK_LINES.split(markdown))
        if block.strip()
    ]


def render_sections(sections: List[Section]) -> str:
    """Formato con índice que leen el clasificador y el orquestador."""
    return "\n\n".join(f"[{section.index}] {section.text}" for section in sections)
