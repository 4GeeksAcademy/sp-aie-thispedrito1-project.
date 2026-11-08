# uis/website — web corporativa pública (Next.js 16, puerto 3000)

Next.js 16 tiene breaking changes: leer la guía en `node_modules/next/dist/docs/` antes de escribir código. Contenido en `data/content.ts`, secciones en `components/`. Auditoría de rendimiento en `AUDIT.md` + `REPORT.md` (raíz) y `audit/`.

## Reglas de rendimiento (medidas, no repetir las pruebas)

- **CSS crítico en un `.css` importado por la ruta, nunca en `<style jsx>` de un Client Component**: styled-jsx se inyecta tras la hidratación (CLS 0,974 en `/application`). Archivo propio de la ruta, no `globals.css`.
- Elemento LCP: `loading="eager"` **y** `fetchPriority="high"` (`next/image` es lazy por defecto; `priority` está deprecado).
- Optimizador de imágenes desactivado a propósito (`images.loader: "custom"` + `image-loader.js`): Unsplash ya es un CDN y `/_next/image` añadía un rodeo. Sin `remotePatterns`.
- `next/font` con `preload: false` se probó y empeoró FCP/SI/CLS: está documentado en un comentario de `app/layout.tsx`. La fuente Manrope llega por `next/font/google`.
- `app/icon.svg` evita el 404 de `/favicon.ico` (Best Practices 100).
- Medir siempre sobre `next build` + `next start`, de una en una, con las URLs de Unsplash precalentadas; nunca `next dev`.
