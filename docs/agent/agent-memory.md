# Memoria y auto-mejora del agente de soporte — Ticket #MEM-092

Hito 8 · Parte 1. Rama `feature/agent-memory`, apilada sobre `feature/mcp-oauth-tools`, 2026-09-28.

El agente de `POST /agent/query` (LangGraph + RAG como herramienta + tools por MCP) ya no empieza cada conversación de cero. Cuando alguien le cuenta algo que merece recordarse, **lo propone en la misma respuesta**. La decisión del usuario se **clasifica explícitamente** y queda **registrada**, y solo lo aprobado se guarda. Todo pasa por un validador de PHI (HIPAA y UK GDPR) que no admite excepciones.

## 1. Arquitectura elegida

**Memoria semántica curada en Postgres (Supabase `healthcore-data`)**, en dos tablas propias:

| Tabla | Qué guarda |
|---|---|
| `agent_memories` | Los hechos aprobados: tipo, sede, texto, quién lo aprobó, desde qué propuesta y su estado (`active`, `superseded`, `expired`, `evicted`, `quarantined`, `revoked`). Nunca se borra nada: cada cambio es un cambio de estado con fecha. |
| `agent_memory_proposals` | El **registro de auditoría**: una fila por propuesta, sea cual sea su final (`pending`, `approved`, `approved_edited`, `rejected`, `discarded`, `expired`, `blocked_phi`). Guarda qué se propuso, la etiqueta de la decisión, la confianza, cuándo y el SHA-256 del mensaje que la originó y del que la decidió. |

**Por qué encaja con lo que HealthCore necesita recordar:**

- **El volumen es pequeño y el valor está en la precisión, no en la similitud.** Las tres familias memorizables del CONTEXT (cambios operativos por sede, patrones de incidentes, preferencias del staff) suman decenas de hechos, no millones. Con un tope de 50 compartidos y 10 preferencias por persona, el agente puede recibirlos todos. No hace falta búsqueda vectorial para elegir entre ellos.
- **La auditoría es un requisito de cumplimiento, no un extra.** Claire Whitfield (CCO) necesita responder a "¿quién autorizó que el agente sepa esto, y cuándo?". Eso es una consulta SQL sobre una tabla duradera, con transacciones: la resolución de la propuesta y la escritura del recuerdo son atómicas.
- **"Una sola propuesta pendiente" la garantiza la base de datos** con un índice único parcial (`uq_agent_memory_proposals_one_pending_per_user … WHERE status = 'pending'`), el mismo patrón que el lock de `job_runs`. Dos peticiones simultáneas no pueden dejar dos pendientes.
- **Sin servicio nuevo:** mismo engine que inventario y telemetría. Los tests usan la SQLite en memoria que ya sustituye a Supabase.

**Opciones descartadas:**

| Opción | Por qué no |
|---|---|
| Redis (episódica, clave-valor) | Ya está en el compose para Celery, y su TTL nativo es cómodo. Pero un registro de auditoría en un almacén en memoria es frágil (un `FLUSHALL` o un reinicio sin AOF lo borra) y obligaría a tener Redis siempre encendido. El historial de chat tampoco se necesita: cada consulta del coordinador es independiente (decisión de la Parte 1 del agente). |
| VectorDB (Qdrant, colección `*_agent_memory`) | Útil con miles de recuerdos. Con decenas añade embeddings (enviar cada recuerdo al proveedor externo) sin mejorar la recuperación, y la auditoría seguiría necesitando otra tabla. **Nunca** la colección `healthcore_knowledge`: el RAG sigue siendo de solo lectura (hay test que lo comprueba). |
| Knowledge graph | Las relaciones que importan aquí son planas (hecho → sede, hecho → quién lo aprobó) y caben en columnas. No hay jerarquías ni dependencias que recorrer. |
| Fine-tuning | No permite olvidar selectivamente, algo imprescindible si un recuerdo resulta falso o contiene PHI. Además es lento y caro de actualizar. |

