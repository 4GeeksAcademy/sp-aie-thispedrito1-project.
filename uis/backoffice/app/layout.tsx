import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HealthCore Backoffice",
  description: "Panel interno para operaciones y metricas de HealthCore Digital.",
};

export default function BackofficeLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>
        <header style={{ borderBottom: "1px solid var(--line)", background: "var(--panel)" }}>
          <div className="shell" style={{ padding: "18px 0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <strong>HealthCore Backoffice</strong>
            <span style={{ color: "var(--muted)", fontSize: 14 }}>Unidad interna · Operaciones</span>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
