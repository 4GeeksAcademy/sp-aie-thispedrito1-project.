# data/ — pipeline de negocio, RAG y modelo de ventas

Contiene tres trabajos independientes; las RFPs tienen su propio `data/pipelines/rfp_intake/CLAUDE.md`. Todo se ejecuta **desde la raíz** con el venv de la API (`services/api/.venv/bin/python`).

- **`data/__init__.py` es obligatorio**: sin él Python fusiona `data/` con `services/api/data/` (TinyDB) como namespace package. `data/pipelines/__init__.py` añade `services/api` a `sys.path`.
- Tests en `tests/pipelines/` (raíz), con su `conftest.py` que añade la raíz al path.

---

## 1. Pipeline de desempeño de negocio (Prefect) — `data/pipelines/PIPELINE_DESIGN.md`

`monthly_clinic_supply_performance`: 4 KPIs (`total_supply_cost`, `supply_consumption_count`, `critical_stockout_count`, `expiry_risk_count`) por `clinic_id` × `month_start` (UTC) → `reporting.monthly_clinic_supply_performance` (DDL literal del CONTEXT). Lee `telemetry_events` en solo lectura.

```bash
services/api/.venv/bin/python data/pipelines/pipeline.py [--month-start 2026-08-01]   # sin servidor de Prefect (API efímera)
services/api/.venv/bin/python -m pytest tests/pipelines/test_pipeline.py              # unitarios, task.fn sin BD
(cd services/api && .venv/bin/python -m pytest tests/test_business_pipeline.py -o addopts="")   # integración
```

Mapa: `pipeline.py` (flow + subflows + CLI), `monthly_clinic_supply_performance/` (`models.py` con `ensure_reporting_schema`, `storage.py`, `run_log.py`, `queries.py`, `trigger.py`), `data/process/supply_performance_transforms.py` (Pandas puro, una función por KPI), `services/reporting/` (API; importa de `data/pipelines/`, nunca al revés). Dashboard `/reporting` en el backoffice.

Topología: flow padre sin lógica ETL que coordina `extract_clinic_supply_activity` → `compute_monthly_clinic_supply_kpis` (una task por KPI con el nombre del CONTEXT) → `load_monthly_clinic_supply_performance` → `export_supply_performance_eval_snapshot` (opcional).

Reglas:
- KPIs con la definición literal del CONTEXT (conteo de eventos); que signifique "veces que cayó"/"lotes" lo garantiza la captura, no la transformación.
- Deduplicar por `eventId` y luego por `delivery_id`/`consumption_id` (`POST /telemetry/events` no deduplica).
- Idempotencia: recalcular la ventana completa y sustituir (upsert `ON CONFLICT (clinic_id, month_start)` + `pipeline_run_partitions` en una transacción). Nunca el mes en curso (`ValueError` / 400).
- Caché de la transformación por huella del contenido (mes + `TRANSFORM_VERSION` + `(id, timestamp)`), 1 h. **Si cambias una regla de las transformaciones, sube `TRANSFORM_VERSION`.** Extracción y carga con `cache_policy=NONE`.
- `extract_domain_activity` y el export van con `return_state=True` → `completed_with_warnings`; lo crítico propaga y queda `failed`. `retry_condition_fn` no reintenta `WindowLockedError`.
- Cero eventos con filas de dominio → `CaptureGapError`, nada publicado. `capture_ratio` < 0,95 → aviso.
- Coste desconocido ≠ 0 (`unit_cost` vacío). Una partición con dos países se rechaza (no se mezclan USD y GBP). `clinic_id` en texto, sin catálogo inventado; `country` es el del producto.
- `/reporting` sin `TTLCache` (escribe otro proceso). Disparo manual por Celery (ver `services/tasks/CLAUDE.md`).
- `storage.fetch_supply_events` recorta `tags` a `EXTRACTED_TAG_KEYS` antes de devolverlos: Prefect guarda los parámetros de cada subflow en su base. No pasar DataFrames como parámetro de flow.

Gotchas:
- El módulo de un `@flow` no lleva `from __future__ import annotations` (Pydantic falla con "not fully defined"); usa `Optional[...]`.
- `pd.to_datetime(..., utc=True, format="ISO8601")` siempre (`isoformat()` omite microsegundos a cero). DataFrame con `dtype=object` (enteros con `None` → float).
- `get_run_logger()` lanza `MissingContextError` fuera de un run: `pipeline._logger()` tiene respaldo para `task.fn`.
- Subflow y task no pueden llamarse igual (la task de carga es `upsert_monthly_clinic_supply_performance_rows`).
- `retry_condition_fn` recibe `(task, task_run, state)` y la excepción va en `state.data`.
- Postgres necesita el esquema antes que las tablas: `ensure_reporting_schema` va antes del `create_all`.
- Upsert portable: `on_conflict_do_update` de los dialectos postgresql/sqlite, elegido por `dialect.name`. `tags["eventId"].as_string()` compila a `->>` y `json_extract`.
- Tests: SQLite no tiene esquemas, la fixture hace `ATTACH DATABASE ':memory:' AS reporting`. `prefect_test_harness` a nivel de módulo (si no, "I/O operation on closed file"). `pipeline.use_engine(engine)` sustituye Supabase: `database.py` carga el `.env` y sin override se hablaría con la base real.

