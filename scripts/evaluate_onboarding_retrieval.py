"""Calibra el umbral de similitud del orquestador de incorporaciones.

    docker compose up -d qdrant
    services/api/.venv/bin/python scripts/index_onboarding_docs.py
    services/api/.venv/bin/python scripts/evaluate_onboarding_retrieval.py

Para cada caso de data/eval/onboarding-retrieval-cases.json busca en el
material de ESE perfil y mide:
- cubiertas: puntuación del mejor fragmento del documento esperado y si es el
  primero (Recall@1) o está entre los 4 primeros (Recall@4);
- no cubiertas: la mejor puntuación de todo el material (debe quedar por
  debajo del umbral).
El umbral bueno queda entre la peor cubierta y la mejor no cubierta. Escribe
el resultado en data/eval/onboarding_retrieval_evaluation.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
for path in (str(ROOT_DIR), str(ROOT_DIR / "services" / "api")):
    if path not in sys.path:
        sys.path.insert(0, path)

CASES = ROOT_DIR / "data" / "eval" / "onboarding-retrieval-cases.json"
OUTPUT = ROOT_DIR / "data" / "eval" / "onboarding_retrieval_evaluation.json"


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / "services" / "api" / ".env")
    from data.pipelines import onboarding_docs
    from data.pipelines.onboarding_docs import HireProfile

    profiles = {
        "en-hipaa-digital": HireProfile("en", "hipaa", "digital_engineer", "digital"),
        "en-uk-revenue": HireProfile("en", "uk_gdpr", "revenue_cycle_analyst", "revenue_cycle"),
        "es-hipaa-digital": HireProfile("es", "hipaa", "digital_engineer", "digital"),
    }
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    threshold = onboarding_docs.DEFAULT_MIN_SCORE
    covered, uncovered = [], []
    for case in cases["covered"]:
        hits = onboarding_docs.retrieve(case["question"], profiles[case["profile"]], min_score=-1.0, k=4)
        docs = [h["doc_id"] for h in hits]
        best = max((h["score"] for h in hits if h["doc_id"] == case["expected_doc"]), default=None)
        covered.append({**case, "top_citation": hits[0]["citation"] if hits else None, "expected_score": best,
                        "recall_at_1": bool(docs) and docs[0] == case["expected_doc"], "recall_at_4": case["expected_doc"] in docs})
    for case in cases["uncovered"]:
        hits = onboarding_docs.retrieve(case["question"], profiles[case["profile"]], min_score=-1.0, k=1)
        uncovered.append({**case, "top_citation": hits[0]["citation"] if hits else None, "top_score": hits[0]["score"] if hits else None})

    worst_covered = min(c["expected_score"] for c in covered if c["expected_score"] is not None)
    best_uncovered = max(u["top_score"] for u in uncovered if u["top_score"] is not None)
    summary = {
        "threshold": threshold,
        "recall_at_1": sum(c["recall_at_1"] for c in covered) / len(covered),
        "recall_at_4": sum(c["recall_at_4"] for c in covered) / len(covered),
        "worst_covered_score": round(worst_covered, 4),
        "best_uncovered_score": round(best_uncovered, 4),
        "covered_above_threshold": sum((c["expected_score"] or 0) >= threshold for c in covered),
        "uncovered_below_threshold": sum((u["top_score"] or 0) < threshold for u in uncovered),
        "separable": worst_covered > best_uncovered,
    }
    OUTPUT.write_text(json.dumps({"summary": summary, "covered": covered, "uncovered": uncovered}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for c in covered:
        print(f"  cubierta  {c['expected_score'] or 0:.3f}  {'OK ' if c['recall_at_1'] else 'R@4' if c['recall_at_4'] else 'NO '}  {c['question']}")
    for u in uncovered:
        print(f"  no cub.   {u['top_score'] or 0:.3f}       {u['question']}  → {u['top_citation']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
