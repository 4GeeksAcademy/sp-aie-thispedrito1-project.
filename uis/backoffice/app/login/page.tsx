"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { authApi } from "../../services/authApi";
import { setAuthToken } from "../../services/session";

function resolveRedirect(nextParam: string | null): string {
  if (nextParam && nextParam.startsWith("/") && !nextParam.startsWith("//")) {
    return nextParam;
  }
  return "/";
}

export default function LoginPage() {
  const router = useRouter();
  const nextParam = typeof window !== "undefined"
    ? new URLSearchParams(window.location.search).get("next")
    : null;

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const response = await authApi.login({
        email: email.trim(),
        password,
      });
      setAuthToken(response.access_token);
      router.replace(resolveRedirect(nextParam));
    } catch (requestError: unknown) {
      const message = requestError instanceof Error ? requestError.message : "No se pudo iniciar sesion.";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="shell" style={{ padding: "24px 0 42px" }}>
      <section className="panel" style={{ maxWidth: 520, margin: "0 auto" }}>
        <h1 style={{ marginTop: 0 }}>Iniciar sesion</h1>

        {error && <p className="error-text">{error}</p>}

        <form onSubmit={handleSubmit} style={{ display: "grid", gap: 12 }}>
          <label>
            Email
            <input type="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          <label>
            Contraseña
            <input type="password" required value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          <button type="submit" disabled={submitting}>
            {submitting ? "Validando..." : "Entrar"}
          </button>

          <p style={{ marginTop: -4 }}>
            <Link href="/forgot-password" style={{ color: "var(--brand)", fontWeight: 700, fontSize: "0.9rem" }}>
              ¿Olvidaste tu contraseña?
            </Link>
          </p>
        </form>

        <p style={{ marginBottom: 0 }}>
          No tienes cuenta?{" "}
          <Link href="/register" style={{ color: "var(--brand)", fontWeight: 700 }}>
            Registrate
          </Link>
        </p>
      </section>
    </main>
  );
}
