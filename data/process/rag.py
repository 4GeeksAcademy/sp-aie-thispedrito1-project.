"""Preparación e indexación de la base de conocimiento RAG de HealthCore (Hito 7).

Dos de las cuatro funciones del pipeline viven aquí:

- `setup()`: lee los documentos de docs/company-knowledge-base/, los parte
  en chunks semánticos y los guarda en la colección `healthcore_knowledge`
  de Qdrant, con el payload del CONTEXT (`company`, `source_document`,
  `section`, `language`, `chunk_index`) más `text`.
- `embed()`: convierte un texto en vector con el modelo de EMBEDDINGS
  (`EMBEDDING_MODEL`). Es la misma función al indexar y al consultar, y
  nunca usa el modelo de generación (`GENERATION_MODEL`, data/pipelines/rag.py).

`retrieve()`, `generate_answer()` y `query()` están en data/pipelines/rag.py.
Diseño completo en docs/rag/rag-design.md.

Estrategia de chunking (los documentos no tienen subtítulos, solo un título
`#` y bloques separados por líneas en blanco):

1. Cada bloque separado por una línea en blanco es una unidad candidata.
2. Un bloque con etiqueta ("Política de cancelación:" + lista, o
   "Recordatorios automáticos: el sistema...") forma un chunk cuya `section`
   es la etiqueta.
3. Una frase de entrada que termina en ":" ("Todo paciente nuevo debe
   completar antes de su primera cita:") se une a la lista que presenta; si
   lo que sigue no es una lista, se antepone al bloque siguiente sin cambiar
   su sección.
4. Un párrafo sin etiqueta forma su propio chunk con el título del
   documento como `section`.

Así ninguna regla queda separada de su condición: cada viñeta viaja con la
etiqueta que le da sentido, y cada párrafo es una regla completa.

Idempotencia: `setup()` borra y vuelve a crear la colección (limpiar y
recargar), con IDs deterministas (`uuid5` de documento + posición). Volver
a ejecutarlo nunca duplica puntos, y un chunk que desaparece del documento
tampoco sobrevive en Qdrant.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
KNOWLEDGE_BASE_DIR = ROOT_DIR / "docs" / "company-knowledge-base"

# Mismo .env que la API. load_dotenv no sobreescribe variables ya definidas,
# así que en Docker ganan las del compose (igual que services/celery_app.py).
load_dotenv(ROOT_DIR / "services" / "api" / ".env")

logger = logging.getLogger(__name__)

# Valores literales del CONTEXT del hito (secciones 2 y 3).
COLLECTION_NAME = "healthcore_knowledge"
COMPANY = "healthcore"
LANGUAGE = "es"
SOURCE_DOCUMENTS: Dict[str, str] = {
    "healthcore-insurance-coverage.es.md": "insurance-coverage",
    "healthcore-appointment-policy.es.md": "appointment-policy",
    "healthcore-referral-process.es.md": "referral-process",
    "healthcore-new-patient-checklist.es.md": "new-patient-checklist",
}
MIN_CHUNKS_PER_DOCUMENT = 3

# Espacio de nombres fijo para los IDs de los puntos: mismo documento y misma
# posición producen siempre el mismo UUID.
POINT_ID_NAMESPACE = uuid.UUID("5b0f3c2e-8d7a-4a51-9a64-7e2f1c9d4b10")

_LIST_ITEM = re.compile(r"^(?:[-*]|\d+\.)\s+")
# "Recordatorios automáticos: el sistema envía..." → etiqueta en línea.
# Corta a propósito (≤ 60 caracteres, sin punto) para no confundir una frase
# normal que contenga dos puntos con una etiqueta.
_INLINE_LABEL = re.compile(r"^([^:.\n]{3,60}):\s+(\S.*)$", re.DOTALL)


class RagConfigError(RuntimeError):
    """Falta configuración (URL, clave o modelo) para hablar con los modelos."""


@dataclass(frozen=True)
class Chunk:
    source_document: str
    section: str
    chunk_index: int
    text: str
    document_title: str

    @property
    def point_id(self) -> str:
        return str(uuid.uuid5(POINT_ID_NAMESPACE, f"{self.source_document}:{self.chunk_index}"))

    def embedding_input(self) -> str:
        """Texto que se convierte en vector: título + sección + cuerpo.

        El título da contexto a los chunks cuya frase no nombra el tema
        ("Ningún coordinador debe confirmar..." es sobre seguros)."""
        header = self.document_title
        if self.section != self.document_title:
            header = f"{header} — {self.section}"
        return f"{header}\n{self.text}"

    def payload(self) -> Dict[str, Any]:
        return {
            "company": COMPANY,
            "source_document": self.source_document,
            "section": self.section,
            "language": LANGUAGE,
            "chunk_index": self.chunk_index,
            "text": self.text,
        }


# --- Chunking ---------------------------------------------------------------


def _unwrap_lines(block: str) -> List[str]:
    """Une las líneas cortadas a mano (continuaciones con sangría) a su
    línea lógica: una viñeta o una frase por elemento."""
    lines: List[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        if lines and not _LIST_ITEM.match(line) and (raw[:1].isspace() or not _LIST_ITEM.match(lines[-1])):
            lines[-1] = f"{lines[-1]} {line}"
        else:
            lines.append(line)
    return lines


def _is_list_block(lines: Sequence[str]) -> bool:
    return bool(lines) and bool(_LIST_ITEM.match(lines[0]))


def _is_lead_in(lines: Sequence[str]) -> bool:
    """Frase de entrada: un solo párrafo, sin lista, que acaba en ':'."""
    return len(lines) == 1 and lines[0].endswith(":") and not _LIST_ITEM.match(lines[0])


def chunk_document(markdown: str, source_document: str) -> List[Chunk]:
    """Parte un documento fuente en chunks semánticos (ver docstring del módulo)."""
    title = ""
    body_lines: List[str] = []
    for line in markdown.splitlines():
        if not title and line.startswith("# "):
            title = line[2:].strip()
        else:
            body_lines.append(line)
    if not title:
        raise ValueError(f"{source_document}: falta el título '# ...' del documento")

    blocks = [_unwrap_lines(b) for b in re.split(r"\n\s*\n", "\n".join(body_lines))]
    blocks = [b for b in blocks if b]

    units: List[tuple] = []  # (section, lines)
    preamble: List[str] = []
    i = 0
    while i < len(blocks):
        lines = blocks[i]
        if _is_lead_in(lines) and i + 1 < len(blocks):
            nxt = blocks[i + 1]
            if _is_list_block(nxt):
                units.append((lines[0][:-1].strip(), preamble + lines + nxt))
                preamble = []
                i += 2
                continue
            preamble.extend(lines)
            i += 1
            continue

        first = lines[0]
        if first.endswith(":") and len(lines) > 1 and _is_list_block(lines[1:]):
            section = first[:-1].strip()
        else:
            match = _INLINE_LABEL.match(first)
            section = match.group(1).strip() if match else title
        units.append((section, preamble + lines))
        preamble = []
        i += 1

    if preamble:  # frase de entrada al final del documento, sin nada detrás
        units.append((title, preamble))

    return [
        Chunk(
            source_document=source_document,
            section=section,
            chunk_index=index,
            text="\n".join(lines),
            document_title=title,
        )
        for index, (section, lines) in enumerate(units)
    ]


def load_chunks(knowledge_base_dir: Path = KNOWLEDGE_BASE_DIR) -> List[Chunk]:
    """Lee los cuatro documentos del CONTEXT y los devuelve troceados.

    Falla si falta alguno o si alguno produce menos de 3 chunks (sección 5
    del CONTEXT): una base de conocimiento incompleta no se indexa."""
    chunks: List[Chunk] = []
    for filename, source_document in SOURCE_DOCUMENTS.items():
        path = knowledge_base_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"Falta el documento fuente {path}")
        document_chunks = chunk_document(path.read_text(encoding="utf-8"), source_document)
        if len(document_chunks) < MIN_CHUNKS_PER_DOCUMENT:
            raise ValueError(
                f"{filename} produjo {len(document_chunks)} chunks; el CONTEXT exige al menos {MIN_CHUNKS_PER_DOCUMENT}"
            )
        chunks.extend(document_chunks)
    return chunks


# --- Clientes y embeddings --------------------------------------------------


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RagConfigError(f"Falta la variable de entorno {name} (ver services/api/.env.example)")
    return value


@lru_cache(maxsize=1)
def get_llm_client():
    """Cliente compatible con OpenAI apuntando a los modelos de 4Geeks.

    Lo comparten embed() y la generación: mismo proveedor, distinto modelo."""
    from openai import OpenAI

    return OpenAI(
        base_url=_required_env("LLM_BASE_URL"),
        api_key=_required_env("LLM_API_KEY"),
        timeout=30.0,
        max_retries=2,
    )


@lru_cache(maxsize=1)
def get_qdrant_client():
    from qdrant_client import QdrantClient

    return QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"), timeout=10)


def get_embedding_model() -> str:
    model = _required_env("EMBEDDING_MODEL")
    if model == os.getenv("GENERATION_MODEL", "").strip():
        raise RagConfigError("EMBEDDING_MODEL y GENERATION_MODEL deben ser modelos distintos")
    return model


def embed(text: str) -> List[float]:
    """Vector de un texto con el modelo de embeddings dedicado.

    Se usa igual para los chunks (setup) y para la pregunta (retrieve): el
    único preprocesado es colapsar espacios, idéntico en ambos casos."""
    normalized = " ".join(text.split())
    if not normalized:
        raise ValueError("No se puede generar el embedding de un texto vacío")
    response = get_llm_client().embeddings.create(model=get_embedding_model(), input=normalized)
    return list(response.data[0].embedding)


# --- Indexación -------------------------------------------------------------


def setup(
    knowledge_base_dir: Path = KNOWLEDGE_BASE_DIR,
    *,
    client: Optional[Any] = None,
    embed_fn: Callable[[str], List[float]] = embed,
) -> Dict[str, Any]:
    """Trocea, genera los vectores y (re)crea la colección `healthcore_knowledge`.

    Los vectores se calculan ANTES de tocar Qdrant: si el modelo de
    embeddings falla, la colección que ya existía sigue intacta."""
    from qdrant_client import models

    chunks = load_chunks(knowledge_base_dir)
    vectors = [embed_fn(chunk.embedding_input()) for chunk in chunks]
    dimension = len(vectors[0])
    if any(len(v) != dimension for v in vectors):
        raise ValueError("El modelo de embeddings devolvió vectores de tamaños distintos")

    qdrant = client or get_qdrant_client()
    if qdrant.collection_exists(COLLECTION_NAME):
        qdrant.delete_collection(COLLECTION_NAME)
    qdrant.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
    )
    qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            models.PointStruct(id=chunk.point_id, vector=vector, payload=chunk.payload())
            for chunk, vector in zip(chunks, vectors)
        ],
        wait=True,
    )

    per_document: Dict[str, int] = {}
    for chunk in chunks:
        per_document[chunk.source_document] = per_document.get(chunk.source_document, 0) + 1
    summary = {
        "collection": COLLECTION_NAME,
        "chunks": len(chunks),
        "vector_size": dimension,
        "per_document": per_document,
    }
    logger.info("Base de conocimiento indexada: %s", summary)
    return summary
