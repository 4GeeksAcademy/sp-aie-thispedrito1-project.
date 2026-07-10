# Propuesta de Arquitectura Backend para HealthCore Digital

## 1. Contexto de negocio y objetivos del backend

HealthCore opera 12 clinicas en Estados Unidos y Reino Unido con procesos distribuidos en multiples sistemas no integrados (agenda, historia clinica, facturacion, seguimiento de pacientes y gestion de talento).

La empresa enfrenta indicadores operativos que requieren una plataforma backend consistente:

- No-shows de aproximadamente 22%.
- Rechazos de facturacion de aproximadamente 14%.
- Datos fragmentados entre paises, sedes y herramientas.
- Requisitos regulatorios duales (HIPAA y UK GDPR).
- Necesidad de reportes ejecutivos confiables y de menor latencia.

Con base en este contexto, los objetivos iniciales del backend son:

1. Unificar contratos de datos para reducir inconsistencias entre sistemas.
2. Separar reglas de negocio de transporte HTTP para mejorar mantenibilidad y testabilidad.
3. Facilitar crecimiento por dominios (pacientes, citas, facturacion, talento, reportes) sin acoplar equipos.
4. Asegurar trazabilidad y controles de acceso compatibles con cumplimiento normativo.
5. Permitir integracion limpia con frontend Next.js y con futuras automatizaciones de agentes.

Estos objetivos se traducen en tres resultados esperados del primer ciclo de backend:

- Contratos API consistentes para el dominio `talent` usados por el frontend sin transformaciones ad hoc.
- Estructura replicable para incorporar nuevos dominios sin rehacer la base del proyecto.
- Base de cumplimiento tecnico (control de acceso, logging y manejo de configuracion por ambiente).

## 2. Patron arquitectonico elegido y justificacion

### Patron propuesto

Se propone una **arquitectura en capas orientada a dominio**:

- Capa API: routers FastAPI y dependencias HTTP.
- Capa de aplicacion/servicios: casos de uso y reglas de negocio.
- Capa de persistencia: repositorios y acceso a datos externos.
- Capa transversal: configuracion, seguridad, observabilidad, utilidades.

### Justificacion para HealthCore

Esta opcion es adecuada porque:

1. HealthCore necesita evolucionar varios dominios en paralelo (operaciones clinicas, acceso del paciente, facturacion, talento), por lo que una organizacion por dominio evita un backend monolitico desordenado.
2. El equipo de tecnologia es pequeno; una arquitectura simple y explicita reduce carga cognitiva y acelera onboarding.
3. El cumplimiento normativo exige trazabilidad y separacion de responsabilidades; mantener la logica fuera de routers facilita auditoria y pruebas.
4. El frontend y futuras integraciones de agentes requieren contratos de API estables; la capa de schemas y servicios reduce cambios rompientes.

### Por que no otras alternativas como base inicial

- MVC clasico puede terminar mezclando reglas de negocio con componentes de transporte si no se disciplina desde el inicio.
- Arquitectura hexagonal completa es valida, pero para esta etapa puede introducir complejidad prematura para un equipo pequeno.
- Serverless puro por funcion no resuelve por si mismo el problema principal actual de coherencia de dominio y gobernanza de datos.

En esta etapa la prioridad es consistencia operativa y claridad de ownership, no maximizar abstraccion.

## 3. Propuesta de estructura backend (carpetas, modulos, dominios)

Se recomienda iniciar con una aplicacion FastAPI bajo una estructura similar a:

