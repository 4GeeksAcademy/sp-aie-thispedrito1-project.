# Diseño RAG — Base de conocimiento de HealthCore (Hito 7)

Asistente para los 8 coordinadores de pacientes de Priya Nair (Head of Patient Experience). El coordinador pregunta en lenguaje natural en el mostrador o al teléfono ("¿cobran cargo por cancelación con 12 horas de anticipación?"). El sistema busca en las políticas internas y un modelo redacta la respuesta **como lo haría el mejor vendedor de servicios de la clínica**: claro, empático y sin inventar coberturas ni políticas.

Solo trabaja con políticas y procedimientos. Ningún documento ni respuesta contiene datos de pacientes (HIPAA / UK GDPR, CONTEXT §6).

## 1. Proceso RAG de extremo a extremo

```
docs/company-knowledge-base/*.es.md   (4 documentos del CONTEXT, copia literal)
        │
        │  INDEXACIÓN — scripts/index_knowledge_base.py → setup()   [data/process/rag.py]
        ▼
  chunk_document()  →  14 chunks semánticos + metadatos
        │
  embed(título + sección + cuerpo)   ← EMBEDDING_MODEL
        │
  Qdrant: colección `healthcore_knowledge` (se borra y se vuelve a crear)
        ─────────────────────────────────────────────────────────────
        │  CONSULTA — POST /knowledge/query → query()   [data/pipelines/rag.py]
        ▼
  retrieve(pregunta, k=5, min_score)
     embed(pregunta) → Qdrant top-5 → descarta score < min_score  (0 a 5 chunks)
        │
  generate_answer(pregunta, contexto)
     prompt de sistema (voz + veracidad + reglas de negocio)
     + bloque CONTEXTO numerado con documento y sección
     → GENERATION_MODEL
        │
        ▼
  {"answer": "..."}  → pantalla /knowledge del backoffice
```

Responsabilidades, cada una en su propia función:

| Función | Archivo | Hace | No hace |
|---|---|---|---|
| `setup()` | `data/process/rag.py` | Lee, trocea, embebe y (re)crea la colección | Buscar ni generar |
| `embed(text)` | `data/process/rag.py` | Texto → vector con el modelo de embeddings | Usar el modelo de chat |
| `retrieve(query, *, k, min_score)` | `data/pipelines/rag.py` | Top-k de Qdrant filtrado por umbral. Devuelve payloads planos + `score` | Devolver objetos del SDK |
| `generate_answer(question, context)` | `data/pipelines/rag.py` | Arma el prompt y llama al modelo de generación | Recuperar |
| `query(question)` | `data/pipelines/rag.py` | `retrieve()` + `generate_answer()` | Nada más: es la composición |

`generate_answer()` recibe el contexto ya recuperado. Así, el agente LangGraph del hito siguiente puede llamar a `retrieve()` y a `generate_answer()` como pasos separados, sin recuperar dos veces.

El endpoint (`services/knowledge/router.py`) solo autentica, valida y traduce errores. Llama a `query()` y devuelve `{"answer": ...}`. Nunca devuelve chunks, fuentes en bruto ni puntuaciones: los fija `services/api/tests/test_knowledge.py`, y `test_serialization.py` recorre el OpenAPI entero. Exige sesión iniciada (`get_current_user`), igual que el resto del backoffice: los coordinadores son personal interno, y así nadie externo consume la cuota del modelo.

**Sin contexto también responde el modelo.** Si ningún chunk supera `min_score`, `generate_answer()` recibe un contexto vacío con el marcador "ninguno: ningún fragmento… superó el umbral". El prompt le ordena decir explícitamente que no hay información suficiente y no suponer nada. Se eligió así, en vez de devolver un texto fijo, porque el ticket exige que la respuesta la genere siempre un modelo.

**Errores.** Si falta configuración (`RagConfigError`) o fallan Qdrant o el proveedor, el endpoint responde `503` con un mensaje genérico en inglés, como el resto de la API. La pantalla lo traduce a "El asistente no está disponible en este momento…" y ofrece "Reintentar". Una respuesta vacía del modelo es un error (`RuntimeError`), nunca un string vacío que la UI confundiría con una respuesta.

