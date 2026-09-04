# Informe de optimización: caching

Rama: `feat/caching-optimisation`. Evidencia recogida con el middleware de timing añadido en `services/api/main.py` (`timing_middleware`), contra una base de datos temporal aislada sembrada con las 94 incidencias históricas reales (`scripts/seed_incidents.py`) y los datos de inventario ya existentes en Supabase (7 productos). No se tocó `services/api/data/suppliers.db.json` del entorno de desarrollo para esta medición.

## Decisiones en el backend

Caché TTL en memoria (`services/api/cache.py`, diccionario protegido con `Lock` — los handlers son `def` síncronos que FastAPI ejecuta en threadpool). Sin Redis: un solo proceso Uvicorn no lo necesita hoy, y añadirlo habría sido infraestructura nueva sin un problema real que la justifique.

### 1. `GET /inventory/products` — TTL 30s

- **Coste**: por cada producto, `_to_supply_read` dispara 2 consultas SQL contra Supabase (patrón N+1 ya señalado en el propio código de `inventory_repository.py`). Cada consulta es un viaje de red completo.
- **Frecuencia**: se llama en cada visita a "Material sanitario" y cada vez que se abre el formulario de salida (`/inventory/orders/outbound`) para poblar el desplegable de productos.
- **Estabilidad**: solo cambia cuando se registra una entrega o un consumo.
- **Medido**: primera petición (fría) **868.5 ms** → peticiones siguientes (caché) **1.8–2.6 ms**. Reducción >99%, porque lo que domina el coste es la latencia de red a Supabase multiplicada por el número de productos, no el cálculo en sí.
- **Invalidación**: `cache.invalidate("inventory_products")` en `create_inbound_order` y `create_outbound_order`. Verificado en vivo: tras una entrega de prueba, la siguiente lectura volvió a costar 908 ms (caché correctamente vacía) y las posteriores volvieron a bajar a milisegundos.

### 2. `GET /api/incidents/summary` — TTL 60s

- **Coste**: `IncidentRepository.summary()` recorre la tabla completa 5 veces (una para el total, cuatro más dentro de `count_by` para agrupar por estado/categoría/origen/sede).
- **Frecuencia**: pantalla de métricas ejecutivas (`/incidents/summary`), pensada para vigilancia de tendencia, no para uso constante.
- **Estabilidad**: solo cambia al crear una incidencia o cambiar su estado — mucho menos frecuente que las visitas al dashboard.
- **Medido**: con 94 incidencias, fría **2.2 ms** → cacheada **1.0–1.1 ms**. La diferencia absoluta es pequeña hoy porque TinyDB es un archivo local, pero el coste sin caché crece linealmente con el número de incidencias (5 barridos completos) mientras que el coste cacheado se mantiene constante — el beneficio se hace más relevante según crece la tabla.
- **Invalidación**: `cache.invalidate("incidents_summary")` en `create_incident` y `patch_incident_status`.

### Qué no se cacheó y por qué

- **`GET /suppliers`**: admite filtros por `country`/`category` vía query params, lo que multiplicaría las claves de caché por cada combinación sin evidencia todavía de que sea un endpoint caliente (hoy 0 proveedores reales en desarrollo). Además es un directorio de gestión activa (alta, cambio de tarifa, cambio de estado, baja) donde las escrituras son relativamente frecuentes respecto a las lecturas — cachear con TTL corto aportaría poco, y con TTL largo arriesgaría mostrar tarifas o estados desactualizados en una pantalla operativa de gestión, no de solo lectura.
- **`GET /inventory/orders`**: ya evita el problema N+1 precargando los productos en un diccionario una sola vez (`list_orders_with_supply_data`, documentado en el propio código), así que su coste ya es un viaje de red por tipo de orden, no uno por fila. Es además una vista de auditoría de solo lectura con tráfico bajo. Queda como candidato de segunda prioridad si el volumen de órdenes crece mucho.

### Intercambio frescura vs. rendimiento