```text
apps/backend-healthcore/
  app/
    main.py
    api/
      v1/
        routers/
          auth.py
          patients.py
          appointments.py
          billing.py
          talent.py
          reports.py
    services/
      auth_service.py
      patient_service.py
      appointment_service.py
      billing_service.py
      talent_service.py
      report_service.py
    repositories/
      patient_repository.py
      appointment_repository.py
      billing_repository.py
      talent_repository.py
      report_repository.py
    schemas/
      auth.py
      patient.py
      appointment.py
      billing.py
      talent.py
      report.py
      common.py
    models/
      patient.py
      appointment.py
      billing.py
      talent.py
    core/
      config.py
      security.py
      dependencies.py
      logging.py
      exceptions.py
    db/
      session.py
      base.py
    integrations/
      ehr_connector.py
      billing_connector.py
      messaging_connector.py
    tests/
      unit/
      integration/
```

### Criterio de separacion

- Por dominio: cada dominio tiene router, servicio, repositorio y schemas propios.
- Por responsabilidad: API no contiene reglas de negocio complejas; servicios no dependen de detalles HTTP; repositorios encapsulan acceso a datos.
- Por escalabilidad: agregar un nuevo dominio replica un patron predecible sin afectar modulos existentes.

## 4. Organizacion de endpoints y routers en FastAPI

La estrategia de rutas sigue convenciones comunes de FastAPI (routers por dominio, prefijo de version, dependencias compartidas):

- Prefijo base: `/api/v1`.
- Un router por dominio.
- Endpoints de lectura/escritura separados de endpoints operativos o analiticos.
- Uso de dependencias para autenticacion/autorizacion, contexto de tenant/sede y trazabilidad.

Propuesta inicial de routers:

- `auth`:
  - `POST /api/v1/auth/login`
  - `POST /api/v1/auth/refresh`
- `patients`:
  - `GET /api/v1/patients`
  - `GET /api/v1/patients/{patient_id}`
  - `POST /api/v1/patients`
- `appointments`:
  - `GET /api/v1/appointments`
  - `POST /api/v1/appointments`
  - `PATCH /api/v1/appointments/{appointment_id}/status`
- `billing`:
  - `GET /api/v1/billing/claims`
  - `POST /api/v1/billing/claims`
  - `PATCH /api/v1/billing/claims/{claim_id}`
- `talent` (alineado al tracker de candidatos ya existente en frontend):
  - `GET /api/v1/talent/candidates`
  - `GET /api/v1/talent/candidates/{candidate_id}`
  - `POST /api/v1/talent/candidates`
  - `PATCH /api/v1/talent/candidates/{candidate_id}`
  - `GET /api/v1/talent/candidates/{candidate_id}/notes`
  - `POST /api/v1/talent/candidates/{candidate_id}/notes`
- `reports`:
  - `GET /api/v1/reports/kpis/no-shows`
  - `GET /api/v1/reports/kpis/revenue`
  - `GET /api/v1/reports/kpis/hiring-funnel`

### Convenciones FastAPI que se adoptan explicitamente

1. `APIRouter` por dominio en lugar de un archivo unico de rutas.
2. Schemas de entrada/salida desacoplados de servicios.
3. Inyeccion de dependencias para seguridad, sesion DB y servicios compartidos.
4. Configuracion centralizada por ambiente en `core/config.py`.
5. Validacion de contratos con Pydantic para respuestas predecibles.

Estas convenciones siguen la practica habitual de proyectos FastAPI productivos: modularidad por routers, contratos de datos explicitos con Pydantic, y uso de `Depends` para preocupaciones transversales sin acoplar los endpoints a implementaciones concretas.

## 5. Estrategia de separacion frontend-backend

HealthCore ya tiene frontend en Next.js para el flujo de talento. El backend debe convivir como sistema separado pero coordinado por contrato.

### Estrategia propuesta

1. Integracion API-first:
- Definir contratos request/response estables por endpoint antes de implementar reglas complejas.
- Versionar cambios incompatibles de API (por ejemplo, mover a `/api/v2` cuando aplique).

2. CORS por ambiente:
- Local: permitir origenes de desarrollo explicitamente.
- Staging/produccion: lista blanca estricta por dominio corporativo.

