# AGENTS.md

## Inicio obligatorio de cada sesion

Al comenzar cada sesion, el agente debe leer estos archivos del banco de memoria en este orden:

1. memory-bank/projectbrief.md
2. memory-bank/techContext.md
3. memory-bank/progress.md

Objetivo de cada archivo:

- memory-bank/projectbrief.md: contexto de negocio, objetivos, alcance, KPIs, stakeholders y prioridades de entrega.
- memory-bank/techContext.md: stack actual, decisiones de arquitectura, restricciones tecnicas e implicaciones para nuevas implementaciones.
- memory-bank/progress.md: estado actual del desarrollo, riesgos activos, trabajo en curso y proximos pasos previstos.

Si falta alguno de estos archivos, el agente debe informarlo antes de proponer cambios o implementar nuevas features.

## Rutas protegidas

Las siguientes carpetas y archivos no deben modificarse sin confirmacion explicita del desarrollador:

- node_modules/
- .git/
- package-lock.json
- apps/talent-pipeline-tracker/AGENTS.md
- Cualquier archivo de credenciales, secretos, tokens o configuraciones sensibles si aparecieran en el repositorio.

## Flujo obligatorio antes de cada commit

Antes de hacer cualquier commit, el agente debe completar este flujo en orden:

1. Verificar el alcance del cambio.
- Confirmar que el commit corresponde a una sola feature, fix o implementacion claramente acotada.
- Excluir cambios no relacionados del area de staging.

2. Actualizar el banco de memoria si aplica.
- Revisar si el cambio modifica objetivos, contexto tecnico o estado del desarrollo.
- Actualizar memory-bank/projectbrief.md, memory-bank/techContext.md o memory-bank/progress.md cuando el cambio afecte materialmente alguno de esos ejes.

3. Validar el cambio.
- Ejecutar la validacion mas acotada y relevante posible.
- Priorizar este orden: comprobacion funcional puntual, test enfocado, lint, typecheck o build del area tocada.
- No avanzar a commit si existe una validacion razonable y no fue ejecutada.

4. Revisar el diff staged.
- Confirmar que solo esten preparados los archivos esperados.
- Detectar y excluir logs, debug, secretos, artefactos temporales o cambios accidentales.

5. Escribir un commit especifico.
- Usar un mensaje que describa la implementacion real entregada.
- Mantener la regla de un commit por feature o implementacion nueva siempre que sea posible.

## Politica de commits

- Un commit por feature o implementacion nueva.
- No mezclar cambios no relacionados en un mismo commit.
- Si el cambio afecta arquitectura, contratos o estado del proyecto, el memory-bank debe quedar actualizado antes del commit.
- Si no fue posible validar por una limitacion del entorno, el agente debe dejarlo explicito antes de cerrar el trabajo.

## Nota de alcance

- Este archivo define reglas generales para todo el monorepo.
- Las apps o subdirectorios pueden tener AGENTS.md adicionales con reglas mas especificas que complementen estas instrucciones.