"""Ejecuta el grafo `rfp_intake` para un ticket y persiste su resultado.

Lo llaman la tarea de Celery (services/tasks/rfp_tasks.py) y el CLI de
reproceso (scripts/reprocess_rfp.py). No conoce Celery ni HTTP.

Rutas: el PDF y su Markdown REDACTADO viven en `data/raw/rfp/` con el
`ticket_id` como nombre. `RFP_RAW_DIR` lo cambia (tests).

Handoff a la Parte 2 (contrato en docs/rfp-intake.md): en éxito, el ticket
queda `analisis_completo` con `next_stage = "generando_borrador"` y
`handoff_payload` = ticket_id + metadatos + workstreams por departamento +
resumen + conflictos + ruta del Markdown. La Parte 2 arranca leyendo esa
fila, sin volver a parsear el PDF.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from sqlmodel import Session

from data.pipelines import ROOT_DIR
from data.pipelines.rfp_intake import repository
from data.pipelines.rfp_intake.departments import DEPARTMENTS, country_rules
from data.pipelines.rfp_intake.graph import OUTCOME_COMPLETE, OUTCOME_PHI, compile_rfp_intake_graph
from data.pipelines.rfp_intake.models import (
    DISCARD_NOT_RFP,
    DISCARD_PHI,
    NEXT_STAGE_DRAFTING,
    STATUS_ANALYZING,
    STATUS_COMPLETE,
    STATUS_DISCARDED,
)

logger = logging.getLogger("healthcore.rfp_intake")

HANDOFF_VERSION = 1


def raw_dir() -> Path:
    configured = os.getenv("RFP_RAW_DIR", "").strip()
    return Path(configured) if configured else ROOT_DIR / "data" / "raw" / "rfp"


def pdf_path_for(ticket_id: uuid.UUID) -> Path:
    return raw_dir() / f"{ticket_id}.pdf"


def relative_to_root(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def _resolve(raw_pdf_path: str) -> Path:
    path = Path(raw_pdf_path)
    return path if path.is_absolute() else ROOT_DIR / path


_graph = None


def _default_graph():
    global _graph
    if _graph is None:
        _graph = compile_rfp_intake_graph()
    return _graph


def run_intake(session: Session, ticket_id: uuid.UUID, *, graph: Optional[Any] = None) -> str:
    """Devuelve el estado final. Si el ticket ya no está `analizando` (mensaje
    de Celery repetido tras un acks_late), no hace nada: idempotente."""
    ticket = repository.get_ticket(session, ticket_id)
    if ticket is None:
        raise LookupError(f"Ticket {ticket_id} no existe")
    if ticket.status != STATUS_ANALYZING:
        logger.info("rfp_intake_skipped ticket=%s status=%s", ticket_id, ticket.status)
        return ticket.status

    repository.start_attempt(session, ticket)
    started = time.monotonic()
    state = (graph or _default_graph()).invoke(
        {"ticket_id": str(ticket_id), "pdf_path": str(_resolve(ticket.raw_pdf_path)), "worker_results": []}
    )
    markdown_path = _resolve(ticket.raw_pdf_path).with_suffix(".md")
    markdown_path.write_text(state.get("markdown", ""), encoding="utf-8")  # siempre la versión redactada

    outcome = state["outcome"]
    if outcome == OUTCOME_COMPLETE:
        _save_complete(session, ticket, state, relative_to_root(markdown_path))
    else:
        _save_discarded(session, ticket, state, phi=outcome == OUTCOME_PHI)
    logger.info(
        "rfp_intake_finished ticket=%s outcome=%s duration_ms=%.0f",
        ticket_id, outcome, (time.monotonic() - started) * 1000,
    )
    return ticket.status


def _save_discarded(session: Session, ticket, state: Dict[str, Any], *, phi: bool) -> None:
    classification = state.get("classification") or {}
    repository.save_result(
        session,
        ticket,
        ticket_fields={
            "status": STATUS_DISCARDED,
            "discard_reason": DISCARD_PHI if phi else DISCARD_NOT_RFP,
            "classifier_reason": None if phi else classification.get("reason"),
            # PHI: Claire Whitfield (compliance) es el árbitro fijo de `phi-detected`.
            "requires_compliance_review": phi,
            "phi_categories": state.get("phi_categories", []),
        },
        metadata_fields=None
        if phi
        else {
            "document_kind": classification.get("document_kind"),
            "classifier_confidence": classification.get("confidence"),
        },
        sections=[],
    )


def _save_complete(session: Session, ticket, state: Dict[str, Any], markdown_path: str) -> None:
    metadata = state["metadata"]
    classification = state["classification"]
    synthesis = state["synthesis"]
    readability = state["readability"]
    rules = country_rules(metadata.get("client_country"))
    results = sorted(state["worker_results"], key=lambda r: list(DEPARTMENTS).index(r["department_id"]))

    sections = [
        {
            "department_id": r["department_id"],
            "owner": DEPARTMENTS[r["department_id"]].owner,
            "key_aspects": r["key_aspects"],
            "open_questions": r["open_questions"],
            "excerpt_indexes": r["excerpt_indexes"],
            "unsupported_claims_removed": r["unsupported_claims_removed"],
        }
        for r in results
    ]
    metadata_fields = {
        "document_kind": classification.get("document_kind"),
        "classifier_confidence": classification.get("confidence"),
        "client_name": metadata.get("client_name"),
        "client_country": metadata.get("client_country"),
        "currency": rules["currency"] if rules else None,
        "program_type": metadata.get("program_type", []),
        "covered_population": metadata.get("covered_population"),
        "covered_population_unit": metadata.get("covered_population_unit"),
        "deadline": metadata.get("deadline"),
        "deadline_text": metadata.get("deadline_text"),
        "budget_range": metadata.get("budget_range"),
        "departments_needed": state["departments"],
        "unmapped_departments": state["unmapped_departments"],
        "open_questions": state["open_questions"],
        "summary": synthesis["summary"],
        "who_to_ask": synthesis["who_to_ask"],
        "conflicts": synthesis["conflicts"],
        **{key: readability.get(key) for key in (
            "word_count", "sentence_count", "estimated_tokens", "flesch_kincaid_grade",
            "gunning_fog", "flesch_reading_ease", "readability_note",
        )},
    }
    handoff = build_handoff(ticket, metadata_fields, sections, markdown_path)
    repository.save_result(
        session,
        ticket,
        ticket_fields={
            "status": STATUS_COMPLETE,
            "classifier_reason": classification.get("reason"),
            "handoff_payload": handoff,
            "next_stage": NEXT_STAGE_DRAFTING,
        },
        metadata_fields=metadata_fields,
        sections=sections,
    )


def build_handoff(ticket, metadata: Dict[str, Any], sections: list, markdown_path: str) -> Dict[str, Any]:
    """Contrato de handoff v1 hacia la Parte 2 (docs/rfp-intake.md)."""
    return {
        "handoff_version": HANDOFF_VERSION,
        "ticket_id": str(ticket.ticket_id),
        "rfp_id": ticket.rfp_id,
        "next_stage": NEXT_STAGE_DRAFTING,
        "markdown_path": markdown_path,
        "metadata": {
            key: metadata.get(key)
            for key in (
                "client_name", "client_country", "currency", "program_type", "covered_population",
                "covered_population_unit", "deadline", "budget_range", "departments_needed",
                "unmapped_departments", "open_questions",
            )
        },
        "workstreams": [
            {
                "department_id": s["department_id"],
                "owner": s["owner"],
                "key_aspects": s["key_aspects"],
                "open_questions": s["open_questions"],
                "excerpt_indexes": s["excerpt_indexes"],
            }
            for s in sections
        ],
        "summary": metadata.get("summary"),
        "who_to_ask": metadata.get("who_to_ask"),
        "conflicts": metadata.get("conflicts"),
    }
