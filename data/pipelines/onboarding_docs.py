"""Material oficial del onboarding: carga, selección por persona, indexado y búsqueda.

Es la ÚNICA fuente que el agente puede usar (BRIEFING, "Material oficial que
debes preparar"). Vive en docs/company-onboarding/<idioma>/<doc_id>.md con
una cabecera (`doc_id`, `stage`, `variant`, `language`, `title`) y secciones
`## ...`. Cada sección es la unidad que se presenta, que se cita como fuente
(`doc_id §Sección`) y que se indexa en Qdrant.

Adaptación al puesto sin modelo: una sección cuyo título acaba en
`{for: digital, revenue_cycle}` solo se presenta a esos departamentos; las
etapas 4 y 5 eligen documento según el marco regulatorio y el puesto. Un
documento que no existe en el idioma de la persona NO se sustituye por el
inglés ni se traduce al vuelo: `select_document` lanza `MissingMaterialError`
(la API ya impide dar de alta esa combinación).

Colección propia, `healthcore_onboarding`, separada de `healthcore_knowledge`
(RAG del Hito 7): son corpus con dueños y lectores distintos. Reutiliza
`embed()` y los clientes de data/process/rag.py.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("healthcore.onboarding.docs")

ROOT_DIR = Path(__file__).resolve().parents[2]
CORPUS_DIR = ROOT_DIR / "docs" / "company-onboarding"
CHECKS_DIR = CORPUS_DIR / "checks"

COLLECTION_NAME = "healthcore_onboarding"
VARIANT_ALL = "all"
POINT_ID_NAMESPACE = uuid.UUID("2f8d4c61-7a0e-4b9f-8c3d-5e1a6b7c9d20")

# Umbral de similitud para considerar que el material cubre la duda.
# Calibrado el 2026-11-07 con scripts/evaluate_onboarding_retrieval.py y los
# embeddings reales (pplx-embed, el mismo modelo que el Hito 7): la peor
# pregunta cubierta da 0.397 y la mejor fuera de tema 0.363 ("¿hay parking?").
# Una pregunta sobre un DETALLE no escrito de un tema que sí está (retención
# de los registros de accesos, 0.448) supera cualquier umbral razonable: esa
# la para la segunda capa (el modelo responde NOT_COVERED). Si cambian los
# documentos o el modelo de embeddings, se vuelve a medir antes de tocarlo.
DEFAULT_MIN_SCORE = 0.38
DEFAULT_K = 4

_FOR_TAG = re.compile(r"\s*\{for:\s*([a-z_,\s]+)\}\s*$")
_REQUIRED_HEADER_KEYS = ("doc_id", "stage", "variant", "language", "title")


class MissingMaterialError(LookupError):
    """No hay material oficial para esta combinación (etapa, variante, idioma)."""


@dataclass(frozen=True)
class Section:
    title: str
    body: str
    departments: Tuple[str, ...] = ()  # vacío = para todos

    def applies_to(self, department: str) -> bool:
        return not self.departments or department in self.departments


@dataclass(frozen=True)
class OfficialDocument:
    doc_id: str
    stage: int
    variant: str
    language: str
    title: str
    sections: Tuple[Section, ...]
    path: str

    def section(self, title: str) -> Optional[Section]:
        return next((s for s in self.sections if s.title == title), None)


@dataclass(frozen=True)
class HireProfile:
    """Lo único de la persona que el material necesita saber. Sin nombre ni
    ningún dato personal: esto entra en el estado del grafo y en los prompts."""

    language: str
    framework: str
    role_variant: str
    department: str


# --- Carga -----------------------------------------------------------------


def parse_document(markdown: str, path: str = "") -> OfficialDocument:
    if not markdown.startswith("---\n"):
        raise ValueError(f"{path}: falta la cabecera")
    header_text, _, body = markdown[4:].partition("\n---\n")
    header: Dict[str, str] = {}
    for line in header_text.splitlines():
        key, _, value = line.partition(":")
        if key.strip():
            header[key.strip()] = value.strip()
    missing = [key for key in _REQUIRED_HEADER_KEYS if not header.get(key)]
    if missing:
        raise ValueError(f"{path}: faltan claves de cabecera {missing}")

    sections: List[Section] = []
    current_title: Optional[str] = None
    current_lines: List[str] = []

    def flush() -> None:
        if current_title is None:
            return
        text = " ".join(" ".join(current_lines).split())
        if not text:
            raise ValueError(f"{path}: la sección {current_title!r} está vacía")
        title, departments = current_title, ()
        tag = _FOR_TAG.search(current_title)
        if tag:
            title = current_title[: tag.start()].strip()
            departments = tuple(d.strip() for d in tag.group(1).split(",") if d.strip())
        sections.append(Section(title, text, departments))

    for line in body.splitlines():
        if line.startswith("## "):
            flush()
            current_title, current_lines = line[3:].strip(), []
        elif line.startswith("# "):
            continue
        elif current_title is not None:
            current_lines.append(line)
    flush()
    if not sections:
        raise ValueError(f"{path}: el documento no tiene secciones")
    return OfficialDocument(
        doc_id=header["doc_id"],
        stage=int(header["stage"]),
        variant=header["variant"],
        language=header["language"],
        title=header["title"],
        sections=tuple(sections),
        path=path,
    )


@lru_cache(maxsize=4)
def load_corpus(corpus_dir: Path = CORPUS_DIR) -> Dict[Tuple[str, str], OfficialDocument]:
    """Todos los documentos oficiales, por (doc_id, idioma)."""
    corpus: Dict[Tuple[str, str], OfficialDocument] = {}
    for path in sorted(corpus_dir.glob("*/*.md")):
        document = parse_document(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT_DIR)))
        if document.language != path.parent.name:
            raise ValueError(f"{path}: el idioma de la cabecera no coincide con la carpeta")
        corpus[(document.doc_id, document.language)] = document
    if not corpus:
        raise ValueError(f"No hay material oficial en {corpus_dir}")
    return corpus


# --- Selección por persona -----------------------------------------------------


def doc_id_for(stage: int, profile: HireProfile) -> str:
    if stage == 2:
        return "company"
    if stage == 3:
        return "operations"
    if stage == 4:
        return f"compliance-{profile.framework.replace('_', '-')}"
    if stage == 5:
        return f"role-{profile.role_variant.replace('_', '-')}"
    if stage == 6:
        return "ways-of-working"
    raise ValueError(f"La etapa {stage} no es de contenido")


def select_document(stage: int, profile: HireProfile, corpus_dir: Path = CORPUS_DIR) -> OfficialDocument:
    doc_id = doc_id_for(stage, profile)
    document = load_corpus(corpus_dir).get((doc_id, profile.language))
    if document is None:
        raise MissingMaterialError(f"No existe material oficial {doc_id!r} en {profile.language!r}")
    return document


def material_available(profile: HireProfile, corpus_dir: Path = CORPUS_DIR) -> List[str]:
    """Documentos que faltan para recorrer las 5 etapas de contenido (vacío = todo existe)."""
    missing = []
    for stage in (2, 3, 4, 5, 6):
        try:
            select_document(stage, profile, corpus_dir)
            load_check_bank(doc_id_for(stage, profile), profile.language)
        except (MissingMaterialError, FileNotFoundError):
            missing.append(doc_id_for(stage, profile))
    return missing


def present(stage: int, profile: HireProfile, *, only_sections: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Material de la etapa para esta persona: secciones de su departamento.

    `only_sections` sirve para el refuerzo: tras un intento fallido se vuelven
    a presentar solo las secciones de las preguntas falladas."""
    document = select_document(stage, profile)
    sections = [s for s in document.sections if s.applies_to(profile.department)]
    if only_sections is not None:
        wanted = set(only_sections)
        sections = [s for s in sections if s.title in wanted]
    return {
        "doc_id": document.doc_id,
        "title": document.title,
        "language": document.language,
        "source": document.path,
        "sections": [{"title": s.title, "body": s.body, "citation": citation(document.doc_id, s.title)} for s in sections],
    }