**Interfaz explícita de lectura/escritura** (`services/agent/memory/store.py`, clase `MemoryStore`): `recall`, `get_pending`, `propose`, `record_blocked`, `resolve`, `commit`, `audit` y `revoke`. Nada del agente guarda estado añadiéndolo al system prompt. El grafo recibe lo que devuelve `recall()` como una **fuente más** (nodo `recall_memory`, con forma de chunk y rotulada como "Nota aprobada por el staff"), y solo cuando el planificador la pide.

## 2. Flujo

```
mensaje ─► ¿propuesta pendiente? ──sí──► classify_decision (etiqueta + confianza)
             │                            └► resolve_decision ─► guardar / guardar editada / rechazar / descartar
             │                                                   (siempre queda en agent_memory_proposals)
             ▼  (mensaje completo, o lo que viniera detrás del "sí")
        store.recall() ─► grafo LangGraph: plan_sources → [incidencias] [inventario] [recall_memory] [RAG] → generate
                                                                     generate = UNA llamada: {answer, memory_proposal, user_requested_memory}
             ▼
   validador PHI del MENSAJE (siempre) y de la PROPUESTA ─► con PHI: blocked_phi + explicación al usuario
             │                                              limpia: propose() pendiente + "¿Quieres que recuerde esto…?"
             ▼
   respuesta = [decisión anterior] + respuesta del agente + [pregunta de memoria / aviso de PHI]
```

| Pieza | Archivo |
|---|---|
| Orquestación del turno | `services/agent/memory/conversation.py` (`handle_turn`) |
| Respuesta + auto-evaluación en una llamada | `services/agent/memory/reply.py` (`generate_reply`, `MEMORY_RULES`) |
| Clasificador y política de decisión | `services/agent/memory/decision.py` |
| Validador de PHI | `services/agent/memory/phi_guard.py` |
| Consolidación y limpieza | `services/agent/memory/consolidation.py` |
| Tablas | `services/agent/memory/models.py` |
| Nodo `recall_memory` y fuente `agent_memory` | `services/agent/graph.py`, `services/agent/planner.py` |
| HTTP | `services/agent/router.py`: `POST /agent/query` (nuevo bloque `memory`), `GET /agent/memory`, `GET /agent/memory/audit` (admin), `DELETE /agent/memory/{id}` (admin) |

## 3. Auto-evaluación: qué se propone y qué no

**Criterio explícito** (`MEMORY_RULES`, no "siempre"): se propone solo si el coordinador **aporta** un hecho nuevo o corregido de una de las tres familias del CONTEXT (`clinic_operations`, `incident_pattern`, `staff_preference`). No se propone si es una consulta puntual o un dato que vive en un sistema en vivo, un saludo o cierre, algo que ya está en una nota aprobada, o cualquier cosa que mencione a un paciente. El planificador tiene una herramienta `recall_agent_memory` que elige cuando el usuario **informa** en vez de preguntar. Sin ella, una afirmación acabaría en el "no tengo información" fijo de la Parte 1.

Una propuesta mal formada (tipo fuera de las tres familias, texto vacío) o una salida que no es JSON **no genera propuesta**: la respuesta se muestra igual. Para la memoria, el sistema falla cerrado.

**Deben generar propuesta** (ejemplos del CONTEXT, verificados con el modelo real, §8):

1. "En la clínica de Manchester el proceso de referidos internos ahora pasa primero por el coordinador…" → `clinic_operations` (turno 1).
2. "Esa alerta de no-show elevado en la clínica de Austin fue porque hubo un cierre de carretera…" → `incident_pattern` (turno 4).
3. "El reporte semanal para Diane Foster debe incluir vacantes por rol…" → `staff_preference` (turno 10).

**No deben generar propuesta:**

