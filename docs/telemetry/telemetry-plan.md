# Plan de Telemetría — HealthCore

**Estado:** propuesta de diseño, sin instrumentar. **Entregable relacionado:** [`event-schemas.json`](./event-schemas.json).

## 1. Contexto y regla de oro

HealthCore opera 12 clínicas (EE.UU.: Texas, Florida, Georgia; Reino Unido: Londres, Manchester). El sistema de inventario (`services/api`, módulo `/inventory`) controla insumos clínicos, y el resto del backoffice (auth, incidencias, proveedores) es hoy una caja negra para el equipo de operaciones.

Todo evento de este catálogo pasa la regla de oro antes de existir:

> _"Capturamos `[event_type]` porque necesitamos saber `[hipótesis]`, lo que nos permite tomar la decisión `[decisión concreta]`."_

Si un evento no completa esa frase con una decisión real, no está en este catálogo — ver también la sección 6, "Riesgos y exclusiones", para lo que se consideró y se descartó explícitamente.

**Nota regulatoria (HIPAA / UK GDPR):** ningún evento de este catálogo contiene datos de pacientes, reales o simulados. `department` describe un área clínica (`general_consultation`, `chronic_care`), nunca una persona. Los eventos que sí manejan un dato personal (email en intentos de login) lo hacen únicamente en forma de hash — ver sección 5.

## 2. Event Envelope

Todo evento se emite envuelto en esta estructura estándar, sin excepción. `properties` es el único campo que varía por `event_type`.

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `eventId` | string (UUID v4) | sí | Identificador único del evento, generado en el punto de emisión. Permite deduplicar si el transporte reintenta. |
| `timestamp` | string (ISO 8601, UTC) | sí | Momento en que ocurrió el evento, no el momento en que llega al pipeline. |
| `sessionId` | string (UUID v4) | sí | Identificador de sesión de cliente, generado al cargar la app. |
| `userId` | string (`user_uuid`) \| `null` | sí | Usuario autenticado (TinyDB). `null` solo en eventos pre-autenticación (ej. `login_failed` antes de validar credenciales). |
| `event_type` | string | sí | Taxonomía `entidad_acción` (ej. `inbound_order_created`). |
| `schemaVersion` | string (semver) | sí | Versión del esquema de ese `event_type` específico — permite evolucionar un evento sin romper a los demás. |
| `requestId` | string (UUID v4) \| `null` | sí | Correlación frontend–backend–logs, propagada por header HTTP. `null` en eventos sin una petición HTTP de origen (ej. `page_viewed`). |
| `properties` | object | sí | Payload específico del evento, limitado estrictamente al allowlist documentado — nunca claves fuera de lista. |

## 3. Catálogo de eventos

**23 eventos totales: 5 obligatorios (del CONTEXT) + 18 identificados en este plan**, cubriendo 7 categorías: inventario, incidencias, proveedores, autenticación, rendimiento, errores de frontend y navegación.

### 3.1 Obligatorios (piso del CONTEXT)

| `event_type` | Entrega | ¿Por qué? |
|---|---|---|
| `inbound_order_created` | batch | Alimenta negociación con proveedores — decisión de ciclo periódico, no reacciona a una orden individual. |
| `outbound_order_created` | batch | Alimenta ajuste de reposición automática — se recalcula sobre una ventana agregada de consumo. |
| `stock_threshold_triggered` | **stream** | La propia decisión que habilita ("reabastecimiento **urgente**, escalar a Marcus") es explícitamente urgente. |
| `direct_stock_edit_rejected` | batch | La decisión (capacitación/permisos) se revisa periódicamente por clínica, no por intento individual. |
| `supply_expiry_flagged` | batch | Un aviso con semanas de anticipación no necesita latencia de segundos; un job diario alcanza. |

### 3.2 Oportunidad — Inventario (extra)

| `event_type` | Entrega | Por qué se propone |
|---|---|---|
| `outbound_order_rejected` | batch | El backend ya rechaza con 400 ("Insufficient stock...") cuando una salida supera el stock — hoy esa señal se pierde. Indica error de conteo del personal o escasez real aún no detectada por `stock_threshold_triggered`. |
| `product_created` | batch | Alta de nuevo SKU en el catálogo — insumo para auditoría periódica de cobertura de categorías. |

