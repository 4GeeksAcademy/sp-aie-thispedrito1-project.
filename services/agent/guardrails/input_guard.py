"""Guardarraíl de ENTRADA (Ticket #SEC-114): decide qué hacer con el mensaje
del usuario ANTES de que llegue al modelo.

Tabla de decisión del README, aplicada en este orden (la primera que salta
gana; el orden importa: un jailbreak con datos de paciente es un jailbreak):

1. Cambio de instrucciones / jailbreak  → BLOCK  (seguridad)
2. Caso de paciente identificable        → BLOCK  (contenido, PHI)
3. Detalles de una brecha activa         → BLOCK  (seguridad, exfiltración)
4. Tarea personal ajena al negocio       → BLOCK  (contenido, alcance)
5. Charla casual o cultura general       → REDIRECT (contenido, alcance)
6. Todo lo demás                          → ALLOW  (grafo: RAG / tools / memoria)

Un BLOCK nunca llega al modelo: la respuesta es un texto fijo. Así un
jailbreak no depende de que el modelo "se resista", y la misma entrada se
rechaza igual la 1.ª y la 20.ª vez, se reformule como se reformule mientras
caiga en alguna regla. El system prompt (`prompt.py`) es la segunda capa para
lo que se escape de aquí.

Extracción gradual de una brecha (CONTEXT §4, caso 4): el agente no tiene
historial de conversación, así que "¿cuántos registros?" suelto no se puede
relacionar con la pregunta anterior. `BreachWindow` recuerda, por usuario y
durante `BREACH_WINDOW`, que ya intentó preguntar por una brecha activa; en
esa ventana las preguntas de detalle sueltas también se bloquean. Vive en
memoria del proceso (decisión del usuario): no guarda texto, solo una hora.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, FrozenSet, Optional, Tuple

from services.agent.guardrails import patterns
from services.agent.guardrails.monitor import BLOCK, CONTENT, REDIRECT, SECURITY, GuardEvent
from services.agent.memory import phi_guard

ALLOW = "allow"

BREACH_WINDOW = timedelta(minutes=15)

# --- Mensajes al usuario (fijos: no los redacta el modelo) ----------------------

SCOPE_REMINDER = (
    "Puedo ayudarte con las políticas, procedimientos y protocolos de HealthCore bajo HIPAA (EE. UU.) "
    "y UK GDPR (R. U.): citas y cancelaciones, seguros, pacientes nuevos, referencias, consentimientos, "
    "y el estado de incidencias o del inventario."
)

JAILBREAK_REFUSAL = (
    "No puedo hacer eso. Mis instrucciones las fija HealthCore y no cambian durante la conversación, "
    "las pida quien las pida y se formulen como se formulen. Sigo siendo el asistente de políticas y "
    "compliance de HealthCore. " + SCOPE_REMINDER
)

PERSONAL_REFUSAL = (
    "Eso queda fuera de lo que hago: no soy un asistente personal (correos o textos personales, "
    "escritura creativa, código, tareas de estudio). " + SCOPE_REMINDER + " ¿Te ayudo con algo de eso?"
)

THERAPY_REFUSAL = (
    "Siento que estés pasando por un momento difícil, pero no puedo hacer ese papel. Si lo necesitas, "
    "habla con tu responsable o con Recursos Humanos; si es una urgencia, con los servicios de "
    "emergencia. " + SCOPE_REMINDER
)

PHI_CASE_REFUSAL = (
    "No puedo tratar casos de pacientes concretos con datos que permitan identificarlos ({reasons}). "
    "Por HIPAA (EE. UU.) y UK GDPR (R. U.), reformula la pregunta sin nombre, edad, fecha de "
    "nacimiento, número de historia clínica, diagnóstico ligado a la persona ni sede; por ejemplo: "
    "«¿Qué política aplica cuando un paciente con Medicaid cancela con menos de 24 horas?». "
    "Si el caso hay que documentarlo, hazlo en el sistema clínico."
)

BREACH_REFUSAL = (
    "No puedo dar detalles de brechas de seguridad activas o en investigación (fechas, alcance, "
    "registros, sedes o sistemas afectados): esa información la gestiona solo el equipo de Compliance "
    "hasta que la investigación se cierra formalmente. Si tienes información sobre un posible "
    "incidente, repórtalo por el canal interno de Compliance. Sí puedo explicarte el procedimiento "
    "general de notificación de brechas bajo HIPAA y UK GDPR."
)

# --- Señales de "caso de paciente" ------------------------------------------------

# "Tengo un paciente...", "mi paciente", "este paciente", "I have a patient".
_SPECIFIC_PATIENT = re.compile(
    r"\b(tengo|atiendo|atendi|trato|vino|viene|ingreso|ingresaron|llamo)\s+(a\s+)?(un|una|el|la|este|esta)?\s*paciente\b"
    r"|\b(mi|este|esta|ese|esa)\s+paciente\b|\bun paciente (mio|nuestro)\b|\bpaciente (que tengo|que vino|que atendi)\b"
    r"|\b(i have|i saw|i am treating) a patient\b|\bmy patient\b|\bpatient of mine\b"
)
# "paciente, John," / "paciente llamado John" (phi_guard solo ve "paciente John").
_NAME_AFTER_PATIENT = re.compile(
    r"\b[Pp]acientes?\b[\s,:;-]+(?:llamad[oa]\s+|de nombre\s+|que se llama\s+)?"
    r"(?!Medicare|Medicaid|NHS|Texas|Florida|Georgia|Austin|Houston|Dallas|Miami|Orlando|Tampa|Atlanta|Savannah|Londres|London|Manchester)"
    r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ'\-]+"
)
# Sedes y ciudades de la red: con otros datos, una ubicación reidentifica.
_LOCATION = re.compile(
    r"\b(austin|houston|dallas|miami|orlando|tampa|atlanta|savannah|londres|london|manchester)\b"
)

# Identificadores directos: uno solo ya identifica a una persona.
DIRECT_IDENTIFIERS: FrozenSet[str] = frozenset(
    {"patient_name", "medical_record_number", "nhs_number", "ssn", "insurance_number", "email", "phone", "uk_postcode"}
)


@dataclass(frozen=True)
class PatientSignals:
    """Lo que se sabe del mensaje, ya calculado para `is_identifiable_patient_case`."""

    categories: FrozenSet[str]  # categorías de phi_guard (+ "patient_name" por la coma)
    specific_patient: bool  # habla de UN paciente concreto ("tengo un paciente...")
    location: bool  # menciona una sede o ciudad de la red


def is_identifiable_patient_case(signals: PatientSignals) -> bool:
    """¿El mensaje describe a un paciente de forma que se le pueda reidentificar?

    CONTEXT §2: prohibido discutir un caso de paciente, real o hipotético,
    con identificadores o cuasi-identificadores (nombre, fecha de nacimiento,
    número de historia clínica, o una combinación de edad + diagnóstico +
    ubicación que permita reidentificarlo).
    """
    # Un identificador directo basta solo.
    if signals.categories & DIRECT_IDENTIFIERS:
        return True
    has_age = "date_of_birth" in signals.categories
    has_clinical = "clinical_content" in signals.categories
    # Hablar de UN paciente concreto + cualquier cuasi-identificador lo señala.
    if signals.specific_patient and (has_age or has_clinical):
        return True
    # Sin "mi paciente", hacen falta dos cuasi-identificadores juntos
    # (edad + diagnóstico, edad + sede, diagnóstico + sede): combinados
    # permiten reidentificar; sueltos son preguntas generales.
    return has_age + has_clinical + signals.location >= 2


def patient_signals(raw: str, normalized: str) -> PatientSignals:
    categories = set(phi_guard.scan(raw).categories)
    if _NAME_AFTER_PATIENT.search(raw):
        categories.add("patient_name")
    return PatientSignals(
        categories=frozenset(categories),
        specific_patient=bool(_SPECIFIC_PATIENT.search(normalized)),
        location=bool(_LOCATION.search(normalized)),
    )


# --- Ventana de extracción gradual de brechas -----------------------------------


@dataclass
class BreachWindow:
    """Usuario → hasta cuándo sus preguntas de detalle se tratan como parte de
    un intento de extraer una brecha activa."""

    ttl: timedelta = BREACH_WINDOW
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    _until: Dict[str, datetime] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def open(self, user_id: Optional[str]) -> None:
        if not user_id:
            return
        with self._lock:
            self._until[user_id] = self.clock() + self.ttl

    def is_open(self, user_id: Optional[str]) -> bool:
        if not user_id:
            return False
        now = self.clock()
        with self._lock:
            until = self._until.get(user_id)
            if until is not None and until <= now:
                del self._until[user_id]
                return False
            return until is not None

    def clear(self) -> None:
        with self._lock:
            self._until.clear()


BREACH_WINDOWS = BreachWindow()


# --- Veredicto ----------------------------------------------------------------------


@dataclass(frozen=True)
class InputVerdict:
    action: str  # ALLOW / BLOCK / REDIRECT
    guard: Optional[str] = None
    failure_type: Optional[str] = None
    reason: Optional[str] = None
    message: Optional[str] = None  # respuesta fija de un BLOCK
    categories: Tuple[str, ...] = ()  # categorías de PHI (para la auditoría de memoria)
    regulation_topic: bool = False  # ALLOW sobre regulación: admite contexto general

    @property
    def event(self) -> Optional[GuardEvent]:
        if self.action == ALLOW:
            return None
        return GuardEvent(self.guard or "", self.action, self.failure_type or "", self.reason or "")


def _block(guard: str, failure_type: str, reason: str, message: str, **extra) -> InputVerdict:
    return InputVerdict(BLOCK, guard, failure_type, reason, message, **extra)


def check_input(
    message: str,
    *,
    user_id: Optional[str] = None,
    breach_windows: Optional[BreachWindow] = None,
) -> InputVerdict:
    windows = breach_windows if breach_windows is not None else BREACH_WINDOWS
    raw = message or ""
    text = patterns.normalize(raw)

    reason = patterns.first_match(patterns.JAILBREAK_RULES, text)
    if reason:
        return _block("input_jailbreak", SECURITY, reason, JAILBREAK_REFUSAL)

    signals = patient_signals(raw, text)
    if is_identifiable_patient_case(signals):
        ordered = tuple(c for c in phi_guard.PHI_CATEGORIES if c in signals.categories)
        reasons = phi_guard.plain_reasons(ordered) or "datos que describen a un paciente concreto"
        if signals.location:
            reasons += ", la sede o la ciudad"
        return _block(
            "input_patient_phi",
            CONTENT,
            "identifiable_patient_case",
            PHI_CASE_REFUSAL.format(reasons=reasons),
            categories=ordered,
        )

    breach = bool(patterns.BREACH_TOPIC.search(text))
    probe = bool(patterns.BREACH_PROBE.search(text))
    if breach and (patterns.BREACH_ACTIVE.search(text) or probe):
        windows.open(user_id)
        return _block("input_breach_extraction", SECURITY, "active_breach_details", BREACH_REFUSAL)
    if probe and not breach and windows.is_open(user_id):
        windows.open(user_id)  # cada intento renueva la ventana
        return _block("input_breach_extraction", SECURITY, "gradual_breach_probe", BREACH_REFUSAL)

    reason = patterns.first_match(patterns.PERSONAL_HARD_RULES, text)
    anchored = bool(patterns.DOMAIN_ANCHOR.search(text))
    if not reason and not anchored:
        reason = patterns.first_match(patterns.PERSONAL_SOFT_RULES, text)
    if reason:
        refusal = THERAPY_REFUSAL if reason == "therapy" else PERSONAL_REFUSAL
        return _block("input_personal_use", CONTENT, reason, refusal)

    if not anchored:
        reason = patterns.first_match(patterns.CASUAL_RULES, text)
        if reason:
            return InputVerdict(REDIRECT, "input_small_talk", CONTENT, reason)

    return InputVerdict(ALLOW, regulation_topic=bool(patterns.REGULATION_TOPIC.search(text)))
