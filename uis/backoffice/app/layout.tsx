import type { Metadata } from "next";
import Link from "next/link";
import { AuthGuard } from "../components/AuthGuard";
import { ErrorTracking } from "../components/ErrorTracking";
import { PageViewTracker } from "../components/PageViewTracker";
import { SessionMenu } from "../components/SessionMenu";
import { ThemeToggle } from "../components/ThemeToggle";
import { WebVitals } from "../components/WebVitals";
import "./globals.css";

export const metadata: Metadata = {
  title: "HealthCore Backoffice",
  description: "Panel interno para operaciones y metricas de HealthCore Digital.",
};

// Reads the saved theme before first paint so switching to "light" never
// flashes the dark theme first — this must run synchronously in <head>,
// before globals.css's default :root (dark) has a chance to render.
const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem("healthcore.theme");
    if (stored === "light") {
      document.documentElement.setAttribute("data-theme", "light");
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
          <header style={{ borderBottom: "1px solid var(--line)", background: "var(--panel)" }}>
            <div className="shell" style={{ padding: "18px 0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <strong>HealthCore Panel</strong>
              <nav style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
                <Link href="/" className="nav-link">
                  Inicio
                </Link>
                <Link href="/suppliers" className="nav-link">
                  Proveedores
                </Link>
                <Link href="/incidents" className="nav-link">
                  Incidencias
                </Link>
                <Link href="/incidents/new" className="nav-link">
                  Reportar incidencia
                </Link>
                <Link href="/incidents/summary" className="nav-link">
                  Resumen
                </Link>
                <Link href="/inventory/products" className="nav-link">
                  Inventario
                </Link>
                <Link href="/inventory/orders" className="nav-link">
                  Historial de órdenes
                </Link>
                <ThemeToggle />
                <SessionMenu />
              </nav>
            </div>
          </header>
          {children}
        </AuthGuard>
      </body>
    </html>
  );
}
