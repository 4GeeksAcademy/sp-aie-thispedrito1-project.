# Aprobación humana y cierre de RFPs (Hito 9, Parte 3)

Rama `feature/rfp-approval-completion`, apilada sobre `feature/rfp-response-generation` (Parte 2), 2026-10-21. Cada responsable del BRIEFING §2.1 aprueba, pide cambios o rechaza su sección. Pausar a uno no bloquea a los demás. Cuando aprueban todos (Compliance siempre), el documento final se genera solo.

## Flujo

```
worker Celery ── rfp_response.generate_ticket (Parte 2) ── evaluacion_completa | needs_human_review
                    └─ encola rfp_approval.advance_ticket (automático)

grafo `rfp_approval` (checkpointer Postgres, hilo `rfp-{ticket_id}`):

  START → open_approval ─Send×N─→ department_branch ─→ arbitrate ─┬─(sin conflicto)──→ synthesize_final_document → END
                                        ▲                         ├─(rechazo/límite)─→ close_without_document → END
                                        └──(Send: secciones que el árbitro obliga a revisar)

  department_branch = subgrafo `rfp_department_approval`:
      screen_section ─(PHI o BAA/DPA)──────────────────────────→ END  ("blocked": decide el árbitro)
           │└─(cambios forzados por el árbitro)→ revise_section ─┐
           └→ request_approval ── interrupt()  ← pausa SOLO esta rama
                   ├─ aprobar / rechazar / cancelar ───────────→ END
                   └─ solicitar cambios → revise_section ──────→ screen_section
      revise_section = ciclo generador-evaluador de la Parte 2 con el motivo como feedback

API  POST /rfp-tickets/{id}/sections/{dept}/decision ─┐  valida contra la petición del grafo (400 por campo),
     POST /rfp-tickets/{id}/arbitration ──────────────┘  guarda la decisión en la fila y encola advance (202)
worker  advance() = start | resume (Command(resume={interrupt_id: decisión})) | retry | sync → filas de Supabase
```

## Mapa del código

- `data/pipelines/rfp_intake/approval_graph.py`: grafo padre, subgrafo por departamento, `build_approval_request` (lo que ve el aprobador), límites y `branch_outcome`.
- `approval_runner.py`: `advance` (el único punto de entrada, idempotente), `submit_section_decision`/`submit_arbitration_decision` (los usan la API y la simulación E2E), `read_view`, `collect_resume`, `sync`, `TraceStore` y `ticket_lock`.
- `arbitration.py`: los tres triggers del BRIEFING §7, `capacity_shortfall` (Learn by Doing delegado), `rule_resolution` y `capacity_resolution`.
- `decisions.py`: validación de la respuesta humana (esquema + reglas + detector de PHI en los comentarios).
- `final_document.py`: synthesizer determinista, sin LLM.
- `checkpointer.py`: `PostgresSaver` con `prepare_threshold=None` y `thread_id_for`.
- `trace.py`: `TraceEntry` y `event_id` determinista. Las Partes 1 (`runner._run_with_trace`, con `stream`) y 2 (`TicketProgress`) escriben también en `rfp_trace_events`.
- `models.py`: estados `esperando_aprobación`/`terminado`, `approval_status` por sección, columnas nuevas (migración idempotente), tablas `rfp_final_documents` y `rfp_trace_events`.
- `services/tasks/rfp_tasks.py`: `rfp_approval.advance_ticket` + encadenamiento desde la Parte 2. `services/rfp_intake/`: 4 endpoints nuevos.
- Backoffice `/rfps/[id]`: `DecisionForm`/`ApprovalBlock` por sección, `ArbitrationPanel`, `FinalDocumentPanel` (descarga .md) y `TracePanel`.
- Scripts: `scripts/advance_rfp_approval.py <ticket_id> [--inline]` y `scripts/run_rfp_e2e.py --offline | --live <pdf> [--scenario …]`.
- Tests: `tests/pipelines/test_rfp_approval.py` (39), `tests/pipelines/test_rfp_e2e.py` (1, Partes 1→3), `tests/pipelines/rfp_fixtures.py` (compartido con el script), `services/api/tests/test_rfp_approval_api.py` (6) y `uis/backoffice/__tests__/rfpApproval.test.ts` (9).

## Estados

| Ticket | Cuándo |
|---|---|
| `evaluacion_completa` / `needs_human_review` | Entrada (Parte 2), con `next_stage = esperando_aprobación`. |
| `esperando_aprobación` | Desde que arranca la Parte 3 hasta el cierre. |
| `terminado` | Documento final guardado (`rfp_final_documents`) y accesible. |
| `descartado` | Reutilizado (decisión del usuario): un departamento rechazó (`discard_reason = rejected_in_approval`) o se agotaron las rondas de arbitraje (`arbitration_limit`). |
| `error` | Fallo técnico durante una reanudación (p. ej. timeout del proxy en una revisión). Celery lo reintenta y vuelve a `esperando_aprobación`. |

