/**
 * Loader de imagenes para next/image.
 *
 * Todas las fotos de la web se sirven desde images.unsplash.com, que ya es un
 * CDN de imagenes: acepta `w` para redimensionar en origen y `auto=format`
 * para negociar AVIF/WebP segun el navegador. Con el optimizador integrado de
 * Next.js cada foto daba un rodeo extra — navegador -> nuestro servidor ->
 * Unsplash -> nuestro servidor -> navegador — y en la medicion de Lighthouse
 * eso subio la descarga de la imagen del LCP de 106 ms a 241 ms (ver
 * REPORT.md). Con este loader se conservan srcset, `sizes` y los atributos
 * width/height que evitan el layout shift, pero los bytes vuelven a salir del
 * edge de Unsplash.
 *
 * @param {{ src: string, width: number, quality?: number }} params
 * @returns {string} URL final de la imagen
 */
export default function unsplashLoader({ src, width, quality }) {
  // Con `loader: "custom"` pasa por aqui TODA imagen, tambien una futura
  // imagen local (`/foo.png`), que no es una URL absoluta y haria estallar
  // `new URL`. En ese caso se devuelve tal cual, sin transformar.
  if (!/^https?:\/\//.test(src)) return src;

  const url = new URL(src);
  url.searchParams.set("auto", "format");
  url.searchParams.set("fit", "crop");
  url.searchParams.set("w", String(width));
  url.searchParams.set("q", String(quality || 75));
  return url.toString();
}
