# Registro de Auditoría — HealthCore

Ticket "Plataforma – Registro de Auditoría" (BRIEFING del currículo `content/contexts/audit-log/healthcore-BRIEFING.es.md`), rama `feature/audit-log` apilada sobre `feature/federated-auth`, 2026-11-07.

> "Necesito poder responder, sin ambigüedad, tres preguntas sobre cualquier acción sensible del sistema: quién la hizo, qué hizo exactamente, y cuándo." — CTO

## 1. Qué se construyó

| Pieza | Dónde |
|---|---|
| Tabla append-only `audit_log_entries` (Supabase) + guardas | `services/audit/models.py` |
| Catálogo cerrado (acciones, actores, motivos, campos) | `services/audit/catalog.py` |
| Encadenamiento por hash y verificación | `services/audit/chain.py` |
| Única forma de escribir (`record`, `commit_record`) | `services/audit/writer.py` |
| Consultas, paginación por cursor | `services/audit/queries.py` |
| Detección de accesos inusuales (BRIEFING §5) | `services/audit/anomalies.py` |
| API de solo lectura `/audit-log` | `services/audit/router.py` |
| Pacientes SINTÉTICOS con toda acción auditada | `services/patients/` (`/patients`) |
| Semilla (BRIEFING §6) | `scripts/seed_audit_log.py` |
| Visor y fichas en el backoffice | `uis/backoffice/app/audit-log/`, `app/patients/` |

El proyecto no tenía módulo de pacientes; el BRIEFING pide auditar sobre todo accesos a fichas clínicas, reclamaciones y exportaciones. Decisión del usuario: un **módulo mínimo de pacientes sintéticos** para que esas acciones se auditen de verdad, no solo en la semilla.

## 2. Esquema de la entrada

| Campo | Contenido | Ejemplo |
|---|---|---|
| `sequence` | Posición en la cadena (la asigna el escritor, no la BD) | `73` |
| `entry_id` | UUID de la entrada | `5b0e…` |
| `occurred_at` | UTC, texto ISO de ancho fijo | `2026-11-07T10:15:02.123456Z` |
| `actor_type` | `human` · `process` · `anonymous` | `process` |
| `actor_id` | Id interno de la cuenta o nombre del proceso. Nunca email | `clinical_documentation_assistant` |
| `on_behalf_of` | Persona que pidió la acción a un proceso | id de Nina |
| `action` | Lista cerrada (§3) | `patient_record_viewed` |
| `resource_type` / `resource_id` | Sale de la acción, no se elige al escribir | `patient_record` / id |
| `patient_ref` | Identificador **opaco** del paciente (BRIEFING §4) | `PT-4C1D9A0B` |
| `department_id` | Departamento al que pertenece la entrada (decide qué Supervisor la ve) | Operaciones clínicas |
| `context` | Motivo del acceso, de una lista cerrada | `referral_resolution` |
| `origin` | IP o identificador de proceso | `ip:10.20.0.21` · `process:financial_close_agent:pid4312` |
| `outcome` | `success` · `denied` · `blocked` | `denied` |
| `details` | JSON canónico solo con claves permitidas (códigos, ids, recuentos, nombres de campo) | `{"changed_fields":["clinical_notes"]}` |
| `prev_hash` / `entry_hash` | Huella de la anterior / SHA-256 de esta entrada (incluye `prev_hash`) | 64 hex |

**Sin PHI (BRIEFING §4)**: no hay ningún campo de texto libre.
- `changed_fields` dice *qué* campo cambió, nunca su valor.
- El motivo es un código de una lista.
- Los ids opacos se validan por formato.
- El resto de valores pasa por `phi_guard`.
- Un login fallido guarda la huella HMAC del email, no el email.

## 3. Acciones instrumentadas

