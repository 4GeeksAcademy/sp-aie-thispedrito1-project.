"""Ejecución trazada del grafo del agente.

`run_agent()` es la única forma de ejecutar el grafo fuera de los tests de
estructura: lo recorre con `stream_mode="updates"` (LangGraph avisa cada vez
que un nodo termina y con lo que escribió), mide cada paso, lee del
checkpointer la lista de checkpoints de la corrida y escribe un trace JSON
consultable en `<trace_dir>/<trace_id>.json`. El orden de los pasos del trace
es el de ejecución real, no uno reconstruido.

Privacidad (HIPAA / UK GDPR, misma regla que los logs del RAG): la pregunta
NUNCA se guarda en el trace. Se sustituye por su SHA-256 y su longitud, que
bastan para correlacionar una corrida con un caso de evaluación conocido. Es
una huella, no una anonimización fuerte: una pregunta corta y predecible se
podría adivinar probando candidatos. De los chunks se guardan fuente, sección,
índice y puntuación, no el texto (ya está en docs/company-knowledge-base/).

Checkpoints: por defecto se borran del checkpointer en memoria al terminar
(`keep_checkpoints=False`), para que la API no acumule una corrida por
petición en RAM; sus identificadores quedan en el trace. Los tests y la
depuración pasan `keep_checkpoints=True` para inspeccionar o reanudar.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.agent.graph import GRAPH_NAME

logger = logging.getLogger(__name__)

TRACE_SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRACE_DIR = REPO_ROOT / "data" / "traces" / "agent"
CHUNK_TRACE_FIELDS = ("source_document", "section", "chunk_index", "score")


class AgentRunError(RuntimeError):
    """Un nodo falló. Lleva el nodo y el trace de la corrida fallida, nunca
    el mensaje original (podría repetir la pregunta o datos del proveedor)."""

    def __init__(self, trace_id: str, node: Optional[str], cause: BaseException) -> None:
        super().__init__(f"Agent run {trace_id} failed at node {node!r} ({type(cause).__name__})")
        self.trace_id = trace_id
        self.node = node
        self.cause = cause


@dataclass(frozen=True)
class AgentRunResult:
    answer: Optional[str]
    outcome: Optional[str]
    trace_id: str
    trace_path: Optional[Path]
    trace: Dict[str, Any]


def get_trace_dir() -> Path:
    raw = os.getenv("AGENT_TRACE_DIR", "").strip()
    return Path(raw) if raw else DEFAULT_TRACE_DIR


def fingerprint(text: str) -> Dict[str, Any]:
    return {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "length": len(text)}


def redact_update(update: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Lo que un nodo escribió, en la forma apta para el trace."""
    redacted: Dict[str, Any] = {}
    for key, value in (update or {}).items():
        if key == "question":
            redacted["question"] = fingerprint(value or "")
        elif key == "context":
            redacted["context"] = [{field: chunk.get(field) for field in CHUNK_TRACE_FIELDS} for chunk in value or []]
        else:
            redacted[key] = value
    return redacted


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _checkpoints(graph: Any, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Checkpoints de la corrida, del más antiguo al más reciente."""
    history = list(graph.get_state_history(config))
    history.reverse()
    return [
        {
            "checkpoint_id": snapshot.config["configurable"]["checkpoint_id"],
            "step": snapshot.metadata.get("step"),
            "source": snapshot.metadata.get("source"),
            "next": list(snapshot.next),
        }
        for snapshot in history
    ]


def _failed_node(graph: Any, config: Dict[str, Any]) -> Optional[str]:
    """El nodo que falló. LangGraph lo apunta en `tasks[].error` del último
    checkpoint; si el fallo fue en una arista condicional, `next` queda vacío
    y ese es el único sitio donde aparece (comprobado). Si no hay error
    registrado, el nodo pendiente en `next` es el que no llegó a completarse."""
    try:
        snapshot = graph.get_state(config)
    except Exception:  # sin checkpoint no hay forma de saberlo
        return None
    for task in snapshot.tasks:
        if task.error:
            return task.name
    return snapshot.next[0] if snapshot.next else None


def write_trace(trace: Dict[str, Any], trace_dir: Path, file_stem: Optional[str] = None) -> Path:
    """Escritura atómica (tmp + rename): nunca queda un JSON a medias."""
    trace_dir.mkdir(parents=True, exist_ok=True)
    path = trace_dir / f"{file_stem or trace['trace_id']}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def run_agent(
    graph: Any,
    question: str,
    *,
    trace_id: Optional[str] = None,
    trace_dir: Optional[Path] = None,
    file_stem: Optional[str] = None,
    keep_checkpoints: bool = False,
) -> AgentRunResult:
    """Ejecuta el grafo compilado para una pregunta y deja su trace en disco.

    Si un nodo falla, el trace se escribe igualmente con `status="failed"` y
    el nodo culpable, y se lanza `AgentRunError`. Si lo que falla es escribir
    el trace, se registra y la respuesta se devuelve igual: un disco lleno no
    debe tumbar la consulta del coordinador (mismo principio que telemetría)."""
    trace_id = trace_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": trace_id}}
    trace: Dict[str, Any] = {
        "trace_id": trace_id,
        "schema_version": TRACE_SCHEMA_VERSION,
        "graph": GRAPH_NAME,
        "started_at": _now(),
        "finished_at": None,
        "duration_ms": None,
        "status": "running",
        "input": {"question": fingerprint(question or "")},
        "steps": [],
        "node_sequence": [],
        "checkpoints": [],
        "outcome": None,
        "answer": None,
        "error": None,
    }
    started = time.perf_counter()
    last = started
    failure: Optional[BaseException] = None

    try:
        for chunk in graph.stream({"question": question}, config, stream_mode="updates"):
            now = time.perf_counter()
            for node, update in chunk.items():
                trace["steps"].append(
                    {
                        "index": len(trace["steps"]) + 1,
                        "node": node,
                        "duration_ms": round((now - last) * 1000, 2),
                        "output": redact_update(update),
                    }
                )
                trace["node_sequence"].append(node)
            last = now
    except Exception as exc:  # cualquier nodo: Qdrant, el proveedor, un bug
        failure = exc

    final_state = graph.get_state(config).values
    trace["checkpoints"] = _checkpoints(graph, config)
    trace["finished_at"] = _now()
    trace["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    if failure is None:
        trace["status"] = "completed"
        trace["outcome"] = final_state.get("outcome")
        trace["answer"] = final_state.get("answer")
    else:
        failed_node = _failed_node(graph, config)
        trace["status"] = "failed"
        trace["error"] = {"node": failed_node, "type": type(failure).__name__}

    if not keep_checkpoints:
        graph.checkpointer.delete_thread(trace_id)

    trace_path: Optional[Path] = None
    try:
        trace_path = write_trace(trace, trace_dir or get_trace_dir(), file_stem)
    except OSError as exc:
        logger.error("Agent trace %s could not be written: %s", trace_id, type(exc).__name__)

    # Nunca la pregunta: solo el recorrido y el desenlace.
    logger.info(
        "Agent run %s %s: %s -> %s (%.0f ms)",
        trace_id,
        trace["status"],
        " > ".join(trace["node_sequence"]) or "-",
        trace["outcome"] or (trace["error"] or {}).get("node"),
        trace["duration_ms"],
    )

    if failure is not None:
        raise AgentRunError(trace_id, trace["error"]["node"], failure) from failure
    return AgentRunResult(
        answer=trace["answer"],
        outcome=trace["outcome"],
        trace_id=trace_id,
        trace_path=trace_path,
        trace=trace,
    )
