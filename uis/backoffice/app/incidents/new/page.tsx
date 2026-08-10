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
  title: "Title",
  description: "Description",
  category: "Category",
  status: "Status",
  origin: "Origin",
  branch: "Branch",
};

type FieldErrors = Partial<Record<string, string>>;

function validateForm(form: IncidentCreateInput): FieldErrors {
  const errors: FieldErrors = {};
  if (!form.title.trim()) errors.title = "Title is required.";
  if (form.title.trim().length > 120) errors.title = "Title must be 120 characters or fewer.";
  if (!form.description.trim()) errors.description = "Description is required.";
  if (!form.category) errors.category = "Select a category.";
  if (!form.origin) errors.origin = "Select an origin.";
  if (!form.branch) errors.branch = "Select a branch.";
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
      setFormError("Please review the highlighted fields before submitting.");
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
      setSuccess("Incident reported successfully. The support team can now track it from the incidents panel.");
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
        setFormError(`Some fields could not be accepted: ${fields}. Please review them and try again.`);
      } else {
        setFormError("The incident could not be saved right now. Please try again in a moment.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <h1>Report an incident</h1>
      <p style={{ color: "var(--muted)", maxWidth: 720 }}>
        Register any operational, clinical-equipment, IT, billing, or compliance incident. The support
        team tracks every report from the incidents panel.
      </p>

      <form onSubmit={submit} className="panel" style={{ maxWidth: 860 }}>
        <div className="form-grid">
          <label style={{ gridColumn: "1 / -1" }}>
            Title *
            <input
              value={form.title}
              maxLength={120}
              onChange={(event) => setField("title", event.target.value)}
              placeholder="Short summary of the incident (max 120 characters)"
            />
            {fieldErrors.title && <span className="error-text">{fieldErrors.title}</span>}
          </label>

          <label>
            Category *
            <select value={form.category} onChange={(event) => setField("category", event.target.value as IncidentCreateInput["category"])}>
              <option value="">Select a category…</option>
              {CATEGORY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {fieldErrors.category && <span className="error-text">{fieldErrors.category}</span>}
          </label>

          <label>
            Origin *
            <select value={form.origin} onChange={(event) => setField("origin", event.target.value as IncidentCreateInput["origin"])}>
              <option value="">Select an origin…</option>
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
                ? { background: "#eff6ff", border: "1px solid var(--brand)", borderRadius: 10, padding: 8 }
                : undefined
            }
          >
            Branch *
            <select value={form.branch} onChange={(event) => setField("branch", event.target.value as IncidentCreateInput["branch"])}>
              <option value="">Select a branch…</option>
              {BRANCH_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {branchHighlighted && (
              <span style={{ color: "var(--brand)", fontSize: 12, fontWeight: 600 }}>
                You are reporting from a specific clinic — double-check the branch.
              </span>
            )}
            {fieldErrors.branch && <span className="error-text">{fieldErrors.branch}</span>}
          </label>

          <label>
            Status
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
            border: "2px solid #b45309",
            background: "#fef3c7",
            color: "#78350f",
            borderRadius: 10,
            padding: "10px 12px",
            fontSize: 14,
            fontWeight: 600,
          }}
        >
          ⚠️ Do not include patient-identifying data (names, dates of birth, medical record numbers,
          contact details). If a patient is involved, reference them only by their internal opaque
          identifier. This is a HIPAA / UK GDPR compliance requirement.
        </div>

        <label style={{ display: "flex", marginTop: 10 }}>
          Description *
          <textarea
            value={form.description}
            rows={5}
            onChange={(event) => setField("description", event.target.value)}
            placeholder="What happened, where, and what was affected — without any patient-identifying data."
          />
          {fieldErrors.description && <span className="error-text">{fieldErrors.description}</span>}
        </label>

        <div style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Submitting…" : "Report incident"}
          </button>
          {formError && <span className="error-text">{formError}</span>}
          {success && <span className="success-text">{success}</span>}
        </div>
      </form>
    </main>
  );
}