**Privacidad en logs.** `retrieve()` registra en el servidor qué chunks pasaron el umbral y con qué puntuación, pero **nunca la pregunta**: el coordinador podría escribir datos de un paciente pese al aviso de la pantalla. El router solo registra el tipo de excepción.

## 2. Estrategia de chunking

**Por unidad semántica, sin tamaño fijo.** Los cuatro documentos son cortos (~1 KB) y no tienen subtítulos: solo un título `#` y bloques separados por líneas en blanco. Cortar por encabezados daría un chunk por documento. Cortar por tamaño fijo partiría listas por la mitad. Por ejemplo, separaría "cargo de 50 USD" de "Medicare o Medicaid: no se les cobra", que es precisamente la excepción que el CONTEXT exige respetar literalmente.

Reglas de `chunk_document()`:

1. Cada bloque separado por una línea en blanco es una unidad candidata. Las líneas cortadas a mano (continuaciones con sangría) se reúnen con su viñeta o frase.
2. **Bloque con etiqueta y lista** ("Política de cancelación:" + viñetas): un chunk cuya `section` es la etiqueta. Cada viñeta viaja con la condición que la encabeza.
3. **Etiqueta en línea** ("Recordatorios automáticos: el sistema envía…"): la etiqueta es la `section`.
4. **Frase de entrada** que termina en ":" y va seguida de una lista ("Todo paciente nuevo debe completar antes de su primera cita:" + pasos numerados): se une a esa lista y le da nombre a la sección. Sin ella, "1. Formulario de historial médico" no diría *cuándo* hay que completarlo. Si no la sigue una lista (la introducción del documento de seguros), se antepone al bloque siguiente sin cambiar su sección.
5. **Párrafo sin etiqueta**: una regla completa, en su propio chunk, con el título del documento como `section`.

Resultado con los documentos actuales (cumple "al menos 3 chunks por documento", CONTEXT §5, que `load_chunks()` comprueba y hace fallar si no se cumple):

| `source_document` | Chunks | Secciones |
|---|---|---|
| `insurance-coverage` | 3 | Estados Unidos (Texas, Florida, Georgia) · Reino Unido (Londres y Manchester) · regla "no confirmar seguros no listados" |
| `appointment-policy` | 4 | Reserva de citas · Política de cancelación · Recordatorios automáticos · regla de 3 no-shows |
| `referral-process` | 4 | Proceso estándar (4 pasos) · Tiempo objetivo · escalado a los 5 días hábiles · referencias fuera de la red |
| `new-patient-checklist` | 3 | Requisitos antes de la primera cita · documentos que traer · historial no completado |

Tamaño: entre 141 y 554 caracteres por chunk. El más largo es el proceso de referencia de 4 pasos, que se mantiene entero a propósito.

**Payload** (CONTEXT §3 + `text`): `company="healthcore"`, `source_document`, `section`, `language="es"`, `chunk_index` (posición dentro de su documento) y `text` (el cuerpo que se pega en el prompt).

**Idempotencia: limpiar y recargar, con IDs deterministas.** `setup()` borra y vuelve a crear la colección, y cada punto lleva un `uuid5` de `source_document:chunk_index`. Volver a ejecutarlo nunca duplica puntos, y un chunk que desaparece del documento tampoco sobrevive (con solo IDs deterministas sí sobreviviría). Los vectores se calculan **antes** de tocar Qdrant: si el proveedor de embeddings falla, la colección anterior sigue intacta. Lo fija `test_setup_does_not_wipe_the_collection_when_embedding_fails`.

## 3. Prácticas de embeddings