Sección (`approval_status`): `pendiente` → `decision_registrada` (la API la guardó) → `en_revisión` (si se pidieron cambios) → `pendiente` … → `aprobada` | `rechazada` | `cancelada` (otro departamento rechazó) | `bloqueada` (un trigger regulatorio la paró antes del humano).

## Decisiones que conviene no romper

- **Pausa por rama, no del flujo.** `interrupt()` dentro del subgrafo de cada departamento; las ramas son tareas paralelas del mismo superstep. Se reanuda con `Command(resume={id_del_interrupt: valor})` solo para quien decidió. Hay test que aprueba Compliance mientras Revenue y Clinical siguen pausados, y la prueba real lo repitió por la UI.
- **Un hilo por ticket (`rfp-{ticket_id}`)**; las ramas se separan por `checkpoint_ns`. Test de dos tickets concurrentes en el mismo saver.
- **El grafo no toca la base de datos.** Lo anterior a un `interrupt()` es puro, porque LangGraph lo reejecuta al reanudar (también en las ramas que siguen esperando). El runner lee `get_state` y escribe.
- **La decisión no viaja por Redis.** La API la valida, la guarda en `pending_decision` y la tarea solo lleva el `ticket_id`. El worker la vuelve a validar contra la petición viva y descarta, con un motivo visible, la que llega tarde o con otra huella de borrador (`draft_digest`).
- **Validación antes del resume** (`decisions.py`): motivo obligatorio para cambios y rechazos, "solicitar cambios" solo si quedan rondas, capacidad obligatoria para que Clinical apruebe y prohibida para los demás, y comentarios con PHI rechazados enteros (no se redactan). El nodo repite la validación como segunda capa.
- **Límites en código:** `DEFAULT_MAX_REVISION_ROUNDS = 2` por sección (la petición deja de ofrecer cambios y `route_after_decision` lanza si alguien se salta la validación) y `DEFAULT_MAX_ARBITRATION_ROUNDS = 2` (después, `descartado`). Cada revisión usa el ciclo de la Parte 2 con `REVISION_MAX_ITERATIONS = 2`.
- **Arbitraje = estado estructurado + árbitro fijo** (BRIEFING §7):
  - `phi-detected` y `baa-dpa-mismatch` salen del evaluador de cumplimiento de la Parte 2, que es la misma regla en todo el flujo. Los resuelve la regla de Claire Whitfield: cambios forzados, con el motivo escrito por código.
  - `capacity-vs-population` sale de `confirmed_capacity`, el número que escribe Dr. Reid al aprobar, comparado con `covered_population`. Lo decide Tom Callahan en una pausa del nodo `arbitrate`: reducir la población o añadir sedes.
  - Los regulatorios van primero. El de capacidad solo se evalúa con Compliance aprobado.
  - Ningún LLM vota.
- **Un borrador con PHI nunca se enseña al aprobador:** `screen_section` lo bloquea antes del `interrupt()`. `detect_conflicts` vuelve a pasar las reglas sobre los borradores aprobados y `build_final_document` se niega a generar con PHI (tercera capa).
- **Synthesizer sin LLM:** solo ordena, encabeza y añade la tabla de aprobaciones. Exige Compliance aprobado y todas las ramas aprobadas.
- **Rechazar descarta la propuesta** (decisión del usuario): la rama que rechaza termina y las pausadas reciben `cancel` en la misma reanudación.
- **Firma:** cualquier usuario autenticado firma en nombre del responsable del BRIEFING (`approver`) y queda su `decided_by` (decisión del usuario).
- **Trazabilidad:** cada nodo devuelve `trace` (agente, entrada, salida, timestamp) al estado y a `rfp_trace_events`, sin texto libre (huellas SHA-256 y recuentos). `event_id` determinista para no duplicar nodos reejecutados. Los eventos `interrupt` marcan el inicio de cada espera humana, para medir el tiempo de aprobación por departamento (KPI del BRIEFING §3).

## Gotchas reales (comprobados con LangGraph 0.6.11)

- **`snapshot.interrupts` sigue listando una pausa ya resuelta.** Una pausa está viva solo si su tarea no tiene `result`. Si la rama vuelve a pausarse (tras pedir cambios), la pausa cambia de id.
- **Una tarea que falló y se reintentó con éxito conserva su `error`** aunque ya esté pausada de nuevo. `advance` marca el ticket `error` al fallar, y `read_view(trust_errors=…)` solo cree en el error si el ticket está en `error`. Si no, la sección se quedaba "en revisión" para siempre. Lo detectó un test, y la prueba real lo ejercitó: timeout del proxy en una revisión → reintento de Celery → `action=retry` sin pedir de nuevo la decisión a Tom.
- **Pooler de Supabase (6543, modo transacción):** psycopg 3 usa prepared statements desde la 5.ª ejecución → `prepare_threshold=None`. Los advisory locks de sesión no sirven ahí: `ticket_lock` usa `pg_advisory_xact_lock` en una transacción abierta.
- **`langgraph-checkpoint-postgres` 3.2 exige `langgraph-checkpoint` 4.x**, que `langgraph` 0.6.11 no acepta: fijado a 3.0.5.
- **Subgrafo dentro de un nodo:** se compila sin `checkpointer=` (hereda el del padre, necesario para pausar). El de revisión de la Parte 2 va con `checkpointer=False`.
- **La pantalla dejaba de consultar durante el reintento** (el ticket pasa por `error` unos segundos). `shouldPollTicket` sigue consultando si una sección está "en revisión". Lo detectó el recorrido en Chrome.
- **`GET /response-handoff` daba 409 en cuanto arrancaba la Parte 3** ("Draft generation is not complete"): mismo salto entre partes que la Parte 2 corrigió en `/handoff`. Ahora responde mientras exista el handoff.

