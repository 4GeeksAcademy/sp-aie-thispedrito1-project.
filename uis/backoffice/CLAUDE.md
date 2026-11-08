@AGENTS.md

# uis/backoffice — backoffice Next.js 16 (puerto 3001)

Interfaz interna completa contra `services/api`. **Toda la UI en español** (también Incidencias, aunque un CONTEXT antiguo la pidiera en inglés). Los errores literales de la API no se traducen.

```bash
npm run dev -- --port 3001
npm test                  # Jest (o npx jest --coverage); backoffice ≥60 %
npx tsc --noEmit
npm run lint              # aquí en realidad ejecuta next build
```

## Mapa

- `services/`: `http.ts` (`requestJson`, `ApiFieldError`, `ApiRequestError` con `status`, `apiUrl`, `redirectToLogin`, mensajes centralizados incluidos `FORBIDDEN_MESSAGES` y `FEDERATED_MESSAGES`), un `*Api.ts` por dominio, `session.ts`, `telemetry.ts`, `rfpEvents.ts`.
- `types/`: contratos y funciones puras testeables (`inventory.ts`, `telemetryReport.ts`, `businessReport.ts`, `rfpEvents.ts`, `chat.ts`, `access.ts`, `navigation.ts`…). Listados con su tipo propio (`IncidentListItem`, `SupplierListItem`); al recortar un campo en la API, actualizar el tipo y dejar que `tsc` encuentre los usos.
- `hooks/`: `useAsyncData`, `usePolling`, `useRfpEvents`, `useChatSocket`, `useAccessProfile`.
- `components/`: `AppShell` (barra lateral agrupada; datos en `types/navigation.ts`, activo = prefijo más largo; `NavItem.capability` + `visibleSections`), `AsyncSection`, `BarList`, `ThemeToggle`, `AuthGuard` (con `OPEN_ROUTES`), `ErrorTracking`, `PageViewTracker`, `WebVitals`.
- Sin variable de entorno por dominio: todo va por `NEXT_PUBLIC_API_URL` o el proxy `/api-proxy` de `next.config.ts`. Excepciones: `NEXT_PUBLIC_TELEMETRY_ENDPOINT` y `NEXT_PUBLIC_CHAT_WS_URL`.

## Reglas que no hay que romper

**Tema**
- Paleta "Turquesa clínico": claro por defecto (`:root`), oscuro en `[data-theme="dark"]`; marca `#0F766E`/`#2DD4BF`, distinta de los colores de estado.
- Todo color es un token CSS de `app/globals.css` (`--bg`, `--panel`, `--text`, `--muted`, `--brand`, `--brand-contrast`, `--line`, `--critical`/`--warning`/`--ok`, `--success-*`/`--error-*`). Nunca un hex en un `.tsx`; fondos traslúcidos con `color-mix(in srgb, var(--token) N%, transparent)`, nunca `rgba()` con el color escrito.
- `ThemeToggle` usa `data-theme` en `<html>` y `localStorage`; un script inline en el `<head>` lo aplica antes del primer paint, por eso `<html>` lleva `suppressHydrationWarning`.
- Nivel de stock = LED cuadrado + número monoespaciado; estado categórico = `.status-badge`. Son dos lenguajes visuales a propósito.

**Datos y errores**
- `useAsyncData` es el único sitio del ciclo cargando/éxito/error. Su `fetcher` debe ir en `useCallback` (si no, bucle de peticiones). Lleva guarda de carrera (`requestIdRef`).
- `AsyncSection` pinta cargando / error con "Reintentar" / vacío. Todo error tiene CTA.
- Nunca mostrar códigos de estado, stack traces ni JSON crudo; nada de `console.log` con datos de usuarios.
- `TelemetryService` es el único con su propio `fetch`: `track()` es fire-and-forget, cola en memoria, lotes cada 10 s o 20 eventos, 3 reintentos. Las variables `NEXT_PUBLIC_*` se leen con el literal `process.env.X` (Next no sustituye `globalThis.process?.env`).
- Lectura de reportes por `requestJson`, no por `TelemetryService` (un fallo debe mostrar "Reintentar"). `reportingApi.ts` convierte un 404 en `null`.

**Formularios y fechas**
- Rango de fechas: los `<input type="date">` son días incluidos y la API usa fin exclusivo en UTC. Convertir con un helper puro testeado (`toReportQuery`), todo en UTC.
- Formularios con validación propia llevan `noValidate` (si no, el globo nativo bloquea el submit en el idioma del navegador).
- Un efecto que rellena un input con datos cargados depende solo de los datos, no del valor del input.
- El formulario de salida de inventario usa el stock de la lista ya cargada (sin petición extra) y bloquea si la cantidad lo supera. `unit_cost` vacío = coste desconocido, nunca 0 (`parseUnitCost`).
- Leer un query param opcional con `window.location.search` en un `useEffect` en vez de `useSearchParams` (que exige `<Suspense>`).

**Pantallas concretas**
- `/telemetry` y `/reporting`: barras CSS (`BarList`), sin librería de gráficos (no tocar `package-lock.json`); muestran el `period` que devuelve el servidor. `/reporting` es solo lectura, coste por país y moneda (nunca USD + GBP), filas "Incompleto" con `clinics_with_unrecorded_cost`.
- `/rfps`: SSE para llegadas + polling de 3 s solo para estados. Recuperación al reconectar = refetch + `findMissedTickets` (excluye `descartado`, `error` y `analizando` sin cliente); avisos en un `Map` por `ticket_id`. Vigilante de 45 s (el proxy de Next no cierra si la API muere). `shouldPollTicket` sigue si hay una sección "en revisión" aunque el ticket pase por `error`.
- `/compliance-chat`: WebSocket directo a la API (`NEXT_PUBLIC_CHAT_WS_URL`, no por `/api-proxy`); token en el primer frame `auth`; "En directo" solo tras `session_snapshot`; reconexión 1 s → 30 s; reabre la última sesión no cerrada.
- `/auth/callback/microsoft` es ruta abierta (con y sin sesión) y usa `useRef` porque React estricto ejecuta el efecto dos veces y el `code` es de un solo uso.
- `next/dynamic` solo divide código si lo llama un Client Component (`ProviderForm`, `OrderDetailPanel`); no aplicarlo desde Server Components.

## Tests

Sin `@testing-library`: `act` de `react` + `createRoot` de `react-dom/client` con `globalThis.IS_REACT_ACT_ENVIRONMENT = true` (patrón en `__tests__/useAsyncData.test.tsx`). `jest.config.js` prioriza `.ts` sobre los `.js` compilados. jsdom no trae `TextDecoder` ni `ReadableStream`: usar el de `util` y un `body.getReader()` falso.

## Gotchas

- Lighthouse no puede medir vistas autenticadas (JWT en `localStorage` + `AuthGuard` → mide `/login`).
- `app/icon.svg` evita el 404 de `/favicon.ico`.
- Docker: `--webpack` + `watchOptions.pollIntervalMs` con `DOCKER_DEV=true`; borrar `.next` al cambiar de bundler.
- `experimental.externalDir`/`turbopack.root` en `next.config.ts` existen por el import relativo de `packages/shared/business-logic`.
- npm 11 bloquea scripts de instalación sin `npm install-scripts approve`; `next build` funciona igual.
