"""Generador mínimo de PDF de texto (sin dependencias), para pruebas.

Lo usan scripts/make_phi_test_rfp.py (caso 4 del BRIEFING: RFP con PHI
ficticia) y los tests de conversión, que necesitan un PDF real para pasar
por MarkItDown. Una página por cada 45 líneas, Helvetica 11, solo ASCII
(el texto se sustituye por "?" fuera de latin-1).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence

LINES_PER_PAGE = 45


def _escape(line: str) -> str:
    text = line.encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(lines: Sequence[str]) -> bytes:
    pages: List[List[str]] = [list(lines[i : i + LINES_PER_PAGE]) for i in range(0, len(lines), LINES_PER_PAGE)] or [[]]
    objects: List[bytes] = []
    # 1 catálogo, 2 árbol de páginas, 3 fuente; después página + contenido por página.
    page_ids = [4 + 2 * n for n in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for n, page_lines in enumerate(pages):
        content_id = page_ids[n] + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        body = "BT /F1 11 Tf 14 TL 56 740 Td " + " ".join(f"({_escape(l)}) Tj T*" for l in page_lines) + " ET"
        stream = body.encode("latin-1")
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def write_pdf(lines: Sequence[str], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_pdf(lines))
    return path