---

## 2. Base de conocimiento RAG — `docs/rag/rag-design.md`

```bash
docker compose up -d qdrant                                          # 127.0.0.1:6333
services/api/.venv/bin/python scripts/index_knowledge_base.py        # 14 chunks en healthcore_knowledge
services/api/.venv/bin/python scripts/evaluate_rag_retrieval.py      # Recall@3 → data/eval/rag_retrieval_evaluation.json
services/api/.venv/bin/python -m pytest tests/pipelines/test_rag.py
```

Necesita `LLM_BASE_URL`, `LLM_API_KEY`, `EMBEDDING_MODEL`, `GENERATION_MODEL` en `services/api/.env`. Mapa: `docs/company-knowledge-base/` (4 documentos literales, lo único que se indexa), `data/process/rag.py` (chunking, `embed`, `setup`, clientes), `data/pipelines/rag.py` (`retrieve`, `generate_answer`, `query`, prompt en tres capas), `services/knowledge/` (`POST /knowledge/query` → `{answer}`), pantalla `/knowledge`.

Reglas:
- Colección `healthcore_knowledge` y payload literales del CONTEXT (`company`, `source_document`, `section`, `language`, `chunk_index`) + `text`.
- Chunking por bloque semántico, no por tamaño; `load_chunks()` falla si un documento da menos de 3. Con un documento de estructura nueva, revisar `chunk_document`.
- `setup()` calcula los vectores antes de tocar Qdrant, limpia y recarga con IDs `uuid5`.
- Se embebe `título — sección + cuerpo`; `text` guarda solo el cuerpo. Mismo preprocesado al indexar y al consultar.
- `retrieve(query, *, k, min_score)` filtra en Python. `DEFAULT_MIN_SCORE = 0.38` calibrado con datos reales: si cambian documentos o modelo de embeddings, reevaluar antes de tocarlo.
- `/knowledge/query` siempre responde con el modelo (`NO_CONTEXT_MARKER`) y pasa por los guardarraíles del agente.
- Reglas de negocio del prompt apuntan al contexto, no copian cifras (test `test_system_prompt_carries_the_context_business_rules`). Respuesta en texto plano (`toPlainText` en el frontend limpia Markdown).
- La pregunta nunca va a los logs. `get_embedding_model`/`get_generation_model` rechazan el mismo ID.

---

## 3. Modelo de ventas — `docs/sales-forecast.md` + `data/eval/evaluation_report.md`

```bash
uv pip install --python services/api/.venv/bin/python -r requirements-ml.txt   # una vez (fuera de requirements.txt a propósito)
services/api/.venv/bin/python scripts/train_sales_forecast.py                   # → docs/sales-forecast/
services/api/.venv/bin/python scripts/evaluate_sales_forecast.py                # → data/eval/ (~18 s)
services/api/.venv/bin/python -m pytest tests/pipelines/test_sales_forecast.py tests/pipelines/test_sales_forecast_evaluation.py
```

Predice `revenue_usd` de la fila `consolidated` de `data/raw/healthcore_sales.csv`. Mapa: `data/process/sales_forecast.py`, `sales_forecast_evaluation.py`, `sales_forecast_comparison.py` (modelos malos a propósito, no candidatos).

Reglas:
- Split cronológico por año (2016-2023 / 2024-2025), nunca aleatorio. Dos capas de tests de fuga: el split y el entrenamiento (`test_model_never_sees_test_values`).
- Modelo = tendencia (regresión sobre `log(revenue)`) × patrón estacional (RF sobre `month_of_year`). RF/XGBoost no extrapolan.
- `visits_count` y `avg_revenue_per_visit_usd` no son variables (`revenue = visits × avg`, fuga); sí sirven para reconstruir un nulo.
- Franja del 80 % = percentiles 10/90 de los errores OOB del RF, no la dispersión entre árboles.
- Métricas: "K2 Score" = R², Gini normalizado, RMSE en % del ingreso medio, PSI con 5 cubetas.
- La evaluación usa solo 2016-2023; la prueba no decide diagnóstico ni corrección (hay test). `TimeSeriesSplit(n_splits=5, test_size=12)` y `check_chronological_folds` siempre. Curva con ventanas móviles en años completos; el punto de 84 meses tiene `std=None`.
- `diagnose_fit`: underfitting primero (entrenamiento ≥ referencia ingenua), overfitting si validación ≥ 1,5 × entrenamiento o ≥ referencia.
- Acción correctiva propuesta y no implementada: `year_parity` en `TREND_FEATURES` (pendiente de Revenue Cycle).
- scikit-learn 1.6.1 y matplotlib 3.9.4 fijados.