def citation(doc_id: str, section_title: str) -> str:
    return f"{doc_id} §{section_title}"


# --- Bancos de preguntas -----------------------------------------------------


@lru_cache(maxsize=64)
def load_check_bank(doc_id: str, language: str, checks_dir: Path = CHECKS_DIR) -> Dict[str, Any]:
    path = checks_dir / f"{doc_id}.{language}.json"
    if not path.exists():
        raise FileNotFoundError(f"No hay comprobación {doc_id!r} en {language!r}")
    bank = json.loads(path.read_text(encoding="utf-8"))
    if bank.get("doc_id") != doc_id or bank.get("language") != language:
        raise ValueError(f"{path}: doc_id/idioma no coinciden con el nombre del archivo")
    return bank


# --- Indexado y búsqueda (Qdrant) ---------------------------------------------


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    stage: int
    variant: str
    language: str
    title: str
    section: str
    departments: Tuple[str, ...]
    text: str

    @property
    def point_id(self) -> str:
        return str(uuid.uuid5(POINT_ID_NAMESPACE, f"{self.doc_id}|{self.language}|{self.section}"))

    def embedding_input(self) -> str:
        return f"{self.title} — {self.section}\n{self.text}"

    def payload(self) -> Dict[str, Any]:
        return {
            "company": "healthcore",
            "doc_id": self.doc_id,
            "stage": self.stage,
            "variant": self.variant,
            "language": self.language,
            "section": self.section,
            # Lista vacía = todos los departamentos (Qdrant no filtra bien "ausente").
            "departments": list(self.departments) or ["*"],
            "citation": citation(self.doc_id, self.section),
            "text": self.text,
        }


