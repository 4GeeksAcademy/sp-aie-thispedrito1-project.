# Agente de soporte con LangGraph, Parte 2: herramientas fuera del RAG

Rama `feature/langgraph-external-tools`, apilada sobre `feature/langgraph-agent-base` (Parte 1, `docs/agent/agent-design.md`), 2026-09-24. Da al agente dos tools de **datos operativos en vivo**: el estado de las incidencias y el stock del inventario. El agente decide por sí mismo, en cada pregunta, si necesita el RAG, una tool o ambos.

## Por qué tools y no "otro RAG"

Las incidencias cambian de estado continuamente. Si se indexaran en Qdrant, la copia quedaría desactualizada en cuanto alguien cambiara un estado. Una tool lee el dato del gestor en el momento de la pregunta; el RAG queda para lo estable (las políticas).

## Grafo

```
START → receive_question ─┬─ (vacía) → reject_question → END
                          └─ plan_sources
                               │  route_next_source, tras cada fuente:
                               ├→ lookup_incident ────────┐  siguiente fuente del plan,
                               ├→ check_inventory_stock ──┤  o tool_fallback si una tool falló,
                               └→ retrieve ───────────────┘  o generate / no_information al acabar
                 tool_fallback → END · no_information → END · generate → END
```

- **`plan_sources`** (`services/agent/planner.py`): *function calling* con el modelo de generación. Se le ofrecen `search_knowledge_base`, `get_incident`, `search_incidents` y `check_inventory_stock`, y elige una o varias. Sus argumentos se validan contra el contrato tipado de cada tool y las llamadas inválidas se descartan. Si el modelo falla, tarda más de 10 s o no propone nada válido, el plan es solo el RAG (el comportamiento de la Parte 1).
- **Orden fijo**: primero las tools en vivo y después el RAG, sea cual sea el orden en que las pidió el modelo, para que los traces sean comparables.
- **`route_next_source`**: una sola arista condicional para todas las fuentes. Primero, si una tool falló, va a `tool_fallback` (cortocircuito: no se consulta el resto). Si no, a la siguiente fuente pendiente. Al acabar, a `generate` si hay evidencia (chunks o datos en vivo correctos) o a `no_information` si no la hay.
- **`generate`** sigue llamando a `rag.generate_answer()` sin cambios: los datos en vivo llegan como elementos de contexto con la misma forma que un chunk (`services/agent/evidence.py`, documento `gestor-de-incidencias (datos en vivo)`), así que las reglas de veracidad del prompt también se les aplican.

## Tools

| | `lookup_incident` | `check_inventory_stock` (extra) |
|---|---|---|
| Lee de | `IncidentRepository` (TinyDB), el de `GET /api/incidents[/{id}]` | `inventory_repository` (Supabase), el de `GET /inventory/products` |
| Transporte | En proceso (decisión del usuario): el agente vive en la misma API | En proceso |
| Auth | No hace falta token de servicio: `POST /agent/query` ya exige usuario autenticado, el mismo nivel que los endpoints de incidencias e inventario | Igual |
| Entrada | `IncidentLookupInput`: `ticket_id` **o** filtros (`status`, `category`, `branch`, `origin`, valores del paquete compartido de reglas), nunca ambos; `extra="forbid"` | `InventoryLookupInput`: `product` (2-80 caracteres, nombre o SKU) |
| Salida | `IncidentLookupOutput`: `mode`, `total`, hasta 10 `IncidentRecord` (id, estado + etiqueta en español, categoría, origen, sede, fechas) | `InventoryLookupOutput`: `total_matches`, hasta 10 `InventoryItem` (id, nombre, SKU, categoría, unidad, país, `current_stock`, caducidad) |
| Solo lectura | Solo `get_by_id()` y `list()` | Solo `list_supplies()` y `get_current_stock()`; la sesión nunca hace commit |
| Timeout | **3 s** | **5 s** (Supabase en frío) |
| No existe | `not_found` → "No encuentro la incidencia #N…" | `not_found` → "No encuentro ningún insumo que coincida con «X»…" |
| Caído o lento | `unavailable` → "No pude confirmar el estado de la incidencia #N ahora mismo…" | `unavailable` → "No pude confirmar el stock de «X» ahora mismo…" |

Una tool nunca lanza una excepción hacia el grafo: `run_tool` (`services/agent/tools/base.py`) convierte cualquier desenlace en un `ToolResult` con `status`. El timeout usa `future.result(timeout=…)` sobre un pool de 4 hilos. Python no puede matar un hilo, así que una consulta colgada ocupa el suyo hasta que termina; el tamaño del pool lo acota.

