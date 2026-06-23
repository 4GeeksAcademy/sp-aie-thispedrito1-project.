# Tech Context - HealthCore Digital

## Stack tecnologico
- Frontend principal en Next.js 16 (App Router) con React 19 y TypeScript.
- Estilos con Tailwind CSS v4 via PostCSS.
- Calidad de codigo con ESLint 9 + configuraciones de Next (core web vitals y TypeScript).
- Capa de consumo de datos via fetch HTTP hacia API externa configurada con NEXT_PUBLIC_API_URL.
- Repositorio con estructura multi-area: app web en apps/talent-pipeline-tracker y utilidades/tipos en raiz.

## Decisiones de arquitectura tomadas
- Separacion por capas en la app web:
  - UI/rutas en app y components.
  - Integracion API centralizada en services/api.ts.
  - Contratos de datos tipados en types/tracker.ts.
- Estrategia hibrida server/client en Next.js:
  - Rutas App Router para navegacion y estructura.
  - Componentes cliente para interactividad, filtros y estado local.
- Integracion API-first sin backend propio en este modulo:
  - CRUD de candidatos y notas depende de servicio remoto.
- Normalizacion defensiva de payloads:
  - Adaptacion de campos heterogeneos (por ejemplo stage/step, candidate_id/record_id).
  - Manejo explicito de errores 422 para diagnostico rapido.
- Resiliencia de interfaz:
  - Fallback de datos de respaldo en dashboard para no bloquear visualizacion en errores de red.

## Restricciones tecnicas
- Dependencia de API externa: disponibilidad y consistencia de datos condicionadas por servicio remoto.
- Cambios de ruptura en Next.js 16: es obligatorio validar convenciones y APIs vigentes antes de implementar features nuevas.
- Calidad de tipado mejorable: existen usos de any que deben migrarse a unknown + narrowing para robustez.
- Divergencia de modelo de datos entre respuestas de API: requiere mantener mapeadores y validaciones en cliente.
- Alcance actual enfocado en frontend: no hay capa de backend local para control de seguridad avanzada, auditoria o reintentos transaccionales.

## Implicaciones para implementaciones futuras
- Priorizar contrato canonico de Candidate y estandarizar campos de pipeline.
- Reducir deuda tecnica de tipado en componentes y servicios.
- Evaluar BFF o backend propio para control de seguridad, trazabilidad y cumplimiento.
- Mantener arquitectura por capas para escalar a nuevos modulos del dominio HealthCore.