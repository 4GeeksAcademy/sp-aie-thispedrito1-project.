"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { authApi } from "../../services/authApi";

export default function ResetPasswordPage() {
  const router = useRouter();

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);

    const token = new URLSearchParams(window.location.search).get("token");
    if (!token) {
      setError("Falta el token de restablecimiento en el enlace.");
      return;
    }
    if (password.length < 8) {
      setError("La contraseña debe tener al menos 8 caracteres.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Las contraseñas no coinciden.");
      return;
    }

    setSubmitting(true);
    try {
      await authApi.resetPassword({ token, new_password: password });
      setSuccess(true);
      setTimeout(() => router.replace("/login"), 1500);
    } catch (requestError: unknown) {
      const message =
        requestError instanceof Error ? requestError.message : "No se pudo restablecer la contraseña.";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="shell" style={{ padding: "24px 0 42px" }}>
      <section className="panel" style={{ maxWidth: 520, margin: "0 auto" }}>
        <h1 style={{ marginTop: 0 }}>Nueva contraseña</h1>
        <p style={{ color: "var(--muted)" }}>Elige una contraseña nueva para tu cuenta.</p>

        {success ? (
          <div style={{
            background: "var(--success-bg, #e6f7e6)",
            border: "1px solid var(--success-border, #b7eb8f)",
            color: "var(--success-text, #135200)",
            borderRadius: 8,
            padding: "12px 16px",
            fontSize: "0.875rem",
          }}>
            Contraseña actualizada. Te llevamos a iniciar sesión...
          </div>
        ) : (
          <>
            {error && (
              <div style={{
                background: "var(--error-bg, #fce4e4)",
                border: "1px solid var(--error-border, #f5c6c6)",
                color: "var(--error-text, #a94442)",
                borderRadius: 8,
                padding: "12px 16px",
                fontSize: "0.875rem",
                marginBottom: 12,
              }}>
                <p style={{ margin: 0 }}>{error}</p>
                <Link href="/forgot-password" style={{ fontWeight: 700, color: "inherit" }}>
                  Solicitar un nuevo enlace
                </Link>
              </div>
            )}

            <form onSubmit={handleSubmit} style={{ display: "grid", gap: 12 }}>
              <label>
                Nueva contraseña
                <input
                  type="password"
                  required
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
              <label>
                Confirmar contraseña
                <input
                  type="password"
                  required
                  minLength={8}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                />
              </label>
              <button type="submit" disabled={submitting} className="primary">
                {submitting ? "Guardando..." : "Restablecer contraseña"}
              </button>
            </form>
          </>
        )}
      </section>
    </main>
  );
}