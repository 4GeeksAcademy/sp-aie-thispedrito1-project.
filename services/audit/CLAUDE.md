# services/audit (+ services/patients) — registro de auditoría append-only

Responde "quién, qué, cuándo, desde dónde" de cada acción sensible, sin PHI y sin poder alterarse. `services/patients/` es un módulo mínimo de pacientes SINTÉTICOS que existe para auditarlo. Detalle en `docs/audit-log/audit-log.md`.

```bash
services/api/.venv/bin/python scripts/seed_audit_log.py      # 8 pacientes + 8 cuentas + 72 entradas, idempotente
(cd services/api && SUPPLIERS_DB_PATH=../../data/audit_log/demo-users.db.json .venv/bin/uvicorn main:app --port 8000)
(cd services/api && .venv/bin/python -m pytest tests/test_audit_log.py -o addopts="")
(cd uis/backoffice && npx jest __tests__/audit.test.ts)
```

Necesita Supabase (`audit_log_entries`, `patient_records`, `patient_insurance_claims`). Pantallas `/audit-log` y `/patients`.

Mapa: `catalog.py` (acciones, procesos, motivos, claves de `details`, formatos de ids opacos, `department_id(code)`), `models.py` (tabla + guardas), `chain.py` (`compute_entry_hash`, `verify_chain`), `writer.py` (`record`, `commit_record`, actores, `request_origin`), `queries.py`, `anomalies.py`, `router.py` (solo GET). Pacientes: `services/patients/rules.py`, `router.py`, `seed_data.py`. Capacidad `audit:read` (rango 2).

## Reglas que no hay que romper

- **Append-only en tres capas**: router solo GET (test), eventos `before_update`/`before_delete` del mapper (`AuditLogImmutableError`) y triggers de BD (Postgres: UPDATE/DELETE/TRUNCATE + RLS + REVOKE; SQLite: `RAISE(ABORT)`), creados por `ensure_audit_log_guards` al arrancar. Nunca `update(AuditLogEntry)`/`delete(AuditLogEntry)` (un test lo busca en todo el código).
- **Toda escritura por `writer.record`** (sin commit: entra en la transacción del cambio). Lecturas de fichas: commit de la entrada ANTES de devolver datos (falla cerrado). Datos en TinyDB: `commit_record` justo después. El login sigue sin Supabase y avisa por stderr.
- **Sin PHI**: catálogo cerrado (fuera de lista = `ValueError`); `details` solo con claves permitidas; ids opacos (`PT-XXXXXXXX`, UUID, `CL-…`, hashes) validados por formato, no por `phi_guard` (daban falsos positivos); el resto sí. `changed_fields` = nombres, nunca valores.
- **Actor**: `human` (id de cuenta), `process` (nombre de `PROCESS_ACTORS` + `on_behalf_of`; origen `process:<nombre>:pid<n>`) o `anonymous` (login fallido, huella HMAC). Los procesos calculan el departamento con `catalog.department_id` (mismo uuid5 que `access_models`, fijado por test).
- **Alcance**: Admin todo; Supervisor su departamento; Empleado solo `/audit-log/my-patients` (sin IP ni hashes). Integridad solo Admin. Ser Admin no da contenido clínico de otro departamento. Clínico solo equipo de cuidado; facturación también Revenue Cycle.
- **Cadena**: `entry_hash` = SHA-256 del JSON canónico (con `sequence` y `prev_hash`); `occurred_at` como texto ISO fijo. `pg_advisory_xact_lock` + índice único en `prev_hash`. Límite: borrar las ÚLTIMAS entradas no se detecta desde dentro (anotar la huella de cabeza fuera).
- Accesos inusuales: últimas 24 h frente a media diaria de 30 días; `recent >= 10` y `>= 3 × media`.
- Semilla idempotente por `details.seed_batch` (el registro no se puede vaciar), fechas en el pasado. Claire y Sandra son Admin.

## Gotchas

- La exportación CSV necesita `response_class` + `responses` (lo vigila `test_serialization.py`).
- El enlace al registro va en la sección "Cumplimiento" del menú ("Administración" es solo Admin, hay test).
- Tests de la raíz que tocan procesos auditados incluyen `AuditLogEntry.__table__`.
- Residual: el login federado aún escribe en `federated_audit_events`, no aquí. El dueño de la tabla (`postgres`) podría desactivar los triggers.
