# Roles y Permisos — HealthCore

Ticket del CTO (James Osei), a raíz del aviso de Compliance (Claire Whitfield): cualquier usuario autenticado tenía el mismo acceso. Rama `feature/roles-permissions`, 2026-11-07. Fuente de verdad: BRIEFING de Roles y Permisos (`content/contexts/roles-permissions/healthcore-BRIEFING.es.md` del currículo).

Dos mecanismos **independientes**:

- **Rol** = qué puede *hacer* una persona (capacidad).
- **Departamento** = qué datos le *competen* (alcance). No da acciones.

Cada decisión de acceso evalúa **primero el rol y después el departamento**.

## 1. Roles y capacidades

Jerarquía estricta por rango. Las capacidades salen del rango (`access_models.CAPABILITY_MIN_RANK`), así que un rol nuevo creado por un Admin con rango 2 tiene exactamente las capacidades de Supervisor.

| Capacidad | Qué permite | Empleado (1) | Supervisor (2) | Admin (3) |
|---|---|:-:|:-:|:-:|
| `records:read` | Leer registros de su departamento | ✅ | ✅ | ✅ |
| `records:create` | Crear registros en su departamento | ✅ | ✅ | ✅ |
| `records:update` | Actualizar registros de su departamento | ❌ | ✅ | ✅ |
| `records:approve` | Aprobar registros de su departamento | ❌ | ✅ | ✅ |
| `records:delete` | Eliminar registros | ❌ | ❌ | ✅ |
| `users:manage` | Crear, listar, asignar y borrar usuarios | ❌ | ❌ | ✅ |
| `access:manage` | Administrar roles y departamentos | ❌ | ❌ | ✅ |
| `departments:read_all` | Visibilidad cruzada entre departamentos | ❌ | ❌ | ✅ |

La visibilidad cruzada es una capacidad de **rol**: el Admin sigue perteneciendo a un departamento (`technology` en la semilla).

## 2. Departamentos

Semilla literal: `clinical_operations`, `patient_experience`, `revenue_cycle`, `compliance`, `people_workforce`, `technology`.

## 3. Modelo de datos

Todo en la TinyDB, junto a `users`, porque las claves foráneas apuntan a usuarios que solo viven ahí (decisión ya registrada en techContext).

- `roles` (`id`, `code`, `name`, `rank`) y `departments` (`id`, `code`, `name`). Los ids son `uuid5` deterministas a partir del código: la semilla da los mismos ids en cualquier base y no duplica nada. `AccessRepository.ensure_catalog` las siembra solas y de forma perezosa.
- `users.role_id` y `users.department_id`: dos claves separadas, ninguna se deriva de la otra. `AuthRepository.assign` solo escribe lo que recibe.
- `internal_reports` (`id`, `department_id`, `title`, `body`, `created_by`, `created_at`, más `approved_by`/`approved_at` para la capacidad de aprobar).
- Incidencias: las nuevas llevan el `department_id` de quien las crea. Las anteriores a este ticket se resuelven **al leerlas** por su categoría (`patient_experience` → `patient_experience`, `billing_error` → `revenue_cycle`, `other` → `clinical_operations`), sin reescribir el archivo de datos.
- Cuentas antiguas con `role` en texto (`user`/`manager`/`admin`) se traducen al leerlas (`employee`/`supervisor`/`admin`). Al reasignarles el rol, el texto antiguo se borra.

## 4. Mecanismo centralizado (`services/api/permissions.py`)

- `require_capability(cap)`: dependencia de FastAPI. Si el rol no alcanza, responde **403** antes de ejecutar la ruta:
  ```json
  {"detail": {"code": "insufficient_role", "capability": "records:update", "required_role": "supervisor", "current_role": "employee", "message": "..."}}
  ```
- `ensure_in_scope(user, department_id)`: 403 `department_out_of_scope` para un registro concreto de otro departamento. No revela de qué departamento es.
- `department_scope(user)`: filtro para listados (alcance, no denegación). Si la cuenta no tiene departamento, devuelve 403 `department_required`, nunca una lista vacía.
- `can_access_department(user, department_id)`: la decisión de departamento (Learn by Doing **delegado**). Admin pasa siempre. Si falta el departamento del usuario o el del registro, deniega: falla cerrado.
- `security.user_from_token` resuelve rol y departamento **leyendo la base en cada petición**, así que un cambio de rol se aplica sin volver a iniciar sesión. Un `role_id` que no existe deja la cuenta sin capacidades (falla cerrado).
- Dos tests vigilan que no se vuelva a repartir la lógica: uno recorre con `ast` todos los `routes/*.py` buscando comparaciones manuales de rol o departamento, y el otro inspecciona `app.routes` para comprobar que cada endpoint sensible lleva su dependencia `require_*`.

## 5. Mapa de endpoints por rol mínimo

