# Auditoría de rendimiento frontend — HealthCore Digital

**Fecha:** 2026-09-07
**Rama:** `feat/frontend-performance-audit`
**Herramienta:** Lighthouse 13.4.1 (`npx lighthouse`), Chrome headless
**Alcance:** los dos frontends del monorepo — `uis/website` (web corporativa pública) y `uis/backoffice` (panel interno).

Este documento cubre las dos primeras fases del ciclo **medir → analizar → corregir → volver a medir**. Las correcciones aplicadas y su impacto medido están en [`REPORT.md`](./REPORT.md).

---

## 1. Metodología

### 1.1 Por qué se mide sobre build de producción

Todas las mediciones se hacen sobre `next build` + `next start`, **nunca** sobre `next dev`. En modo desarrollo el bundle no está minificado, React corre en su build de desarrollo (más lento y más pesado) y Next.js inyecta overlays de depuración. Una puntuación de Lighthouse tomada en `dev` no describe lo que ve un usuario real y no sirve para comparar antes/después.

```bash
# Web corporativa
cd uis/website && npm run build && npx next start -p 3000

# Backoffice
cd uis/backoffice && npm run build && npx next start -p 3001
```

### 1.2 Páginas auditadas y por qué

Lighthouse analiza **una página cada vez**. Se eligieron las de mayor complejidad visual o mayor tráfico:

| App | Página | Modos | Motivo |
|---|---|---|---|
| `uis/website` | `/` (home) | móvil + escritorio | Página de entrada, la que indexa el buscador y la que tiene la imagen del hero (elemento LCP). |
| `uis/website` | `/application` | móvil | La vista más compleja de la web pública: formulario de registro de paciente con ~12 campos, validación en cliente y estilos propios. |
| `uis/backoffice` | `/login` | móvil + escritorio | Ver limitación en 1.3. |

### 1.3 Limitación conocida: las vistas internas del backoffice no son medibles con Lighthouse sin sesión

El backoffice guarda la sesión como un JWT en `localStorage` y `components/AuthGuard.tsx` redirige a `/login` cualquier ruta que no esté en `PUBLIC_ROUTES`. Lighthouse arranca una instancia limpia de Chrome, sin `localStorage`, así que al pedir `/inventory/products` acaba midiendo la redirección al login, no el dashboard.

Se audita `/login` en su lugar, y **no es un atajo**: `app/layout.tsx` monta `ErrorTracking`, `WebVitals`, `PageViewTracker` y `AuthGuard` en **todas** las rutas de la app, de modo que el coste de JavaScript compartido —que es lo que domina el arranque de las vistas internas— sí queda medido en `/login`. Lo que `/login` no captura es el coste de renderizar tablas grandes con datos reales; ese eje se aborda en el análisis de código (sección 3) y ya se trató en el hito anterior de caching (`CACHING_REPORT.md`).

### 1.4 Fiabilidad de las mediciones

Las primeras mediciones mostraron varianza notable entre pasadas (el Speed Index de la home dio 1.7 s, 0.8 s, 1.1 s y 2.0 s en ejecuciones consecutivas), causada por ejecuciones de Lighthouse compitiendo por CPU en la misma máquina. **Todas las cifras de este documento se confirmaron con 3 pasadas consecutivas idénticas**, ejecutadas de una en una. Las primeras cifras anómalas se descartaron y las líneas base se volvieron a medir restaurando el código original con `git stash`, para garantizar que el "antes" y el "después" se midieron en las mismas condiciones.

Las URLs de imagen de Unsplash se precalientan antes de medir (dos pasadas de `curl`), porque una variante de ancho nueva tarda ~650 ms la primera vez que su CDN la genera y ~50-90 ms una vez cacheada. Medir en frío describiría el CDN de un tercero, no nuestro código.

---

## 2. Puntuaciones iniciales

Informes HTML completos y capturas en [`audit/before/`](./audit/before/).

| Página | Modo | Performance | Accessibility | Best Practices | SEO |
|---|---|---|---|---|---|
| website `/` | móvil | **98** | 100 | **96** | 100 |
| website `/` | escritorio | 100 | 100 | **96** | 100 |
| website `/application` | móvil | **76** | 100 | **96** | 100 |
| backoffice `/login` | móvil | **99** | 100 | **96** | 100 |
| backoffice `/login` | escritorio | 100 | 100 | **96** | 100 |

