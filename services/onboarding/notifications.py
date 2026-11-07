"""Textos de la bandeja de salida (decisión del usuario: sin correos reales).

Plantillas fijas, nunca texto generado por un modelo. A la persona
incorporada se le escribe en su idioma; al equipo, en español (el idioma del
backoffice). Lo único variable es: nombre de la etapa, fecha de vencimiento,
nombre visible de la incorporación (para que el equipo sepa de quién se
trata), nombre de un sistema y textos ya validados (comentario de un rechazo,
duda ya redactada). `render` pasa todo por `sensitive.assert_clean`: si algo
sensible llegara hasta aquí, el aviso no se guarda.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple

from services.onboarding import sensitive
from services.onboarding.catalog import RECIPIENT_HIRE, SYSTEMS, get_stage

STAFF_LANGUAGE = "es"

_HIRE_TEMPLATES: Dict[str, Dict[str, Tuple[str, str]]] = {
    "en": {
        "onboarding_started": ("Welcome to HealthCore", "Your onboarding has started. The People team is confirming your paperwork; your first content stage will open as soon as they finish."),
        "stage_rejected": ("Stage returned: {stage}", "The stage \"{stage}\" was returned to the start by the approver. Comment: {comment}"),
        "reminder": ("Due in 24 hours: {stage}", "The stage \"{stage}\" is due on {due}. You can continue from your onboarding page."),
        "onboarding_completed": ("Onboarding completed", "Your onboarding is complete. Welcome to HealthCore."),
        "access_blocked": ("Access request blocked", "Access to {system} needs the Compliance stage to be approved first. The request was not issued."),
    },
    "es": {
        "onboarding_started": ("Bienvenida a HealthCore", "Tu incorporación ha empezado. El equipo de Personas está confirmando tus trámites; tu primera etapa de contenido se abrirá cuando terminen."),
        "stage_rejected": ("Etapa devuelta: {stage}", "La etapa «{stage}» ha vuelto al inicio por decisión de quien la aprueba. Comentario: {comment}"),
        "reminder": ("Vence en 24 horas: {stage}", "La etapa «{stage}» vence el {due}. Puedes continuar desde tu página de incorporación."),
        "onboarding_completed": ("Incorporación completada", "Has completado tu incorporación. Bienvenida/o a HealthCore."),
        "access_blocked": ("Solicitud de acceso bloqueada", "El acceso a {system} necesita antes la aprobación de la etapa de Cumplimiento. La solicitud no se ha emitido."),
    },
}

_STAFF_TEMPLATES: Dict[str, Tuple[str, str]] = {
    "approval_requested": ("Aprobación pendiente: {stage}", "{hire} está esperando tu decisión en la etapa «{stage}»."),
    "check_failed_twice": ("Dos intentos fallidos: {stage}", "{hire} no ha superado la comprobación de «{stage}» en dos intentos. El agente sigue reforzando el contenido; la etapa no se salta."),
    "escalation": ("Etapa vencida: {stage}", "La etapa «{stage}» de {hire} venció el {due} y sigue pendiente."),
    "compliance_delay": ("Cumplimiento con retraso", "La etapa «{stage}» de {hire} venció el {due}. No escala ni se puede saltar: solo se informa."),
    "question_routed": ("Duda sin respuesta en el material oficial", "{hire} ha preguntado algo que el material oficial no cubre: «{question}»"),
    "security_event": ("Evento de seguridad: acceso bloqueado", "Se bloqueó una solicitud de acceso de {hire} a {system} (sistema con información de pacientes) porque la etapa de Cumplimiento no está aprobada."),
    "onboarding_completed": ("Incorporación cerrada", "La incorporación de {hire} se ha completado."),
}


def render(kind: str, recipient_role: str, *, hire_language: str, hire_name: str, params: Mapping[str, Any]) -> Tuple[str, str]:
    """(asunto, cuerpo) del aviso."""
    language = hire_language if recipient_role == RECIPIENT_HIRE else STAFF_LANGUAGE
    if recipient_role == RECIPIENT_HIRE:
        template = _HIRE_TEMPLATES.get(language, _HIRE_TEMPLATES["en"]).get(kind)
    else:
        template = _STAFF_TEMPLATES.get(kind)
    if template is None:
        raise KeyError(f"No hay plantilla para {kind!r} → {recipient_role!r}")
    stage_number = params.get("stage_number")
    values = {
        "stage": get_stage(int(stage_number)).title(language) if stage_number else "",
        "hire": hire_name,
        "due": params.get("due", ""),
        "comment": params.get("comment", ""),
        "question": params.get("question", ""),
        "system": SYSTEMS[params["system"]].name if params.get("system") in SYSTEMS else "",
    }
    subject, body = (part.format(**values) for part in template)
    sensitive.assert_clean([subject, body])
    return subject, body
