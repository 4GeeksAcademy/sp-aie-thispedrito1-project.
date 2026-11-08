# Progress - HealthCore Digital

Estado, hitos entregados, riesgos y próximos pasos. El detalle técnico de cada hito está en el `CLAUDE.md` de su módulo y en su documento de `docs/`; el historial, en git y en los PRs.

## Estado actual

- Plataforma con backend FastAPI monolítico, backoffice y web corporativa en Next.js 16, agentes LangGraph, pipeline de datos con Prefect, cola Celery y servidor MCP con OAuth.
- `main` incluye todo hasta la auditoría NIST (PR #45).
- **Pila pendiente de aprobación del profesor** (no mergear hasta que el usuario lo diga): #46 `feat/final-pitch` → #47 `feature/onboarding-orchestrator` → #48 `feature/supplier-contracts` → #49 `feature/financial-close` → #50 `feature/roles-permissions` → #51 `feature/federated-auth` → #52 `feature/audit-log`. Cada clase nueva se apila sobre la última.

## Hitos completados

| Hito | Módulo / documento |
|---|---|
| Briefing, gobernanza (`AGENTS.md`, `.agents/`, skills) y memory-bank | `memory-bank/`, `AGENTS.md` |
| Web corporativa migrada a React y backoffice inicial | `uis/website`, `uis/backoffice` |
| Auth: JWT, usuarios/perfiles, reset de contraseña con Resend (AUTH-01/03) | `services/api` |
| Batería de tests y auditoría de gestión de errores | `TESTING.md` |
| Gestor de incidencias con validación compartida | `services/api`, `packages/shared/incidents_validation` |
| Inventario sobre Supabase + interfaz del backoffice (Hito 5) | `services/api`, `uis/backoffice/app/inventory` |
| Rediseño visual, tema claro/oscuro y backoffice en español | `uis/backoffice` |
| Docker del monorepo | `docker-compose.yml` |
| Caché TTL y lazy loading | `CACHING_REPORT.md` |
| Telemetría: plan, captura, almacenamiento, reporte y dashboard | `docs/telemetry/`, `/telemetry` |
| Auditoría de rendimiento frontend | `AUDIT.md`, `REPORT.md` |
| Auditoría de serialización | `docs/serialization-audit.md` |
| Pipeline de negocio (diseño, Prefect resiliente, subflows y dashboard `/reporting`) | `data/pipelines/PIPELINE_DESIGN.md` |
| Job nocturno de telemetría (#DEV-53) | `docs/nightly-export.md` |
| Cola de tareas asíncronas (#DEV-55) | `docs/async-tasks.md` |
| Predicción de ventas y su evaluación | `docs/sales-forecast.md`, `data/eval/evaluation_report.md` |
| RAG y base de conocimiento (Hito 7) | `docs/rag/rag-design.md` |
| Agente LangGraph, Partes 1 y 2 (tools en vivo) | `docs/agent/agent-design.md`, `agent-tools.md` |
| Supabase unificado en `healthcore-data` | `CLAUDE.md` (gotchas transversales) |
| Servidor MCP con OAuth | `docs/mcp/mcp-server.md` |
| Memoria del agente (#MEM-092) y harness/guardrails (#SEC-114) | `docs/agent/agent-memory.md`, `agent-guardrails.md` |
| RFPs: recepción, respuesta, aprobación (Hito 9) | `docs/rfp-intake.md`, `rfp-response.md`, `rfp-approval.md` |
| Tiempo real: SSE de RFPs y chat de compliance por WebSocket | `docs/sse-notifications.md`, `docs/websocket-chat.md` |
| Auditoría OWASP Top 10 y endurecimiento del servidor | `docs/security/owasp-top10-audit.md` |
| Prácticas seguras de IA (NIST): rate limit, confirmación de cierres, auditoría | `docs/security/nist-ai-security-report.md` |
| Pitch final (vídeo fuera del repo), barra lateral y paleta turquesa | `uis/backoffice` |
| Orquestador de incorporaciones | `docs/onboarding/onboarding-orchestrator.md` |
| Proveedores y contratos (supervisor + especialistas) | `docs/supplier-contracts/supplier-contracts.md` |
| Cierre financiero mensual | `docs/financial-close/financial-close.md` |
| Roles y permisos (rol × departamento) | `docs/roles-permissions/roles-permissions.md` |
| Autenticación federada con Microsoft | `docs/federated-auth/federated-auth.md` |
| Registro de auditoría + pacientes sintéticos | `docs/audit-log/audit-log.md` |
| Documentación para agentes dividida por módulo (`CLAUDE.md` por carpeta) y memory-bank depurado | `CLAUDE.md` |

## En curso

- Consolidación del modelo canónico de candidatos en el tracker (divergencias `stage`/`step`).
- Mejora de la robustez de tipos en el frontend.

## Próximos pasos (prioridad)

1. Resolver los errores de lint por `any` y migrar a `unknown` + narrowing.
2. Contrato canónico de `Candidate` y notas en el tracker.
3. Migrar `VALID_SUPPLY_CATEGORIES` a las categorías del CONTEXT (`medication/ppe/consumable/equipment`); brecha conocida.
4. Persistir los eventos de backend que aún solo se loguean: `direct_stock_edit_rejected` y `outbound_order_rejected` pueden pasar `db_session` desde su ruta; `api_error_response` sigue bloqueado (se emite desde el handler global).
5. Conectar los eventos en stream a alertas reales (p. ej. `stock_threshold_triggered` → Marcus).
6. Pipeline de negocio: catálogo clínica→país (P5/P6) y captura de `department` (P4); blocks de Prefect si se adopta un servidor.
7. Índice único sobre `tags->>'eventId'` en `telemetry_events` con `ON CONFLICT DO NOTHING` (hoy un reintento duplica eventos).
8. Cola: construir y probar `worker`/`flower` en Docker; `--basic-auth` en Flower si sale de localhost.
9. Seguridad (OWASP §6): LangGraph 1.x, límite de intentos de login, aislamiento por clínica en las tools del agente, autorización por rol en la firma de RFP, cookie HttpOnly + CSP, imagen de producción sin Swagger ni `--reload`, `uis` sin root, subir Starlette/Pillow, python-jose → PyJWT.
10. Seguridad (NIST §6): BAA/DPA con el proveedor LLM y Supabase antes de datos reales (P0); rate limit en Redis con varios workers; alertas sobre los contadores de guardarraíles; plan de incidentes y RTO/RPO aprobados por Compliance.
11. Convertir `packages/shared/business-logic` en paquete npm real con npm workspaces (`transpilePackages` en `next.config.ts`, adiós a `externalDir`/`turbopack.root`); exige consolidar los `package-lock.json`.
12. Arreglar `test_onboarding_resume.py` para que no use `from conftest import Driver`.

## Riesgos activos

- **Supabase gratuito se pausa** tras ~7 días sin uso y el síntoma parece un proyecto borrado. Solo queda un hueco libre (el otro es una app en producción del usuario).
- **Dos `.env` con `DATABASE_URL`** (raíz y `services/api`): si divergen, Docker usa el suyo.
- **Proxy LLM inestable**: modelos bloqueados sin aviso y deepseek lento; un pico de latencia hace caer el plan del agente a solo RAG.
- **Registro de auditoría**: borrar las últimas entradas no se detecta desde la cadena (anclar la huella de cabeza fuera); el dueño de la tabla podría desactivar los triggers; el login federado aún audita en `federated_audit_events`.
- **Pipeline de negocio**: cambiar una regla de transformación sin subir `TRANSFORM_VERSION` sirve resultados viejos de caché durante una hora.
- **Cola**: la imagen del worker nunca se ha construido; Flower sin login.
- **Estado en memoria de proceso** (caché, rate limit, chat, guardarraíles, TinyDB): roto con varios workers de uvicorn.
- `GET /telemetry/report` responde 500 (no 400) a fechas mal formadas.
- Batería completa de la raíz (`pytest tests`): dos fallos conocidos que pasan solos (`test_onboarding_resume`, `test_ticket_shows_progress_in_real_time`, sensible al tiempo).
- Proveedor LLM y Supabase sin BAA/DPA: el proyecto solo puede manejar datos sintéticos.

## Criterio de avance para la siguiente iteración

- Sin errores críticos de lint en módulos clave.
- Contrato de datos unificado en el tracker.
- Checklist mínimo de cumplimiento técnico para nuevas features.
