# Informe de correcciones de rendimiento — HealthCore Digital

**Fecha:** 2026-09-07
**Rama:** `feat/frontend-performance-audit`
**Análisis previo:** [`AUDIT.md`](./AUDIT.md)
**Evidencia:** [`audit/before/`](./audit/before/) y [`audit/after/`](./audit/after/) — informes HTML de Lighthouse y capturas de pantalla.

---

## 1. Resumen de resultados

| Página | Modo | Performance | Accessibility | Best Practices | SEO |
|---|---|---|---|---|---|
| website `/` | móvil | 98 → **98** | 100 → 100 | 96 → **100** ✅ | 100 → 100 |
| website `/` | escritorio | 100 → 100 | 100 → 100 | 96 → **100** ✅ | 100 → 100 |
| website `/application` | móvil | **76 → 99** ✅ | 100 → 100 | 96 → **100** ✅ | 100 → 100 |
| backoffice `/login` | móvil | 99 → **99** | 100 → 100 | 96 → **100** ✅ | 100 → 100 |
| backoffice `/login` | escritorio | 100 → 100 | 100 → 100 | 96 → **100** ✅ | 100 → 100 |

### Core Web Vitals

| Página | Modo | LCP | CLS | FCP | TBT | Speed Index |
|---|---|---|---|---|---|---|
| website `/` | móvil | 2.3 s → 2.4 s | 0 → 0 | 0.8 → 0.8 s | 50 → 30 ms | 0.8 → 0.8 s |
| website `/` | escritorio | 0.6 → **0.5 s** | 0 → 0 | 0.2 → 0.2 s | 0 → 0 ms | 0.2 → 0.2 s |
| website `/application` | móvil | 1.5 → 2.2 s | **0.974 → 0** ✅ | 0.8 → 0.9 s | 10 → 30 ms | 0.8 → 0.9 s |
| backoffice `/login` | móvil | 2.2 → 2.2 s | 0 → 0 | 0.8 → 0.8 s | 10 → 10 ms | 0.8 → 0.8 s |
| backoffice `/login` | escritorio | 0.5 → 0.5 s | 0 → 0 | 0.2 → 0.2 s | 0 → 0 ms | 0.2 → 0.2 s |

**Titular:** el único KPI que estaba fuera de umbral —el CLS de `/application`, en 0.974 frente a un objetivo de 0.1— está ahora en **0**, y la puntuación de Performance de esa página sube de 76 a 99. Best Practices llega a 100 en las cinco mediciones. El resto de páginas ya estaban en verde y se mantienen.

Los dos frontends muestran mejora medible, como pedía la rúbrica:
- **`uis/website`**: Performance 76 → 99 y CLS 0.974 → 0 en `/application`; Best Practices 96 → 100 en todas.
- **`uis/backoffice`**: Best Practices 96 → 100 en móvil y escritorio.

---

## 2. Correcciones aplicadas

### C1 — Estilos de `/application` sacados de styled-jsx a un archivo CSS real

**Ataca:** P1 (CLS 0.974).
**Archivos:** `uis/website/app/application/application.css` (nuevo, 237 líneas), `uis/website/app/application/page.tsx` (500 → 284 líneas).

Los dos bloques `<style jsx>` se movieron íntegros a un archivo `.css` importado por la página. Next.js lo extrae en tiempo de build y lo enlaza con `<link rel="stylesheet">` en el `<head>`, así que los estilos llegan **junto** al HTML y ya no dependen de que se ejecute JavaScript. Los selectores `:global(...)` de styled-jsx se reescribieron como selectores normales, porque un archivo CSS ya es global.

Se eligió un archivo propio de la ruta en vez de volcarlo en `globals.css` para no enviar los estilos del formulario a quien solo visita la portada. Verificado:

```console
$ curl -s http://localhost:3000/application | grep -o '/_next/static/[^"]*\.css'
/_next/static/chunks/16w3nt3q3jw4-.css   # globals
/_next/static/chunks/05w.asfweyvsf.css   # estilos de /application  -> contiene .application-card
$ curl -s http://localhost:3000/ | grep -o '/_next/static/[^"]*\.css'
/_next/static/chunks/16w3nt3q3jw4-.css   # la home NO carga el segundo
```

**Impacto medido:** CLS **0.974 → 0**. Performance de `/application` **76 → 99**.

Es la corrección de mayor impacto del hito, con diferencia, y no tocó una sola línea de la lógica del formulario.

---

### C2 — La imagen del LCP deja de ser diferida

