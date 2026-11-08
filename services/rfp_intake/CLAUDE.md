# services/rfp_intake — API de RFPs y stream SSE

La lógica (grafos, reglas, estados) está en `data/pipelines/rfp_intake/CLAUDE.md`: léelo antes de cambiar nada aquí.

- Rutas: `POST /rfp-tickets` (multipart, 202 + `ticket_id`), listados y detalle, `/handoff`, `/response-handoff`, `POST …/sections/{dept}/decision`, `POST …/arbitration`, `GET …/final-document`, `GET …/trace`, `GET /rfp-tickets/events` (SSE).
- `GET /rfp-tickets/events` va **declarado antes de `/{ticket_id}`** y hereda el JWT del router.
- `/handoff` y `/response-handoff` responden mientras exista el handoff, aunque el ticket haya avanzado de parte.
- `events.py` (`RfpEventHub`): una suscripción a Redis por proceso, una cola por navegador (100; cliente lento → stream cerrado, nunca eventos perdidos en silencio), keep-alive 15 s. `Cache-Control: no-transform` para que el proxy de Next no acumule.
- `install_shutdown_signal_hook()` en el **startup**: sin él uvicorn espera a que cierren los streams y queda zombi ("Waiting for connections to close"). Parchear `uvicorn.Server.handle_exit` al importar no sirve. Un zombi no aparece en `lsof -sTCP:LISTEN`: matar por PID.

Tests: `services/api/tests/` (endpoints y stream).