### Métricas de detalle (Core Web Vitals)

| Página | Modo | LCP | CLS | FCP | TBT | Speed Index |
|---|---|---|---|---|---|---|
| website `/` | móvil | 2.3 s | 0 | 0.8 s | 50 ms | 0.8 s |
| website `/` | escritorio | 0.6 s | 0 | 0.2 s | 0 ms | 0.2 s |
| website `/application` | móvil | 1.5 s | **0.974** | 0.8 s | 10 ms | 0.8 s |
| backoffice `/login` | móvil | 2.2 s | 0 | 0.8 s | 10 ms | 0.8 s |
| backoffice `/login` | escritorio | 0.5 s | 0 | 0.2 s | 0 ms | 0.2 s |

Umbrales de referencia: LCP < 2.5 s · CLS < 0.1 · Performance ≥ 90.

**Lectura rápida:** el proyecto parte de una base sana. Solo hay un valor fuera de rango, pero está fuera de rango por un factor de casi 10: el **CLS de 0.974 en `/application`**.

---

## 3. Problemas identificados y causa raíz

### P1 — CLS de 0.974 en `/application`: el CSS depende de JavaScript para existir

**Gravedad:** alta. Es el único KPI del proyecto fuera de umbral, y por mucho.

**Qué marca Lighthouse.** La auditoría `cls-culprits-insight` señala un único culpable que aporta el 100 % del CLS: `<section class="jsx-3ebac2bf6e60e1e7 application-card">`, un bloque de 1286 px de alto que se recoloca entero durante la carga.

**Causa raíz.** `uis/website/app/application/page.tsx` es un Client Component (`"use client"`) que declaraba sus estilos en dos bloques `<style jsx>` de **styled-jsx**, uno para la página y otro dentro del componente `Field`. En el App Router de Next.js, el CSS de styled-jsx dentro de un Client Component **lo inyecta JavaScript después de la hidratación**, no viaja en el HTML del servidor.

Verificado empíricamente, no por deducción:

```console
$ curl -s http://localhost:3000/application | grep -c "application-card{"
0
$ curl -s http://localhost:3000/application | grep -o 'rel="stylesheet"[^>]*'
rel="stylesheet" href="/_next/static/chunks/16w3nt3q3jw4-.css"   # solo globals.css
```

El HTML inicial no contiene **ni una** regla para `.application-card`, `.field` ni `.btn`. La secuencia real que ve el usuario es:

1. El navegador recibe el HTML y pinta el formulario **sin estilos**: sin padding, sin bordes, sin el `display: grid` de los campos. Ocupa una altura distinta.
2. Descarga y ejecuta el bundle de JavaScript.
3. React hidrata y styled-jsx inserta las reglas de golpe.
4. La sección entera de 1286 px se recoloca.

Ese paso 4 es el CLS de 0.974.

**Principio general.** Cualquier CSS que necesite JavaScript para llegar al documento es un layout shift esperando a ocurrir. El CSS crítico tiene que estar en el `<head>` cuando llega el HTML.

---

### P2 — La imagen del LCP de la home está marcada como diferida

**Gravedad:** media.

**Qué marca Lighthouse.** La auditoría `lcp-discovery-insight` falla dos de sus tres comprobaciones sobre el mismo elemento:

```
priorityHinted    = false   ("fetchpriority=high should be applied")
requestDiscoverable = true
eagerlyLoaded     = false   ("LCP resources should not use loading=lazy")
```

**Causa raíz.** En `components/HeroSection.tsx`, la imagen del hero —que **es** el elemento LCP de la página— se declaraba como `<img ... loading="lazy">`. Se le estaba pidiendo explícitamente al navegador que retrasara justo el recurso que define la métrica que Lighthouse cronometra, y sin ninguna señal de prioridad.

El desglose por fases del LCP lo confirma:

| Fase | Valor original |
|---|---|
| Time to first byte | 20 ms |
| Resource load delay | 56 ms |
| Resource load duration | 106 ms |
| **Element render delay** | **792 ms** |

792 de los ~974 ms observados se iban en "render delay": el navegador ya tenía la imagen y aun así retrasaba pintarla, porque `loading="lazy"` la clasifica como no prioritaria.

---

### P3 — Imágenes sin `width`/`height` y servidas a mayor resolución de la necesaria

**Gravedad:** media-baja.

