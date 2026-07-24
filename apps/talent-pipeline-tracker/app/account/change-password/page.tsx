"use client";

import Link from "next/link";
import { useState } from "react";

import { talentApi } from "@/services/api";

export default function ChangePasswordPage() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setSuccess(null);

    if (newPassword.length < 8) {
      setError("La nueva contraseña debe tener al menos 8 caracteres.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("La nueva contraseña y la confirmación no coinciden.");
      return;
    }

    setSubmitting(true);
    try {
      await talentApi.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      setSuccess("Contraseña actualizada correctamente.");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (requestError: unknown) {
      const message =
        requestError instanceof Error ? requestError.message : "No se pudo cambiar la contraseña.";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-50 p-6 md:p-12 font-sans">
      <div className="max-w-3xl mx-auto bg-white border border-slate-200 rounded-xl p-6 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Cambiar contraseña</h1>
            <p className="text-sm text-slate-500 mt-1">Actualiza tu contraseña estando conectado.</p>
          </div>
          <Link
            href="/account/profile"
            className="px-3 py-2 text-sm font-semibold border border-slate-300 rounded-lg hover:bg-slate-50"
          >
            Volver al perfil
          </Link>
        </div>

        {error && <div className="bg-red-50 border border-red-200 text-red-700 p-3 rounded-lg text-sm">{error}</div>}
        {success && (
          <div className="bg-green-50 border border-green-200 text-green-700 p-3 rounded-lg text-sm">{success}</div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <label className="block">
            <span className="block text-sm font-semibold text-slate-700 mb-1">Contraseña actual</span>
            <input
              type="password"
              required
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900"
            />
          </label>

          <label className="block">
            <span className="block text-sm font-semibold text-slate-700 mb-1">Nueva contraseña</span>
            <input
              type="password"
              required
              minLength={8}
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900"
              placeholder="Mínimo 8 caracteres"
            />
          </label>

          <label className="block">
            <span className="block text-sm font-semibold text-slate-700 mb-1">Confirmar nueva contraseña</span>
            <input
              type="password"
              required
              minLength={8}
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900"
            />
          </label>

          <button
            type="submit"
            disabled={submitting}
            className="rounded-lg bg-blue-700 hover:bg-blue-800 text-white font-semibold py-2.5 px-4 disabled:opacity-60"
          >
            {submitting ? "Guardando..." : "Cambiar contraseña"}
          </button>
        </form>
      </div>
    </main>
  );
}
