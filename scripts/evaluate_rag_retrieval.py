"""Evalúa la recuperación de la base de conocimiento RAG (Recall@3 y min_score).

Uso (desde la raíz del repo, con el venv de la API, Qdrant levantado y la
colección ya indexada con scripts/index_knowledge_base.py):

    services/api/.venv/bin/python scripts/evaluate_rag_retrieval.py
    services/api/.venv/bin/python scripts/evaluate_rag_retrieval.py --min-score 0.45

Qué mide, con data/eval/test-queries.json:
1. Recall@3 sin umbral: ¿el chunk correcto sale entre los 3 primeros? Mide
   solo la calidad del orden (embeddings + chunking).
2. Recall@3 con el umbral: lo mismo tras aplicar min_score. Si baja respecto
   a (1), el umbral está descartando contexto bueno.
3. Separación de puntuaciones: la puntuación del chunk correcto de cada
   pregunta frente a la mejor puntuación de las preguntas fuera de tema. Un
   buen min_score queda entre ambas.

Escribe data/eval/rag_retrieval_evaluation.json. Ni la pregunta ni los
chunks contienen datos de pacientes (CONTEXT, secciones 5 y 6).
Códigos de salida: 0 Recall@3 con umbral ≥ objetivo; 1 por debajo o error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data.pipelines.rag import get_min_score, retrieve  # noqa: E402

QUERIES_PATH = ROOT_DIR / "data" / "eval" / "test-queries.json"
OUTPUT_PATH = ROOT_DIR / "data" / "eval" / "rag_retrieval_evaluation.json"
CANDIDATES = 5  # se piden 5 para ver también si el correcto quedó 4.º o 5.º


def rank_of(expected: Dict[str, Any], results: List[Dict[str, Any]]) -> Optional[int]:
    """Posición (1-based) del chunk esperado en los resultados, o None."""
    for position, item in enumerate(results, start=1):
        if item.get("source_document") == expected["source_document"] and item.get("chunk_index") == expected["chunk_index"]:
            return position
    return None


def evaluate(min_score: float) -> Dict[str, Any]:
    spec = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    at = int(spec["recall_at"])
    rows = []
    for item in spec["queries"]:
        results = retrieve(item["question"], k=CANDIDATES, min_score=0.0)
        rank = rank_of(item["expected"], results)
        expected_score = results[rank - 1]["score"] if rank else None
        rows.append(
            {
                "id": item["id"],
                "expected": f'{item["expected"]["source_document"]}#{item["expected"]["chunk_index"]}',
                "rank": rank,
                "expected_score": round(expected_score, 4) if expected_score is not None else None,
                "hit_without_threshold": rank is not None and rank <= at,
                "hit_with_threshold": rank is not None and rank <= at and expected_score >= min_score,
                "top": [f'{r["source_document"]}#{r["chunk_index"]}:{r["score"]:.3f}' for r in results[:at]],
            }
        )
    negatives = []
    for item in spec.get("out_of_scope", []):
        results = retrieve(item["question"], k=1, min_score=0.0)
        top = results[0] if results else None
        negatives.append(
            {
                "id": item["id"],
                "top_score": round(top["score"], 4) if top else None,
                "passes_threshold": bool(top and top["score"] >= min_score),
            }
        )

    total = len(rows)
    positive_scores = [r["expected_score"] for r in rows if r["expected_score"] is not None]
    negative_scores = [n["top_score"] for n in negatives if n["top_score"] is not None]
    return {
        "collection": spec["collection"],
        "min_score": min_score,
        "recall_at": at,
        "recall_target": spec["recall_target"],
        "questions": total,
        "recall_without_threshold": round(sum(r["hit_without_threshold"] for r in rows) / total, 4),
        "recall_with_threshold": round(sum(r["hit_with_threshold"] for r in rows) / total, 4),
        "lowest_correct_chunk_score": min(positive_scores) if positive_scores else None,
        "highest_out_of_scope_score": max(negative_scores) if negative_scores else None,
        "out_of_scope_passing_threshold": sum(n["passes_threshold"] for n in negatives),
        "queries": rows,
        "out_of_scope": negatives,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-score", type=float, default=None, help="umbral a evaluar (por defecto, el de query())")
    args = parser.parse_args()
    min_score = args.min_score if args.min_score is not None else get_min_score()

    try:
        report = evaluate(min_score)
        OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - cualquier fallo es un error de CLI
        print(f"Error al evaluar la recuperación: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    for row in report["queries"]:
        mark = "OK " if row["hit_with_threshold"] else "---"
        print(f'{mark} {row["id"]:6} esperado {row["expected"]:26} rank={row["rank"]} score={row["expected_score"]}  top3={row["top"]}')
    for row in report["out_of_scope"]:
        print(f'    {row["id"]:6} fuera de tema, mejor score={row["top_score"]} pasa umbral={row["passes_threshold"]}')
    print(
        f'\nRecall@{report["recall_at"]}: {report["recall_without_threshold"]:.0%} sin umbral · '
        f'{report["recall_with_threshold"]:.0%} con min_score={min_score} (objetivo {report["recall_target"]:.0%})'
    )
    print(
        f'Score más bajo de un chunk correcto: {report["lowest_correct_chunk_score"]} · '
        f'score más alto fuera de tema: {report["highest_out_of_scope_score"]}'
    )
    print(f"Informe: {OUTPUT_PATH}")
    return 0 if report["recall_with_threshold"] >= report["recall_target"] else 1


if __name__ == "__main__":
    sys.exit(main())