| BRIEFING §2 | Acción | Dónde se dispara | Actor |
|---|---|---|---|
| Todo acceso (lectura) a una ficha, con contexto | `patient_record_viewed` (también `denied`) | `GET /patients/{ref}?purpose=` | persona |
| Modificación de datos clínicos | `patient_clinical_updated` | `PATCH /patients/{ref}/clinical` | persona |
| Modificación de facturación | `patient_billing_updated` | `PATCH /patients/{ref}/billing` | persona |
| Aprobación / denegación de reclamación | `insurance_claim_approved` / `_denied` | `POST /patients/{ref}/claims/{claim}/decision` | persona |
| Usuarios y sus roles/departamentos | `user_created`, `user_updated`, `user_assignment_changed`, `user_deleted`, `role_created`, `role_updated`, `department_created`, `department_updated` | `routes/users.py`, `routes/access.py` | persona |
| Agentes que tocan datos de pacientes | `patient_documentation_drafted` | asistente de documentación clínica | **proceso** (`on_behalf_of` = quien lo pidió) |
| (otros procesos) | `access_request_issued` / `_blocked` | agente de incorporaciones (sistemas con datos de pacientes) | **proceso** o persona |
| (otros procesos) | `financial_report_prepared` | agente de cierre financiero | **proceso** |
| Cualquier exportación de pacientes | `patient_data_exported` (también `denied`) | `POST /patients/export` | persona |
| Autenticación (README) | `login_succeeded`, `login_failed` | `/auth/login`, `/auth/token` | persona / anónimo |

Los intentos rechazados por departamento quedan con `outcome=denied`: un acceso denegado a la ficha de un paciente es lo primero que Compliance quiere ver.

**No cubierto** (pendiente): el login federado con Microsoft sigue en su propia tabla `federated_audit_events`; las lecturas del propio visor de auditoría no se auditan (llenarían la cadena con cada página).

## 4. Inmutabilidad: tres capas

1. **API**: `/audit-log` solo tiene rutas GET. El test `test_the_audit_api_has_no_write_routes` recorre `app.routes`.
2. **Aplicación**: los eventos `before_update`/`before_delete` del mapper lanzan `AuditLogImmutableError`. Además, `test_no_code_path_updates_or_deletes_entries` busca `update(AuditLogEntry` y similares en todo el código.
3. **Base de datos**:
   - Postgres tiene un trigger `BEFORE UPDATE OR DELETE` (por fila) y otro `BEFORE TRUNCATE`, con `ERRCODE insufficient_privilege`. SQLite (los tests) tiene triggers `RAISE(ABORT)`.
   - En Supabase hay además RLS sin políticas y `REVOKE ALL` a `anon`/`authenticated`: la API REST pública de Supabase no ve la tabla.
   - `ensure_audit_log_guards` reinstala todo al arrancar la API (es idempotente).

Escritura **en la misma transacción** que la acción:
- **Cambios en Supabase** (pacientes, procesos): el cambio y su entrada se guardan juntos o no se guarda ninguno.
- **Lecturas de fichas**: la entrada se confirma *antes* de devolver los datos (falla cerrado).
- **Usuarios, roles y departamentos** (están en TinyDB): la entrada se confirma justo después del cambio. La ruta exige la sesión de Supabase desde el principio, así que sin Supabase falla antes de tocar nada.
- **Login**: es la excepción documentada. Sigue funcionando sin Supabase (decisión previa del proyecto) y avisa por stderr.

Concurrencia: `pg_advisory_xact_lock` serializa el cálculo del siguiente eslabón hasta el commit, también entre la API y el worker de Celery. El índice único de `prev_hash` impide que dos escrituras bifurquen la cadena.

## 5. Evidencia de alteración (encadenamiento por hash)

`entry_hash = SHA-256(JSON canónico de todos los campos, incluido prev_hash)`. `verify_chain` recalcula cada huella (no se fía de la guardada) y para en la primera rotura:

| Alteración manual (como DBA, quitando el trigger) | Lo que dice la verificación | Test |
|---|---|---|
| `UPDATE … SET context = … WHERE sequence = 2` | rota en **#2**, `tampered_entry` | `test_manual_tampering_of_an_entry_breaks_the_chain` |
| Lo mismo y además recalcular el `entry_hash` de #2 | rota en **#3**, `broken_link` | `test_recomputing_the_tampered_hash_still_breaks_the_next_link` |
| `DELETE … WHERE sequence = 2` | rota en **#3**, `missing_entry` | `test_deleting_an_entry_in_the_middle_is_detected` |