1. "¿Cuál es la tasa de no-show de esta semana?": consulta puntual; el dato vive en el dashboard (turno 8, sin propuesta).
2. "Gracias, con eso resuelvo mi reporte.": cierre de conversación (turno 9, sin propuesta).
3. "El paciente Johnson canceló su cita de mañana, apúntalo.": intento de guardar PHI. **Se rechaza explícitamente** y se explica por qué (turno 7, `blocked_phi`).
4. "¿Cuánto se cobra por un no-show a un paciente de pago privado en Texas?": la respuesta viene de la política del RAG, así que no hay nada nuevo que recordar (turno 11).

## 4. Decisión del usuario y auditoría

- **Clasificación explícita, no `"sí" in mensaje`:** `classify_decision` pide al modelo un JSON validado con Pydantic: `label` ∈ {`approve`, `reject`, `edit`, `unrelated`, `unclear`}, `confidence`, `edited_content` y `remaining_message`. Si falla, tarda más de 10 s o devuelve algo inválido, el resultado es `unclear` con confianza 0.
- **Política fija** (`resolve_decision`, función pura sin red): con confianza por debajo de 0,75 **nada** cuenta como decisión, y el resultado es `discard`. Tampoco un "no": la auditoría registra "no quedó claro" en vez de atribuir una negativa. `approve` → guardar. `edit` con texto → guardar la versión editada, que vuelve a pasar el validador de PHI. `edit` sin texto, `unrelated` o `unclear` → descartar.
- **Una pendiente a la vez:** el índice único parcial la garantiza. La pendiente se resuelve **antes** de ejecutar el grafo, así que una propuesta nueva solo puede surgir con la anterior ya cerrada.
- **Silencio:** una pendiente sin respuesta caduca a los **30 minutos** (`PENDING_TTL`) como `expired`. Un "sí" al día siguiente no puede aprobar algo de ayer por accidente.
- **Respuesta + otra pregunta en el mismo mensaje:** el clasificador separa `remaining_message`. "Sí. ¿Y en Londres cómo es?" guarda la nota y responde a lo de Londres, ya con la nota recién guardada (test `test_approval_and_a_new_question_in_the_same_message`).
- **Registro auditable:** cada propuesta tiene su fila con texto propuesto, texto final, etiqueta, confianza, `created_at`, `resolved_at`, id del recuerdo y huellas SHA-256 de los mensajes (**nunca** el texto del usuario, que podría contener PHI; mismo criterio que los traces del agente). Además hay una línea JSON por decisión en el logger `healthcore.agent.memory.audit`. En el trace del grafo, la propuesta aparece solo como tipo + huella, porque cuando se escribe aún no ha pasado el validador.

## 5. Qué nunca se recuerda (HIPAA + UK GDPR)

Restricción no negociable del CONTEXT. **Validación determinista** con expresiones regulares (`phi_guard.scan`), no con el modelo: la misma entrada da siempre el mismo veredicto, se puede testear y no depende del proveedor. Es deliberadamente conservadora: un falso positivo solo obliga a reformular sin datos del paciente, mientras que un falso negativo es un incidente de cumplimiento.

| Categoría | Ejemplo bloqueado | Marco |
|---|---|---|
| `patient_name` | "el paciente Johnson", "la Sra. García", "Mr Jones" | HIPAA (nombres) · UK GDPR (dato personal) |
| `medical_record_number` | "MRN 448812", "historia clínica nº…" | HIPAA (MRN) |
| `nhs_number` | "943 476 5919" | UK GDPR (identificador propio del R. U.) |
| `ssn` | "123-45-6789" | HIPAA |
| `insurance_number` | "Póliza número AB-99812" | HIPAA (health plan beneficiary number) |
| `date_of_birth` | "fecha de nacimiento…", "tiene 54 años" | HIPAA (fechas y edades) |
| `clinical_content` | diagnóstico, resultados de laboratorio, síntomas, "850 mg" | UK GDPR art. 9 (datos de salud) · HIPAA (PHI) |
| `email`, `phone`, `uk_postcode` | contacto y dirección | HIPAA · UK GDPR |