### 3.3 Oportunidad — Incidencias (`/api/incidents`)

| `event_type` | Entrega | Por qué se propone |
|---|---|---|
| `incident_created` | **stream** | Una incidencia `compliance_breach` puede requerir notificación regulatoria en ventanas cortas (HIPAA/UK GDPR) — esperar un lote arriesga el plazo. |
| `incident_status_changed` | batch | Tiempos de ciclo de vida (`open → in_progress → resolved/discarded`) se analizan en tendencia. |
| `incident_status_transition_rejected` | batch | Señal de confusión de proceso o bug de integración, revisada en agregado. |

### 3.4 Oportunidad — Proveedores (`/suppliers`)

| `event_type` | Entrega | Por qué se propone |
|---|---|---|
| `supplier_created` | batch | Diversificación de cadena de suministro, revisión periódica. |
| `supplier_rate_updated` | batch | Tendencia de tarifas para detectar incrementos sostenidos y renegociar. |
| `supplier_status_changed` | **stream** | Una suspensión puede cortar el suministro de insumos críticos — riesgo operativo urgente, no de revisión periódica. |

### 3.5 Oportunidad — Autenticación (`/auth`)

| `event_type` | Entrega | Por qué se propone |
|---|---|---|
| `login_succeeded` | batch | Línea base de uso (horas pico, frecuencia) — también sirve para comparar picos anómalos de `login_failed`. |
| `login_failed` | **stream** | Señal de seguridad — detectar fuerza bruta mientras ocurre, no al día siguiente. |
| `password_reset_requested` | **stream** | Mismo principio que `login_failed`: un pico de solicitudes es señal temprana de credential stuffing. |
| `password_reset_completed` | batch | Tasa de conversión solicitado→completado, para detectar fallas de entrega de email (Resend). |
| `session_expired` | batch | Se analiza junto con `flow_abandoned` para decidir si el TTL del token es demasiado corto. |

### 3.6 Oportunidad — Rendimiento y errores backend

| `event_type` | Entrega | Por qué se propone |
|---|---|---|
| `api_latency_recorded` | batch | Misma fuente que el `timing_middleware` ya existente — decisión de qué cachear se toma sobre percentiles agregados, no por petición. |
| `api_error_response` | **stream** | Un 5xx en producción merece visibilidad casi inmediata, equivalente a lo que vería un on-call. |

### 3.7 Oportunidad — Errores de frontend

| `event_type` | Entrega | Por qué se propone |
|---|---|---|
| `frontend_error_captured` | **stream** | Una pantalla rota (ej. el formulario de órdenes) bloquea el trabajo del operador ahora mismo. |

### 3.8 Oportunidad — Navegación

| `event_type` | Entrega | Por qué se propone |
|---|---|---|
| `page_viewed` | batch | Pregunta explícita del tech lead: "qué secciones visitan más los operadores". |
| `flow_abandoned` | batch | Pregunta explícita del tech lead: "hay flujos que se abandonan a la mitad". |

El detalle completo de cada evento (`properties` con tipo, obligatoriedad y descripción) vive en [`event-schemas.json`](./event-schemas.json) — este documento resume el porqué, ese archivo es la fuente de verdad estructural.

## 4. Campos comunes de inventario

Todos los eventos de dominio `inventory` que aplican comparten este subconjunto mínimo, tal como exige el CONTEXT: `clinic_id`, `country` (`US`/`UK`), `product_id`, `product_category`, `quantity`, y `department` solo cuando aplica (nunca en eventos que no involucran consumo por área clínica, ej. `inbound_order_created` o `product_created`).

## 5. Datos sensibles y PII

| Evento | Dato sensible | Mitigación |
|---|---|---|
| `login_failed` | Identificador intentado (típicamente email) | Se emite como `attempted_identifier_hash` (HMAC-SHA256 con clave de aplicación) — nunca el valor original. |
| `password_reset_requested` | Identificador solicitado | Mismo tratamiento de hash. La respuesta HTTP de `/auth/forgot-password` ya es siempre 200 por anti-enumeración — el evento de telemetría no debe reabrir esa fuga por otra vía. |
| `incident_created` | Riesgo de PHI incidental en texto libre | El allowlist **excluye deliberadamente** `title` y `description`. La UI del backoffice ya advierte no introducir datos de pacientes, pero un evento de telemetría no debe depender solo de ese aviso — excluir el texto libre es la mitigación real. |
| `frontend_error_captured` | Valores de formulario en el stack trace | `error_message` se trunca a 200 caracteres y se sanitiza; nunca se emite el stack trace completo ni valores crudos de campos de formulario. |
| `api_error_response` | Detalle de excepción interna | Nunca se incluye stack trace ni mensaje de excepción crudo — mismo principio que el handler global de `main.py`, que nunca expone detalle interno al cliente. Solo un `error_id` de correlación con el log del servidor. |

