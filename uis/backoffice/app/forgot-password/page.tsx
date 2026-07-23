"use client";

import Link from "next/link";
import { useState } from "react";

import { authApi } from "../../services/authApi";

const CONFIRMATION_MESSAGE =
  "Si esa dirección está registrada, recibirás un enlace para restablecer tu contraseña en breve.";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submitting || submitted) return;

    setSubmitting(true);
    try {
      await authApi.forgotPassword({ email: email.trim() });
    } catch {
      // Intentionally ignored: never reveal whether the address exists.
    } finally {
      setSubmitting(false);
      setSubmitted(true);
    }
  };

  return (
    <main className="shell" style={{ padding: "24px 0 42px" }}>
      <section className="panel" style={{ maxWidth: 520, margin: "0 auto" }}>
        <h1 style={{ marginTop: 0 }}>¿Olvidaste tu contraseña?</h1>
        <p style={{ color: "var(--muted)" }}>
          Introduce tu email y te enviaremos un enlace para restablecerla.
        </p>

        {submitted ? (
          <div style={{
            background: "var(--success-bg, #e6f7e6)",
            border: "1px solid var(--success-border, #b7eb8f)",
            color: "var(--success-text, #135200)",
            borderRadius: 8,
            padding: "12px 16px",
            fontSize: "0.875rem",
          }}>
            {CONFIRMATION_MESSAGE}
          </div>
        ) : (
          <form onSubmit={handleSubmit} style={{ display: "grid", gap: 12 }}>
            <label>
              Email
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <button type="submit" disabled={submitting} className="primary">
              {submitting ? "Enviando..." : "Enviar enlace"}
            </button>
          </form>
        )}

        <p style={{ marginBottom: 0 }}>
          <Link href="/login" style={{ color: "var(--brand)", fontWeight: 700 }}>
            Volver a iniciar sesión
          </Link>
        </p>
      </section>
    </main>
  );
}