- **`/inventory/products` (30s)**: durante esa ventana, alguien en otra pestaña podría ver el `current_stock` ligeramente desactualizado tras un movimiento de otro usuario. Se acepta porque la caché solo afecta la **lectura** de la lista — la validación real de stock al registrar un consumo (`create_consumption`, en `inventory_repository.py`) nunca pasa por la caché y siempre lee el stock actual antes de escribir, así que el rechazo por stock insuficiente (`InsufficientStockError`) sigue siendo siempre exacto aunque la lista mostrada tenga hasta 30s de antigüedad.
- **`/api/incidents/summary` (60s)**: es explícitamente una vista agregada "para visibilidad ejecutiva" (texto literal de la UI), pensada para vigilar tendencias, no un panel operativo en tiempo real — ese rol lo cumple `/api/incidents` (listado con filtros), que no está cacheado y siempre refleja el estado actual.

## Decisiones en el frontend

### Lazy Loading (`next/dynamic`)

1. **`ProviderForm` en `/suppliers`** (`components/ProviderDirectory.tsx`): antes se montaba siempre, aunque la mayoría de visitas a esa pantalla son para consultar/filtrar el directorio, no para dar de alta un proveedor. Ahora vive detrás de un botón "+ Añadir proveedor" y se carga con `next/dynamic`. Es además la única pantalla del backoffice donde un formulario vivía *dentro* de otra página en vez de en su propia ruta — incidencias e inventario ya seguían el patrón de ruta separada (que Next.js ya divide en trozos automáticamente), lo que la convertía en el candidato genuino.
2. **`OrderDetailPanel` en `/inventory/orders`**: cada fila del historial gana un botón "Ver detalle" que expande IDs internos y la marca de tiempo ISO completa — información de auditoría/trazabilidad (relevante para HIPAA/UK GDPR) que la mayoría de quienes solo ojean el historial no necesita. El panel se carga con `next/dynamic` solo cuando se expande una fila.

**Candidato descartado — tabla "CME snapshot" en la portada (`/`)**: se consideró aplicar `next/dynamic` a esta tabla secundaria, pero `app/page.tsx` es un Server Component, y la documentación real de Next.js 16 (`node_modules/next/dist/docs/01-app/02-guides/lazy-loading.md`) es explícita: *"When a Server Component dynamically imports a Client Component, automatic code splitting is currently not supported"* y `ssr: false` directamente no está permitido fuera de un Client Component. Aplicar la técnica ahí no habría dado ningún beneficio real — solo complejidad. Se descartó tras verificar la documentación, no por intuición.

**Candidato descartado — secciones de `uis/website` (Hero/Beneficios/Servicios/Contacto)**: son Server Components estáticos sin JavaScript de cliente que difieran, y la web depende de SSR para SEO (incluye JSON-LD `LocalBusiness`). Ocultarlas detrás de una frontera lazy client-only las sacaría del HTML inicial sin ahorrar bundle — mal intercambio para contenido de marketing indexable.

### `useMemo`

**Resumen de gasto mensual por moneda** en `/suppliers` (`ProviderDirectory.tsx`): agrupa y suma `monthly_rate` por `currency` (USD/GBP) — un cálculo no trivial que antes se habría recalculado en cada render. El componente ya tiene estado que cambia en cada pulsación de tecla al editar la tarifa de cualquier fila (`editingRateById`), lo que re-renderiza todo `ProviderDirectory` constantemente durante la edición; sin memoizar con `[suppliers]` como dependencia, ese cálculo se repetiría en cada tecla pulsada sin que los proveedores hayan cambiado.

## Cómo probarlo manualmente

1. `cd services/api && uvicorn main:app --reload --port 8000` (con `.env` configurado).
2. Login y token, luego `GET /inventory/products` dos veces seguidas — la terminal del servidor muestra las líneas de timing (`GET /inventory/products → 200 | ...ms`); la segunda debe ser drásticamente más rápida.
3. Registrar una entrega u consumo (`POST /inventory/orders/inbound` u `outbound`) y repetir el `GET` — debe volver a ser lenta (caché invalidada), y rápida de nuevo en la siguiente llamada.
4. Mismo patrón con `GET /api/incidents/summary` y `POST /api/incidents` / `PATCH /api/incidents/{id}/status`.
5. En `/suppliers`: el botón "+ Añadir proveedor" revela el formulario (Network tab del navegador debería mostrar un chunk JS nuevo cargándose en ese momento). En `/inventory/orders`: "Ver detalle" en cualquier fila.