## Respuestas a las preguntas de diseño

1. **¿Qué pasa si un departamento rechaza tras la interrupción?** Hay dos botones distintos:
   - "Solicitar cambios" vuelve al generador de la Parte 2 **dentro de la misma ejecución**: `revise_section` reutiliza su subgrafo con el comentario como feedback y la rama se vuelve a pausar con el borrador nuevo. No hay que relanzar el ticket. Máximo 2 rondas.
   - "Rechazar" descarta la propuesta. No hay nada que reescribir.
2. **Información mínima para decidir** (`build_approval_request`):
   - el borrador y si es provisional, con el feedback concreto de los evaluadores;
   - los aspectos clave que la RFP pide a ese departamento;
   - los hechos fijados por país (moneda, BAA/DPA, marco);
   - la población cubierta (y la capacidad, si es Clinical);
   - las rondas de cambios que quedan.

   No hace falta releer la RFP.
3. **Triggers en estado estructurado:**
   - PHI y BAA/DPA salen de las banderas y `rule_id` del evaluador de cumplimiento (`contains_phi`, `baa-us`, `dpa-uk`).
   - La capacidad sale de un **número** que Clinical confirma al aprobar, comparado con `covered_population`, nunca de interpretar prosa.

   Así el nodo de arbitraje decide con datos que se pueden comparar.

## Verificación real (2026-10-21)

deepseek-v4-flash vía proxy de 4Geeks, Supabase `healthcore-data`, checkpointer Postgres.

| Recorrido | Resultado |
|---|---|
| `scripts/run_rfp_e2e.py --live healthcore-request-2.pdf` (Thames Valley, UK, aprobar todo) | `analisis_completo` (44 s) → `needs_human_review` (83 s) → `esperando_aprobación` → Compliance aprueba con los otros dos pausados → `terminado`. Documento en GBP con DPA/UK GDPR. 137 s en total. |
| Cadena completa por la UI en Chrome (Meridian, US), con Redis en Docker, worker nativo `--pool=solo`, API y backoffice | Subida → Parte 1 (71 s) → Parte 2 (un reintento de Celery por timeout del proxy) → **Parte 3 encadenada sola** → los 3 pausados. |

Pasos de la cadena por la UI:

1. Claire aprueba; Revenue y Clinical siguen pausados.
2. Dr. Reid pide cambios con un comentario que lleva un paciente ficticio → 400 con el mensaje en el campo y el texto sin repetir.
3. Dr. Reid pide cambios con un comentario válido → el borrador vuelve reescrito, ronda 1 de 2.
4. Aprueba confirmando capacidad 500 y Tom aprueba Revenue: llegan casi a la vez y se aplican en **una** reanudación (`resume:2`); la tarea siguiente solo sincroniza.
5. Panel de arbitraje `capacity-vs-population`: 800 contra 500.
6. Tom reduce a 500 → revisión forzada de Revenue y Clinical → timeout del proxy en Clinical → reintento `action=retry` con éxito.
7. Ambos aprueban → `terminado`, documento final con "500 empleados", 60 pasos de traza en orden (P1: 8, P2: 24, P3: 28) y sin errores en la consola.

Sesión con un token local de `create_access_token` (el `sub` es el `id` UUID del usuario, no la clave de TinyDB), sin login.

## Ejemplo reproducible para el PR

`services/api/.venv/bin/python scripts/run_rfp_e2e.py --offline` (sin red, ~2 s): la RFP de Meridian pasa por las tres partes con un modelo guionizado y el escenario `parallel`:

1. Compliance aprueba con los otros dos pausados.
2. Clinical pide cambios.
3. Clinical confirma capacidad para 500.
4. Tom arbitra y reduce la población a 500.
5. Revenue y Clinical aprueban la versión revisada.

Imprime el estado tras cada paso, el documento final y la traza por agente. `tests/pipelines/test_rfp_e2e.py` hace el mismo recorrido como test.

## Residuales

- Cualquier usuario autenticado puede firmar por cualquier departamento (decisión del usuario): el rastro es `decided_by`. Mapear usuarios a departamentos queda para otra entrega.
- Un `error` de la Parte 3 solo se reintenta una vez en Celery. Si persiste, `scripts/advance_rfp_approval.py <id> --inline`.
- Una RFP sin población cubierta nunca dispara `capacity-vs-population` (no se inventa la cifra) y no se pide capacidad a Clinical.
- El documento final se entrega en Markdown (descarga `.md`); no hay PDF.
