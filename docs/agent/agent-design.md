# Agente de soporte con LangGraph, Parte 1: diseño

Rama `feature/langgraph-agent-base`, apilada sobre `feature/rag-knowledge-base`, 2026-09-24. Migra el asistente de políticas del Hito 7 a un grafo de LangGraph con estado, nodos, aristas condicionales, checkpointing y trace. Lo que recupera y lo que responde no cambia: `retrieve()` y `generate_answer()` siguen en `data/pipelines/rag.py` y el grafo las llama sin copiarlas.

## Grafo

```
START → receive_question ─┬─ (pregunta vacía) ─────────────→ reject_question → END
                          └─ retrieve ─┬─ (ningún chunk ≥ 0.38) → no_information → END
                                       └─ generate ────────────────────────────→ END
```

| Nodo | Única responsabilidad | Llama a |
|---|---|---|
| `receive_question` | Normalizar la pregunta y limpiar el estado | — |
| `reject_question` | Cortar una pregunta vacía (`outcome=invalid_question`) | — |
| `retrieve` | Recuperar el contexto | `rag.retrieve(question, k=5, min_score=get_min_score())` |
| `no_information` | Responder con honestidad sin contexto, con texto fijo y sin llamar al modelo | — |
| `generate` | Redactar la respuesta con el contexto que dejó `retrieve` | `rag.generate_answer(question, context)` |

Ningún nodo llama a `rag.query()`. Hay un test que lo sustituye por una función que lanza un error y recorre los tres caminos.

**Aristas:** `route_after_receive` y `route_after_retrieve` son funciones que leen el estado y devuelven el nombre del siguiente nodo. Sus destinos posibles se declaran con `path_map`. `route_after_retrieve` no vuelve a mirar las puntuaciones, porque el umbral vive solo en `retrieve()`.

**Cambio respecto al Hito 7:** sin contexto, `/knowledge/query` sigue pidiendo al modelo que diga que no sabe. `/agent/query` responde con un texto fijo (`NO_INFORMATION_ANSWER`), como pide el ticket ("no forzar la generación sobre contexto vacío"). Así la respuesta es determinista, más barata y no puede inventar nada.

## Estado

`AgentState` tiene cuatro claves: `question`, `context`, `answer` y `outcome`. No lleva historial: cada consulta del coordinador es independiente, y arrastrarlo solo ampliaría lo que se guarda en cada checkpoint, incluidos posibles datos de pacientes. Un nodo que escriba una clave ajena lanza `AgentStateError` con su nombre, porque LangGraph 0.6 la descarta en silencio (comprobado).

## Compilación

`compile_agent_graph()` se ejecuta al importar `services/agent/router.py`, es decir, al arrancar la API. Falla con `AgentGraphError` en estos casos:

- una arista hacia un nodo inexistente o un grafo sin entrada (lo detecta el propio `compile()`);
- un nodo sin conexión desde START, un nodo que nunca llega a END o una arista condicional sin `path_map`. Estos tres **no** los detecta LangGraph 0.6 (comprobado), así que los cubre nuestra validación.

La validación se hace sobre las aristas **declaradas** (`builder.edges` + `builder.branches`), no sobre `get_graph()`. El dibujo añade por su cuenta una arista a END en los nodos sin salida y esconde el callejón sin salida.

## Checkpointing

`InMemorySaver`, con un `thread_id` por corrida (igual al `trace_id`). Deja un checkpoint por transición: entrada + START + uno por nodo. Los tests comprueban que la lista del trace coincide con `get_state_history()` y que una corrida pausada antes de `generate` (`interrupt_before`) se reanuda sin volver a recuperar. En la API, los checkpoints se liberan al terminar (`keep_checkpoints=False`) para no acumular una corrida por petición en RAM, y sus ids quedan en el trace. No hay base de datos nueva: el registro duradero es el trace.

## Trace

`run_agent()` recorre el grafo con `stream_mode="updates"` y escribe `<AGENT_TRACE_DIR>/<trace_id>.json`, por defecto `data/traces/agent/` (en `.gitignore`). El trace incluye `steps` (nodo, duración, salida), `node_sequence`, `checkpoints`, `outcome`, `answer`, `status` y `error`. El orden de los pasos es el de ejecución real.

- **Privacidad (decisión del usuario):** la pregunta solo aparece como SHA-256 + longitud. De los chunks se guardan la fuente, la sección, el índice y la puntuación, pero no el texto. En los errores se guarda el nodo y el tipo de excepción, nunca el mensaje. La huella no es una anonimización fuerte: una pregunta corta se puede adivinar probando candidatos.
- **Fallo de un nodo:** el trace se escribe con `status=failed` y el nodo culpable, que se lee de `tasks[].error` del último checkpoint (si falla una arista condicional, `next` queda vacío y el error aparece en el nodo que la precede). Después el endpoint responde con un 503 limpio.
- Si lo que falla es escribir el trace, se registra en el log y la respuesta se devuelve igual.
- Se eligió un log JSON propio, no LangSmith (decisión del usuario): no hace falta cuenta y las preguntas no salen a un servicio externo.

## Endpoint

`POST /agent/query` (autenticado): `{question}` → `{answer, trace_id, outcome}`. Convive con `/knowledge/query` y la pantalla "Asistente" no cambia (decisión del usuario). Errores: 422 si la pregunta está en blanco, 503 "not configured" ante `RagConfigError` y 503 "temporarily unavailable" ante cualquier otro fallo, nunca con detalles internos.

## Evals

`data/eval/agent-eval-cases.json` tiene 5 casos. `scripts/record_agent_traces.py` los ejecuta una vez contra Qdrant y el modelo reales y guarda `data/eval/agent-traces/<id>.json`, que se versiona. `tests/pipelines/test_agent_evals.py` evalúa esos traces sin volver a llamar al modelo: huella de la pregunta, coherencia con los checkpoints, recorrido y orden, que `generate` solo reciba contexto no vacío y el anclaje de la respuesta.

Resultado (2026-09-24): **26/26**, detalle en `data/eval/agent_evals_result.txt`.

| Caso | Recorrido | Anclaje verificado |
|---|---|---|
| `no-show-private-us` | receive → retrieve → generate | recupera `appointment-policy`, responde "50 USD" |
| `no-show-medicare` | receive → retrieve → generate | "no se les cobra" + "historial" |
| `unlisted-insurance` | receive → retrieve → generate | recupera `insurance-coverage`, menciona facturación y Tom Callahan |
| `off-topic` | receive → retrieve → no_information | `generate` no se ejecuta |
| `blank-question` | receive → reject_question | `retrieve` no se ejecuta |

**Lo que mostró el trace:** la primera corrida tardó 66 s, de los cuales 62,5 s se fueron en `generate` y 3,5 s en `retrieve`. Confirma que la latencia rara que se detectó en el Hito 7 viene del proveedor del modelo (timeout de 30 s + reintento del SDK), no de Qdrant. Queda sin corregir.

Hay que volver a grabar si cambian los casos, los documentos, el prompt, el umbral o el grafo. El eval de la huella falla si una pregunta cambia y no se vuelve a grabar.
