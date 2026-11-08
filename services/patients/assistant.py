"""Asistente de documentación clínica (proceso automatizado, sin modelo).

Prepara un borrador de nota de seguimiento a partir de los campos
estructurados de la ficha. Es determinista a propósito: lo que importa en
esta clase es que su acción sobre datos de un paciente quede registrada como
la de un PROCESO (`actor_type=process`, `actor_id=clinical_documentation_assistant`)
que actuó a petición de una persona (`on_behalf_of`), no como si la persona
hubiera escrito la nota.
"""

from __future__ import annotations

from services.patients.models import PatientRecord

DRAFT_TEMPLATE = (
    "Borrador generado por el asistente de documentación (revisar antes de firmar).\n"
    "Motivo de seguimiento: {diagnosis}.\n"
    "Resumen de notas previas: {summary}\n"
    "Plan propuesto: revisar evolución en la próxima cita y confirmar adherencia al tratamiento."
)


def draft_follow_up_note(patient: PatientRecord) -> str:
    notes = " ".join(patient.clinical_notes.split())
    summary = notes if len(notes) <= 240 else notes[:237].rstrip() + "…"
    return DRAFT_TEMPLATE.format(diagnosis=patient.diagnosis, summary=summary)
