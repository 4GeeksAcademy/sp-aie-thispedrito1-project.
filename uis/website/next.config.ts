import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Las fotos de la web viven en images.unsplash.com, que ya es un CDN de
  // imagenes. En vez de usar el optimizador integrado de Next.js (que las
  // reprocesaria en nuestro propio servidor, añadiendo un salto de red que
  // medimos: la descarga del LCP pasaba de 106 ms a 241 ms), delegamos el
  // redimensionado y la negociacion de formato en el propio Unsplash mediante
  // un loader propio. Seguimos obteniendo srcset, `sizes` y width/height —que
  // es lo que evita el layout shift— pero sin el rodeo.
  //
  // Nota: con un loader propio NO hace falta `remotePatterns`; esa lista solo
  // la aplica el optimizador integrado, que aqui ya no interviene.
  images: {
    loader: "custom",
    loaderFile: "./image-loader.js",
  },
  // Docker Desktop's virtiofs bind mount on macOS doesn't reliably forward
  // native filesystem-change events into the container, so webpack's
  // default watcher can miss host edits. Polling instead always works,
  // at a small CPU cost — only enabled inside the container (DOCKER_DEV),
  // never for native `npm run dev` on the host.
  ...(process.env.DOCKER_DEV ? { watchOptions: { pollIntervalMs: 1000 } } : {}),
};

export default nextConfig;
