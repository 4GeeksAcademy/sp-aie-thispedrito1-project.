---
name: Regla de desarrollo del monorepo
scope: siempre activa
appliesTo: "**"
purpose: Mantener cambios acotados, validados y alineados con el banco de memoria.
---

# Regla de desarrollo del monorepo

## Alcance de aplicacion
- Siempre activa.
- Aplica a cualquier archivo del monorepo salvo rutas protegidas definidas en AGENTS.md.

## Regla
- El agente debe trabajar con cambios pequenos, acotados a una sola feature, fix o implementacion por vez.
- Antes de editar, debe revisar el contexto minimo necesario en memory-bank/projectbrief.md, memory-bank/techContext.md y memory-bank/progress.md.
- Si el cambio altera arquitectura, alcance, riesgos o estado del proyecto, debe actualizar el archivo correspondiente del memory-bank antes de preparar un commit.
- No debe modificar rutas protegidas sin confirmacion explicita del desarrollador.
- Debe ejecutar la validacion mas estrecha posible antes de cerrar el trabajo o preparar un commit.

## Criterio de cumplimiento
- El cambio queda limitado al alcance pedido.
- El memory-bank queda sincronizado si hubo impacto funcional o tecnico relevante.
- Existe al menos una validacion realizada o una limitacion explicita del entorno.
