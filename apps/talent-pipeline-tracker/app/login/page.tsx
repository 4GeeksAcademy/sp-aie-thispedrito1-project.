"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { talentApi } from "@/services/api";
import { setAuthToken } from "@/services/session";

function resolveRedirect(nextParam: string | null): string {
  if (nextParam && nextParam.startsWith("/") && !nextParam.startsWith("//")) {
    return nextParam;
  }
  return "/";
}

export default function LoginPage() {
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const response = await talentApi.login({
        email: email.trim(),
        password,
      });
      setAuthToken(response.access_token);
      const nextParam = typeof window !== "undefined"
        ? new URLSearchParams(window.location.search).get("next")
        : null;
      router.replace(resolveRedirect(nextParam));
    } catch (requestError: unknown) {
      const message = requestError instanceof Error ? requestError.message : "No se pudo iniciar sesion.";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-100 flex items-center justify-center p-6">
      <section className="w-full max-w-md bg-white border border-slate-200 rounded-2xl shadow-sm p-6 space-y-5">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Iniciar sesion</h1>
          <p className="text-sm text-slate-500 mt-1">Accede al panel interno de Talent Pipeline.</p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg p-3">{error}</div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <label className="block">
            <span className="block text-sm font-semibold text-slate-700 mb-1">Email</span>
            <input
              type="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900"
              placeholder="tu@email.com"
            />
          </label>

          <label className="block">
            <span className="block text-sm font-semibold text-slate-700 mb-1">Contrasena</span>
            <input
              type="password"
              required
              minLength={1}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900"
              placeholder="Ingresa tu contrasena"
            />
          </label>

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-blue-700 hover:bg-blue-800 text-white font-semibold py-2.5 disabled:opacity-60"
          >
            {submitting ? "Validando..." : "Entrar"}
          </button>
        </form>

        <p className="text-sm text-slate-600">
          No tienes cuenta?{" "}
          <Link href="/register" className="text-blue-700 font-semibold hover:underline">
            Registrate
          </Link>
        </p>
      </section>
    </main>
  );
}