Los nombres de personas **sin** relación con un paciente pasan: "el reporte para Diane Foster" es memorizable según el CONTEXT. También pasan "los pacientes Medicare…" y las sedes, gracias a una lista corta de palabras que no son nombres.

**Se aplica en cuatro momentos:**
1. Sobre el **mensaje del usuario, siempre**, proponga el modelo o no.
2. Sobre el **texto propuesto**, antes de mostrarlo.
3. Sobre la **edición** del usuario.
4. En la **consolidación**, sobre la versión final y sobre todo lo ya guardado: lo que el validador marque pasa a `quarantined` y deja de llegar al modelo.

El punto 1 salió de la prueba real (§8): con el ejemplo de Johnson, el modelo no propuso nada, no marcó `user_requested_memory` y respondió *"Recibido, anoto la cancelación"*. Un requisito de cumplimiento no puede depender de que el modelo rellene bien un campo. Ahora cualquier mensaje con PHI queda excluido de la memoria, registrado como `blocked_phi` (solo categorías, sin texto) y con un aviso explícito al usuario. El prompt también prohíbe decir "anoto", "apunto" o "guardo".

## 6. Consolidación y limpieza

| Mecanismo | Regla | Por qué |
|---|---|---|
| Deduplicación | Similitud ≥ 0,85 con un recuerdo activo del mismo tipo y sede → no se crea fila, se renueva `last_confirmed_at` | Que dos personas confirmen lo mismo refuerza el hecho sin duplicarlo |
| Sustitución | Similitud ≥ 0,5, mismo tipo y sede → el anterior pasa a `superseded` con `superseded_by` | Una corrección no debe convivir con el dato que corrige |
| Tope | 50 hechos compartidos; 10 preferencias por persona; se expulsa (`evicted`) el confirmado hace más tiempo | La memoria no crece sin límite y siempre cabe entera en el contexto |
| Caducidad | 180 días sin reconfirmar → `expired`; se aplica también al leer | Horarios y protocolos de clínica cambian por trimestres; un dato de hace medio año sin confirmar es sospechoso |
| Re-verificación de PHI | En cada escritura, sobre todo lo activo → `quarantined` | El CONTEXT exige re-verificar al consolidar; además, si el validador mejora, lo antiguo se revisa solo |
| Propuestas pendientes | 30 minutos → `expired` | El silencio nunca es aprobación |

La similitud es Jaccard sobre palabras significativas, sin embeddings: con decenas de entradas basta, se puede razonar a mano y no envía la memoria a un proveedor externo. Nada se borra: todo cambio es de estado, con fecha.

## 7. Decisiones de diseño (preguntas del README)

**¿Qué tipos de memoria necesita HealthCore y por qué se descartaron los demás?** Memoria semántica curada (hechos aprobados) sobre un almacén relacional con auditoría. La episódica (historial de chat) no aporta: el ticket habla de hechos que otra persona no debería tener que repetir, no de retomar una conversación, y guardar historiales ampliaría la superficie de PHI. La vectorial y el grafo se descartan por volumen y por la forma de las relaciones (§1). El fine-tuning, porque no permite olvidar.

**¿Qué nunca debe entrar, lo pida quien lo pida?** Cualquier identificador de paciente e información clínica, bajo HIPAA y UK GDPR (§5). No hay excepción ni rol que la salte: ni un admin puede aprobar una propuesta con PHI, porque el validador corre antes de mostrarla y otra vez al consolidar.

**¿Cómo decide qué olvidar, y qué pasa con una pendiente sin respuesta?** Olvida por caducidad (180 días sin reconfirmar), por sustitución (una corrección posterior), por tope (lo menos confirmado recientemente) y por cuarentena de PHI. Un admin también puede retirar un recuerdo (`DELETE /agent/memory/{id}` → `revoked`). Una pendiente sin respuesta caduca a los 30 minutos, y si el usuario cambia de tema se descarta en ese mismo turno. Nunca se asume aprobación.