Todos los demás eventos: sin PII ni PHI.

## 6. Throttle y debounce

Eventos de alta frecuencia o con riesgo de ruido necesitan una estrategia explícita antes de emitirse, no solo una clasificación stream/batch:

- **`api_latency_recorded`**: se genera en cada petición HTTP. En vez de emitir cada una en crudo, se agrega server-side en percentiles (p50/p95/p99) por `route_template` en ventanas de 60 segundos antes de enviarse al pipeline — evita saturarlo y es coherente con su clasificación batch.
- **`page_viewed`**: se debe deduplicar dentro de una ventana corta (ej. 500ms) por sesión+ruta, para no contar dos veces un mismo render (doble invocación de efectos en desarrollo, navegación rápida atrás/adelante).
- **`flow_abandoned`**: requiere un debounce de inactividad (ej. varios segundos sin interacción, o navegación explícita fuera de la ruta del formulario) antes de considerarse abandono real — de lo contrario, cualquier clic accidental fuera del formulario contaría como abandono.
- **`login_failed` / `password_reset_requested`**: **no se throttlean del lado del cliente** — perder una sola señal de estos dos eventos puede ocultar el inicio de un ataque. El control de volumen (ej. "más de N intentos en M minutos dispara una alerta") es lógica de consumo aguas abajo sobre el stream completo, no una reducción en el punto de emisión.

## 7. Riesgos y exclusiones

**Brechas de esquema encontradas al revisar el código real (`services/api/`), documentadas para que el equipo las resuelva antes de instrumentar:**

- **Categorías de producto no coinciden con el CONTEXT.** `CONTEXT-healthcore.es.md` especifica `medication/ppe/consumable/equipment`; el código real (`services/api/models.py::VALID_SUPPLY_CATEGORIES`) usa `ppe/wound_care/diagnostics/medications/consumables`. Este plan usa los valores del CONTEXT porque el README exige que el plan coincida exactamente con él — pero **alguien debe decidir y alinear** cuál es la taxonomía real antes de instrumentar `product_category`.
- **Falta el campo `expiry_date` en `MedicalSupply`.** El CONTEXT pide registrar la fecha de vencimiento "en el modelo de Product, no solo en la orden" — hoy ese campo no existe en `inventory_models.py`. `supply_expiry_flagged` no se puede calcular hasta que se añada.
- **No existe un umbral mínimo configurable por clínica/producto.** `stock_threshold_triggered` necesita un valor `threshold_value` que hoy no tiene dónde vivir (no hay tabla de configuración de mínimos). Requiere una tabla nueva antes de poder disparar el evento.
- **No existe un endpoint de edición directa de stock que rechazar.** `direct_stock_edit_rejected` es un evento obligatorio del CONTEXT, pero hoy no hay ningún endpoint que permita ni intente escribir `current_stock` directamente (solo existen `/orders/inbound` y `/orders/outbound`). El evento queda diseñado para dispararse el día que se exponga — o cambia de origen a la capa de repositorio si en el futuro cualquier código intenta saltarse el patrón de órdenes.

**Descartado deliberadamente (no entra en este catálogo):**

- **Texto libre de incidencias (`title`, `description`) como propiedad de evento** — mayor riesgo de fuga de PHI que valor analítico; se puede seguir auditando manualmente desde la propia tabla de incidencias si hace falta, sin pasar por telemetría.
- **Tracking de mouse/scroll/heatmaps** — ninguna pregunta de negocio actual lo requiere; el costo de captura y almacenamiento no se justifica sin una hipótesis concreta.
- **Telemetría por cada tecla presionada en formularios** — demasiado granular, no habilita ninguna decisión que `flow_abandoned` no cubra ya a un nivel razonable, y es invasivo sin beneficio adicional.
- **Cadencia exacta de batch (cada cuánto corre el job, tamaño de lote)** — se deja fuera de este documento a propósito: es una decisión de implementación del pipeline (próximo hito), no de diseño de qué capturar.