| | Valor |
|---|---|
| Modelo de embeddings (`EMBEDDING_MODEL`) | `madrid-spain/openrouter/perplexity/pplx-embed-v1-0.6b` (el único de embeddings que ofrece el proxy de 4Geeks) |
| Modelo de generación (`GENERATION_MODEL`) | `madrid-spain/openai/gpt-5.6-luna` |
| Proveedor | Modelos proporcionados por 4Geeks: proxy LiteLLM en `llm.4geeks.ai`, compatible con OpenAI (`LLM_BASE_URL`, SDK `openai`) |
| Dimensión del vector | 1024 (la da el modelo; `setup()` la lee del primer vector y rechaza tamaños mezclados) |
| Distancia en Qdrant | Coseno (`Distance.COSINE`) |
| `min_score` | **0.38** (`DEFAULT_MIN_SCORE`, sobrescribible con `RAG_MIN_SCORE`) |

- **Dos modelos, siempre distintos.** `get_embedding_model()` y `get_generation_model()` lanzan `RagConfigError` si ambas variables tienen el mismo ID. Hay test para las dos.
- **Una sola `embed()`** para indexar y para consultar, con el mismo preprocesado: colapsar espacios y saltos de línea. No se cambian mayúsculas ni se quitan acentos: los modelos de embeddings modernos ya los manejan, y cambiar el texto solo en un lado haría incomparables pregunta y chunk.
- **Contexto en el texto embebido.** Al indexar se embebe `título — sección\ncuerpo`, pero el payload `text` guarda solo el cuerpo. Así, "Ningún coordinador debe confirmar cobertura…" se encuentra al preguntar por *seguros* aunque la frase no lo diga, y el prompt no repite el título en cada fragmento.
- **Umbral en Python, a la vista.** `retrieve()` pide k=5 a Qdrant y filtra con `score >= min_score` en el código, no con `score_threshold` de Qdrant. Así se puede testear con un cliente simulado: devuelve menos de k, o cero, cuando nada lo supera.
- **Por qué `gpt-5.6-luna` para generar:** el proxy ofrece 4 modelos de chat y los 4 funcionaron en la prueba. Se eligió uno de los dos más rápidos (1,4 s en una frase, frente a 3,7 s de `deepseek-v4-flash` y `mimo-v2.5`), que acepta `temperature`, de una familia que sigue bien el prompt de sistema. `glm-5.3-flash` era igual de rápido. Cambiar de modelo es cambiar una variable de entorno.
- **Cómo se afina `min_score`:** `scripts/evaluate_rag_retrieval.py` compara la puntuación del chunk correcto de cada pregunta de prueba con la mejor puntuación de 4 preguntas fuera de tema (cafetería, impresora, tiempo, wifi). El umbral debe quedar por encima de las fuera de tema y por debajo de las correctas, y el Recall@3 con umbral no puede bajar del objetivo. Se puede sobreescribir con `RAG_MIN_SCORE` sin tocar código.

## 4. Prompt de generación

Tres capas en el mensaje de sistema (`data/pipelines/rag.py`):

1. **`ASSISTANT_ROLE`** — audiencia y voz del CONTEXT: coordinadores de 12 clínicas (EE. UU. y R. U.), "el mejor vendedor de servicios de la clínica", frases que se puedan repetir al paciente, en español.
2. **`GROUNDING_RULES`** — usar exclusivamente el CONTEXTO, no redondear ni inventar coberturas, tarifas o plazos (KPI de *faithfulness*), decir explícitamente cuando no hay información, no pedir datos de pacientes, cerrar con una línea `Fuente:` (documento y sección) para que la respuesta sea trazable.
3. **`BUSINESS_RULES`** — lo específico del CONTEXT §4 y §6:
   - seguro no listado → verificar con facturación, nunca confirmar;
   - país no especificado → responder separando EE. UU. y R. U.;
   - importes en la moneda del país, sin convertir;
   - Medicare/Medicaid → nunca un cargo por no-show;
   - plazos internos (los 11 días de referencia) → nunca como compromiso al paciente.

   Las reglas remiten al contexto en vez de copiar cifras, para no contradecir un documento que cambie.

El mensaje de usuario lleva el CONTEXTO numerado (`[n] Documento: … · Sección: …`) y la pregunta. `temperature=0.2`, porque se busca fidelidad, no creatividad.