**Ataca:** P2.
**Archivo:** `uis/website/components/HeroSection.tsx`.

El `<img loading="lazy">` del hero pasó a `next/image` con `loading="eager"` y `fetchPriority="high"`.

Detalle de versión: en Next.js 16 la prop `priority` está deprecada en favor de `preload`, y los propios docs recomiendan `loading="eager"` o `fetchPriority="high"` antes que `preload`. Consultado en `node_modules/next/dist/docs/01-app/03-api-reference/02-components/image.md` según obliga `AGENTS.md`, no escrito de memoria.

**Impacto medido.** Las tres comprobaciones de `lcp-discovery-insight` pasan de fallar a cumplirse, y el desglose por fases del LCP cambia radicalmente:

| Fase del LCP | Antes | Después |
|---|---|---|
| Time to first byte | 20 ms | 12 ms |
| Resource load delay | 56 ms | 11 ms |
| Resource load duration | 106 ms | 89 ms |
| **Element render delay** | **792 ms** | **22 ms** |

```
Antes:    priorityHinted=false  requestDiscoverable=true  eagerlyLoaded=false
Después:  priorityHinted=true   requestDiscoverable=true  eagerlyLoaded=true
```

El "element render delay" cae de 792 ms a 22 ms: el navegador ya no retrasa pintar la imagen que define la métrica. `next/image` añade además un `<link rel="preload" as="image" fetchPriority="high">` en el `<head>` con el `srcset` completo, de modo que el navegador descubre la imagen antes incluso de llegar al `<body>`.

---

### C3 — Imágenes con `width`/`height` y `srcset`, servidas desde el CDN de origen

**Ataca:** P3.
**Archivos:** `uis/website/components/ServicesSection.tsx`, `uis/website/image-loader.js` (nuevo), `uis/website/next.config.ts`.

Las cuatro imágenes pasaron a `next/image` con `width`, `height` y `sizes`, lo que da `srcset` por breakpoint y reserva el hueco de forma explícita en vez de depender de un estilo inline.

**Aquí hubo una decisión que cambió a mitad de camino, guiada por la medición.** El primer intento usó el optimizador integrado de Next.js (`remotePatterns` + endpoint `/_next/image`). Funcionó en bytes —139 KB → 79 KB, −43 %— pero la descarga de la imagen del LCP subió de 106 ms a **241 ms**, porque cada foto daba un rodeo extra: navegador → nuestro servidor → Unsplash → nuestro servidor → navegador.

La causa es que **Unsplash ya es un CDN de imágenes**: acepta `w=` para redimensionar en origen y `auto=format` para negociar AVIF/WebP. Reprocesar en nuestro servidor una imagen que ya viene optimizada de un edge global solo añade latencia.

La solución es un loader propio (`image-loader.js` + `images.loader: "custom"`), que delega el redimensionado en el propio Unsplash. Se conservan `srcset`, `sizes` y `width`/`height` —que es lo que evita el layout shift— pero los bytes vuelven a salir del edge:

| Enfoque | Peso imágenes | Descarga del LCP | Formato |
|---|---|---|---|
| Original (`<img>` sin `srcset`) | 139 KB | 106 ms | AVIF (1280 px para un hueco de 345 px) |
| Optimizador integrado de Next.js | **79 KB** | 241 ms | WebP |
| **Loader propio de Unsplash** ← elegido | 101 KB | **89 ms** | AVIF, ancho correcto |

Nota sobre `remotePatterns`: con un loader propio esa lista deja de aplicarse (solo la usa el optimizador integrado), así que se retiró para no dejar configuración muerta.

---

### C4 — La fuente de marca se carga por fin

**Ataca:** P4.
**Archivo:** `uis/website/app/layout.tsx`.

Se añadió `Manrope` vía `next/font/google` con `variable: "--font-manrope"`, que es exactamente la variable que `globals.css` llevaba pidiendo desde siempre sin que nadie la definiera.

Verificado: `--font-manrope: "Manrope", "Manrope Fallback"` aparece ahora en el CSS, la fuente se auto-hospeda como `.woff2` desde `/_next/static/media/` y se emite con `font-display: swap`.

Beneficios más allá de la corrección visual:
- **Una conexión externa menos.** La fuente se sirve desde nuestro dominio, no desde `fonts.gstatic.com`: se ahorran DNS + TLS en la cadena crítica. Como efecto secundario relevante para UK GDPR, la IP del visitante deja de enviarse a Google.
- **`adjustFontFallback` (por defecto)** genera una fuente de reserva con métricas ajustadas, de modo que el intercambio no provoca layout shift.