**¿Cómo se evita el envenenamiento?**
- **El agente solo propone lo que el usuario dijo y el usuario lo confirma:** no hay escritura sin una propuesta pendiente del propio usuario (`MemoryStore.commit` lo comprueba) ni sin decisión clasificada.
- **Precedencia:** el prompt presenta los recuerdos como "Nota aprobada por el staff", no como política. Si contradicen una política del RAG o un dato en vivo (incidencias, inventario), prevalecen estos y el agente menciona la discrepancia. Una memoria envenenada no puede cambiar una tarifa ni el estado de un ticket.
- **Al recibir un dato, el agente no lo da por cierto:** en la prueba real respondió *"No puedo confirmar ese cambio con la base de conocimiento… verifícalo con el responsable de la sede"*.
- **Trazabilidad y reversibilidad:** cada recuerdo tiene `approved_by_user_id` y `source_proposal_id`. Un admin ve toda la auditoría y puede revocar. Nada se borra, así que se puede reconstruir qué sabía el agente en cada momento.
- **Límites:** tipos cerrados (tres familias), textos de 500 caracteres como máximo, tope por ámbito y caducidad.
- **Residual reconocido:** un hecho compartido lo aprueba una sola persona. En producción, el aviso del README aplica: la memoria core pasaría por tickets de modificación revisados por un equipo dedicado. Aquí se omite a propósito, como indica el ticket.

**¿Por qué no hace falta multi-agente?** Porque la auto-evaluación ya ocurre **en la misma llamada** que redacta la respuesta: `generate_reply` pide un JSON con `answer` y `memory_proposal`, y el resto son reglas deterministas en código (validador de PHI, política de decisión, consolidación). Lo único que usa una segunda llamada es clasificar la decisión del usuario, y solo en los turnos con propuesta pendiente. Es un clasificador con salida estructurada, no un agente con herramientas ni estado propio. Un "agente evaluador" separado añadiría latencia y otro sitio donde puede fallar sin aportar nada que un campo de la salida y un `if` no hagan ya, y además haría más difícil auditar quién decidió qué.

## 8. Evidencia real (2026-09-28)

`services/api/.venv/bin/python scripts/record_memory_evidence.py`, con la API real (uvicorn), Supabase real (`healthcore-data`), Qdrant real y el modelo real del proxy de 4Geeks. Se usaron dos coordinadores de prueba (Ana y Luis) y un admin en una TinyDB temporal. Resultados completos en `docs/agent/memory-evidence/` (`turns.json`, `memory-visible.json`, `audit.json`).

> Modelo de generación: `madrid-spain/z-ai/glm-5.3-flash`, pasado como variable de entorno solo para esta prueba. El proxy devuelve `403 Model is blocked` para `gpt-5.6-luna`, el configurado en `.env`. `glm-5.3-flash` soporta el modo JSON y *function calling* (comprobado).

| # | Quién | Mensaje (resumen) | Resultado |
|---|---|---|---|
| 1 | Ana | Manchester: referidos primero por el coordinador | Responde sin darlo por verificado + **propone** (`clinic_operations`) |
| 2 | Ana | "Sí, guárdalo por favor." | `approve` 0,98 → **guardado** (`consolidation=created`); sin ejecutar el grafo |
| 3 | **Luis** | ¿Cómo van los referidos internos en Manchester? | **Usa el recuerdo de Ana** + la política de referidos del RAG |
| 4 | Ana | Austin: el no-show alto fue por un cierre de carretera | **Propone** (`incident_pattern`) |
| 5 | Ana | "No, no hace falta que lo recuerdes." | `reject` 0,98 → **no se guarda** |
| 6 | **Luis** | ¿Por qué hubo alerta de no-show en Austin? | "No tengo información suficiente…": **la memoria no cambió** |
| 7 | Ana | "El paciente Johnson canceló su cita de mañana, apúntalo." | **`blocked_phi`** (`patient_name`) + explicación HIPAA/UK GDPR; auditoría sin el nombre |
| 8 | Luis | ¿Tasa de no-show de esta semana? | Sin propuesta |
| 9 | Luis | "Gracias, con eso resuelvo mi reporte." | Sin propuesta |
| 10 | Ana | Preferencia del reporte de Diane Foster | **Propone** (`staff_preference`) |
| 11 | Ana | ¿Cargo por no-show en Texas? (cambio de tema) | `unrelated` → **descartada** + responde 50 USD con la excepción de Medicare/Medicaid |

