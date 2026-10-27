# Notificaciones en tiempo real de RFPs (SSE)

Sistemas en Tiempo Real, Parte 1. Rama `feature/sse-notifications`, apilada sobre `feature/rfp-approval-completion`, 2026-10-27.

Problema (RFI del equipo comercial): el equipo de Tom Callahan solo se enteraba de una RFP nueva recargando `/rfps`. Ahora la pantalla la muestra sola en cuanto el ticket se registra como RFP válida, y se reconecta sola si se cae la conexión.

Sin modelos ni agentes: esta parte es solo una capa de comunicación.

## 1. Flujo

```
worker Celery (Parte 1)                API FastAPI                         navegador (/rfps)
run_intake → graph.stream             RfpEventHub (1 por proceso)          useRfpEvents
  nodo `orchestrate` termina  ──►  Redis pub/sub  ──►  cola por conexión  ──►  fetch + ReadableStream
  notify_ticket_created()          healthcore:rfp-events   GET /rfp-tickets/events   (Authorization: Bearer)
```

- **Cuándo** (decisión del usuario): al terminar el nodo `orchestrate`, que solo corre si el clasificador aceptó el documento. Es el momento del BRIEFING ("clasificado como una RFP válida y el flujo empieza a procesarlo"), con el ticket aún `analizando`, y ya hay cliente, país y tipo de programa. Un documento que no es RFP o que trae PHI nunca llega ahí, así que no se avisa.
- **Por qué Redis**: el aviso nace en el worker (otro proceso) y los metadatos aún no están en la base de datos (la Parte 1 lo guarda todo al final, en una transacción). Redis ya estaba en el proyecto: no hay dependencias nuevas.
- **Un publicador que nunca lanza**: si Redis falla, se registra `rfp_event_publish_failed` y el análisis sigue. La pantalla lo recupera (§4).

## 2. Contrato del evento

```text
id: 6f1c1e5e-2d0b-4d7e-9a43-0d6c2a7c1b11
event: rfp_ticket_created
data: {"ticket_id": "6f1c1e5e-…", "rfp_id": "RFP-20261027-0001", "client_name": "Westbrook Manufacturing", "client_country": "US", "program_type": ["occupational_health"], "status": "analizando", "created_at": "2026-10-27T14:32:00Z"}

```

- Campos del BRIEFING §3, con **los nombres y valores que ya usa el sistema de RFPs** (BRIEFING §5): `status` es `analizando` (no `analyzing`), `ticket_id` es el UUID del ticket y `program_type` es la lista que guarda `rfp_metadata`.
- JSON plano en `data:`, sin sobre `{"event","data"}`. El sobre solo existe dentro de Redis, para que un tipo de evento nuevo use el mismo canal.
- **Sin PHI, en dos capas**: el emisor construye el payload con una lista cerrada (`TICKET_CREATED_FIELDS`) y la API vuelve a filtrar cada mensaje de Redis (`parse_message`). Un campo de más publicado en el canal no llega al navegador (hay un test con `patient_name`).
- Cabeceras: `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform` (el `no-transform` evita que el middleware de compresión de Next.js acumule el stream), `X-Accel-Buffering: no`, `Connection: keep-alive`. Primer fragmento `: connected` inmediato y `: keep-alive` cada 15 s sin eventos.
- **Autenticación**: el mismo JWT del backoffice, heredado del router `/rfp-tickets` (`get_current_user`). Sin token o con uno inválido, 401 y ni una suscripción. El token se valida al conectar; si caduca a mitad, la siguiente reconexión da 401 y el cliente lleva al login.

## 3. Reparto en la API

`services/rfp_intake/events.py`:

- `RfpEventHub`: **una** suscripción a Redis por proceso y una `asyncio.Queue` (máximo 100) por navegador. Si Redis cae, reintenta con backoff hasta 30 s; el stream sigue abierto con keep-alives.
- **Cliente lento → se le cierra el stream**, no se le descartan eventos en silencio ni crece la memoria. Reconectará y recuperará (§4).
- `subscribe()` espera (máx. 2 s) a que la escucha de Redis esté activa antes de responder: cuando el cliente recibe las cabeceras, ya está suscrito.
- La ruta `/events` está declarada antes de `/{ticket_id}` (si no, "events" se leería como UUID y daría 422).

## 4. Reconexión y recuperación

Cliente: `types/rfpEvents.ts` (lógica pura), `services/rfpEvents.ts` (una conexión) y `hooks/useRfpEvents.ts` (ciclo de vida).

