"use client";

import { useEffect } from "react";

import { track } from "../services/telemetry";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    track("frontend_error_captured", {
      component_name: "ErrorBoundary",
      route_template: window.location.pathname,
      error_name: error.name,
      error_message: error.message.slice(0, 200),
    });
  }, [error]);

  return (
    <main className="shell" style={{ padding: "48px 0" }}>
      <div className="panel" style={{ maxWidth: 480 }}>
        <h1>Algo salió mal</h1>
        <p style={{ color: "var(--muted)" }}>
          Ocurrió un error inesperado en esta pantalla. Puedes intentar de nuevo o volver al inicio.
        </p>
        <div style={{ display: "flex", gap: 10, marginTop: 14 }}>
          <button type="button" onClick={reset}>
            Reintentar
          </button>
          <a href="/" className="nav-link">
            Volver al inicio
          </a>
        </div>
      </div>
    </main>
  );
}
