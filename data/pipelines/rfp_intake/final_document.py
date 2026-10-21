"""Ultimate document synthesizer (Hito 9, Parte 3): documento final de la propuesta.

Consolida las secciones APROBADAS en el FinalDocument del BRIEFING §2.3
(`ticket_id`, `sections`, `currency`, `generated_at`) y en un Markdown listo
para entregar a Revenue Cycle.

Sin modelo, a propósito: cada sección ya la firmó su responsable, y un LLM
que "pulamos el conjunto" cambiaría texto aprobado (o metería una cifra) sin
que nadie lo vuelva a revisar. El synthesizer solo ordena, encabeza y añade
la tabla de aprobaciones.

Se niega a generar (FinalDocumentError) si falta una aprobación, si no está
Compliance (BRIEFING §5: siempre obligatoria) o si un texto contiene PHI
(trigger `phi-detected`: "el synthesizer no debe correr"). El nodo de
arbitraje ya lo impide antes; esto es la segunda capa.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from data.pipelines.rfp_intake.decisions import contains_patient_data
from data.pipelines.rfp_intake.departments import COMPLIANCE, DEPARTMENT_IDS, DEPARTMENTS, country_rules
from data.pipelines.rfp_intake.generators import GENERATORS

COUNTRY_NAMES = {"US": "Estados Unidos", "UK": "Reino Unido"}
PROGRAM_LABELS = {
    "occupational_health": "salud ocupacional",
    "corporate_wellness": "bienestar corporativo",
    "referral_network": "red de referidos",
    "other": "otro",
}
POPULATION_UNITS = {"employees": "empleados", "students": "estudiantes", "members": "miembros"}


class FinalDocumentError(RuntimeError):
    """El documento final no puede generarse con este estado."""


def _section_body(department_id: str, draft: str) -> str:
    """Los generadores suelen abrir con el título como subtítulo; el
    documento ya lo pone como encabezado."""
    title = GENERATORS[department_id].section_title
    lines = draft.strip().splitlines()
    if lines and lines[0].strip().rstrip(".:").lower() == title.lower():
        lines = lines[1:]
    return "\n".join(lines).strip()


def _population(context: Dict[str, Any]) -> str:
    count = context.get("covered_population")
    if count is None:
        return "por confirmar"
    unit = POPULATION_UNITS.get(context.get("covered_population_unit") or "", context.get("covered_population_unit") or "")
    return f"{count:,} {unit}".replace(",", ".").strip()


def build_final_document(context: Dict[str, Any], outcomes: Dict[str, Dict[str, Any]], *, generated_at: datetime) -> Dict[str, Any]:
    departments = [d for d in DEPARTMENT_IDS if d in outcomes]
    if COMPLIANCE not in departments:
        raise FinalDocumentError("Falta la sección de Compliance: su aprobación es obligatoria.")
    not_approved = [d for d in departments if outcomes[d].get("status") != "approved"]
    if not_approved:
        raise FinalDocumentError(f"Faltan aprobaciones: {', '.join(not_approved)}.")
    with_phi = [d for d in departments if contains_patient_data(outcomes[d].get("draft") or "")]
    if with_phi:
        raise FinalDocumentError(f"phi-detected en {', '.join(with_phi)}: el documento no se genera.")

    rules = country_rules(context.get("client_country")) or {}
    currency = rules.get("currency") or context.get("currency")
    sections: List[Dict[str, Any]] = []
    approvals: List[Dict[str, Any]] = []
    for department_id in departments:
        outcome = outcomes[department_id]
        department = DEPARTMENTS[department_id]
        sections.append({
            "department_id": department_id,
            "department": department.name,
            "title": GENERATORS[department_id].section_title,
            "content": _section_body(department_id, outcome["draft"]),
            "approver": department.owner,
        })
        approvals.append({
            "department_id": department_id,
            "department": department.name,
            "approver": department.owner,
            "decided_by": outcome.get("decided_by"),
            "approved_at": outcome.get("decided_at"),
            "revision_rounds": outcome.get("revision_round", 0),
        })

    programs = ", ".join(PROGRAM_LABELS.get(p, p) for p in context.get("program_type") or []) or "por confirmar"
    country = context.get("client_country")
    lines = [
        f"# Propuesta económica para {context.get('client_name') or 'el cliente'}",
        "",
        f"- **Referencia:** {context.get('rfp_id')}",
        f"- **Programa:** {programs}",
        f"- **País del cliente:** {COUNTRY_NAMES.get(country or '', country or 'por confirmar')}",
        f"- **Moneda de la cotización:** {currency or 'por confirmar'}",
        f"- **Protección de datos:** {rules.get('regulation', 'por confirmar')} — cláusula {rules.get('data_agreement', 'por confirmar')}",
        f"- **Población cubierta:** {_population(context)}",
        "",
    ]
    for number, section in enumerate(sections, start=1):
        lines += [f"## {number}. {section['title']}", "", f"_{section['department']} · aprobado por {section['approver']}_", "", section["content"], ""]
    lines += [
        "## Aprobaciones",
        "",
        "| Departamento | Responsable | Fecha de aprobación (UTC) | Rondas de cambios |",
        "|---|---|---|---|",
    ]
    for approval in approvals:
        lines.append(
            f"| {approval['department']} | {approval['approver']} | {(approval['approved_at'] or '')[:16].replace('T', ' ')} "
            f"| {approval['revision_rounds']} |"
        )
    lines += [
        "",
        f"_Documento generado automáticamente el {generated_at:%Y-%m-%d %H:%M} UTC al completarse todas las "
        "aprobaciones, incluida la obligatoria de Compliance and Data Governance._",
    ]
    return {
        "sections": sections,
        "currency": currency,
        "client_country": country,
        "approvals": approvals,
        "content_markdown": "\n".join(lines) + "\n",
        "generated_at": generated_at.isoformat(),
    }
