import type { Metadata } from "next";
import Link from "next/link";
import { AuthGuard } from "../components/AuthGuard";
import { SessionMenu } from "../components/SessionMenu";
import "./globals.css";

export const metadata: Metadata = {
  title: "HealthCore Backoffice",
  description: "Panel interno para operaciones y metricas de HealthCore Digital.",
};

export default function BackofficeLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>
        <AuthGuard>
          <header style={{ borderBottom: "1px solid var(--line)", background: "var(--panel)" }}>
            <div className="shell" style={{ padding: "18px 0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <strong>HealthCore Backoffice</strong>
              <nav style={{ display: "flex", gap: 14, alignItems: "center" }}>
                <Link href="/" className="nav-link">
                  Inicio
                </Link>
                <Link href="/suppliers" className="nav-link">
                  Proveedores
                </Link>
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
