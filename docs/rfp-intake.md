# Recepción y enrutamiento de RFPs (Hito 9, Parte 1)

Rama `feature/rfp-intake` sobre `main`, 2026-10-19. Revenue Cycle (Tom Callahan, la "Ventas" de HealthCore) sube una RFP en PDF desde el backoffice y, sin leer el documento, ve qué tiene que pedir a cada departamento y a quién.

## Flujo

```
backoffice /rfps ── POST /rfp-tickets (multipart) ──► API existente
                                                      ├─ valida %PDF y ≤10 MB
                                                      ├─ guarda data/raw/rfp/<ticket_id>.pdf
                                                      ├─ crea ticket `analizando` en Supabase
                                                      ├─ encola rfp_intake.process_ticket (Celery/Redis)
                                                      └─ 202 {ticket_id, rfp_id, status}   (~0,7 s)

worker Celery ── runner.run_intake(ticket_id) ── grafo `rfp_intake` (LangGraph):

  convert → phi_screen ─PHI──────────────────────→ discard ─→ END
                └→ classify ─no es RFP────────────→ discard
                      └→ orchestrate ─Send×N─→ department_worker (paralelo) → synthesize ─→ END

  → una transacción: estado final + rfp_metadata + rfp_department_sections + handoff

backoffice /rfps/[id] ── GET /rfp-tickets/{id} cada 3 s mientras `analizando`
```

| Paso | Qué hace | Quién decide |
|---|---|---|
| `convert` | PDF → Markdown con MarkItDown; limpia `(cid:127)` y líneas partidas; trocea en bloques numerados | código |
| `phi_screen` | Detecta PHI de paciente y redacta los párrafos que la contienen. Si hay, para: ningún modelo ve el documento | código (regex) |
| `classify` | ¿Es una RFP de cliente para HealthCore? `{is_rfp, confidence, document_kind, reason}`; solo pasa con confianza ≥ 0,6 | modelo |
| `orchestrate` | Metadatos del BRIEFING §2.3 + qué bloques lee cada departamento | modelo + validación |
| `department_worker` ×N | `key_aspects` y `open_questions` de su departamento, con metadatos + solo sus extractos | modelo + validación |
| `synthesize` | Resumen para Ventas, "qué pedir a quién" y contradicciones entre departamentos | modelo; contactos del catálogo |

## Mapa del código

- `data/pipelines/rfp_intake/`: `departments.py` (catálogo §2.1 y reglas por país), `conversion.py`, `phi.py`, `readability.py`, `agents.py` (los 4 agentes), `graph.py`, `models.py` (3 tablas), `repository.py`, `runner.py`, `sample_pdf.py` (generador mínimo de PDF para pruebas).
- `services/rfp_intake/`: `router.py` + `schemas.py`, montado en `services/api/main.py`. Solo HTTP.
- `services/tasks/rfp_tasks.py`: tarea de Celery `rfp_intake.process_ticket` sobre `ObservableTask` (log estructurado + DLQ).
- `scripts/reprocess_rfp.py` (reproceso manual, `--inline` sin Celery) y `scripts/make_phi_test_rfp.py` (PDF del caso 4 con PHI ficticia).
- `uis/backoffice`: `app/rfps/page.tsx` (subida + lista), `app/rfps/[id]/page.tsx` (detalle), `types/rfp.ts`, `services/rfpApi.ts`, `hooks/usePolling.ts`. `services/http.ts` deja de forzar `Content-Type: application/json` cuando el cuerpo es `FormData`.
- Tests: `tests/pipelines/test_rfp_intake.py` (49, sin red), `services/api/tests/test_rfp_intake_api.py` (9), `uis/backoffice/__tests__/rfp.test.ts` (11).

## Persistencia (Supabase, vía SQLModel)

- `rfp_tickets`: `ticket_id`, `rfp_id` (`RFP-AAAAMMDD-XXXXXX`), `status`, `raw_pdf_path`, `created_by`, `discard_reason` (`not_rfp` / `phi_detected`), `classifier_reason`, `requires_compliance_review`, `phi_categories` (solo categorías), `error_message`, `task_id`, `attempts`, `handoff_payload`, `next_stage`, fechas.
- `rfp_metadata`: campos del BRIEFING §2.3 + `currency` + `unmapped_departments` + `open_questions` + métricas de legibilidad + resultado del synthesizer (`summary`, `who_to_ask`, `conflicts`).
- `rfp_department_sections`: una fila por departamento con `key_aspects`, `open_questions`, `excerpt_indexes`, `unsupported_claims_removed` y, vacías hasta las Partes 2 y 3, `draft_content`, `evaluation_results`, `approval_status`, `approver`, `approved_at` (se crean ya porque `create_all` no añade columnas a una tabla existente).