- **`fetch` + `ReadableStream`, no `EventSource`**: `EventSource` no permite enviar `Authorization: Bearer`. El parser SSE (`createSseParser`) es incremental: une eventos partidos entre trozos y un `\r\n` partido.
- **Backoff**: 1 s, 2 s, 4 s… hasta 30 s, ±20 % de azar para que muchas pestañas no reconecten a la vez; vuelve a 1 s tras una conexión buena. El evento `online` del navegador adelanta el reintento. Un 401 no se reintenta.
- **Vigilante de inactividad (45 s = 3 keep-alives)**: si no llega ni un byte, se aborta y se reconecta. Imprescindible, no teórico: en la verificación, al matar la API con `kill -9`, el proxy de Next.js **no cerró** la respuesta al navegador, que seguía mostrando "En directo" sin recibir nada; el vigilante lo detectó y reconectó.

**Estrategia de recuperación elegida: refetch de la lista + SSE para lo posterior.**

1. Al (re)conectar, el cliente pide `GET /rfp-tickets` una vez. Como el servidor ya está suscrito cuando llegan las cabeceras, no queda hueco entre esa lectura y el siguiente evento.
2. `findMissedTickets(known, fetched)` decide qué tickets merecen aviso "recuperado". `known` = la lista al cargar la pantalla + los avisos ya mostrados; **el polling de estados no lo amplía** (un ticket que trajo el polling con el stream caído sigue mereciendo aviso).
3. Criterio (delegado al asistente): avisar solo de lo que en directo también habría avisado. Se excluyen `descartado`, `error` y los `analizando` sin cliente (pueden no haber pasado aún el clasificador).
4. La comparación se repite **en cada refresco del polling**, no solo al reconectar. Gotcha encontrado al planear la verificación: una RFP clasificada durante el corte cuyo análisis aún no ha terminado al reconectar está `analizando` sin cliente; su evento ya pasó y, comparando solo al reconectar, no avisaría nunca. Con el polling, avisa en cuanto la lista la muestra con cliente.

**Deduplicación**: los avisos viven en un `Map` por `ticket_id`. El mismo ticket nunca aparece dos veces, llegue en directo, recuperado o repetido (un reintento de Celery o `scripts/reprocess_rfp.py` vuelven a publicar; la entrega es "al menos una vez"). `mergeArrivals` tampoco duplica filas: completa la fila existente con cliente y país del aviso mientras la lista no los traiga.

**Por qué no `Last-Event-ID`**: necesitaría guardar los eventos en el servidor (los metadatos no están en la base de datos hasta el final de la Parte 1) y añadir una tabla o un buffer con su política de expiración. La lista de tickets ya es la fuente de verdad.

## 5. Interfaz

`/rfps`: indicador "En directo / Sin conexión · reintento en N s / Conectando…"; un aviso por RFP nueva con borde ámbar (el color de marca, que ningún otro dato del dashboard usa de fondo), "requiere procesamiento", enlace al ticket y "Ocultar"; los recuperados con borde discontinuo y "no llegó en directo"; fila resaltada con la etiqueta NUEVA. Un evento no vuelve a pedir la lista: se añade al estado. El polling de 3 s se mantiene **solo para ver avanzar los estados** (decisión del usuario).

## 6. Gotcha real: la API no se apagaba con un stream abierto

Encontrado al cortar la API en la verificación manual; los tests no lo veían. Al recibir SIGTERM, uvicorn **espera a que se cierren todas las conexiones** antes de lanzar los eventos de shutdown, y un stream SSE nunca se cierra solo: la API quedaba en "Waiting for connections to close" para siempre (también cada recarga de `--reload` y cada `docker stop`), sirviendo keep-alives desde un proceso zombi que ya no escuchaba en el puerto (`lsof -sTCP:LISTEN` no lo encuentra: matarlo por PID).

- Primer intento fallido: parchear `uvicorn.Server.handle_exit` al importar. Uvicorn instala sus manejadores de señal **antes** de importar la app, así que guardaba el método original. El test pasaba porque llamaba al método después del parche: no reproducía el orden real.
- Arreglo: `install_shutdown_signal_hook()` en el **startup** de la app envuelve el manejador de SIGINT/SIGTERM ya instalado: cierra los streams (los navegadores reconectan con su backoff) y llama al de uvicorn. El test instala primero un manejador "de uvicorn" y después el gancho, como en la vida real. Verificado con el proceso real: con un stream abierto, SIGTERM → "Application shutdown complete" en menos de 1 s.