| Endpoint | Rol mínimo | ¿Departamento? |
|---|---|---|
| `GET /auth/me` | autenticado | no (devuelve rol **y** departamento) |
| `POST/GET /users`, `DELETE /users/{id}` | admin | no |
| `PATCH /users/{id}/assignment` | admin | no (409 si se quedaría sin ningún admin) |
| `GET/PUT /users/{id}` | el propio usuario o admin | no (`PUT` ya no acepta `role`: 422) |
| `GET/POST /roles`, `GET/PUT /roles/{id}` | admin | no (el rango de los 3 roles base no cambia: 409) |
| `GET/POST /departments`, `GET/PUT /departments/{id}` | admin | no |
| `GET /internal-reports`, `GET /internal-reports/{id}` | employee | **sí** |
| `POST /internal-reports` | employee | **sí** (solo un admin elige otro departamento) |
| `PUT /internal-reports/{id}`, `POST …/{id}/approve` | supervisor | **sí** |
| `DELETE /internal-reports/{id}` | admin | todos |
| `GET /api/incidents`, `/summary`, `/{id}`, `POST /api/incidents` | employee | **sí** |
| `PATCH /api/incidents/{id}/status` | supervisor | **sí** |
| `POST /suppliers` | employee | no (catálogo de toda la red) |
| `PATCH /suppliers/{id}/rate`, `/status` | supervisor | no |
| `DELETE /suppliers/{id}` | admin | no |
| `POST /inventory/products`, `/orders/inbound`, `/orders/outbound` | employee | no (inventario de toda la red) |
| `PATCH /inventory/products/{id}/stock` (siempre 400) | supervisor | no |
| Endpoints de admin anteriores (`POST /reporting/pipeline-runs`, `/tasks/{id}`, `/agent/guardrails/summary`, `/agent/memory/{id}`…) | admin | no (siguen comparando `role == "admin"` en `services/*/router.py`; funciona porque el rol llega ya resuelto, pero no pasan por `require_capability`) |

Fuera de alcance, con su propio directorio de roles: onboarding (`onboarding_staff`), proveedores y contratos y cierre financiero (sus responsables del BRIEFING), y la firma de RFP (pendiente desde la auditoría OWASP).

## 6. Backoffice

- `types/access.ts` (lógica pura: `can`, etiquetas, alcance), `hooks/useAccessProfile.ts` (una petición a `/auth/me`, en caché por token) y `services/accessApi.ts`.
- Menú: las entradas declaran `capability`, y "Administración → Roles y accesos" solo aparece para Admin.
- `/internal-reports`: alcance visible, crear en el propio departamento, y los botones Editar/Aprobar (Supervisor) y Eliminar (Admin) solo cuando el rol los permite.
- `/admin/access` (solo Admin): usuarios con **dos desplegables separados** (rol y departamento, cada uno manda solo su campo), alta de cuentas, roles y departamentos. Un no-admin ve "Acceso denegado (403)" y la página no llama a la API.
- Incidencias y proveedores ocultan las acciones que el rol no permite.
- `/register` ya no crea cuentas: explica que las crea un Admin. `http.ts` traduce los 403 por `code`, y `useAsyncData` muestra el motivo de un 403 en lugar de "la API no responde".

## 7. Semilla y verificación

```bash
services/api/.venv/bin/python scripts/seed_roles_permissions.py       # data/roles_permissions/demo-users.db.json (fuera de git)
cd services/api && SUPPLIERS_DB_PATH=../../data/roles_permissions/demo-users.db.json .venv/bin/uvicorn main:app --port 8000
(cd services/api && .venv/bin/python -m pytest tests/test_roles_permissions.py -o addopts="")
(cd uis/backoffice && npx jest __tests__/access.test.tsx)
```

La contraseña de las cuentas de la semilla es aleatoria en cada ejecución (o `ROLES_SEED_PASSWORD`) y el script la imprime.

Evidencia en `evidence/`: `curl-403.txt` (las pruebas 1-4 de Compliance contra la API real) y capturas de Chrome (Empleado en informes internos, Admin en `/admin/access`, Empleado recibiendo 403).

## 8. Bug real encontrado en la verificación en Chrome

`/admin/access` no cargaba: `GET /roles` daba 500 con `JSONDecodeError`. La pantalla pide tres listados a la vez, y cada repositorio de TinyDB comparte **un solo manejador de archivo** entre todos los hilos de FastAPI. Con dos lecturas simultáneas, una movía la posición del archivo mientras la otra leía. El riesgo ya existía, pero era raro; este ticket lo volvió frecuente, porque cada petición resuelve rol y departamento. Arreglo: `database.LockedJSONStorage`, que serializa lecturas y escrituras con un candado del proceso. El test `test_concurrent_reads_of_the_shared_tinydb_do_not_corrupt` falla sin el candado (comprobado) y pasa con él.

## 9. Pruebas que pedirá Compliance

| # | Prueba | Test |
|---|---|---|
| 1 | Empleado → update solo de Supervisor = 403 | `test_compliance_1_…` |
| 2 | Supervisor → `GET /roles` / asignación = 403 | `test_compliance_2_…` (6 endpoints) |
| 3 | Marcus → informe de Tom por id = 403 | `test_compliance_3_…` |
| 4 | Cambiar departamento ≠ cambiar rol, y al revés | `test_compliance_4_…`, `test_compliance_4b_…` |
| 5 | Verificación en dependencia, no en el cuerpo de la ruta | `test_compliance_5_…` (ast), `test_compliance_5b_…` (dependencias) |

## 10. Residuales conocidos

- `GET /api/incidents/{id}` y `/internal-reports/{id}` distinguen "no existe" (404) de "es de otro departamento" (403), tal como pide el BRIEFING. Eso revela que el id existe.
- La cuenta de servicio del MCP es Supervisor de `patient_experience`: el agente ya no ve las incidencias de facturación ni las de "otros". Es la consecuencia de mínimo privilegio; si hiciera falta, la alternativa sería una cuenta de servicio por departamento.
- `POST /knowledge/query`, `/agent/*` y los módulos agénticos siguen exigiendo solo sesión (o su propio directorio de roles): no tienen datos con alcance departamental.
- El candado de TinyDB es por proceso: con varios workers de uvicorn sobre el mismo archivo seguiría habiendo carreras entre procesos (TinyDB no está pensada para eso).
