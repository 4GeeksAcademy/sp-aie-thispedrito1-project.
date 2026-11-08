# services/agent — agente LangGraph (RAG + tools + memoria + guardarraíles + chat)

Un único agente detrás de `POST /agent/query` y del chat WebSocket: el "asistente de compliance" de Claire Whitfield. Usa el RAG de `data/` (ver `data/CLAUDE.md`). Diseño en `docs/agent/` (`agent-design.md`, `agent-tools.md`, `agent-memory.md`, `agent-guardrails.md`) y `docs/websocket-chat.md`.

## Comandos (desde la raíz)

```bash
docker compose up -d qdrant && services/api/.venv/bin/python scripts/index_knowledge_base.py   # si no existe la colección
cp services/api/data/suppliers.db.json /tmp/agent-eval.db.json
SUPPLIERS_DB_PATH=/tmp/agent-eval.db.json services/api/.venv/bin/python scripts/seed_incidents.py
SUPPLIERS_DB_PATH=/tmp/agent-eval.db.json services/api/.venv/bin/python scripts/record_agent_traces.py [caso ...]
services/api/.venv/bin/python -m pytest tests/pipelines/test_agent_graph.py tests/pipelines/test_agent_tools.py \
  tests/pipelines/test_agent_mcp_client.py tests/pipelines/test_agent_evals.py tests/pipelines/test_agent_memory.py \
  tests/pipelines/test_agent_guardrails.py tests/pipelines/test_agent_chat_contract.py
services/api/.venv/bin/python scripts/run_guardrail_cases.py [--live]        # casos en data/eval/guardrail-cases.json
services/api/.venv/bin/python scripts/record_memory_evidence.py              # API en 8010, escribe en Supabase
```

`record_agent_traces.py` necesita la cadena completa: API en 8000 + `python -m mcps.healthcore` con el mismo `SUPPLIERS_DB_PATH` sembrado y la cuenta de servicio. Hace una llamada MCP de calentamiento (la primera supera los 5 s). Si un caso cae a `fallback` por un pico del proxy, regrabar solo ese. `record_memory_evidence.py`: vaciar antes la memoria de la corrida anterior.

## Mapa

- `graph.py`: `AgentState`, `AgentNodes` (dependencias inyectables), aristas (`route_after_receive`, `route_next_source`), `compile_agent_graph()` + `validate_structure()`. Nodos: `receive_question`, `reject_question`, `plan_sources`, `lookup_incident`, `check_inventory_stock`, `recall_memory`, `retrieve`, `no_information`, `tool_fallback`, `generate`.
- `planner.py`: function calling con `GENERATION_MODEL` (`PLANNER_TIMEOUT_S` 20 s, sin reintentos); ante cualquier fallo, plan = solo RAG.
- `tools/`: `base.py` (`ToolResult`, `run_tool` con timeout), `incidents.py` (por MCP), `inventory.py` (en proceso, solo lectura). `evidence.py`: datos en vivo → chunks para `rag.generate_answer()`.
- `tracing.py`: `run_agent()` (`stream_mode=["updates","custom"]`, `on_token`, `should_stop`), trace JSON en `AGENT_TRACE_DIR`. `router.py`: `/agent/query`, `/agent/memory*`, `/agent/guardrails/summary`.
- `mcp_client.py`: `MultiServerMCPClient` + `ClientCredentialsAuth` (httpx.Auth contra Logto).
- `memory/`: `models.py`, `store.py` (`MemoryStore`, única interfaz), `reply.py`, `decision.py`, `phi_guard.py`, `consolidation.py`, `conversation.py` (`handle_turn`).
- `guardrails/`: `patterns.py`, `input_guard.py`, `prompt.py`, `isolation.py`, `output_guard.py`, `general.py`, `monitor.py`.
- `streaming.py` + `chat/` (`models`, `events`, `hub`, `store`, `service`, `router`).

## Reglas que no hay que romper

**Grafo**
- Los nodos llaman a `rag.retrieve()` y `rag.generate_answer()`, nunca a `rag.query()`. `route_after_retrieve` no vuelve a mirar puntuaciones: el umbral vive solo en `retrieve()`.
- Sin contexto → `NO_INFORMATION_ANSWER` fijo, sin llamar al modelo.
- El grafo no toca la base de datos: la memoria entra como `memories` y sale como `memory_proposal`. Checkpoints `InMemorySaver` liberados al terminar.
- Se compila al importar el router: un grafo roto impide arrancar la API.
- Traces sin la pregunta (SHA-256 + longitud), chunks sin texto, errores con nodo y tipo pero sin mensaje. Log propio, no LangSmith.
- Los evals juzgan traces grabados: si cambian casos, documentos, prompt, umbral o grafo, regrabar (el eval de huella detecta un caso sin regrabar).