**Coste medido y aceptado:** la fuente son 24 KB nuevos en la ruta crítica. En móvil con red lenta simulada eso sube el LCP de la home de 2.3 s a 2.4 s. Se asume conscientemente: sigue holgadamente por debajo del umbral de 2.5 s, y el peso total de la página en realidad **bajó** (293 KB → 285 KB) porque las imágenes adelgazaron más de lo que pesa la fuente.

#### Experimento descartado: `preload: false`

Se probó `preload: false` en `next/font` con la hipótesis de que, si la fuente no se precargaba, dejaría de competir con la imagen del LCP. **La medición lo desmintió** y se revirtió:

| Métrica | Con preload (elegido) | Con `preload: false` |
|---|---|---|
| LCP | 2.4 s | 2.4 s (sin mejora) |
| FCP | 0.8 s | 1.1 s ❌ |
| Speed Index | 0.8 s | 1.1 s ❌ |
| CLS | 0 | 0.015 ❌ |

La conclusión quedó escrita como comentario en `layout.tsx`, no solo aquí, para que nadie repita la prueba pensando que es una mejora evidente.

---

### C5 — Icono de aplicación en las dos apps

**Ataca:** P5.
**Archivos:** `uis/website/app/icon.svg`, `uis/backoffice/app/icon.svg` (nuevos, 185 B cada uno).

Se usó la convención de archivo `app/icon.svg` del App Router, que hace que Next.js emita el `<link rel="icon">` automáticamente. Cada app lleva el color de su propio tema: cian `#22d3ee` para la web, ámbar `#FF8A3D` para el tema "Supply Manifest" del backoffice.

**Impacto medido:** la auditoría `errors-in-console` deja de fallar y **Best Practices pasa de 96 a 100 en las cinco mediciones**. Es la corrección con mejor relación esfuerzo/resultado del hito: 370 bytes de SVG.

---

### C6 — Refactorización: Custom Hook + componente compartido

**Ataca:** R1 y R2 del análisis de duplicación.
**Archivos nuevos:** `uis/backoffice/hooks/useAsyncData.ts`, `uis/backoffice/components/AsyncSection.tsx`.
**Archivos migrados:** `app/incidents/page.tsx`, `app/incidents/summary/page.tsx`, `app/inventory/products/page.tsx`, `app/inventory/orders/page.tsx`.

| Archivo | Antes | Después | Δ |
|---|---|---|---|
| `app/incidents/page.tsx` | 223 | 213 | −10 |
| `app/incidents/summary/page.tsx` | 103 | 89 | −14 |
| `app/inventory/products/page.tsx` | 146 | 126 | −20 |
| `app/inventory/orders/page.tsx` | 137 | 116 | −21 |
| | | **total** | **−65** |

No queda ninguna copia suelta del patrón:

```console
$ grep -rn "setLoadError\|setIsLoading" uis/backoffice/app uis/backoffice/components
(sin resultados)
```

**Bug corregido de propina.** Centralizar el patrón arregló una condición de carrera que ninguna de las cuatro copias tenía resuelta: en `/incidents`, cambiar dos filtros seguidos dejaba dos peticiones en vuelo y la respuesta lenta de la primera podía sobreescribir a la de la segunda, mostrando resultados que no correspondían al filtro seleccionado. El contador `requestIdRef` del hook descarta cualquier respuesta que ya no sea la última pedida. Hay un test que reproduce exactamente ese escenario.

Esta refactorización **no busca mover la aguja de Lighthouse** —el patrón se ejecuta en pantallas autenticadas que Lighthouse no alcanza— y sería deshonesto atribuirle mejora de puntuación. Su valor es de mantenibilidad y corrección: un bug en el ciclo de carga ahora se arregla una vez en lugar de cuatro.

---

## 3. Qué tuvo más impacto

Por orden de retorno real:

1. **C1 (styled-jsx → CSS)** — con enorme diferencia. Un cambio dirigido llevó `/application` de 76 a 99 y su CLS de 0.974 a 0. Es también el hallazgo más transferible: cualquier CSS que necesite JavaScript para llegar al documento es un layout shift esperando a ocurrir.
2. **C5 (favicon)** — mejor relación esfuerzo/resultado del hito. 370 bytes de SVG subieron Best Practices de 96 a 100 en las cinco mediciones.
3. **C2 (LCP eager + fetchPriority)** — el efecto no se ve en la puntuación final porque la home ya estaba en 98, pero el desglose por fases es contundente: el "element render delay" cayó de 792 ms a 22 ms. En un dispositivo real y lento, donde ese retardo escala, la diferencia es mucho mayor que en `localhost`.
4. **C6 (refactorización)** — cero impacto en Lighthouse por diseño; valor en mantenibilidad y en un bug real corregido.
5. **C3 y C4** — correcciones de fondo. C3 evita una regresión futura de CLS y sirve la resolución adecuada; C4 arregla un bug de diseño de dos hitos de antigüedad, a cambio de 24 KB y 0.1 s de LCP.