## 7. Verificación manual (2026-10-27)

Redis en Docker + API y backoffice nativos + Supabase real; token local con `create_access_token(subject=<id>, role=<rol>)`, sin login.

| Prueba | Resultado |
|---|---|
| `curl` sin token | 401 |
| `curl` con token | cabeceras de §2 y `: connected`; un `: keep-alive` a los 15 s |
| Publicar con el código del worker (`publish_event`) + campo `patient_name` | llega `rfp_ticket_created` con los 7 campos; `patient_name` filtrado |
| Chrome en `/rfps` (a través del proxy de Next.js) | "En directo"; al publicar, aviso + fila NUEVA sin recargar |
| Mismo evento publicado dos veces | un único aviso |
| API caída (SIGTERM, con el arreglo de §6) | "Sin conexión · reintento en 8 s" (1 → 2 → 4 → 8 s) |
| Ticket insertado en Supabase durante el corte (`RFP-PRUEBA-SSE-REC`, `analisis_completo`) | al volver la API, reconexión sola y aviso "no llegó en directo (recuperada al sincronizar)"; sin duplicar el anterior. Ticket borrado después |
| `kill -9` de la API | el proxy de Next.js no cerró la respuesta; el vigilante de 45 s reconectó |

No verificado: una RFP real subida por la UI con el worker y deepseek (el emisor está cubierto por los tests del runner con el grafo real y un modelo falso, y el camino Redis → navegador con el publicador real).

## 8. Preguntas de diseño (para el PR)

**¿Conexiones independientes o una capa intermedia? ¿Y con 50 personas?** Cada pestaña tiene su conexión SSE (es la forma de empujarle datos), pero no su propia suscripción: el `RfpEventHub` de cada proceso de la API mantiene **una** conexión a Redis y reparte a una cola en memoria por pestaña. Con 50 personas: 50 conexiones HTTP largas y 50 colas pequeñas, pero una suscripción a Redis por proceso, y el worker publica una sola vez. Es trivial para asyncio (cada conexión inactiva es una corrutina dormida). Lo que habría que vigilar: los límites de conexiones de los proxies, que con varios procesos de uvicorn cada uno tiene su hub (Redis pub/sub ya reparte a todos), y la reconexión en masa tras reiniciar la API, que se suaviza con el ±20 % de azar del backoff.

**¿Qué estrategia de recuperación y cómo se evitan duplicados?** Refetch de la lista al reconectar (y en cada refresco del polling) + SSE para lo posterior, con un criterio explícito de qué merece aviso (§4). Duplicados: un `Map` por `ticket_id` para los avisos y una fusión con la lista por `ticket_id`.

**¿Por qué SSE y no WebSockets? ¿Cuándo dejaría de serlo?** La notificación es unidireccional (servidor → navegador), y SSE es HTTP normal: el mismo JWT por cabecera, el mismo proxy de Next.js, sin protocolo nuevo, y el framing `event:`/`data:` ya da eventos con nombre. Dejaría de ser la herramienta correcta si el canal tuviera que ser bidireccional y de baja latencia: por ejemplo, que Tom aceptara o asignara el ticket desde el mismo canal, o un chat entre departamentos (la Parte 2). Una acción puntual sigue cabiendo en un `POST` normal; un diálogo continuo pide WebSocket.

## 9. Mapa del código

- `data/pipelines/rfp_intake/notifications.py`: payload y publicación en Redis.
- `data/pipelines/rfp_intake/runner.py`: llamada al terminar `orchestrate`.
- `services/rfp_intake/events.py`: hub, framing SSE, gancho de apagado. `router.py`: `GET /rfp-tickets/events`. `services/api/main.py`: startup y shutdown.
- `uis/backoffice/types/rfpEvents.ts`, `services/rfpEvents.ts`, `hooks/useRfpEvents.ts`, `app/rfps/page.tsx`, estilos `.rfp-arrival*`/`.rfp-live*` en `app/globals.css`. `services/http.ts` exporta `apiUrl` y `redirectToLogin`.
- Tests: `services/api/tests/test_rfp_events.py` (11), los 5 de "Aviso en tiempo real" en `tests/pipelines/test_rfp_intake.py` (con el fixture automático `published_rfp_events` de `tests/pipelines/conftest.py`, que impide publicar en un Redis real), y `uis/backoffice/__tests__/rfpEvents.test.ts` (18) + `useRfpEvents.test.tsx` (4).