**Minimización de datos:** la tool de incidencias no devuelve `title` ni `description` (texto libre que puede contener datos de pacientes), con el mismo criterio que `incident_created` en telemetría. Hay un test de integración que lo fija con una incidencia real creada por la API.

**El stock es el total de la red**, igual que en `/inventory/products`: no se desglosa por clínica.

## Trace (v2)

Añade `plan` (lo que eligió el modelo, con sus argumentos), `plan_status` (`model`/`fallback`) y **`sources_used`** (las fuentes consultadas de verdad y en qué orden, por ejemplo `["incidents", "knowledge_base"]`). Cada paso de tool lleva su `ToolResult` completo: estado, argumentos, datos, tipo de error, timeout y duración.

## Bug real encontrado al grabar contra el modelo

La primera versión ofrecía al modelo una sola función `lookup_incident` con `ticket_id` y los cuatro filtros opcionales. El modelo real **rellenaba todos los campos** con valores inventados (`{"ticket_id": 12, "status": "open", "category": "clinical_equipment", …}`, casi siempre el primer valor de cada lista). El contrato rechazaba la llamada, con razón, y el agente caía al RAG, que respondía "no tengo información". Los tests con dobles no podían detectarlo.

Solución: dos funciones para el modelo (`get_incident` solo con `ticket_id`, y `search_incidents` con los cuatro filtros **obligatorios pero admitiendo `null`**, "null si la pregunta no lo menciona"). Después, las 5 preguntas reales se enrutaron bien. Hay dos tests de regresión en `test_agent_tools.py`.

## Evals

`data/eval/agent-eval-cases.json` tiene 5 casos de la Parte 1, ahora también verificando que no se usa ninguna tool, y 7 nuevos (5 de incidencias y 2 de inventario). Se grabaron contra los servicios reales: Qdrant, el modelo y el gestor de incidencias sobre una **copia temporal** de la TinyDB sembrada con `scripts/seed_incidents.py` (las 94 incidencias del CSV histórico); el archivo versionado no se tocó. El inventario se leyó de Supabase (`healthcore-data`). **Resultado: 86/86 evals** (`data/eval/agent_evals_result.txt`).

| Caso | Fuentes (`sources_used`) | Qué verifica |
|---|---|---|
| 5 de la Parte 1 (políticas, fuera de tema, vacía) | `knowledge_base` / ninguna | Enrutamiento a RAG, **sin** tools; anclaje en las políticas |
| `ticket-status` | `incidents` | Tool, **sin** RAG; la respuesta repite el estado en vivo ("resuelto") |
| `ticket-search` | `incidents` | Modo búsqueda (`status=open`, `branch=manchester_central`); la respuesta da el total en vivo (2) |
| `ticket-not-found` | `incidents` | Fallback honesto sin modelo ("No encuentro la incidencia #4821") |
| `ticket-plus-policy` | `incidents` → `knowledge_base` | Las dos fuentes en ese orden; "descartado" + "50 USD" |
| `incidents-outage` | `incidents` | El gestor no responde: `TimeoutError` a los 3,0 s y fallback, sin caer al RAG |
| `inventory-stock` | `inventory` | Tool extra, **sin** RAG; la respuesta da el stock en vivo (350 cajas de guantes, total de la red) |
| `inventory-not-found` | `inventory` | Fallback honesto sin modelo ("No encuentro ningún insumo que coincida con «respiradores N95»") |

`incidents-outage` sustituye el repositorio por uno que no responde nunca (`"outage": "incidents"` en el caso, solo lo usa el script de grabación): ejercita el timeout real sin cambiar ningún dato.

**Concordancia de género:** el modelo dice "el ticket está resuelto" y no "resuelta". El eval acepta las dos concordancias del mismo estado (`resuelt[ao]`), pero sigue rechazando un estado distinto.

## Comandos

```bash
cp services/api/data/suppliers.db.json /tmp/agent-eval.db.json
SUPPLIERS_DB_PATH=/tmp/agent-eval.db.json services/api/.venv/bin/python scripts/seed_incidents.py
docker compose up -d qdrant && services/api/.venv/bin/python scripts/index_knowledge_base.py
SUPPLIERS_DB_PATH=/tmp/agent-eval.db.json services/api/.venv/bin/python scripts/record_agent_traces.py
services/api/.venv/bin/python -m pytest tests/pipelines/test_agent_tools.py tests/pipelines/test_agent_evals.py -v
```
