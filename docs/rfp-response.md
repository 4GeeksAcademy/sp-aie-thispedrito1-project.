# Generación y evaluación de respuestas a RFP (Hito 9, Parte 2)

Rama `feature/rfp-response-generation`, apilada sobre `feature/rfp-intake` (PR #39 de la Parte 1, sin mergear), 2026-10-21. Con lo que dejó la Parte 1 (qué toca a cada departamento), el sistema redacta un primer borrador de la propuesta económica por departamento y lo autoevalúa antes de que lo vea una persona.

## Flujo

```
worker Celery ── rfp_intake.process_ticket (Parte 1) ── analisis_completo
                    └─ encola rfp_response.generate_ticket (automático, decisión del usuario)

worker Celery ── response_runner.run_response(ticket_id)
   lee rfp_tickets.handoff_payload (contrato v1 de la Parte 1; nunca el PDF)
   ticket → generando_borrador
   grafo `rfp_response` (LangGraph):

   START ─Send×N─→ draft_section ─→ END          un subgrafo por departamento, en paralelo

   draft_section = subgrafo `rfp_section`:
       generate ─┬→ evaluate_readability ─┐
                 ├→ evaluate_relevance   ─┼→ judge ─(suspende y quedan intentos)→ generate
                 └→ evaluate_compliance  ─┘       └─(aprueba / límite / no arreglable)→ END

   progreso en vivo (on_progress → Supabase): sección redactando / en evaluación, ticket → en_evaluación
   final, en una transacción: borradores + EvaluationResult + historial + response_handoff
   ticket → evaluacion_completa | needs_human_review   (next_stage = esperando_aprobación)

backoffice /rfps/[id] ── poll cada 3 s mientras hay trabajo (y en analisis_completo, porque la Parte 2 arranca sola)
```

## Mapa del código

- `data/pipelines/rfp_intake/generators.py`: un `SectionGenerator` por departamento (`REVENUE_GENERATOR` "Términos económicos", `CLINICAL_GENERATOR` "Viabilidad clínica y capacidad", `COMPLIANCE_GENERATOR` "Protección de datos y cumplimiento normativo"), cada uno con su encargo; `GeneratorInput` (lo único que reciben) y `fixed_facts` (moneda y BAA/DPA por país).
- `data/pipelines/rfp_intake/evaluators.py`: `EvaluationResult` y los tres evaluadores. Legibilidad (`py-readability-metrics` para palabras/frases + Fernández Huerta), pertinencia (modelo) y cumplimiento (reglas deterministas del BRIEFING). `build_feedback` y `combine`.
- `data/pipelines/rfp_intake/response_graph.py`: grafo `rfp_response` + subgrafo `rfp_section`, límite de iteraciones (`DEFAULT_MAX_ITERATIONS = 3`).
- `data/pipelines/rfp_intake/response_runner.py`: lee el handoff, `TicketProgress` (progreso en vivo) y `build_response_handoff` (contrato hacia la Parte 3).
- `data/pipelines/rfp_intake/models.py` + `repository.py`: estados y columnas nuevas, `ensure_rfp_response_columns` (migración idempotente que `main.py` ejecuta al arrancar), `start_drafting`, `record_section_progress`, `save_response`, `reset_for_response`; `recover_stale` vigila también los estados de la Parte 2.
- `services/tasks/rfp_tasks.py`: tarea `rfp_response.generate_ticket` (1 reintento) y el encadenamiento desde la Parte 1.
- `services/rfp_intake/`: el detalle devuelve borrador y evaluación por sección; `GET /rfp-tickets/{id}/response-handoff` (nuevo, 409 hasta que termina). `GET /handoff` sigue respondiendo cuando la Parte 2 ya avanzó el estado (antes habría dado 409).
- `scripts/generate_rfp_response.py <ticket_id> [--inline]`: relanzar la Parte 2.
- Backoffice: `types/rfp.ts` (estados, `EvaluationResult`, `describeEvaluation`, `shouldPoll`), `app/rfps/[id]/page.tsx` (`DraftBlock`), `app/globals.css` (`.rfp-status-review`, `.rfp-draft`).
- Tests: `tests/pipelines/test_rfp_response.py` (49, sin red), `services/api/tests/test_rfp_intake_api.py` (+3), `uis/backoffice/__tests__/rfp.test.ts` (+5).

## Estados

| Estado del ticket | Cuándo |
|---|---|
| `analisis_completo` | Entrada (Parte 1). La Parte 2 no lo reescribe al terminar. |
| `generando_borrador` | Los generadores redactan la primera versión. |
| `en_evaluación` | Desde la primera evaluación hasta el final, incluidas las correcciones (ciclo generador-evaluador). |
| `needs_human_review` | Alguna sección agotó el límite (o no era arreglable). Sus borradores van igual a la Parte 3. |
| `evaluacion_completa` | **Añadido (decisión del usuario):** las tres secciones aprobaron. Quedarse en `en_evaluación` parecería que sigue trabajando, y los estados de la Parte 3 están vetados aquí. |
| `error` | Fallo técnico (modelo caído, handoff inválido). Nunca se disfraza de "suspendido". |

En los dos estados finales, `next_stage = "esperando_aprobación"`: es la bandera para la Parte 3 (un `next_stage`, no un `status`).

Cada sección tiene su `draft_status`: `pendiente` → `generando_borrador` → `en_evaluación` → `aprobada_por_evaluadores` | `needs_human_review`, más `draft_iterations`, `evaluation_results` (el último) y `evaluation_history` (todos).

## EvaluationResult

Se guarda en `rfp_department_sections.evaluation_results` con las claves del README:

```json
{
  "section_id": "dfc9f902-…:compliance", "department_id": "compliance", "iteration": 2,
  "readability": {"pass": true, "score": 52.9, "details": {"words": 210, "sentences": 14, "words_per_sentence": 15.0, "problems": [], "formula": "fernandez_huerta"}},
  "relevance":   {"pass": true, "missing_aspects": []},
  "compliance":  {"pass": true, "rule_ids": ["no-phi", "no-invented-figures", "currency-by-country", "baa-us"], "violations": [], "contains_phi": false},
  "overall_pass": true,
  "feedback_for_generator": "",
  "retryable": true,
  "evaluated_at": "2026-10-21T…"
}
```

## Reglas de cumplimiento (BRIEFING §5 y §2.3)

| `rule_id` | Regla | Comprobación |
|---|---|---|
| `no-phi` | Sin nombres, diagnósticos ni identificadores de paciente | `phi.find_matches` + `is_patient_phi` (el mismo detector de la Parte 1). Marca `contains_phi`, y el ticket queda `requires_compliance_review` (árbitro `phi-detected`). El mensaje nombra las categorías, nunca el texto. |
| `baa-us` | Cliente US → cláusula BAA | La sección de compliance debe nombrar BAA/Business Associate Agreement y HIPAA; ninguna sección puede citar DPA/UK GDPR. |
| `dpa-uk` | Cliente UK → DPA con UK GDPR | La sección de compliance debe nombrar DPA y UK GDPR; ninguna sección puede citar BAA/HIPAA. |
| `currency-by-country` | USD (US) / GBP (UK) | Revenue debe citar la moneda del país; ninguna sección puede citar la del otro. |
| `no-invented-figures` | Nunca inventar cifras | Toda cifra de la sección (en dígitos, con "3.000" leído como 3000, o escrita con letras desde 10) debe estar en el handoff del departamento. |
| `client-country-known` | Sin país no hay moneda ni BAA/DPA | No arreglable por el generador (`retryable = false`): va directa a `needs_human_review` sin gastar iteraciones. |

## Contrato de handoff hacia la Parte 3 (v1)

`rfp_tickets.response_handoff` (o `GET /rfp-tickets/{id}/response-handoff`):

```json
{
  "handoff_version": 1, "ticket_id": "…", "rfp_id": "RFP-20261019-6BE2D4",
  "ticket_status": "needs_human_review", "next_stage": "esperando_aprobación",
  "client_name": "Meridian Manufacturing, Inc.", "client_country": "US", "currency": "USD", "max_iterations": 3,
  "sections": [
    {"department_id": "clinical", "owner": "Dr. Marcus Reid", "draft_status": "needs_human_review",
     "provisional": true, "iterations": 3, "draft_content": "…", "evaluation_result": {"…": "EvaluationResult"}}
  ]
}
```

`handoff_payload` (el contrato de la Parte 1) no se toca: la Parte 2 lo lee y escribe el suyo en otra columna.

## Decisiones que conviene no romper

- **Entrada = handoff de la Parte 1**, nunca el PDF: el generador recibe `GeneratorInput` (metadatos + `key_aspects`/`open_questions` de SU departamento + resumen). Hay test que comprueba que no ve los aspectos de otros departamentos ni el `markdown_path`.
- **Cumplimiento es código, pertinencia es modelo.** El README pide reglas concretas, no opiniones. Pertinencia sí es lenguaje natural; un aspecto que el modelo no clasifica cuenta como no cubierto ("el silencio no aprueba").
- **Feedback construido por código**, de más a menos grave (cumplimiento → pertinencia → legibilidad), con la cifra y el límite de cada problema.
- **Legibilidad en español** (decisión del usuario: propuesta en español). Las fórmulas de `py-readability-metrics` son de inglés; se usan sus recuentos de palabras/frases y la nota Fernández Huerta (206,84 − 0,60·sílabas/100 palabras − 1,02·palabras/frase), con un contador de sílabas español. Umbrales (`readability_verdict`, Learn by Doing delegado): ≥100 palabras, ≤450, ≤25 palabras por frase de media y nota ≥50. El texto legal de Compliance ronda 51-53 bien escrito; un mínimo de 60 lo haría suspender siempre.
- **Límite de 3 iteraciones** por sección (1 + 2 correcciones). Al agotarlo: último borrador + su EvaluationResult, `needs_human_review`, y el ticket sigue.
- **El grafo no toca la base de datos**: avisa con `on_progress` y el runner escribe. Cada aviso usa su propia sesión y un candado (llegan desde hilos en paralelo); un fallo al escribir progreso solo se registra. Cada aviso renueva `updated_at`, así que `recover_stale` (30 min) solo caduca un ticket que de verdad se paró.
- **Migración idempotente** (`ADD COLUMN IF NOT EXISTS`) para las columnas nuevas: `create_all` no altera tablas que ya existen en Supabase.

## Respuestas a las preguntas de diseño

1. **Qué recibe cada evaluador.** Solo su parte: legibilidad, el texto; pertinencia, texto + `key_aspects` de su departamento; cumplimiento, texto + departamento + país + cifras permitidas. Nunca el documento completo ni otras secciones (hay test para pertinencia).
2. **Conflictos al escribir en paralelo.** Cada evaluador escribe su propia clave (`readability`, `relevance`, `compliance`) y solo `judge` las combina, en el superstep siguiente. Entre departamentos, cada subgrafo devuelve su sección a una lista con reducer (`operator.add`), así que nadie pisa a nadie. En la base de datos, el progreso se serializa con un candado.
3. **Cómo ve Ventas que un borrador es provisional.** Badge "Provisional · revisión humana" con borde discontinuo rojo, el borrador con borde rojo y, encima, "Borrador provisional: no superó la evaluación tras N intentos. No usarlo sin revisar estos puntos:" seguido del último `feedback_for_generator`. En el handoff, `provisional: true`.
4. **¿El feedback es específico?** Sí: cada línea dice qué regla falló y qué hacer ("[baa-us] Incluye una cláusula BAA (HIPAA)…", "Quita estas cifras…: 950", "Aborda este aspecto: … — motivo del revisor", "Acorta las frases: la media es de 32 palabras (máximo 25)"). La prueba real lo confirma: Compliance pasó de 46 a 52,9 de legibilidad y cubrió el aspecto que faltaba en una sola vuelta.

## Verificación real (2026-10-21)

`deepseek-v4-flash` vía proxy de 4Geeks, Supabase `healthcore-data`, `scripts/generate_rfp_response.py --inline` sobre los tickets que la Parte 1 analizó el 19-oct:

| RFP | Resultado | Iteraciones (rev / clin / comp) | Tiempo |
|---|---|---|---|
| Meridian (US), 1.ª corrida | `needs_human_review` (clinical agotó el límite) | 1 / 3 / 2 | 68 s |
| Meridian (US), tras los arreglos | `evaluacion_completa` | 1 / 3 / 2 | 100 s |
| Thames Valley (UK), 2 corridas | `error` por `APITimeoutError` del proxy (sin reintento con `--inline`) | — | — |
| Thames Valley (UK), tras los arreglos | `evaluacion_completa` (GBP; DPA + UK GDPR) | 2 / 2 / 1 | ~2 min |

Lo que solo encontró la prueba real (corregido, con test de regresión que falla sin el arreglo):
- **Cifras escritas con letras.** deepseek escribió "ochocientos empleados" y "doce meses": la regla anti-invención solo veía dígitos, así que "novecientos cincuenta empleados" inventado habría pasado. `spoken_numbers_es` las convierte (desde 10: por debajo son expresiones como "las dos partes"; "por ciento" no es 100).
- **"3.000" en formato español.** Con Thames Valley, la población verdadera ("3.000 estudiantes" frente al "3,000" de la RFP) se marcaba como inventada y el generador la "corrigió" a «[por confirmar]». Ahora se normaliza el separador de miles español antes de comparar.
- **Efecto "tapar un agujero, abrir otro"** (1.ª corrida de Meridian): Clinical arreglaba el aspecto que se le pedía y dejaba de cubrir otro. Los `key_aspects` de Clinical son tareas internas ("evaluar…", "verificar…") que una sección de 350 palabras difícilmente cubre todas. No se corrigió: es exactamente el caso que el límite de iteraciones y `needs_human_review` deben absorber.
- **Timeouts del proxy** (`APITimeoutError` a los 60 s) en dos corridas de Thames Valley: el ticket terminó en `error` (con `--inline` no hay reintento; con Celery hay 1).

### Ejemplo de sección que pasa (Meridian, Revenue Cycle, 1.ª iteración)

> La propuesta se cotiza en dólares estadounidenses, según lo fijado para el cliente en Estados Unidos. La estructura de precios se definirá por servicio o por persona cubierta, con los importes pendientes de confirmar. El contrato tendrá una vigencia inicial de doce meses e incluirá una cláusula de renovación opcional. […] Los supuestos de la cotización consideran una población cubierta de 800 empleados de Meridian Manufacturing, Inc. […] Se garantiza el cumplimiento de HIPAA mediante la firma de un Acuerdo de Asociación Comercial (BAA). Pendiente de confirmar: la estructura de precios esperada […].

Legibilidad 52,5 ✓ · pertinencia ✓ · cumplimiento ✓ (`no-phi`, `no-invented-figures`, `currency-by-country`, `baa-us`).

### Ejemplo de sección que falla (Meridian, Clinical Operations, 1.ª corrida: límite agotado → `needs_human_review`)

> HealthCore brinda servicios clínicos de acuerdo con la RFP: atención en sitio tres días por semana en Austin, Texas; tamizajes anuales de salud a los 800 empleados; y manejo de enfermedades crónicas como diabetes e hipertensión. Se revisa la disponibilidad de clínicas y personal en Austin para asegurar la cobertura. […]

| Iteración | Legibilidad | Pertinencia | Cumplimiento | Feedback recibido |
|---|---|---|---|---|
| 1 | ✗ 43,1 | ✗ | ✓ | "Aborda: evaluar disponibilidad de clínicas o personal en Austin" + "Simplifica el vocabulario: 43, mínimo 50" |
| 2 | ✗ 44,3 | ✗ | ✓ | "Aborda: verificar licencias de salud ocupacional en Texas" + legibilidad |
| 3 | ✓ 51,2 | ✗ | ✓ | "Aborda: estrategia concreta para enfermedades crónicas" → límite agotado |

## Residuales

- Pertinencia exige cubrir **todos** los `key_aspects`, que en Clinical son a veces tareas internas más que contenido para el cliente. Una mejora sería que la Parte 1 separe "qué debe decir la propuesta" de "qué debe hacer el departamento".
- El proxy de 4Geeks corta llamadas a los 60 s con cierta frecuencia; una corrida completa hace hasta 18 llamadas.
- `/rfps` sigue haciendo poll de un ticket que se quedó en `analisis_completo` si el encadenamiento falló (Redis caído justo al terminar la Parte 1); se relanza con `scripts/generate_rfp_response.py`.
- La regla de cifras escritas con letras no cubre ordinales ni fracciones ("primer", "la mitad").
