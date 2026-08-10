"use client";

import { useState } from "react";

import { ApiFieldError } from "../../../services/http";
import { createIncident } from "../../../services/incidentsApi";
import {
  BRANCH_OPTIONS,
  CATEGORY_OPTIONS,
  ORIGIN_OPTIONS,
  STATUS_OPTIONS,
  type IncidentCreateInput,
} from "../../../types/incident";

const EMPTY_FORM: IncidentCreateInput = {
  title: "",
  description: "",
  category: "",
  status: "open",
  origin: "",
  branch: "",
};

const FIELD_LABELS: Record<string, string> = {
  title: "Título",
  description: "Descripción",
  category: "Categoría",
  status: "Estado",
  origin: "Origen",
  branch: "Sede",
};

type FieldErrors = Partial<Record<string, string>>;

function validateForm(form: IncidentCreateInput): FieldErrors {
  const errors: FieldErrors = {};
  if (!form.title.trim()) errors.title = "El título es obligatorio.";
  if (form.title.trim().length > 120) errors.title = "El título debe tener 120 caracteres o menos.";
  if (!form.description.trim()) errors.description = "La descripción es obligatoria.";
  if (!form.category) errors.category = "Selecciona una categoría.";
  if (!form.origin) errors.origin = "Selecciona un origen.";
  if (!form.branch) errors.branch = "Selecciona una sede.";
  return errors;
}

export default function ReportIncidentPage() {
  const [form, setForm] = useState<IncidentCreateInput>(EMPTY_FORM);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const setField = <K extends keyof IncidentCreateInput>(key: K, value: IncidentCreateInput[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setFieldErrors((prev) => ({ ...prev, [key]: undefined }));
  };

  const branchHighlighted = form.origin === "branch";

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError(null);
    setSuccess(null);

    const errors = validateForm(form);
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      setFormError("Revisa los campos marcados antes de enviar.");
      return;
    }

    setIsSubmitting(true);
    try {
      await createIncident({
        ...form,
        title: form.title.trim(),
        description: form.description.trim(),
      });
      setForm(EMPTY_FORM);
      setFieldErrors({});
      setSuccess("Incidencia reportada correctamente. El equipo de soporte ya puede hacerle seguimiento desde el panel de incidencias.");
    } catch (submitError) {
      if (submitError instanceof ApiFieldError) {
        const apiErrors: FieldErrors = {};
        for (const { field, message } of submitError.fieldErrors) {
          apiErrors[field] = message;
        }
        setFieldErrors(apiErrors);
        const fields = submitError.fieldErrors
          .map(({ field }) => FIELD_LABELS[field] ?? field)
          .join(", ");
        setFormError(`Algunos campos no fueron aceptados: ${fields}. Revísalos e inténtalo de nuevo.`);
      } else {
        setFormError("No se pudo guardar la incidencia en este momento. Inténtalo de nuevo en unos segundos.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <h1>Reportar una incidencia</h1>
      <p style={{ color: "var(--muted)", maxWidth: 720 }}>
        Registra cualquier incidencia operativa, de equipo clínico, informática, de facturación o de
        cumplimiento normativo. El equipo de soporte hace seguimiento de cada reporte desde el panel de
        incidencias.
      </p>

      <form onSubmit={submit} className="panel" style={{ maxWidth: 860 }}>
        <div className="form-grid">
          <label style={{ gridColumn: "1 / -1" }}>
            Título *
            <input
              value={form.title}
              maxLength={120}
              onChange={(event) => setField("title", event.target.value)}
              placeholder="Resumen breve de la incidencia (máx. 120 caracteres)"
            />
            {fieldErrors.title && <span className="error-text">{fieldErrors.title}</span>}
          </label>

          <label>
            Categoría *
            <select value={form.category} onChange={(event) => setField("category", event.target.value as IncidentCreateInput["category"])}>
              <option value="">Selecciona una categoría…</option>
              {CATEGORY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {fieldErrors.category && <span className="error-text">{fieldErrors.category}</span>}
          </label>

          <label>
            Origen *
            <select value={form.origin} onChange={(event) => setField("origin", event.target.value as IncidentCreateInput["origin"])}>
              <option value="">Selecciona un origen…</option>
              {ORIGIN_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {fieldErrors.origin && <span className="error-text">{fieldErrors.origin}</span>}
          </label>

          <label
            style={
              branchHighlighted
                ? { background: "rgba(255, 138, 61, 0.08)", border: "1px solid var(--brand)", borderRadius: 10, padding: 8 }
                : undefined
            }
          >
            Sede *
            <select value={form.branch} onChange={(event) => setField("branch", event.target.value as IncidentCreateInput["branch"])}>
              <option value="">Selecciona una sede…</option>
              {BRANCH_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {branchHighlighted && (
              <span style={{ color: "var(--brand)", fontSize: 12, fontWeight: 600 }}>
                Estás reportando desde una clínica concreta — verifica la sede.
              </span>
            )}
            {fieldErrors.branch && <span className="error-text">{fieldErrors.branch}</span>}
          </label>

          <label>
            Estado
            <select value={form.status} onChange={(event) => setField("status", event.target.value as IncidentCreateInput["status"])}>
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {fieldErrors.status && <span className="error-text">{fieldErrors.status}</span>}
          </label>
        </div>

        <div
          role="alert"
          style={{
            marginTop: 14,
            border: "2px solid var(--warning)",
            background: "rgba(255, 194, 75, 0.1)",
            color: "var(--warning)",
            borderRadius: 10,
            padding: "10px 12px",
            fontSize: 14,
            fontWeight: 600,
          }}
        >
          ⚠️ No incluyas datos que identifiquen a pacientes (nombres, fechas de nacimiento, números de
          historia clínica, datos de contacto). Si hay un paciente involucrado, referéncialo solo con su
          identificador interno opaco. Es un requisito de cumplimiento HIPAA / UK GDPR.
        </div>

        <label style={{ display: "flex", marginTop: 10 }}>
          Descripción *
          <textarea
            value={form.description}
            rows={5}
            onChange={(event) => setField("description", event.target.value)}
            placeholder="Qué pasó, dónde, y qué se vio afectado — sin datos que identifiquen a pacientes."
          />
          {fieldErrors.description && <span className="error-text">{fieldErrors.description}</span>}
        </label>

        <div style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Enviando…" : "Reportar incidencia"}
          </button>
          {formError && <span className="error-text">{formError}</span>}
          {success && <span className="success-text">{success}</span>}
        </div>
      </form>
    </main>
  );
}
