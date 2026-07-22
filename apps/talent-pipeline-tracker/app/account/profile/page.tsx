"use client";

import { useEffect, useState } from "react";

import { talentApi } from "@/services/api";
import { clearAuthToken } from "@/services/session";
import type { AuthMeResponse } from "@/types/auth";

export default function ProfilePage() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [address, setAddress] = useState("");

  useEffect(() => {
    const loadProfile = async () => {
      try {
        setLoading(true);
        setError(null);
        const me = await talentApi.getMe();
        hydrateForm(me);
      } catch (requestError: unknown) {
        const message = requestError instanceof Error ? requestError.message : "No se pudo cargar el perfil.";
        setError(message);
      } finally {
        setLoading(false);
      }
    };

    void loadProfile();
  }, []);

  const hydrateForm = (me: AuthMeResponse) => {
    setEmail(me.email);
    setName(me.profile.name || "");
    setPhone(me.profile.phone || "");
    setAddress(me.profile.address || "");
  };

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setSuccess(null);

    try {
      const profile = await talentApi.updateMyProfile({
        name: name.trim() || undefined,
        phone: phone.trim() || undefined,
        address: address.trim() || undefined,
      });
      setName(profile.name || "");
      setPhone(profile.phone || "");
      setAddress(profile.address || "");
      setSuccess("Perfil actualizado correctamente.");
    } catch (requestError: unknown) {
      const message = requestError instanceof Error ? requestError.message : "No se pudo actualizar el perfil.";
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  const handleLogout = () => {
    clearAuthToken();
    if (typeof window !== "undefined") {
      window.location.assign("/login");
    }
  };

  if (loading) {
    return (
      <main className="min-h-screen bg-slate-50 p-6 md:p-12 font-sans">
        <div className="max-w-3xl mx-auto bg-white border border-slate-200 rounded-xl p-6">
          <p className="text-slate-600">Cargando perfil...</p>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-50 p-6 md:p-12 font-sans">
      <div className="max-w-3xl mx-auto bg-white border border-slate-200 rounded-xl p-6 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Perfil de cuenta</h1>
            <p className="text-sm text-slate-500 mt-1">Gestiona tus datos de contacto para operaciones internas.</p>
          </div>
          <button
            type="button"
            onClick={handleLogout}
            className="px-3 py-2 text-sm font-semibold border border-slate-300 rounded-lg hover:bg-slate-50"
          >
            Cerrar sesion
          </button>
        </div>

        {error && <div className="bg-red-50 border border-red-200 text-red-700 p-3 rounded-lg text-sm">{error}</div>}
        {success && <div className="bg-green-50 border border-green-200 text-green-700 p-3 rounded-lg text-sm">{success}</div>}

        <form onSubmit={handleSave} className="space-y-4">
          <label className="block">
            <span className="block text-sm font-semibold text-slate-700 mb-1">Email</span>
            <input
              type="email"
              value={email}
              readOnly
              className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-slate-700"
            />
          </label>

          <label className="block">
            <span className="block text-sm font-semibold text-slate-700 mb-1">Nombre</span>
            <input
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900"
              maxLength={160}
            />
          </label>

          <label className="block">
            <span className="block text-sm font-semibold text-slate-700 mb-1">Telefono</span>
            <input
              type="text"
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900"
              maxLength={40}
            />
          </label>

          <label className="block">
            <span className="block text-sm font-semibold text-slate-700 mb-1">Direccion</span>
            <input
              type="text"
              value={address}
              onChange={(event) => setAddress(event.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900"
              maxLength={300}
            />
          </label>

          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-blue-700 hover:bg-blue-800 text-white font-semibold py-2.5 px-4 disabled:opacity-60"
          >
            {saving ? "Guardando..." : "Guardar cambios"}
          </button>
        </form>
      </div>
    </main>
  );
}