## 8. Cómo leer `event-schemas.json`

Estructura personalizada y documentada (no JSON Schema draft-07), elegida por legibilidad dado que cada evento necesita campos que un JSON Schema puro no modela bien de forma compacta (clasificación obligatorio/oportunidad, hipótesis, decisión, modo de entrega). El archivo tiene dos secciones:

- `envelope`: la tabla de la sección 2 de este documento, en forma de datos.
- `events`: un objeto por `event_type` con `classification`, `domain`, `description`, `hypothesis`, `decision`, `delivery` (`mode` + `justification`), `contains_pii_or_phi` + `pii_notes`, y `properties` (el allowlist: `name`, `type`, `required`, `description`).

## 9. Almacenamiento (Fase 3)

**Estado:** implementado. `POST /telemetry/events` (`services/api/routes/telemetry.py`) ya no es el stub de la Fase 1 — valida cada evento del lote por separado (`TelemetryEvent.model_validate` dentro de un `try/except`, nunca `list[TelemetryEvent]` como tipo del body, para que un evento mal formado no tumbe el lote completo con un 422) y persiste los válidos en Supabase (`telemetry_events`, `services/api/telemetry_models.py`) en una sola operación de bulk insert (`services/api/telemetry_repository.py`). El modelo Pydantic `TelemetryEvent` no se tocó — se reutiliza tal cual como validador por item.

**Mapeo `tags` (decisión, ver `services/api/telemetry_service.py::build_tags`):** la columna `tags` guarda `event.properties` (el allowlist por evento, ya aplicado aguas arriba por quien emite — el stub de almacenamiento no filtra nada) **más** los campos de correlación del envelope: `eventId`, `sessionId`, `userId`, `requestId`, `schemaVersion`. Decisión explícita del usuario: preferir trazabilidad completa (qué sesión/usuario generó cada fila, deduplicar un lote reintentado por `eventId`) sobre un `tags` más angosto ceñido solo a las dimensiones de negocio del CONTEXT — ambas opciones cumplían el mínimo pedido por la guía de la clase.

**`level`/`value`/`message`:** no vienen en el envelope ni en `event-schemas.json` — se derivan en `telemetry_service.py`. `level` (`info`/`warn`/`error`) es una decisión de negocio (qué event_type merece atención en el futuro dashboard de cumplimiento) implementada como contribución humana en `derive_level`. `value` toma el primer campo numérico conocido entre una lista corta de nombres comunes en el catálogo (`value`, `duration_ms`, `quantity`, `current_stock`, etc.) — no todo evento tiene un número obvio, así que queda `null` cuando ninguno aplica. `message` toma `properties.error_message` cuando existe (hoy solo `frontend_error_captured` lo trae).

**Decisión de alcance — eventos originados en el backend no se persisten (todavía):** `stock_threshold_triggered`, `login_failed`, `api_error_response` y el resto de los eventos que el propio backend emite vía `telemetry_service.emit_backend_event` (sin pasar por el endpoint HTTP) siguen sin guardarse en `telemetry_events` — solo se loguean, igual que en la Fase 1. Persistirlos ahí requeriría abrir una sesión a Supabase desde código que corre dentro de peticiones normales (`routes/auth.py`, el exception handler global, rutas de inventario), usando el engine cacheado que la fixture `client` de los tests **no** intercepta en esos puntos — cada test que ejercita login o escritura de inventario habría intentado conectarse a la Supabase real en vez de la SQLite de prueba. Se dejó fuera de esta clase a propósito; ver `memory-bank/techContext.md` para el detalle y `memory-bank/progress.md` para el próximo paso sugerido.

**Índices:** `timestamp` y `event_type` (consulta por rango de tiempo y por tipo), más un índice GIN sobre `tags` en Postgres (vía `postgresql_using="gin"`, un kwarg específico de dialecto que SQLAlchemy simplemente ignora en SQLite — la tabla de pruebas en memoria recibe un índice normal en su lugar, sin fallar).