## 5. Evaluación de la recuperación

`data/eval/test-queries.json`: 14 preguntas, una por chunk (los 4 documentos y todas las secciones). Están redactadas como las haría un coordinador y sin copiar el texto del documento, para no inflar el resultado. Incluye además 4 preguntas fuera de tema para el umbral.

```bash
docker compose up -d qdrant
services/api/.venv/bin/python scripts/index_knowledge_base.py
services/api/.venv/bin/python scripts/evaluate_rag_retrieval.py   # escribe data/eval/rag_retrieval_evaluation.json
docker compose stop qdrant
```

Resultados reales (2026-09-21, `data/eval/rag_retrieval_evaluation.json`):

| | Resultado |
|---|---|
| Chunks tras `setup()` | 14 (3 + 4 + 4 + 3); tras ejecutarlo dos veces, siguen siendo 14 puntos |
| Posición del chunk correcto | 1.º en las 14 preguntas |
| Recall@3 sin umbral | 100 % |
| Recall@3 con `min_score=0.38` | **100 %** (objetivo del CONTEXT: ≥ 80 %) |
| Score del chunk correcto | de 0.423 (`ref-1`, pasos de la derivación) a 0.753 |
| Mejor score de una pregunta fuera de tema | 0.336 (cafetería); ninguna supera el umbral |

**Cómo se eligió 0.38.** Con el valor provisional de 0.5 el Recall@3 bajaba al 93 %: el orden seguía siendo perfecto, pero el umbral descartaba `ref-1` (0.423). El umbral tiene que caer entre la peor pregunta correcta (0.423) y la mejor fuera de tema (0.336). 0.38 es aproximadamente el punto medio y deja ~0.04 de margen a cada lado. Es un conjunto pequeño (14 + 4 preguntas): si se añaden documentos, hay que volver a ejecutar el script antes de tocar el umbral.

**Respuestas reales de extremo a extremo** (mismo día, `query()` y `POST /knowledge/query`):

- "¿Cobran cargo por cancelación con 12 horas de anticipación?" → sí, para pago privado, separando EE. UU. (50 USD) y R. U. (40 GBP); Medicare/Medicaid sin cargo. Fuente: `appointment-policy`.
- "Un paciente con Medicare no se presentó…, ¿le cobro?" → "No…", y se registra en su historial.
- "¿Aceptan el seguro Kaiser Permanente?" (no listado) → no lo confirma y remite a facturación.
- "¿Cuál es el horario de la cafetería?" → 0 chunks sobre el umbral → "No tengo información suficiente en la base de conocimiento…".
- Referencias → "11 días… no es un plazo garantizado".

**Riesgos conocidos:**

- **Latencia.** La mayoría de respuestas tardan entre 1,4 y 6 s, pero una tardó 71 s: encaja con un timeout de 30 s del cliente seguido de un reintento del SDK (`max_retries=2`) contra el proveedor. No se ha cambiado; si se repite, bajar `max_retries` o el timeout en `get_llm_client()`.
- **Formato.** El modelo tendía a responder en Markdown (`**50 USD**`), y la pantalla, que muestra texto plano, enseñaba los asteriscos. Lo detectó el recorrido en Chrome, no los tests. El prompt pide ahora texto plano y la pantalla limpia `**`/`__`/`#` de forma defensiva (`toPlainText`, `types/knowledge.ts`).

## 6. Pruebas

- `tests/pipelines/test_rag.py` (21): chunking con los documentos reales, `setup()` idempotente sobre `QdrantClient(":memory:")`, `retrieve()` con un Qdrant simulado (excluye por debajo de `min_score`, devuelve menos de k o ninguno, respeta k), `generate_answer()`/`query()` con el modelo simulado (devuelve la salida del modelo, no el chunk crudo, y usa el modelo de generación), y las reglas del CONTEXT en el prompt. Sin red.
- `services/api/tests/test_knowledge.py` (5): contrato del endpoint.
- `uis/backoffice/__tests__/knowledgeAssistant.test.tsx` (7): estados de la pantalla.
