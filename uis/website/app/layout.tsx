import type { Metadata } from "next";
import { Manrope } from "next/font/google";
import "./globals.css";

// globals.css ya pedia `font-family: var(--font-manrope)`, pero esa variable
// no la declaraba nadie: la web llevaba desde su migracion a React cayendo
// silenciosamente al sans-serif del sistema. next/font descarga la fuente en
// tiempo de build y la auto-hospeda desde nuestro propio dominio, con lo que
// desaparece la peticion a fonts.gstatic.com (una conexion externa menos en
// la cadena critica) y, al usar `display: swap` por defecto, el texto se pinta
// de inmediato con la fuente de reserva en vez de quedarse invisible.
// `adjustFontFallback` (activo por defecto) ajusta las metricas de esa fuente
// de reserva para que el intercambio no provoque layout shift.
const manrope = Manrope({
  subsets: ["latin"],
  variable: "--font-manrope",
  // Se deja el preload por defecto (true) DESPUES DE MEDIRLO. Se probo
  // `preload: false` para que la fuente no compitiera con la imagen del LCP,
  // y el resultado fue peor en conjunto: el LCP no mejoro (seguia en 2.4 s)
  // pero el FCP subio de 0.8 s a 1.1 s, el Speed Index de 0.8 s a 1.1 s y
  // aparecio un CLS de 0.015 al entrar Manrope tarde y sustituir a la fuente
  // de reserva. Queda documentado para que nadie repita la prueba.
});

export const metadata: Metadata = {
  title: "HealthCore Digital | Atencion ambulatoria moderna y segura",
  description:
    "HealthCore Digital moderniza la atencion ambulatoria con citas online, IA clinica y cumplimiento HIPAA y UK GDPR.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es" className={manrope.variable}>
      <body>{children}</body>
    </html>
  );
}