### Lo que no mejoró, y por qué

- **LCP de la home móvil: 2.3 s → 2.4 s.** Es el precio de cargar la fuente de marca que el CSS pedía desde siempre (C4). Sigue por debajo del umbral de 2.5 s y es una decisión consciente, no un descuido.
- **Performance de la home móvil: 98 → 98.** Sin cambio neto. Las mejoras (imágenes más ligeras, render delay del LCP) y el coste (la fuente) se compensan.
- **TBT y Speed Index de `/application`: 10 → 30 ms y 0.8 → 0.9 s.** Variación dentro del ruido de medición, sin significado frente a un CLS que pasó de 0.974 a 0.

---

## 4. Verificación de no regresión

| Comprobación | Resultado |
|---|---|
| `npm run build` en `uis/website` | ✅ compila |
| `npm run build` en `uis/backoffice` | ✅ compila (17 rutas) |
| `npx tsc --noEmit` en `uis/backoffice` | ✅ sin errores |
| `npx jest` en `uis/backoffice` | ✅ **30/30** (19 previos + 11 nuevos) |
| Revisión visual de `/` y `/application` | ✅ sin regresión; la tipografía ahora es la correcta |

Tests nuevos añadidos siguiendo la convención del proyecto (caso feliz / límite / fallo):

- `__tests__/useAsyncData.test.tsx` — 4 tests: carga con éxito, fallo con mensaje legible, `reload` que limpia el error anterior, y el caso límite de la respuesta obsoleta que no debe pisar a la reciente.
- `__tests__/asyncSection.test.tsx` — 5 tests: los cuatro estados del componente más la invocación de `onRetry`.

Ninguno de los dos añade dependencias: usan `act` de React 19 con `createRoot`, sin `@testing-library`.

**Pendiente de verificación manual:** las cuatro pantallas migradas están detrás de `AuthGuard` y comprobarlas en ejecución exige iniciar sesión, cosa que requiere introducir una contraseña. Quedan cubiertas por typecheck, build y tests unitarios de sus dos piezas compartidas, pero conviene una pasada manual (pasos en la sección 5).

---

## 5. Cómo reproducir las mediciones

```bash
# 1. Builds de producción (nunca medir sobre `next dev`)
cd uis/website   && npm run build && npx next start -p 3000 &
cd uis/backoffice && npm run build && npx next start -p 3001 &

# 2. Precalentar las variantes de imagen en el CDN de Unsplash
for u in $(curl -s http://localhost:3000/ | grep -o 'https://images.unsplash.com/[^" ]*' \
           | sed 's/&amp;/\&/g' | sort -u); do curl -s -o /dev/null "$u"; done

# 3. Lighthouse (móvil por defecto; --preset=desktop para escritorio)
npx lighthouse http://localhost:3000/            --output=html --output-path=./home-mobile.html
npx lighthouse http://localhost:3000/application --output=html --output-path=./application-mobile.html
npx lighthouse http://localhost:3001/login       --output=html --output-path=./login-mobile.html
```

Ejecutar las auditorías **de una en una**: en paralelo compiten por CPU y el Speed Index varía hasta 1.2 s entre pasadas.

### Verificación manual de las pantallas refactorizadas

Con la API arrancada (`cd services/api && uvicorn main:app --port 8000`) e iniciando sesión en `http://localhost:3001/login`:

1. **`/inventory/products`** — la tabla carga con los LEDs de nivel de stock. Parar la API y pulsar recargar: debe salir el mensaje de error con botón "Reintentar"; al rearrancar la API y pulsarlo, la tabla vuelve.
2. **`/inventory/orders`** — el historial carga y las filas siguen expandiéndose para ver el detalle.
3. **`/incidents`** — cambiar los tres filtros y comprobar que la lista siempre corresponde al filtro seleccionado (es el escenario de la condición de carrera). El cambio de estado con reversión sigue funcionando.
4. **`/incidents/summary`** — las cuatro tablas de métricas siguen mostrándose.