def load_chunks(corpus_dir: Path = CORPUS_DIR) -> List[Chunk]:
    return [
        Chunk(doc.doc_id, doc.stage, doc.variant, doc.language, doc.title, s.title, s.departments, s.body)
        for doc in load_corpus(corpus_dir).values()
        for s in doc.sections
    ]


def _default_embed(text: str) -> List[float]:
    from data.process.rag import embed

    return embed(text)


def _default_client():
    from data.process.rag import get_qdrant_client

    return get_qdrant_client()


def setup(
    *,
    client: Optional[Any] = None,
    embed_fn: Callable[[str], List[float]] = _default_embed,
    corpus_dir: Path = CORPUS_DIR,
) -> Dict[str, Any]:
    """(Re)crea la colección. Los vectores se calculan antes de tocar Qdrant:
    si el modelo de embeddings falla, la colección anterior sigue intacta."""
    from qdrant_client import models

    chunks = load_chunks(corpus_dir)
    vectors = [embed_fn(chunk.embedding_input()) for chunk in chunks]
    dimension = len(vectors[0])
    if any(len(v) != dimension for v in vectors):
        raise ValueError("El modelo de embeddings devolvió vectores de tamaños distintos")
    qdrant = client or _default_client()
    if qdrant.collection_exists(COLLECTION_NAME):
        qdrant.delete_collection(COLLECTION_NAME)
    qdrant.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
    )
    qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[models.PointStruct(id=c.point_id, vector=v, payload=c.payload()) for c, v in zip(chunks, vectors)],
        wait=True,
    )
    per_language: Dict[str, int] = {}
    for chunk in chunks:
        per_language[chunk.language] = per_language.get(chunk.language, 0) + 1
    summary = {"collection": COLLECTION_NAME, "chunks": len(chunks), "vector_size": dimension, "per_language": per_language}
    logger.info("onboarding_corpus_indexed %s", json.dumps(summary))
    return summary


def visible_variants(profile: HireProfile) -> List[str]:
    """Variantes que esta persona puede consultar: lo común, SU marco y SU puesto."""
    return [VARIANT_ALL, profile.framework, profile.role_variant]


def retrieve(
    question: str,
    profile: HireProfile,
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    k: int = DEFAULT_K,
    client: Optional[Any] = None,
    embed_fn: Callable[[str], List[float]] = _default_embed,
) -> List[Dict[str, Any]]:
    """Secciones del material oficial de ESTA persona que superan el umbral.

    El filtro va a Qdrant (idioma, variante, departamento): una persona bajo
    UK GDPR nunca recibe una cita de la política HIPAA. El umbral se aplica
    aquí, a la vista, para poder testearlo. En el log solo van citas y
    puntuaciones, nunca la pregunta."""
    from qdrant_client import models

    qdrant = client or _default_client()
    query_filter = models.Filter(
        must=[
            models.FieldCondition(key="language", match=models.MatchValue(value=profile.language)),
            models.FieldCondition(key="variant", match=models.MatchAny(any=visible_variants(profile))),
            models.FieldCondition(key="departments", match=models.MatchAny(any=["*", profile.department])),
        ]
    )
    response = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=embed_fn(question),
        query_filter=query_filter,
        limit=k,
        with_payload=True,
    )
    results = [
        {**(point.payload or {}), "score": float(point.score)}
        for point in response.points
        if point.score is not None and point.score >= min_score
    ]
    logger.info(
        "onboarding_retrieve hits=%d/%d min_score=%.2f %s",
        len(results),
        len(response.points),
        min_score,
        [(r.get("citation"), round(r["score"], 3)) for r in results],
    )
    return results