Estados: `analizando` → `analisis_completo` | `descartado` | `error`. `error` es un añadido a la tabla del README (decisión del usuario): un fallo técnico no es un rechazo del clasificador y no debe parecerlo.

## Contrato de handoff hacia la Parte 2 (v1)

La Parte 2 busca tickets con `status = 'analisis_completo'` y `next_stage = 'generando_borrador'` (o llama a `GET /rfp-tickets/{id}/handoff`) y arranca de `handoff_payload`, sin volver a leer el PDF:

```json
{
  "handoff_version": 1,
  "ticket_id": "dfc9f902-…",
  "rfp_id": "RFP-20261019-6BE2D4",
  "next_stage": "generando_borrador",
  "markdown_path": "data/raw/rfp/dfc9f902-….md",
  "metadata": {"client_name": "Meridian Manufacturing, Inc.", "client_country": "US", "currency": "USD",
               "program_type": ["occupational_health", "corporate_wellness"], "covered_population": 800,
               "covered_population_unit": "employees", "deadline": "2026-08-23", "budget_range": null,
               "departments_needed": ["revenue", "clinical", "compliance"], "unmapped_departments": [], "open_questions": []},
  "workstreams": [{"department_id": "compliance", "owner": "Claire Whitfield",
                   "key_aspects": ["Cliente US: la propuesta debe incluir una cláusula BAA (HIPAA).", "…"],
                   "open_questions": ["…"], "excerpt_indexes": [14, 18, 27]}],
  "summary": "…", "who_to_ask": [{"department_id": "revenue", "owner": "Tom Callahan", "request": "…"}], "conflicts": []
}
```

`markdown_path` apunta al Markdown **redactado** y `excerpt_indexes` a sus bloques, así que un generador puede releer solo sus extractos.

## Decisiones que conviene no romper

- **PHI: redactar y parar** (decisión del usuario; regla del árbitro `phi-detected` del BRIEFING §7). `phi.py` reutiliza las regex de `services/agent/memory/phi_guard.py` (nueva función pública `find_matches`) y añade campos etiquetados ("Patient:", "DOB:", "MRN:", "Diagnosis:"). La decisión (`is_patient_phi`) difiere de la memoria: en una RFP, un email o "manejo de diabetes" no son PHI por sí solos. Regla: un identificador fuerte basta; clínico + contacto también (conservador); clínico solo o contacto solo, no. Se redacta el **párrafo entero**: la regex detecta bien, pero redactar fragmento a fragmento dejaba "04/12/1979" o el diagnóstico a la vista (lo detectó el PDF del caso 4, no los tests).
- **Lo que el BRIEFING declara sin excepción vive en código, no en el prompt**: `revenue` y `compliance` siempre participan (`required_departments`); moneda y BAA/DPA salen de `client_country` (`COUNTRY_RULES`) y se pasan al worker como "HECHOS FIJADOS"; los contactos salen del catálogo, nunca del modelo.
- **Anti-invención**: la población cubierta del orquestador se anula si no aparece en el texto; cualquier aspecto o pregunta de un worker con una cifra que no esté en sus extractos ni en los metadatos se elimina (`unsupported_claims_removed`). Las cifras permitidas salen del contenido de los bloques, no del texto con las etiquetas `[n]`.
- **Fallo del modelo ≠ "no es RFP"**: salida inválida o proveedor caído lanzan `AgentError` → reintentos → `error`. Nunca `descartado`.
- **El grafo no toca la base de datos**: `runner.py` escribe todo en una transacción. Un fallo a mitad deja el ticket `analizando` sin resultados parciales.
- **El PDF se llama como el ticket** (`data/raw/rfp/<ticket_id>.pdf`): el nombre original podría llevar PHI. `data/raw/rfp/` está en `.gitignore`.
- **Celery recibe solo el `ticket_id`**: ningún contenido de la RFP viaja por Redis.

## Respuestas a las preguntas de diseño