**Tools**
- Incidencias son DOS funciones para el modelo (`get_incident`, `search_incidents` con filtros obligatorios que admiten `null`). Juntas, el modelo inventaba valores. No volver a juntarlas.
- Orden fijo: tools en vivo → RAG. Si una tool falla, `tool_fallback` sin consultar el resto. Una tool nunca lanza: todo es `ToolResult.status` (`ok`/`not_found`/`unavailable`).
- Sin `title`/`description` de incidencias. Stock = total de la red.
- Incidencias solo por MCP (`via="mcp"` en el trace); un test con `ast` impide reintroducir el acceso en proceso. El agente solo tiene `incidents:read`.

**Memoria**
- Postgres (`agent_memories`, `agent_memory_proposals`), nunca Qdrant ni `healthcore_knowledge` (test con `ast`). Una sola propuesta `pending` por usuario (índice único parcial).
- Hechos operativos compartidos, preferencias privadas. Topes: 50 compartidos, 10 preferencias/usuario; caducidad 180 días; deduplicar ≥0,85, sustituir ≥0,5.
- El mensaje del usuario se valida SIEMPRE contra PHI, proponga el modelo o no.
- Ningún texto del usuario en la auditoría: solo SHA-256. `resolve_decision`: confianza < 0,75 → descartar; un `reject` dudoso es `discard`.
- Supabase caído → responde sin memoria y sin proponer.
- El planificador elige `recall_agent_memory` también cuando el usuario informa (si no, acabaría en `no_information`).

**Guardarraíles**
- Un BLOCK nunca llega al modelo (texto fijo). El prompt es la segunda capa.
- PHI sobre el texto original; el resto sobre el normalizado. El canario también en el original.
- Caso de paciente: un identificador directo basta; paciente concreto + edad o clínico también; sin paciente concreto, dos cuasi-identificadores. "Pacientes de 65 años con Medicare" y "un paciente de Manchester, ¿NHS?" deben pasar.
- Notas de memoria = contenido externo, saneadas como el RAG. `EMBEDDED_INSTRUCTION_RULES` solo en `isolation.sanitize_text`, nunca sobre el mensaje del usuario; `test_real_knowledge_base_policies_are_not_neutralized` debe seguir sin falsos positivos.
- Nada de texto en logs. Tests sin red con `offline_agent_harness` (sustituye `answer_general`, reinicia `MONITOR`/`BREACH_WINDOWS`/`LLM_LIMITER`).
- Dominio ampliado en el prompt (políticas HIPAA/UK GDPR, brechas, BAA/DPA) sin inventar documentos; lo que la KB no cubre se deriva a Compliance.

**Chat WebSocket**
- Streaming con el modo `custom` de LangGraph (el modelo se llama con el SDK `openai`, no LangChain). `should_stop` va en `configurable`, no en el estado.
- Abort real en dos puntos: `_stream_reply` antes de cada trozo (cierra la respuesta HTTP) y `run_agent` entre nodos.
- La puerta de tokens: publicar bajo `runtime.lock` solo si la generación es la actual; la interrupción la quita con el mismo candado antes de `generation_interrupted`; el hub entrega con `call_soon_threadsafe`.
- Parcial `interrupted` nunca sobrescrito; la respuesta nueva es otra fila. `handle_turn` no ofrece memoria de una respuesta cortada.
- Guardarraíl palabra a palabra (`check_output` sobre lo acumulado, `split_ready_text`).
- Mensaje con PHI → guardado y retransmitido sin texto (`redacted`). Auth por primer frame `auth` (o `?token=`). Solo el dueño escribe; un admin observa.
- Rehidratación: suscribirse antes de leer historial; `_pump` descarta tokens ya en el snapshot.
- Streaming no pide `response_format=json_object` (deepseek retiene toda la salida); `POST /agent/query` sí.

## Gotchas

- LangGraph 0.6.11: `compile()` no detecta nodos huérfanos ni callejones sin salida y descarta claves fuera del estado (por eso `validate_structure()` y `_checked`). `get_graph()` inventa aristas a END: validar sobre `builder.edges` + `builder.branches`. Si falla una arista condicional, el culpable está en `tasks[].error`.
- `phi_guard.insurance_number` exige límite de palabra y un dígito (antes "sección Cobertura" daba falso positivo).
- SQLite de tests devuelve fechas sin zona: `consolidation.as_utc`. `consolidation.sweep` no hace commit (lo hace `store.resolve`).
- `TestClient` abre un event loop por `websocket_connect` y al cerrar espera los hilos del turno; el test de reconexión abre el socket nuevo antes de cerrar el viejo. Dobles de modelo con 20 ms por palabra.
- `chat/service.py` importa `database` dentro de la función (los tests de la raíz no tienen `services/api` en el path).
- La espera larga del proveedor está dentro de `create()`; cerrar el stream desde otro hilo no corta.
- Ventana de brecha, monitor, hub de chat y rate limit viven en memoria del proceso: con varios workers, cada uno los suyos.
- `POST /knowledge/query` también pasa por los guardarraíles (`check_input` → `isolate_evidence` → `check_output`).
