# Skill: Memory Bank Sync

## Objetivo unico
Determinar y ejecutar la actualizacion minima necesaria del memory-bank despues de un cambio relevante en el proyecto.

## Cuando usar esta skill
- Cuando una implementacion cambie el estado del desarrollo.
- Cuando una implementacion cambie arquitectura, restricciones o stack.
- Cuando cambien prioridades, alcance o definicion de siguientes pasos.

## Inputs
- Resumen breve del cambio realizado.
- Lista de archivos tocados.
- Indicio del tipo de impacto: negocio, arquitectura, progreso o ninguno.
- Resultado de validacion disponible para ese cambio.

## Procedimiento
1. Clasificar el impacto del cambio:
   - projectbrief.md para cambios de objetivos, alcance, stakeholders, KPIs o prioridades.
   - techContext.md para cambios de stack, arquitectura, restricciones o decisiones tecnicas.
   - progress.md para cambios en estado actual, completado, riesgos, trabajo en curso o proximos pasos.
2. Determinar si el impacto es material o no.
3. Si el impacto es material, actualizar solo el archivo minimo necesario del memory-bank.
4. Comprobar que la actualizacion del memory-bank refleje el estado real posterior al cambio.
5. Devolver un resumen de que archivo fue actualizado o por que no hacia falta actualizar ninguno.

## Output esperado
- Estado: actualizado o no requerido.
- Archivo afectado: projectbrief.md, techContext.md, progress.md o ninguno.
- Motivo: una frase breve.
- Evidencia: referencia corta al cambio que disparo la decision.

## Criterios de aceptacion
- La skill elige correctamente el archivo del memory-bank que corresponde al impacto del cambio.
- La skill evita actualizaciones innecesarias cuando no hubo impacto material.
- La skill no actualiza mas de lo necesario para reflejar el estado real del proyecto.
- La skill deja un resultado verificable: archivo actualizado o decision justificada de no actualizar.

## Ejemplo de uso
Input:
- Cambio: se crea una nueva skill reutilizable para validacion pre-commit.
- Archivos: skills/pre-commit-readiness/SKILL.md, AGENTS.md
- Impacto: progreso del proyecto y gobernanza operativa
- Validacion: revision del archivo y chequeo de errores del workspace

Resultado esperado:
- Estado: actualizado
- Archivo afectado: progress.md
- Motivo: el proyecto avanzo en gobernanza y automatizacion del flujo del agente
- Evidencia: nueva skill reusable disponible en skills/