**Causa raíz.** Ninguna de las cuatro imágenes de la web (hero + 3 tarjetas de servicio en `ServicesSection.tsx`) declaraba `width` ni `height`, ni generaba `srcset`. Se descargaba siempre el original (1280 px de ancho para el hero, 900 px para las tarjetas) independientemente del espacio real que ocupa —~345 px en móvil—. Lighthouse cifra el desperdicio en **52 KiB** (`image-delivery-insight`).

Que el CLS de la home saliera 0 pese a la ausencia de `width`/`height` es **suerte, no diseño**: las alturas están fijadas con estilos inline (`height: 260`, `height: 180`), así que el hueco no cambia al cargar. Basta con que alguien quite ese estilo inline en un rediseño para que aparezca un CLS. Los atributos `width`/`height` son la garantía explícita.

---

### P4 — La fuente de marca nunca se cargó (bug latente, no solo rendimiento)

**Gravedad:** baja en métricas, alta en corrección.

**Causa raíz.** `uis/website/app/globals.css` declara:

```css
body { font-family: var(--font-manrope), sans-serif; }
```

…pero **nadie definía `--font-manrope`**. `app/layout.tsx` no importaba `next/font` ni ninguna otra fuente. La variable CSS no existía, así que el valor era inválido y el navegador caía silenciosamente al `sans-serif` del sistema. La web llevaba desde su migración a React renderizándose con una tipografía que no era la de su propio diseño, sin ningún error visible.

---

### P5 — 404 de `/favicon.ico` en cada carga de página, en las dos apps

**Gravedad:** baja, pero es la única causa del techo de 96 en Best Practices.

**Causa raíz.** Ni `uis/website` ni `uis/backoffice` tienen ningún archivo de icono (`favicon.ico`, `app/icon.*`). Todo navegador pide `/favicon.ico` por defecto; el servidor responde 404 y eso queda registrado como error de consola. La auditoría `errors-in-console` de Lighthouse falla en consecuencia, y esa auditoría es la que mantenía Best Practices en **96 en las cinco mediciones**.

---

## 4. Análisis de refactorización: código duplicado

La rúbrica pide identificar al menos dos casos de lógica o componentes repetidos que puedan extraerse. Se encontraron dos, ambos en `uis/backoffice`, y ambos afectan a las mismas cuatro pantallas.

### R1 — El ciclo de carga asíncrona, repetido en 4 archivos → **Custom Hook**

**Dónde aparece:**

| Archivo | Qué carga |
|---|---|
| `app/incidents/page.tsx` | lista de incidencias (con filtros) |
| `app/incidents/summary/page.tsx` | métricas agregadas |
| `app/inventory/products/page.tsx` | catálogo de material sanitario |
| `app/inventory/orders/page.tsx` | historial de entradas/salidas |

**Qué se repite.** Las cuatro declaraban el mismo triplete de estado y el mismo par `useCallback` + `useEffect`, con diferencias solo en el nombre de la variable y el texto del mensaje de error:

```tsx
const [X, setX] = useState<T[]>([]);
const [isLoading, setIsLoading] = useState(true);
const [loadError, setLoadError] = useState<string | null>(null);

const loadX = useCallback(async () => {
  setIsLoading(true);
  setLoadError(null);
  try {
    setX(await getX());
  } catch {
    setLoadError("No se pudo cargar …");
  } finally {
    setIsLoading(false);
  }
}, []);

useEffect(() => { void loadX(); }, [loadX]);
```

**Por qué es candidato a refactorización.** No es solo volumen de código. Es que **un bug en este patrón hay que arreglarlo cuatro veces**, y al revisarlo se encontró exactamente ese caso:

> **Condición de carrera en `/incidents`.** Es la única de las cuatro que relanza la petición cuando cambian los filtros. Si se cambian dos filtros seguidos quedan dos peticiones en vuelo, y ninguna copia comprobaba cuál era la más reciente: si la primera respondía después que la segunda, sobreescribía la lista y la pantalla mostraba resultados que **no correspondían a los filtros seleccionados**. Ninguna de las cuatro copias tenía guarda contra esto.

**Cómo queda la abstracción.** `uis/backoffice/hooks/useAsyncData.ts`:

```tsx
const fetchIncidents = useCallback(() => getIncidents(filters), [filters]);
const { data, isLoading, error, reload } = useAsyncData<Incident[]>(
  fetchIncidents,
  "No se pudo cargar la lista de incidencias. …",
);
```

