"""Reglas deterministas del harness (Ticket #SEC-114): qué parece un jailbreak,
una tarea personal, charla casual o un intento de sacar datos de una brecha.

Todas se evalúan sobre el texto NORMALIZADO (`normalize`): minúsculas, sin
tildes, sin caracteres invisibles, con el leetspeak más común deshecho y los
espacios colapsados. Así "IGNÓRA tus instrucciones", "ign0ra   tus
instrucciones" o "i​gnora" caen en la misma regla que la frase limpia:
reformular la ortografía no basta para pasar el filtro.

La detección de PHI NO vive aquí: es `services/agent/memory/phi_guard.py`,
que trabaja sobre el texto original (las mayúsculas de un nombre propio o el
formato de un NHS number se perderían al normalizar).

Criterio: expresiones regulares, no el modelo. La misma entrada da siempre el
mismo veredicto, se puede testear sin red y no depende del proveedor del LLM.
El modelo es la segunda capa (system prompt), no la única.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Pattern, Tuple

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
# Solo dígitos o símbolos pegados a letras: "1gn0r4" → "ignora", pero "45 años"
# y "72 horas" no cambian.
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_LEET_WORD = re.compile(r"\b(?=\w*[a-z])(?=\w*[0-9@$])[\w@$]+\b")


def normalize(text: str) -> str:
    text = (text or "").translate(_ZERO_WIDTH)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = _LEET_WORD.sub(lambda match: match.group(0).translate(_LEET), text)
    text = re.sub(r"[¿¡\"'`´“”«»]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _rx(pattern: str) -> Pattern[str]:
    return re.compile(pattern)


# --- Seguridad: cambio de instrucciones / jailbreak ----------------------------
# (motivo, patrón). El motivo va a los logs y al resumen; el texto nunca.
JAILBREAK_RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    (
        "ignore_instructions",
        _rx(
            r"\b(ignora\w*|ignore|olvida\w*|forget|omite|saltate|descarta|disregard|override|anula|desactiva|salta)\b"
            r".{0,40}\b(instrucciones|instruccion|reglas|normas|indicaciones|restricciones|politicas|directrices"
            r"|guardarrailes|filtros|prompt|instructions|rules|guidelines|guardrails)\b"
        ),
    ),
    (
        "no_rules_persona",
        _rx(
            r"\b(asistente|modo|ia|bot|chatbot|assistant|mode|ai|version)\s+(sin|without|with no|libre de)\s+"
            r"(ninguna\s+)?(reglas|restricciones|filtros|limites|censura|normas|rules|restrictions|filters|limits|censorship)"
        ),
    ),
    (
        "act_without_rules",
        _rx(
            r"\b(actua|actuar|act|comportate|pretend|finge|fingir|haz como|imagina|imagine|supon)\b.{0,50}"
            r"\b(sin (reglas|restricciones|limites|filtros|normas)|without (rules|restrictions|limits|filters)"
            r"|si no tuvieras|as if you (had|have) no|como si no)\b"
        ),
    ),
    (
        "role_reassignment",
        _rx(
            r"\b(a partir de ahora|desde ahora|de ahora en adelante|from now on)\b.{0,20}"
            r"\b(eres|seras|vas a ser|actuaras|responderas|you are|you re|you will be|you will)\b"
            r"|\b(ahora eres|ahora seras|now you are|you are now)\s+(un|una|el|la|a|an|my|mi)\b"
        ),
    ),
    (
        "forget_identity",
        _rx(
            r"\b(olvida\w*|forget|deja de|stop being|ya no eres|no eres)\b.{0,40}"
            r"\b(healthcore|la empresa|la compania|trabajas|eres un asistente|compliance|tu rol|tu papel|your role"
            r"|you work|asistente de)\b"
        ),
    ),
    (
        "no_rules_claim",
        _rx(
            r"\b(no tienes|ya no tienes|you have no|you don t have|you do not have|tus reglas ya no|no hay)\b.{0,20}"
            r"\b(reglas|restricciones|limites|rules|restrictions|limits)\b"
        ),
    ),
    (
        "developer_mode",
        _rx(
            r"\b(modo|mode)\s+(desarrollador|developer|dios|god|dan|sin censura|jailbreak|root|admin|administrador|debug|mantenimiento)\b"
            r"|\bjailbreak\w*\b|\bdo anything now\b|\bdan mode\b"
        ),
    ),
    (
        "prompt_extraction",
        _rx(
            r"\b(revela\w*|muestra\w*|dime|repite|imprime|ensena\w*|copia|pega|cual es|cuales son|reveal|show|print|repeat|tell me|what is|what are)\b"
            r".{0,30}\b(system prompt|prompt del sistema|tu prompt|el prompt|tus instrucciones|instrucciones (iniciales|del sistema|ocultas|internas|originales|previas)"
            r"|tus reglas internas|mensaje del sistema|your (instructions|prompt|rules|system message)|initial instructions)\b"
        ),
    ),
    (
        "new_instructions",
        _rx(
            r"\b(nuevas instrucciones|nuevas reglas|new instructions|new rules|instrucciones actualizadas|updated instructions"
            r"|tus instrucciones (ahora )?son|your instructions are now)\b"
        ),
    ),
    (
        "role_marker_injection",
        _rx(
            r"(^|\s)(system|sistema|developer|assistant)\s*:\s"
            r"|</?\s*(system|sistema|instrucciones|fuente_externa|mensaje_usuario)\b"
            r"|\[/?(inst|system)\]|<\|im_(start|end)\|>"
        ),
    ),
)

# --- Contenido: uso como chatbot personal --------------------------------------
# "Duras": personales siempre, aunque mencionen la clínica ("un poema sobre la
# clínica" sigue sin ser trabajo de compliance).
PERSONAL_HARD_RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    (
        "creative_writing",
        _rx(r"\b(poema|poesia|cancion|letra de una|cuento|relato|novela|ensayo|redaccion escolar|poem|song|essay|short story|lyrics)\b"),
    ),
    (
        "homework",
        _rx(
            r"\b(tarea|deberes|trabajo|examen|tfg|tfm|tesis|practica)\b.{0,30}"
            r"\b(universidad|uni|clase|colegio|master|curso|facultad|escuela|instituto)\b|\bhomework\b|\bmy (thesis|assignment)\b"
        ),
    ),
    (
        "code_request",
        _rx(
            r"\b(escribe\w*|hazme|haz|genera\w*|crea\w*|programa\w*|corrige\w*|arregla\w*|depura\w*|ayuda\w* con|write|create|fix|debug|generate)\b"
            r".{0,40}\b(codigo|script|programa en|funcion en|clase en|query sql|consulta sql|en python|en javascript|en java"
            r"|html|css|regex|code|python|javascript|typescript|sql)\b"
        ),
    ),
    ("salary", _rx(r"\b(aumento de sueldo|aumento salarial|subida de sueldo|subida salarial|pay raise|salary raise|a raise)\b")),
    (
        "therapy",
        _rx(
            r"\b(actua como|haz de|se mi|sé mi|be my|act as my|quiero que seas mi)\s+(mi\s+)?"
            r"(terapeuta|psicolog[oa]|novi[oa]|pareja|amig[oa]|coach|therapist|girlfriend|boyfriend|friend)\b"
        ),
    ),
    (
        "personal_errand",
        _rx(
            r"\b(receta de cocina|receta para (cocinar|hacer)|plan de viaje|mis vacaciones|itinerario|horoscopo|apuestas"
            r"|mi cv|mi curriculum|carta de presentacion|cover letter|my resume|lista de la compra)\b"
        ),
    ),
)

# "Blandas": escribir a alguien de tu entorno es personal salvo que el mensaje
# hable de un asunto de compliance o de la operación de HealthCore.
PERSONAL_SOFT_RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    (
        "personal_correspondence",
        _rx(
            r"\b(correo|email|e-mail|mail|mensaje|carta|whatsapp|letter|message)\b.{0,40}"
            r"\b(mi jef[ea]|mi pareja|mi novi[oa]|mi espos[oa]|mi madre|mi padre|mi amig[oa]|mi casero|personal"
            r"|my boss|my (wife|husband|partner|friend|landlord))\b"
        ),
    ),
)

# --- Contenido: charla casual o cultura general (permitida con reconducción) ---
CASUAL_RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    (
        "greeting",
        _rx(
            r"^(hola|buenas|buenos dias|buenas tardes|buenas noches|hey|hi|hello|que tal|como estas|como va"
            r"|gracias|muchas gracias|thanks|thank you|adios|hasta luego|hasta manana|bye)\b[\s\w,!.?]{0,30}$"
        ),
    ),
    (
        "trivia",
        _rx(
            r"\b(que hora es|que dia es hoy|que tiempo hace|el tiempo en|va a llover|capital de|quien gano|quien es el presidente"
            r"|quien invento|quien descubrio|quien escribio|cuantos habitantes|cuanto mide|chiste|futbol|partido de"
            r"|recomiendame (una|un) (pelicula|serie|libro|restaurante)|what time is it|weather|capital of|who won|tell me a joke)\b"
        ),
    ),
)

# --- Palabras de dominio ---------------------------------------------------------
# Si aparecen, el mensaje no es "casual" ni una correspondencia personal:
# se deja pasar al grafo (RAG / tools).
DOMAIN_ANCHOR = _rx(
    r"\b(healthcore|hipaa|gdpr|rgpd|ico|baa|dpa|phi|compliance|cumplimiento|politica|politicas|procedimiento|protocolo"
    r"|consentimiento|privacidad|datos personales|brecha|breach|paciente|pacientes|cita|citas|no-show|no show|cancelacion"
    r"|seguro|seguros|aseguradora|medicare|medicaid|nhs|self-pay|referencia|derivacion|especialista|clinica|clinicas|sede"
    r"|incidencia|incidencias|ticket|inventario|stock|insumo|insumos|coordinador|recepcion|facturacion)\b"
)

# Regulación sanitaria a nivel de industria: si la base de conocimiento no
# tiene la respuesta, el agente puede dar contexto general + la política
# interna aplicable (CONTEXT §2, "fuera de dominio pero permitido").
REGULATION_TOPIC = _rx(
    r"\b(hipaa|hitech|gdpr|uk gdpr|rgpd|ico|baa|business associate|dpa|data processing agreement|phi|protected health information"
    r"|notificacion de brechas?|notificar (una )?brecha|breach notification|minimizacion de datos|derecho de acceso|subject access)\b"
)

# --- Seguridad: extracción de una brecha activa ----------------------------------
BREACH_TOPIC = _rx(
    r"\b(brecha|brechas|breach|breaches|filtracion|fuga de datos|incidente de seguridad|ciberataque|ransomware|hackeo|hackearon"
    r"|nos hackearon|data leak|security incident)\b"
)
BREACH_ACTIVE = _rx(
    r"\b(activa|activas|actual|en curso|en investigacion|abierta|reciente|ultima|de (este|esta) (mes|semana|ano)"
    r"|que (hubo|tuvimos|hay|tenemos|paso|ocurrio)|nuestra|ongoing|current|active|under investigation|recent|we had)\b"
)
# Preguntas de detalle típicas de la extracción gradual. Solas no dicen nada;
# con la palabra "brecha" o dentro de la ventana de un intento anterior, sí.
BREACH_PROBE = _rx(
    r"\b(cuando se (descubrio|detecto|produjo|supo|notifico)|cuando (fue|paso|ocurrio)|cuantos (registros|pacientes|expedientes|datos|afectados|usuarios|historiales)"
    r"|que (clinica|clinicas|sede|sedes|centro|sistema|sistemas|proveedor|datos)|donde (fue|ocurrio|paso)|quien (fue|lo hizo|la causo|la provoco)"
    r"|como (entraron|ocurrio|paso|se produjo)|registros afectados|pacientes afectados|alcance|estado de la investigacion"
    r"|when was it (discovered|detected)|how many (records|patients)|which (clinic|system|vendor)|who (did|caused))\b"
)


def first_match(rules: Tuple[Tuple[str, Pattern[str]], ...], text: str) -> str:
    """Motivo de la primera regla que coincide, o cadena vacía."""
    for reason, pattern in rules:
        if pattern.search(text):
            return reason
    return ""