3. Variables de entorno:
- Frontend: `NEXT_PUBLIC_API_URL`.
- Backend: `API_ENV`, `DB_URL`, `JWT_SECRET`, `ALLOWED_ORIGINS`, `LOG_LEVEL`.
- Mantener plantillas `.env.example` y validacion de variables al arranque.

4. Monorepo con separacion de responsabilidades:
- Mantener frontend y backend en carpetas separadas dentro del monorepo.
- Compartir tipos o contratos versionados en `packages/shared` cuando aporte consistencia.
- Evitar dependencias ciclicas entre apps.

Decision de alcance:
- Para este milestone se mantiene monorepo con limites claros entre apps.
- Si el equipo o la superficie de despliegue crece significativamente, se evaluara separar repositorios sin cambiar el contrato API.

## 6. Riesgos y puntos de atencion con mitigaciones

### Riesgo 1: Limites de dominio poco claros

Impacto:
- Duplicacion de logica y endpoints inconsistentes.

Mitigacion:
- Definir ownership por dominio desde el sprint 1.
- Agregar checklist de PR para validar que cada cambio respete capas y dominio.
- Responsable sugerido: Tech Lead backend.
- Momento de control: diseno de historias y revision de PR.

### Riesgo 2: Logica de negocio dentro de routers

Impacto:
- Baja testabilidad, deuda tecnica y regresiones frecuentes.

Mitigacion:
- Regla de arquitectura: router delgado, servicio con casos de uso.
- Exigir pruebas unitarias de servicios para reglas criticas.
- Responsable sugerido: equipo backend en code review.
- Momento de control: definicion de Done por historia.

### Riesgo 3: Deriva de configuracion entre local, staging y produccion

Impacto:
- Incidentes operativos y errores de integracion FE-BE.

Mitigacion:
- Configuracion centralizada y validada al iniciar.
- Matriz de variables obligatorias por ambiente y revision previa a deploy.
- Responsable sugerido: backend + DevOps.
- Momento de control: pipeline de CI y checklist de release.

### Riesgo 4: Incumplimiento en manejo de datos sensibles

Impacto:
- Riesgo legal y reputacional bajo HIPAA/UK GDPR.

Mitigacion:
- Politica de minimizacion de datos en respuestas API.
- Auditoria de accesos y mascarado de datos sensibles en logs.
- Responsable sugerido: cumplimiento + seguridad + backend.
- Momento de control: revision de arquitectura y auditorias internas.

## 7. Decisiones tecnicas iniciales y proximos pasos

### Decisiones iniciales

1. Adoptar arquitectura en capas orientada a dominio como estandar del backend.
2. Iniciar con API versionada en `/api/v1` y routers por dominio.
3. Priorizar dominio `talent` para alinear con el tracker frontend ya construido.
4. Estandarizar contratos con schemas Pydantic y manejo de errores uniforme.
5. Definir seguridad base con JWT para endpoints protegidos y permisos por rol.

### Proximos pasos del sprint

1. Crear esqueleto de carpetas y archivo `main.py` con registro de routers vacios.
2. Publicar primer contrato de endpoints del dominio `talent` y adaptarlo al frontend existente.
3. Implementar trazabilidad basica (request-id, logging estructurado, errores estandar).
4. Incorporar pruebas unitarias para servicios de `talent` y pruebas de integracion de routers.
5. Revisar con Cumplimiento y CTO las politicas de datos sensibles antes de expandir a `patients` y `billing`.

Secuencia recomendada (2 semanas):

- Semana 1: contratos API `talent`, wiring de routers y capa de servicios con pruebas unitarias.
- Semana 2: endurecimiento transversal (seguridad base, manejo de errores, logs, configuracion por ambientes) y validacion conjunta con frontend.

---

## Cierre

Esta propuesta prioriza coherencia de dominio, mantenibilidad operativa y cumplimiento regulatorio, permitiendo que HealthCore evolucione el backend por fases sin sacrificar calidad arquitectonica desde el inicio.