**Límite conocido**: borrar las *últimas* entradas no deja rastro dentro de la cadena. Por eso `GET /audit-log/integrity` (y el botón del visor) devuelve la **huella de cabeza** y el número de entradas, para que Compliance las anote fuera del sistema. El siguiente paso sería anclar esa huella en un sistema externo cada cierto tiempo.

**Verificación contra Supabase real (2026-11-07)**, después de sembrar 72 entradas:

```
UPDATE: rechazado -> audit_log_entries is append-only: UPDATE refused
DELETE: rechazado -> audit_log_entries is append-only: DELETE refused
TRUNCATE: rechazado -> audit_log_entries is append-only: TRUNCATE refused
RLS activo: True | triggers: ['audit_log_entries_no_truncate', 'audit_log_entries_no_update_delete'] | filas: 72
Cadena: True verificadas 72 cabeza 72
```

## 6. Quién consulta qué (BRIEFING §3)

| Rol | `/audit-log` | `/audit-log/integrity` | `/audit-log/my-patients` |
|---|---|---|---|
| Admin (James, Claire, Sandra) | todo (`departments:read_all`) | sí | sí |
| Supervisor | solo entradas de su departamento (`audit:read`, rango 2) | 403 | sí |
| Empleado | 403 `insufficient_role` | 403 | quién accedió a **sus pacientes asignados** (sin IP ni huellas) |

- **"Admin ve quién accedió a qué, no el contenido clínico"**: el registro no tiene contenido clínico. Además, en `/patients` ser Admin **no** da acceso clínico a otros departamentos. Solo el equipo de cuidado ve la sección clínica, y Revenue Cycle ve facturación y reclamaciones de cualquier paciente.
- **Departamento de cada entrada**: el del paciente si actúa su equipo de cuidado; Revenue Cycle si actúa facturación; el de la cuenta afectada en los cambios de usuario. Los cambios de roles y departamentos no llevan departamento, así que solo los ve un Admin.
- **Visor**:
  - filtros por actor, acción, tipo de recurso, id de recurso, referencia de paciente y rango de fechas (días completos, fin exclusivo en UTC, mismo contrato que `/telemetry`);
  - paginación por cursor (`before`, máximo 100 por página).

## 7. Detección de accesos inusuales (BRIEFING §5)

`GET /audit-log/anomalies` compara las lecturas de fichas de cada persona en las últimas 24 h con su media diaria de los 30 días anteriores. Los días sin accesos cuentan como 0. La regla (`anomalies.is_unusual_access`, Learn by Doing delegado al asistente) exige dos condiciones: al menos 10 fichas en 24 h (`MIN_RECENT_ACCESSES`), para que el primer día de alguien no genere ruido, y al menos 3 veces su media diaria (`RATIO_THRESHOLD`), para que quien abre 20 fichas al día por su trabajo no salte por abrir 25. Se calcula al consultar y no escribe nada en el registro. La semilla incluye a Leo (facturación) abriendo 24 fichas en tres horas. Verificado contra Supabase real: Tom (Supervisor de Revenue Cycle) ve a Leo marcado (24 frente a una media de 0,23 al día), y Marcus (Operaciones clínicas) no ve ninguna alerta.

## 8. Cómo probarlo

```bash
services/api/.venv/bin/python scripts/seed_audit_log.py      # pacientes + 8 cuentas + 72 entradas (idempotente)
(cd services/api && SUPPLIERS_DB_PATH=../../data/audit_log/demo-users.db.json .venv/bin/uvicorn main:app --port 8000)
(cd uis/backoffice && npm run dev -- --port 3001)            # /patients y /audit-log
(cd services/api && .venv/bin/python -m pytest tests/test_audit_log.py -o addopts="")
(cd uis/backoffice && npx jest __tests__/audit.test.ts)
```

Cuentas (contraseña impresa por la semilla): `claire.compliance@` / `sandra.finance@` / `james.admin@` (Admin), `marcus.clinical@` / `tom.billing@` (Supervisor), `nina.coordinator@` / `leo.billing@` / `priya.access@` (Empleado), todas `@healthcore.example`.