Devuelve `{ data, isLoading, error, reload }`. Internamente lleva un contador de peticiones (`requestIdRef`) que descarta cualquier respuesta que ya no sea la última pedida, y una guarda de desmontaje. El `fetcher` debe venir envuelto en `useCallback` por quien llama, porque es dependencia del efecto: una función nueva en cada render provocaría un bucle de peticiones (documentado en el JSDoc del hook).

### R2 — El bloque JSX de estados de carga/error/vacío, repetido en 4 archivos → **componente compartido**

**Dónde aparece:** los mismos cuatro archivos, más una variante en `components/ProviderDirectory.tsx`.

**Qué se repite.** El mismo JSX carácter por carácter, con las mismas clases y los mismos estilos inline:

```tsx
{isLoading && <p style={{ color: "var(--muted)" }}>Cargando …</p>}

{!isLoading && loadError && (
  <div className="panel" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
    <span className="error-text">{loadError}</span>
    <button type="button" onClick={() => void loadX()}>Reintentar</button>
  </div>
)}

{!isLoading && !loadError && items.length === 0 && ( /* panel de vacío */ )}
{!isLoading && !loadError && items.length > 0 && ( /* contenido */ )}
```

**Por qué es candidato.** Aparte de la duplicación, esa cascada de condiciones negadas (`!isLoading && !loadError && …`) es fácil de escribir mal al añadir una pantalla nueva: basta olvidar una negación para que el contenido se pinte a la vez que el mensaje de error. Y el proyecto tiene una regla explícita, de la auditoría de gestión de errores, de que **todo estado de error debe llevar su CTA de reintento**; con el bloque copiado a mano, cumplirla depende de que quien copie no se deje el botón.

**Cómo queda la abstracción.** `uis/backoffice/components/AsyncSection.tsx`:

```tsx
<AsyncSection
  isLoading={isLoading}
  error={error}
  onRetry={reload}
  loadingLabel="Cargando productos…"
  isEmpty={products.length === 0}
  emptyLabel="Todavía no se ha registrado ningún material sanitario."
>
  {/* el contenido real */}
</AsyncSection>
```

Encadena los estados con `return` tempranos en vez de condiciones negadas, y el botón "Reintentar" deja de ser opcional: viene con el componente.

---

## 5. Skills de agente

El brief planteaba instalar opcionalmente skills externas (`core-web-vitals`, `performance`, `web-perf`) para guiar la corrección. **No se instaló ninguna.** El diagnóstico se hizo con la evidencia que ya daban los informes JSON de Lighthouse (`cls-culprits-insight`, `lcp-discovery-insight`, `lcp-breakdown-insight`, `image-delivery-insight`, `errors-in-console`) y con la documentación de Next.js 16 incluida en `node_modules/next/dist/docs/`, que es la fuente obligatoria según `apps/talent-pipeline-tracker/AGENTS.md` para esta versión.

Esa consulta a los docs locales evitó un error concreto: en Next.js 16 la prop `priority` de `next/image` está **deprecada** en favor de `preload`, y la propia documentación recomienda `loading="eager"` o `fetchPriority="high"` antes que `preload`. Escribir `priority` de memoria habría usado una API obsoleta.

---

## 6. Fuera de alcance

Decisiones tomadas deliberadamente, para que no se lean como olvidos:

- **`unused-javascript` / `legacy-javascript` / `network-dependency-tree`** aparecen como fallos en las cinco mediciones. Son características del runtime de Next.js 16 y de los polyfills que emite su compilador. Corregirlos exigiría tocar la configuración del bundler o la arquitectura de la app, y el brief prohíbe explícitamente reestructurar los frontends para esta auditoría.
- **`render-blocking-insight`** apunta al `<link rel="stylesheet">` de `globals.css`. Eliminarlo requeriría extraer CSS crítico e inyectarlo inline; no compensa con hojas de estilo de 38 y 267 líneas.
- **Vistas autenticadas del backoffice.** Ver limitación 1.3. Su coste de datos ya se atacó en el hito de caching (`CACHING_REPORT.md`): caché TTL de 30 s en `GET /inventory/products` y de 60 s en `GET /api/incidents/summary`, más lazy loading con `next/dynamic` en `ProviderForm` y `OrderDetailPanel`.
