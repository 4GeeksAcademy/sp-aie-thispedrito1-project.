# Chat de compliance en tiempo real (WebSocket)

Sistemas en Tiempo Real, Parte 2. Rama `feature/websocket-chat`, apilada sobre `feature/sse-notifications`, 2026-10-27/28.

Problema (RFI del área de Claire Whitfield, Compliance): el Compliance assistant respondía de golpe tras 5-20 s. Si contestaba sobre HIPAA cuando la pregunta era de UK GDPR, había que esperar a que terminase para corregirlo. Ahora la respuesta llega palabra a palabra por WebSocket y se puede interrumpir a mitad: la generación se aborta de verdad y la nueva entrada abre un turno nuevo.

El agente es el mismo de `POST /agent/query` (grafo LangGraph + memoria #MEM-092 + guardarraíles #SEC-114). No cambian su lógica, sus herramientas ni su memoria; solo el canal. `POST /agent/query` sigue funcionando igual.

## 1. Flujo

```
navegador (/compliance-chat)          API FastAPI (un proceso)                        hilo del turno
useChatSocket ── WS ──► chat_socket ── submit() ──► ChatService ── asyncio.to_thread ──► handle_turn (sin cambios)
   ▲                      │  _receive                 │                                  └ run_agent(on_token, should_stop)
   │                      │  _pump ◄── cola ◄── ChatHub  "chat.<session_id>" ◄── publish ◄─── graph.stream(["updates","custom"])
   └──────── eventos ─────┘                                                                   └ nodo generate → generate_reply(stream=True)
```

- **Productor** (`services/agent/chat/service.py`): un turno del agente por mensaje, en un hilo aparte porque el grafo es síncrono. Publica en el canal de su sesión sin saber quién escucha.
- **Pub/sub** (`hub.py`, BRIEFING §5): un canal `chat.<session_id>` en memoria con una cola por WebSocket conectado. Si no hay nadie conectado, la generación sigue y el siguiente snapshot la recoge.
- **Consumidores** (`router.py`): cada socket tiene dos tareas. `_pump` vacía su cola hacia el navegador y es el único que escribe en el socket. `_receive` lee lo que manda el navegador.

## 2. Cómo llegan los tokens (modo `custom` de LangGraph)

El proyecto llama al modelo con el SDK `openai`, no con LangChain, así que el modo `messages` de LangGraph no ve los tokens. Se usa el modo **`custom`**:

1. `run_agent(..., on_token=..., should_stop=...)` recorre el grafo con `stream_mode=["updates", "custom"]` y pone `should_stop` en `configurable` (no en el estado: no viaja en los checkpoints, comprobado).
2. El nodo `generate` (`graph._default_generate`) llama a `streaming.current_answer_stream()`. Si hay consumidor, `reply.generate_reply(..., answer_stream=...)` pide la misma respuesta con `stream=True`, con el mismo prompt y el mismo `parse_reply` final.
3. El modelo responde en JSON (`{"answer": …, "memory_proposal": …}`). `AnswerDeltaExtractor` saca de los trozos solo el texto de `answer`, ya descodificado, y lo emite con `get_stream_writer()`.

Comprobado en LangGraph 0.6.11 con una sonda: los eventos `custom` llegan en tiempo real (0,31 s, 0,61 s, 0,91 s), no acumulados al final del nodo.

Los textos que no salen del modelo en streaming (rechazos fijos del guardarraíl, "sin información", decisión de memoria, modo general) se envían al terminar el turno, troceados por palabras. El contrato es el mismo para la pantalla.

## 3. Abort real (no ignorar tokens)

Al llegar `interrupt_requested` (o un `user_message` con una respuesta en curso):

1. Bajo `runtime.lock`, se activa el `stop` de la generación y se quita como generación actual. Desde ese instante la "puerta" de los tokens está cerrada: `_on_delta` solo publica si su generación sigue siendo la actual.
2. Dentro del nodo `generate`, `_stream_reply` comprueba `stop` antes de cada trozo del proveedor. Si está activo, sale con `GenerationAborted` y el `finally` **cierra la respuesta HTTP**, con lo que el proveedor deja de generar. Si la interrupción llega antes (planificando o consultando el RAG), `run_agent` corta entre nodos y `generate` ni empieza.
3. Se guarda el texto ya mostrado como mensaje `interrupted` (nunca se sobrescribe) y la sesión pasa a `interrupted`.
4. Se publica `generation_interrupted`. El hub entrega en orden de publicación (siempre con `call_soon_threadsafe`), así que detrás no puede llegar ningún `token_chunk` de esa generación.
5. Se guarda y publica el `user_message` con `new_input` y arranca un **turno nuevo** con otro `message_id`.

`handle_turn` no ofrece memoria de una respuesta interrumpida: si la corrida acaba justo cuando llega la orden, `streaming_run` la convierte en `GenerationAborted`.

Sin `interrupt()` de LangGraph: el HITL pausa un grafo para reanudarlo y aquí no hay nada que reanudar. El abort termina la corrida y el siguiente turno es una corrida nueva.

## 4. Guardarraíl de salida palabra a palabra

Decisión del usuario: "revisar antes de cada palabra". Antes, `check_output` revisaba la respuesta completa; con streaming, el texto se ve antes de que exista la respuesta completa. Ahora ningún trozo se publica sin que `check_output` haya revisado **todo lo acumulado más ese trozo**. Se libera por palabras completas (`split_ready_text`, Learn by Doing delegado), porque media palabra (`ana.lop`) todavía no se puede juzgar. Si salta, se aborta la generación y el mensaje se sustituye por el texto fijo del guardarraíl (`generation_blocked`), y la activación queda en `MONITOR`. El `check_output` de `handle_turn` sobre la respuesta completa sigue como segunda capa.

## 5. Contrato de eventos (BRIEFING §4)

Todo frame es `{"event", "data"}`. Lo define `services/agent/chat/events.py` y lo refleja `uis/backoffice/types/chat.ts`.

| Dirección | Evento | `data` |
|---|---|---|
| C → S | `auth` | `token` (primer frame, si no vino `?token=`) |
| C → S | `user_message` | `session_id`, `text` |
| C → S | `interrupt_requested` | `session_id`, `new_input` |
| S → C | `session_snapshot` | `session_id`, `status`, `messages[]`, `in_progress` |
| S → C | `user_message` | `session_id`, `message_id`, `text` (null si traía datos de paciente), `status` |
| S → C | `token_chunk` | `session_id`, `message_id`, `token`, `sequence` |
| S → C | `generation_interrupted` | `session_id`, `message_id`, `status: "interrupted"` |
| S → C | `generation_completed` | `session_id`, `message_id`, `status`, `text` (versión final), `outcome` |
| S → C | `generation_blocked` / `generation_failed` | `session_id`, `message_id`, `status`, `text` |
| S → C | `chat_error` | `session_id`, `code`, `message` |

Añadidos al BRIEFING, solo donde no llega: `message_id` en `token_chunk` (para saber a qué burbuja va), `text` en `generation_completed` (la versión persistida, que puede añadir la nota de memoria), `in_progress` en el snapshot y los tres eventos de la última fila. Nada del contrato SSE de RFPs.

**ChatSession** (BRIEFING §3): `session_id`, `agent_id = compliance_assistant`, `user_id`, `status` (`active`/`interrupted`/`closed`), `created_at`. El `session_id` va en la URL del socket. No se usa como `thread_id` de LangGraph, porque cada corrida del agente sigue siendo independiente y borra sus checkpoints al terminar (Parte 1 del agente). El hilo se rehidrata desde `chat_messages`.

## 6. Autenticación y acceso

- Mismo JWT que el backoffice (`security.user_from_token`, compartido con `get_current_user`).
- `?token=`: se valida **antes** de aceptar; si no vale, el handshake se rechaza (HTTP 403). Inconveniente: uvicorn escribe la URL completa, token incluido, en su log de acceso.
- Primer frame `auth` (lo que usa el backoffice): el socket se acepta, pero no sale ningún evento de chat hasta recibir un token válido. Sin token en 5 s, o con uno inválido, se cierra con 4401.
- Cierres: 4401 sin autenticar (lleva al login), 4403 sesión ajena, 4404 inexistente, 4409 cerrada, 4429 cola llena (reconecta), 1011 sin base de datos.
- Solo el dueño escribe. Un admin puede conectarse como supervisor de solo lectura (`chat_error` `read_only` si escribe).

## 7. Reconexión y rehidratación

- Navegador (`hooks/useChatSocket.ts`): backoff 1 s, 2 s, 4 s… hasta 30 s ±20 % (`reconnectDelay` de la Parte 1), reintento inmediato con el evento `online`, mismo `session_id`. "En directo" (y entrada habilitada) solo tras el `session_snapshot`: primero se restaura el contexto y después se aceptan tokens nuevos.
- Servidor: al conectar, primero se suscribe al canal y después lee historial + `in_progress` (texto ya publicado y último `sequence`). `_pump` descarta los tokens con `sequence` ya incluido en el snapshot. Si reconectas a mitad de una respuesta, ves lo ya escrito y sigue escribiéndose.
- La pantalla abre la última sesión no cerrada (`openLatestChatSession`), así que recargar la página tampoco pierde el hilo.
- Reductor idempotente (`applyChatEvent`): todo por `message_id` y los tokens por `sequence`. Un reenvío no duplica nada.

## 8. Privacidad (HIPAA / UK GDPR)

- Solo personal interno (BRIEFING §1); la pantalla lo recuerda.
- Un mensaje del usuario con datos de paciente (mismos detectores que el guardarraíl de entrada y la memoria) se guarda **sin texto** (`redacted`), y su eco a los sockets también va sin texto. El guardarraíl de entrada responde con su rechazo fijo, como en `POST /agent/query`.
- Logs: sesión, `message_id` y número de trozos; nunca el texto.

## 9. Gotchas reales

- **`TestClient` de Starlette: cada `websocket_connect` tiene su propio event loop** y al cerrarse espera a los hilos lanzados desde él. Cerrar el socket que lanzó un turno bloqueaba hasta que el turno acababa. Con uvicorn hay un solo loop. El test de reconexión a mitad abre el socket nuevo antes de cerrar el viejo.
- **Doble del modelo sin pausa**: emitía todas las palabras antes de que la orden de parar (que viaja por otro hilo) le llegase. Se añadió una pausa realista de 20 ms por palabra.
- **El proxy `/api-proxy` de Next no garantiza WebSocket** (los docs de Next 16 no lo cubren): el navegador conecta directo a la API (`NEXT_PUBLIC_CHAT_WS_URL`, por defecto `ws://<host>:8000`).

## 10. Verificación

Automática:
- `services/api/tests/test_agent_chat.py` (14, socket real, grafo real con modelo doble): JWT por query y por primer frame, socket ligado a la sesión del dueño, streaming con `sequence` consecutivo, **interrupción a mitad** (el modelo ve la orden y para, 0 tokens de la generación original tras el aviso, parcial `interrupted` con el texto mostrado, turno nuevo con `new_input`), **rehidratación** al reconectar (también a mitad de respuesta, sin repetir tokens), supervisor con los mismos eventos y una sola llamada al agente, guardarraíl palabra a palabra, PHI sin eco ni guardado, sesión cerrada.
- `tests/pipelines/test_agent_chat_contract.py` (26): contrato de cada evento, validación de lo que manda el cliente, extractor de `answer` partiendo el JSON en todas las posiciones, y abort que **cierra la conexión con el proveedor sin leer el resto**.
- `uis/backoffice/__tests__/chat.test.ts` + `useChatSocket.test.tsx` (16): reductor (escritura en vivo, parcial interrumpido, token tardío ignorado, rehidratación) y hook (auth por primer frame, backoff 1 s → 2 s, mismo `session_id`, 4401/4409).

Manual contra el modelo real (2026-10-28, deepseek-v4-flash por el proxy de 4Geeks, Supabase y Qdrant reales, Chrome; usuarios en una TinyDB temporal con tokens de `create_access_token`; filas de chat borradas después):

| Prueba | Resultado |
|---|---|
| Conexión y auth | "En directo" tras el snapshot. El log de uvicorn muestra `WebSocket /agent/chat/ws/chat_…` **sin token** (primer frame `auth`). |
| Escritura en vivo | Tras 64,5 s de espera (planificador agotó sus 20 s + reintento del proxy), la burbuja creció en 11 pasos (93 → 656 caracteres) durante 1,4 s. |
| Interrupción a mitad | Un script de la página pulsó "Interrumpir y enviar" al aparecer el primer texto. Trace `aborted: … -> generate`, log `interrupted after 32 chunks`; burbuja `interrupted` con su texto parcial, y turno nuevo con "espera, mi pregunta era sobre UK GDPR, no HIPAA". |
| Interrumpir una respuesta no streameada | En modo general (regulación fuera de la KB) el texto se publica entero de una vez: la interrupción llegó tarde y se trató como mensaje normal. Correcto: no había nada que abortar. |
| Caída de la API (`kill -9`) | "Sin conexión · reintento en 4 s" (backoff 1 → 2 → 4 s), envío desactivado, mensajes visibles. Al volver la API: "En directo" y los 10 mensajes rehidratados con sus estados. |
| Recarga de la página | Misma conversación desde Supabase (`openLatestChatSession` + snapshot), con la respuesta "Interrumpida" en su sitio. |

Hallazgos del proveedor (medidos, ver §13).

## 13. El proveedor y el streaming (medido el 2026-10-28)

- **Modo JSON retiene la salida**: con `response_format=json_object` llegaron 93 trozos en 3 instantes distintos, todo en 0,1 s al final. Sin él, 137 trozos en 21 instantes a lo largo de 1,9 s. Por eso `_stream_reply` no pide `json_object` (el prompt sigue exigiendo el JSON, y `parse_reply` cubre una salida que no lo sea). `POST /agent/query` no cambia. En 3 preguntas reales sin modo JSON, la salida fue JSON válido las 3 veces.
- **El reparto varía por petición** (OpenRouter enruta a servidores distintos): la misma consulta ha llegado repartida en 8,3 s y otras veces en una ráfaga de 0,03 s tras 10-18 s de "pensar". Cuando llega en ráfaga, el chat emite igualmente decenas de `token_chunk`, pero el ojo lo ve casi de golpe.
- **La espera de 7-18 s ocurre dentro de `create()`**, antes de que exista el stream. Y cerrar el stream desde otro hilo NO funciona con el cliente síncrono de httpx (probado: siguió leyendo 40 trozos y se colgó hasta el timeout de 30 s). Por eso el abort se comprueba en el propio hilo, entre trozos y entre nodos. La pantalla corta al instante (puerta de tokens) y la conexión se cierra en cuanto `create()` devuelve el control. Para cortar también durante esa espera haría falta el cliente asíncrono con cancelación de tareas: queda como mejora.
- **glm-5.3-flash ya no está permitido** para el equipo (403 "team not allowed to access model").

## 11. Preguntas de diseño (para el PR)

**¿Por qué WebSockets y no el SSE de la Parte 1?** SSE es de un solo sentido: el servidor habla y el navegador escucha. Aquí el navegador tiene que mandar una interrupción **mientras** el servidor sigue enviando tokens de la misma respuesta. Con SSE, eso sería una segunda petición HTTP sin relación con el stream abierto, que tendría que encontrar desde otro proceso la generación en curso para cortarla. El requisito que obliga a un canal bidireccional es "interrumpir a mitad y redirigir": `interrupt_requested` y `token_chunk` conviven en el mismo socket, en sentidos opuestos y a la vez.

**Varios clientes en la misma sesión (supervisor), sin duplicar llamadas al agente.** El productor y los consumidores están separados por el hub. El turno del agente se lanza una vez por mensaje (`ChatService.submit`, con un candado por sesión) y publica en `chat.<session_id>`. Cada socket es una suscripción con su propia cola. Un supervisor (admin) es otra suscripción al mismo canal: recibe los mismos eventos en el mismo orden y no puede escribir. El test `test_supervisor_receives_the_same_events_without_a_second_agent_run` comprueba que los dos ven lo mismo y que el modelo se llamó una sola vez. En memoria y en un proceso (el BRIEFING lo permite); con varios workers de uvicorn haría falta Redis como en la Parte 1.

**Abort del stream vs `interrupt()` de LangGraph.** No se usa `interrupt()`. El HITL pausa el grafo en un checkpoint para reanudarlo después con una decisión humana, como en la aprobación de RFPs del Hito 9. Aquí no hay nada que reanudar: la respuesta equivocada se descarta. El abort es una señal (`should_stop` en `configurable`) que el nodo `generate` consulta entre trozos del proveedor, cerrando la conexión HTTP, y que `run_agent` consulta entre nodos. El mensaje parcial se guarda tal como se mostró con `status = interrupted` y no se toca más. El siguiente turno es una corrida nueva del grafo con `new_input`, con su propio `message_id`, y queda enlazado a su pregunta por `replies_to`.

## 12. Mapa del código

- `services/agent/streaming.py`: `AnswerStream`, `current_answer_stream()`, `GenerationAborted`.
- `services/agent/memory/reply.py`: `AnswerDeltaExtractor`, `_stream_reply` (sin `json_object`, §13).
- `services/agent/graph.py` (`_default_generate`) y `services/agent/tracing.py` (`run_agent` con `on_token`/`should_stop`, trace `aborted`).
- `services/agent/chat/`: `models.py` (`chat_sessions`, `chat_messages`), `events.py` (contrato), `hub.py` (pub/sub), `store.py` (repositorio + redacción), `service.py` (productor), `router.py` (HTTP + WS), `schemas.py`.
- `services/api/security.py`: `user_from_token`. `services/api/main.py`: router + `stop_all` al apagar.
- `uis/backoffice`: `types/chat.ts`, `services/chatApi.ts`, `hooks/useChatSocket.ts`, `app/compliance-chat/` (página + `chat.css`), enlace "Chat de compliance" en `app/layout.tsx`, `NEXT_PUBLIC_CHAT_WS_URL` en `.env.local.example`.