**Ciclo aprobado (1-2-3):** Luis nunca contó el cambio de Manchester y lo recibe: *"En la clínica de Manchester, los referidos internos ahora pasan primero por el coordinador antes de llegar al especialista…"*. `GET /agent/memory` de Luis devuelve ese único recuerdo.

**Ciclo rechazado (4-5-6):** después del "no", Luis pregunta por Austin y el agente responde que no tiene información. En la auditoría queda la fila `rejected`, con `memory_id = null`.

**Auditoría final** (`GET /agent/memory/audit`, 4 filas de esta corrida):

| status | decision_label | confianza | tipo | PHI | nota |
|---|---|---|---|---|---|
| approved | approve | 0,98 | clinic_operations | — | consolidation=created |
| rejected | reject | 0,98 | incident_pattern | — | — |
| blocked_phi | — | — | — | patient_name | Bloqueada por el validador de PHI antes de mostrarse |
| discarded | unrelated | 0,95 | staff_preference | — | Cambio de tema |

Las filas de una primera grabación, que destapó el fallo del turno 7, se borraron antes de repetirla. Las tablas se habían creado en esa misma corrida y solo contenían esas 4 filas.

## 9. Tests

| Archivo | Qué fija |
|---|---|
| `tests/pipelines/test_agent_memory.py` (49) | Validador de PHI con los ejemplos literales del CONTEXT (5 que pasan, 15 que se bloquean), salida estructurada, política de decisión (la ambigüedad nunca aprueba), consolidación (duplicado, sustitución, otra sede, tope, caducidad, re-verificación de PHI, cuarentena), una pendiente por usuario, caducidad de la pendiente, privacidad de las preferencias, que la memoria **no** toque Qdrant ni `*_knowledge` (recorre los imports con `ast`) y el paso de la memoria por el grafo y el trace |
| `services/api/tests/test_agent_memory_api.py` (16) | Los ciclos completos por HTTP con dobles deterministas: aprobado y reflejado en otra consulta, rechazado, PHI (con y sin propuesta del modelo), no memorable, sin propuesta, ambiguo, cambio de tema, "sí + pregunta", edición, edición con PHI, una sola pendiente, Supabase caído y permisos de admin |

Se actualizaron a propósito los tests que fijaban el contrato anterior: la lista de nodos y aristas, las herramientas del planificador, `generate` → `generate_reply` y el bloque `memory` en la respuesta.

## 10. Residuales conocidos

- **`gpt-5.6-luna` bloqueado en el proxy.** `services/api/.env` sigue apuntando a él, así que sin cambiar `GENERATION_MODEL`, `/agent/query`, `/knowledge/query` y el planificador fallan (503 / fallback a solo RAG). La evidencia usó `glm-5.3-flash` por variable de entorno.
- **Los traces grabados de los evals del agente (`data/eval/agent-traces/`) son anteriores a este ticket.** Siguen pasando porque los campos nuevos del trace son aditivos, pero el prompt de generación cambió y el modelo original ya no está disponible. Hay que volver a grabarlos cuando se decida el modelo definitivo.
- **El validador de PHI es de reglas.** Un nombre de paciente sin la palabra "paciente" ni tratamiento ("Johnson canceló") no lo detecta la regla `patient_name` por sí sola. Lo mitigan que el modelo tiene prohibido proponer eso y que la propuesta debe ser un patrón operativo autocontenido. Un NER clínico sería la mejora natural.
- **La similitud de Jaccard** puede no reconocer como corrección un cambio redactado con palabras muy distintas. En ese caso convivirían dos notas hasta que caduque la vieja o un admin la retire.
