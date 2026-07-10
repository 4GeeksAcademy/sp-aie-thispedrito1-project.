# Skill: Pre-Commit Readiness

## Objetivo unico
Determinar si un cambio esta realmente listo para commit antes de crear el commit.

## Cuando usar esta skill
- Cuando se termine una feature, fix o implementacion nueva.
- Cuando haya dudas sobre si el alcance del commit esta limpio.
- Cuando se necesite verificar que validacion, staging y memory-bank estan alineados.

## Inputs
- Lista de archivos modificados o staged.
- Descripcion corta del cambio implementado.
- Validaciones ejecutadas o pendientes.
- Indicio de si el cambio afecta alcance, arquitectura o progreso del proyecto.

## Procedimiento
1. Revisar si los archivos modificados pertenecen a una sola unidad de cambio.
2. Confirmar si el memory-bank debe actualizarse:
   - projectbrief.md si cambia alcance, objetivos o prioridades.
   - techContext.md si cambia stack, arquitectura o restricciones.
   - progress.md si cambia estado, riesgos o siguientes pasos.
3. Verificar que exista al menos una validacion relevante para el area tocada.
4. Revisar que el staging no incluya archivos accidentales, temporales o no relacionados.
5. Emitir un veredicto final: listo para commit o no listo para commit.

## Output esperado
La skill debe devolver un resultado con esta estructura:

- Estado: listo para commit o no listo para commit.
- Alcance detectado: una frase breve.
- Validaciones: ejecutadas, faltantes o bloqueadas.
- Memory-bank: actualizado, no requerido o pendiente.
- Bloqueadores: lista vacia o lista de problemas concretos.

## Criterios de aceptacion
- La skill identifica si el cambio esta mezclando trabajo no relacionado.
- La skill confirma si falta actualizar algun archivo del memory-bank.
- La skill detecta ausencia de validacion cuando si existia una validacion razonable.
- La skill produce un veredicto binario y verificable: listo o no listo para commit.
- La skill no propone hacer el commit si existe al menos un bloqueador activo.

## Ejemplo de uso
Input:
- Archivos: AGENTS.md, memory-bank/progress.md
- Cambio: actualizacion de politica de trabajo del agente
- Validacion: revision manual del diff
- Impacto: afecta reglas operativas y estado del proyecto

Resultado esperado:
- Estado: listo para commit
- Alcance detectado: actualizacion de gobernanza del agente
- Validaciones: diff revisado manualmente
- Memory-bank: actualizado
- Bloqueadores: ninguno