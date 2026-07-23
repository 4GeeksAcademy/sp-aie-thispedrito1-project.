"use client";

import { useEffect, useState } from "react";

import { authApi } from "../../../services/authApi";
import { clearAuthToken } from "../../../services/session";
import type { AuthMeResponse } from "../../../types/auth";

export default function AccountProfilePage() {
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
        const me = await authApi.getMe();
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
      const profile = await authApi.updateMyProfile({
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

  return (
    <main className="shell" style={{ padding: "24px 0 42px" }}>
      <section className="panel" style={{ maxWidth: 760, margin: "0 auto", display: "grid", gap: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
          <div>
            <h1 style={{ margin: 0 }}>Perfil de cuenta</h1>
            <p style={{ color: "var(--muted)", marginBottom: 0 }}>Gestiona tu informacion personal para el backoffice.</p>
          </div>
          <button type="button" onClick={handleLogout}>
            Cerrar sesion
          </button>
        </div>

        {loading && <p style={{ margin: 0 }}>Cargando perfil...</p>}
        {error && <p className="error-text">{error}</p>}
        {success && <p className="success-text">{success}</p>}

        {!loading && (
          <form onSubmit={handleSave} style={{ display: "grid", gap: 12 }}>
            <label>
              Email
              <input type="email" value={email} readOnly style={{ background: "#f8fafc" }} />
            </label>
            <label>
              Nombre
              <input type="text" value={name} onChange={(event) => setName(event.target.value)} maxLength={160} />
            </label>
            <label>
              Telefono
              <input type="text" value={phone} onChange={(event) => setPhone(event.target.value)} maxLength={40} />
            </label>
            <label>
              Direccion
              <input type="text" value={address} onChange={(event) => setAddress(event.target.value)} maxLength={300} />
            </label>
            <button type="submit" disabled={saving}>
              {saving ? "Guardando..." : "Guardar cambios"}
            </button>
          </form>
        )}
      </section>
    </main>
  );
}
