"use client";

import { useState } from "react";

import type { Supplier, SupplierCreateInput } from "../types/supplier";

type ProviderFormProps = {
  onSubmit: (payload: SupplierCreateInput) => Promise<Supplier>;
};

const categories = [
  "medical_supplies",
  "laboratory_services",
  "pharmaceutical",
  "clinical_software",
  "it_infrastructure",
  "hr_and_payroll_software",
  "cleaning_and_facilities",
  "patient_communication",
  "billing_and_coding_software",
  "training_platforms",
];

export function ProviderForm({ onSubmit }: ProviderFormProps) {
  const [form, setForm] = useState<SupplierCreateInput>({
    name: "",
    country: "USA",
    categories: ["medical_supplies"],
    monthly_rate: 1,
    currency: "USD",
    status: "active",
    compliance_agreement: null,
    contact_email: "",
    notes: "",
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setSuccess(null);

    if (!form.name.trim() || form.monthly_rate <= 0 || form.categories.length === 0) {
      setError("Completa los campos requeridos y usa una tarifa mayor a 0.");
      return;
    }

    setIsSubmitting(true);

    try {
      await onSubmit({
        ...form,
        name: form.name.trim(),
        contact_email: form.contact_email?.trim() || undefined,
        notes: form.notes?.trim() || undefined,
      });
      setSuccess("Proveedor creado correctamente.");
      setForm({
        name: "",
        country: "USA",
        categories: ["medical_supplies"],
        monthly_rate: 1,
        currency: "USD",
        status: "active",
        compliance_agreement: null,
        contact_email: "",
        notes: "",
      });
    } catch (submitError) {
      const message = submitError instanceof Error ? submitError.message : "No se pudo crear el proveedor.";
      setError(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form onSubmit={submit} className="panel" style={{ marginBottom: 18 }}>
      <h2 style={{ marginTop: 0 }}>Registrar proveedor</h2>
      <div className="form-grid">
        <label>
          Nombre
          <input value={form.name} onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))} required />
        </label>
        <label>
          Pais
          <select
            value={form.country}
            onChange={(event) => {
              const country = event.target.value as "USA" | "UK";
              setForm((prev) => ({ ...prev, country, currency: country === "USA" ? "USD" : "GBP" }));
            }}
          >
            <option value="USA">USA</option>
            <option value="UK">UK</option>
          </select>
        </label>
        <label>
          Categoria
          <select
            value={form.categories[0]}
            onChange={(event) => setForm((prev) => ({ ...prev, categories: [event.target.value] }))}
          >
            {categories.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Tarifa mensual
          <input
            type="number"
            min={1}
            step="0.01"
            value={form.monthly_rate}
            onChange={(event) => setForm((prev) => ({ ...prev, monthly_rate: Number(event.target.value) }))}
            required
          />
        </label>
        <label>
          Moneda
          <select value={form.currency} onChange={(event) => setForm((prev) => ({ ...prev, currency: event.target.value as "USD" | "GBP" }))}>
            <option value="USD">USD</option>
            <option value="GBP">GBP</option>
          </select>
        </label>
        <label>
          Estado
          <select value={form.status} onChange={(event) => setForm((prev) => ({ ...prev, status: event.target.value as "active" | "suspended" }))}>
            <option value="active">active</option>
            <option value="suspended">suspended</option>
          </select>
        </label>
        <label>
          Compliance
          <select
            value={form.compliance_agreement ?? "none"}
            onChange={(event) =>
              setForm((prev) => ({
                ...prev,
                compliance_agreement:
                  event.target.value === "none" ? null : (event.target.value as "BAA" | "DPA" | "both"),
              }))
            }
          >
            <option value="none">No aplica</option>
            <option value="BAA">BAA</option>
            <option value="DPA">DPA</option>
            <option value="both">both</option>
          </select>
        </label>
        <label>
          Contact email
          <input
            type="email"
            value={form.contact_email ?? ""}
            onChange={(event) => setForm((prev) => ({ ...prev, contact_email: event.target.value }))}
          />
        </label>
      </div>
      <label style={{ display: "block", marginTop: 10 }}>
        Notas
        <textarea value={form.notes ?? ""} onChange={(event) => setForm((prev) => ({ ...prev, notes: event.target.value }))} rows={3} />
      </label>

      <div style={{ marginTop: 12, display: "flex", gap: 10, alignItems: "center" }}>
        <button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Guardando..." : "Crear proveedor"}
        </button>
        {error && <span className="error-text">{error}</span>}
        {success && <span className="success-text">{success}</span>}
      </div>
    </form>
  );
}
