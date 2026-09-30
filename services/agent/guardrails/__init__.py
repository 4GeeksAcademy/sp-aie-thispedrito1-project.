"""Harness de protección del agente de HealthCore (Ticket #SEC-114).

Capas, cada una para un tipo de fallo distinto (README: "un único guardrail
nunca es suficiente"):

- `input_guard`: jailbreak, caso de paciente identificable, extracción de una
  brecha activa, uso personal (BLOCK) y charla casual (REDIRECT). Sin modelo.
- `prompt`: system prompt con jerarquía de instrucciones y dominio del
  CONTEXT; el mensaje y el contexto van delimitados en el turno `user`.
- `isolation`: neutraliza instrucciones dentro del contenido de RAG, tools y
  memoria, y lo encierra en `<fuente_externa>`.
- `output_guard`: estructura, filtración del prompt, PHI y cifras de brechas.
- `general`: respuesta breve con reconducción fija (casual / regulación).
- `monitor`: log por activación + resumen (`GET /agent/guardrails/summary`).

El orden de ejecución vive en `services/agent/memory/conversation.py`.
"""
