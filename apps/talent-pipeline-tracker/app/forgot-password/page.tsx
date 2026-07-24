"use client";

import Link from "next/link";
import { useState } from "react";

import { talentApi } from "@/services/api";

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
      // The API always returns 200 to avoid user enumeration; we mirror that
      // by showing the same confirmation regardless of the outcome.
      await talentApi.forgotPassword({ email: email.trim() });
    } catch {
      // Intentionally ignored: never reveal whether the address exists.
    } finally {
      setSubmitting(false);
      setSubmitted(true);
    }
  };

  return (
    <main className="min-h-screen bg-slate-100 flex items-center justify-center p-6">
      <section className="w-full max-w-md bg-white border border-slate-200 rounded-2xl shadow-sm p-6 space-y-5">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">¿Olvidaste tu contraseña?</h1>
          <p className="text-sm text-slate-500 mt-1">
            Introduce tu email y te enviaremos un enlace para restablecerla.
          </p>
        </div>

        {submitted ? (
          <div className="bg-green-50 border border-green-200 text-green-700 text-sm rounded-lg p-3">
            {CONFIRMATION_MESSAGE}
          </div>
        ) : (
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

            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-lg bg-blue-700 hover:bg-blue-800 text-white font-semibold py-2.5 disabled:opacity-60"
            >
              {submitting ? "Enviando..." : "Enviar enlace"}
            </button>
          </form>
        )}

        <p className="text-sm text-slate-600">
          <Link href="/login" className="text-blue-700 font-semibold hover:underline">
            Volver a iniciar sesión
          </Link>
        </p>
      </section>
    </main>
  );
}
