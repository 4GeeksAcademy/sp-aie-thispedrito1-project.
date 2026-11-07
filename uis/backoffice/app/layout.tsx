import type { Metadata } from "next";
import { AppShell } from "../components/AppShell";
import { AuthGuard } from "../components/AuthGuard";
import { ErrorTracking } from "../components/ErrorTracking";
import { PageViewTracker } from "../components/PageViewTracker";
import { WebVitals } from "../components/WebVitals";
import "./globals.css";

export const metadata: Metadata = {
  title: "HealthCore Backoffice",
  description: "Panel interno para operaciones y metricas de HealthCore Digital.",
};

// Lee el tema guardado antes del primer pintado, para que quien eligió el
// oscuro no vea un destello del claro (el :root por defecto de globals.css).
const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem("healthcore.theme");
    if (stored === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    }
  } catch (e) {}
})();
`;

export default function BackofficeLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <ErrorTracking />
        <WebVitals />
        <PageViewTracker />
        <AuthGuard>
          <AppShell>{children}</AppShell>
        </AuthGuard>
      </body>
    </html>
  );
}