1. **Departamento que no existe en el BRIEFING.** El orquestador puede nombrarlo, pero `required_departments` solo admite `revenue`/`clinical`/`compliance`; el resto queda en `unmapped_departments` y la UI lo muestra en "Pendiente de confirmar" para que Revenue Cycle decida a quién derivarlo. No se crea un worker para él.
2. **Qué recibe cada worker.** Solo `WorkerInput`: metadatos compartidos + sus bloques + los hechos fijados de su departamento. Nunca el documento completo ni lo de otros departamentos (hay test). Si falta una cifra, va a `open_questions`; si la inventa, el código la elimina.
3. **Cuándo no es una RFP.** Una organización cliente pide a HealthCore que le *preste* servicios y solicita propuesta; el formato no decide (el correo de Thames Valley es RFP). Un proveedor que vende algo a HealthCore no lo es. Solo pasa un "sí" con confianza ≥ 0,6. Un falso negativo no se pierde: queda `descartado` con `classifier_reason` visible en la lista y se recupera con `scripts/reprocess_rfp.py`.
4. **Dos workers contradictorios.** El synthesizer las lista en `conflicts` (estructura persistida) y no las resuelve: la Parte 3 tiene un árbitro fijo por tipo de conflicto. Lo regulatorio (moneda, BAA/DPA) ni siquiera puede contradecirse: lo fija el código.
5. **Dónde corre y cómo el ticket sigue siendo verdad.** En el worker de Celery (fuera del proceso de la API). Durante los reintentos (2, backoff 15/30 s) sigue `analizando`; fallo definitivo → `on_failure` lo pasa a `error` + DLQ; PDF ilegible o timeout van directos a `error`; si el worker muere sin llegar a `on_failure`, `repository.recover_stale` lo pasa a `error` a los 30 min al consultarlo. Pasó de verdad en la verificación (ver abajo).

## Verificación real (2026-10-19)

Modelo `deepseek-v4-flash` vía proxy de 4Geeks, Supabase `healthcore-data`, Redis en Docker, API + worker + backoffice nativos, subida por la UI en Chrome:

| PDF | Resultado | Tiempo |
|---|---|---|
| `healthcore-request-1.pdf` (Meridian, formal) | `analisis_completo`; US, USD, 800 employees, 2026-08-23; revenue/clinical/compliance; BAA (HIPAA) | 66 s |
| `healthcore-request-2.pdf` (Thames Valley, correo) | `analisis_completo`; UK, GBP, 3000 students; revenue/clinical/compliance; DPA (UK GDPR); fecha sin año → pregunta abierta | 48 s |
| `healthcore-request-3.pdf` (pitch de MedFlow) | `descartado` / `not_rfp`: "un proveedor externo ofrece vender un sistema EHR…" | 3,4 s |
| caso 4 (`make_phi_test_rfp.py`, PHI ficticia) | `descartado` / `phi_detected`, `requires_compliance_review`; ninguna llamada al modelo | 0,3 s |

`POST /rfp-tickets` respondió 202 en 670 ms. Búsqueda del nombre, MRN y fecha de nacimiento ficticios en las tres tablas, los logs de API y worker y los `.md` guardados: cero apariciones.

Lo que solo encontró la prueba real (corregido, con test de regresión):
- Con el correo de Thames Valley el orquestador no pidió `revenue` → ahora es obligatorio por código.
- El worker de Revenue preguntaba "¿la moneda será USD u otra?" junto a "Cotizar en USD" → hechos fijados en el mensaje del worker.
- **Celery con pool `prefork` falla en macOS** (Python 3.12, billiard 4.3): cada tarea moría al instante con `ValueError: not enough values to unpack (expected 3, got 0)` en `fast_trace_task`, antes de entrar en la tarea, así que `on_failure` no corría y los tickets quedaban `analizando` (el caso que cubre `recover_stale`). En dev nativo el worker se arranca con `--pool=solo`; el paralelismo por departamento no se pierde porque lo hace LangGraph con hilos. No se investigó la causa (sin impacto en Docker/Linux, que usa fork).

## Residuales

- La subida tarda ~0,7 s: son tres viajes a Supabase (crear ticket, guardar `task_id`). Se podría guardar el `task_id` en la misma escritura generándolo antes de encolar.
- La conversión no reconstruye tablas: los pesos de los criterios de Meridian (35 %/25 %…) llegan desemparejados y el worker de Revenue lo deja como pregunta.
- `recover_stale` solo actúa al consultar el ticket, no en segundo plano.
- Un PDF escaneado (sin texto) termina en `error` ("no contiene texto extraíble"); no hay OCR.
