"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { talentApi } from "@/services/api";

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

    // Read the reset token from the URL query string (?token=...) at submit time.
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
      await talentApi.resetPassword({ token, new_password: password });
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
    <main className="min-h-screen bg-slate-100 flex items-center justify-center p-6">
      <section className="w-full max-w-md bg-white border border-slate-200 rounded-2xl shadow-sm p-6 space-y-5">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Nueva contraseña</h1>
          <p className="text-sm text-slate-500 mt-1">Elige una contraseña nueva para tu cuenta.</p>
        </div>

        {success ? (
          <div className="bg-green-50 border border-green-200 text-green-700 text-sm rounded-lg p-3">
            Contraseña actualizada. Te llevamos a iniciar sesión...
          </div>
        ) : (
          <>
            {error && (
              <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg p-3 space-y-2">
                <p>{error}</p>
                <Link href="/forgot-password" className="font-semibold underline">
                  Solicitar un nuevo enlace
                </Link>
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <label className="block">
                <span className="block text-sm font-semibold text-slate-700 mb-1">Nueva contraseña</span>
                <input
                  type="password"
                  required
                  minLength={8}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900"
                  placeholder="Mínimo 8 caracteres"
                />
              </label>

              <label className="block">
                <span className="block text-sm font-semibold text-slate-700 mb-1">Confirmar contraseña</span>
                <input
                  type="password"
                  required
                  minLength={8}
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900"
                  placeholder="Repite la contraseña"
                />
              </label>

              <button
                type="submit"
                disabled={submitting}
                className="w-full rounded-lg bg-blue-700 hover:bg-blue-800 text-white font-semibold py-2.5 disabled:opacity-60"
              >
                {submitting ? "Guardando..." : "Restablecer contraseña"}
              </button>
            </form>
          </>
        )}
      </section>
    </main>
  );
}
