"use client";

import { FormEvent, useMemo, useState } from "react";

type Region = "US" | "UK" | "";

interface FormDataState {
  firstName: string;
  lastName: string;
  dateOfBirth: string;
  nationalId: string;
  email: string;
  phone: string;
  region: Region;
  postalCode: string;
  preferredClinic: string;
  dataConsent: boolean;
  reminderConsent: boolean;
}

const clinicsByRegion: Record<Exclude<Region, "">, string[]> = {
  US: [
    "Houston, TX",
    "Dallas, TX",
    "Austin, TX",
    "Miami, FL",
    "Orlando, FL",
    "Tampa, FL",
    "Atlanta, GA",
    "Savannah, GA",
    "Augusta, GA",
  ],
  UK: ["London Central", "Manchester North", "Manchester South"],
};

const disposableDomains = new Set([
  "mailinator.com",
  "10minutemail.com",
  "guerrillamail.com",
  "tempmail.com",
  "yopmail.com",
]);

const initialState: FormDataState = {
  firstName: "",
  lastName: "",
  dateOfBirth: "",
  nationalId: "",
  email: "",
  phone: "",
  region: "",
  postalCode: "",
  preferredClinic: "",
  dataConsent: false,
  reminderConsent: false,
};

const normalizePhone = (value: string) => value.replace(/[\s()-]/g, "");

export default function ApplicationPage() {
  const [formData, setFormData] = useState<FormDataState>(initialState);
  const [errors, setErrors] = useState<Record<keyof FormDataState, string>>({} as Record<keyof FormDataState, string>);
  const [status, setStatus] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  const clinics = useMemo(() => (formData.region ? clinicsByRegion[formData.region] : []), [formData.region]);

  const validate = (data: FormDataState) => {
    const nextErrors: Partial<Record<keyof FormDataState, string>> = {};

    if (!/^[\p{L}\s'-]{2,}$/u.test(data.firstName.trim())) {
      nextErrors.firstName = "El nombre debe tener al menos 2 caracteres validos.";
    }
    if (!/^[\p{L}\s'-]{2,}$/u.test(data.lastName.trim())) {
      nextErrors.lastName = "Los apellidos deben tener al menos 2 caracteres validos.";
    }

    const birthDate = new Date(data.dateOfBirth);
    if (!data.dateOfBirth || Number.isNaN(birthDate.getTime())) {
      nextErrors.dateOfBirth = "Selecciona una fecha de nacimiento valida.";
    } else {
      const now = new Date();
      const age = now.getFullYear() - birthDate.getFullYear();
      const monthDelta = now.getMonth() - birthDate.getMonth();
      const finalAge = monthDelta < 0 || (monthDelta === 0 && now.getDate() < birthDate.getDate()) ? age - 1 : age;
      if (finalAge < 18) {
        nextErrors.dateOfBirth = "Debes tener 18 anos o mas para registrarte.";
      }
    }

    if (!/^[A-Za-z0-9-]{6,20}$/.test(data.nationalId.trim())) {
      nextErrors.nationalId = "El documento debe tener entre 6 y 20 caracteres alfanumericos.";
    }

    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(data.email.trim())) {
      nextErrors.email = "Ingresa un correo electronico valido.";
    } else {
      const domain = data.email.split("@")[1]?.toLowerCase() || "";
      if (disposableDomains.has(domain)) {
        nextErrors.email = "No se aceptan correos temporales.";
      }
    }

    if (!data.region) {
      nextErrors.region = "Selecciona una region de atencion.";
    }

    const normalizedPhone = normalizePhone(data.phone.trim());
    if (!data.phone.trim()) {
      nextErrors.phone = "Ingresa un telefono de contacto.";
    } else if (data.region === "US" && !/^\+?1?\d{10}$/.test(normalizedPhone)) {
      nextErrors.phone = "Para EE.UU. usa 10 digitos con +1 opcional.";
    } else if (data.region === "UK" && !/^(\+44\d{10}|0\d{10})$/.test(normalizedPhone)) {
      nextErrors.phone = "Para Reino Unido usa +44 seguido de 10 digitos o local de 11 digitos.";
    }

    if (!data.postalCode.trim()) {
      nextErrors.postalCode = "Ingresa el codigo postal.";
    } else if (data.region === "US" && !/^\d{5}(-\d{4})?$/.test(data.postalCode.trim())) {
      nextErrors.postalCode = "Formato EE.UU. esperado: 12345 o 12345-6789.";
    } else if (data.region === "UK" && !/^[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}$/i.test(data.postalCode.trim())) {
      nextErrors.postalCode = "Formato Reino Unido esperado: SW1A 1AA.";
    }

    if (!data.preferredClinic) {
      nextErrors.preferredClinic = "Selecciona una clinica preferida.";
    } else if (data.region && !clinicsByRegion[data.region].includes(data.preferredClinic)) {
      nextErrors.preferredClinic = "La clinica no corresponde a la region elegida.";
    }

    if (!data.dataConsent) {
      nextErrors.dataConsent = "Debes aceptar el tratamiento de datos para continuar.";
    }

    return nextErrors;
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextErrors = validate(formData);
    setErrors(nextErrors as Record<keyof FormDataState, string>);

    if (Object.keys(nextErrors).length > 0) {
      setStatus({ kind: "error", message: `Detectamos ${Object.keys(nextErrors).length} errores. Revisa los campos marcados.` });
      return;
    }

    setStatus({ kind: "ok", message: "Registro validado correctamente. Los datos estan listos para envio seguro." });
    setFormData(initialState);
  };

  return (
    <main className="container application-page" style={{ padding: "30px 0 44px" }}>
      <a href="/" className="back-link">← Volver al inicio</a>
      <section className="application-card">
        <h1 style={{ marginTop: 0 }}>Registro de paciente digital</h1>
        <p style={{ color: "var(--muted)", lineHeight: 1.7 }}>
          Completa los datos esenciales para habilitar reservas online y verificacion clinica. La region determina el
          marco regulatorio aplicable: HIPAA (EE.UU.) o UK GDPR (Reino Unido).
        </p>

        {status && (
          <div
            role="status"
            className={`status-banner ${status.kind === "ok" ? "status-ok" : "status-error"}`}
            style={{
              border: `1px solid ${status.kind === "ok" ? "#34d399" : "#f87171"}`,
              padding: 12,
              borderRadius: 10,
              marginBottom: 16,
              background: status.kind === "ok" ? "rgba(52,211,153,.12)" : "rgba(248,113,113,.12)",
            }}
          >
            {status.message}
          </div>
        )}

        <form onSubmit={onSubmit} className="application-form" noValidate>
          <Field label="Nombre" error={errors.firstName} required>
            <input value={formData.firstName} onChange={(e) => setFormData((p) => ({ ...p, firstName: e.target.value }))} />
          </Field>
          <Field label="Apellidos" error={errors.lastName} required>
            <input value={formData.lastName} onChange={(e) => setFormData((p) => ({ ...p, lastName: e.target.value }))} />
          </Field>
          <Field label="Fecha de nacimiento" error={errors.dateOfBirth} required>
            <input type="date" value={formData.dateOfBirth} onChange={(e) => setFormData((p) => ({ ...p, dateOfBirth: e.target.value }))} />
          </Field>
          <Field label="Documento de identidad" error={errors.nationalId} required>
            <input value={formData.nationalId} onChange={(e) => setFormData((p) => ({ ...p, nationalId: e.target.value }))} />
          </Field>
          <Field label="Correo electronico" error={errors.email} required>
            <input type="email" value={formData.email} onChange={(e) => setFormData((p) => ({ ...p, email: e.target.value }))} />
          </Field>
          <Field label="Telefono" error={errors.phone} required>
            <input value={formData.phone} onChange={(e) => setFormData((p) => ({ ...p, phone: e.target.value }))} />
          </Field>
          <Field label="Region" error={errors.region} required>
            <select value={formData.region} onChange={(e) => setFormData((p) => ({ ...p, region: e.target.value as Region, preferredClinic: "" }))}>
              <option value="">Selecciona una region</option>
              <option value="US">Estados Unidos (HIPAA)</option>
              <option value="UK">Reino Unido (UK GDPR)</option>
            </select>
          </Field>
          <Field label="Codigo postal" error={errors.postalCode} required>
            <input value={formData.postalCode} onChange={(e) => setFormData((p) => ({ ...p, postalCode: e.target.value }))} />
          </Field>
          <Field label="Clinica preferida" error={errors.preferredClinic} required>
            <select value={formData.preferredClinic} onChange={(e) => setFormData((p) => ({ ...p, preferredClinic: e.target.value }))}>
              <option value="">Selecciona una clinica</option>
              {clinics.map((clinic) => (
                <option key={clinic} value={clinic}>
                  {clinic}
                </option>
              ))}
            </select>
          </Field>

          <label className="consent-row">
            <input
              className="consent-check"
              type="checkbox"
              checked={formData.dataConsent}
              onChange={(e) => setFormData((p) => ({ ...p, dataConsent: e.target.checked }))}
            />
            <span>Acepto el tratamiento de mis datos para prestacion asistencial y coordinacion operativa. *</span>
          </label>
          {errors.dataConsent && <small style={{ color: "#fca5a5" }}>{errors.dataConsent}</small>}

          <label className="consent-row">
            <input
              className="consent-check"
              type="checkbox"
              checked={formData.reminderConsent}
              onChange={(e) => setFormData((p) => ({ ...p, reminderConsent: e.target.checked }))}
            />
            <span>Acepto recibir recordatorios de cita por correo y telefono.</span>
          </label>

          <div className="form-actions">
            <button
              className="btn btn-secondary"
              type="button"
              onClick={() => {
                setFormData(initialState);
                setErrors({} as Record<keyof FormDataState, string>);
                setStatus(null);
              }}
            >
              Limpiar formulario
            </button>
            <button className="btn btn-primary" type="submit">Enviar registro</button>
          </div>
        </form>
      </section>

      <style jsx>{`
        .application-page {
          display: grid;
          gap: 12px;
        }

        .back-link {
          display: inline-flex;
          align-items: center;
          width: fit-content;
          color: #a5f3fc;
          border-bottom: 1px solid transparent;
          transition: border-color 0.2s ease, color 0.2s ease;
        }

        .back-link:hover {
          color: #67e8f9;
          border-color: rgba(103, 232, 249, 0.6);
        }

        .application-card {
          border: 1px solid var(--line);
          border-radius: 16px;
          padding: 24px;
          background: linear-gradient(180deg, rgba(15, 23, 42, 0.82), rgba(2, 6, 23, 0.88));
          box-shadow: 0 14px 36px rgba(2, 6, 23, 0.35);
          backdrop-filter: blur(3px);
        }

        .status-banner {
          transition: transform 0.18s ease;
        }

        .status-ok {
          box-shadow: 0 0 0 1px rgba(52, 211, 153, 0.16) inset;
        }

        .status-error {
          box-shadow: 0 0 0 1px rgba(248, 113, 113, 0.16) inset;
        }

        .application-form {
          display: grid;
          gap: 14px;
        }

        .consent-row {
          display: flex;
          gap: 10px;
          align-items: flex-start;
          color: #cbd5e1;
          line-height: 1.5;
        }

        .consent-check {
          margin-top: 3px;
          accent-color: var(--accent);
          width: 16px;
          height: 16px;
          flex: 0 0 auto;
        }

        .form-actions {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
        }

        .btn {
          border-radius: 10px;
          border: 1px solid var(--line);
          font: inherit;
          font-weight: 600;
          padding: 10px 14px;
          cursor: pointer;
          transition: transform 0.12s ease, border-color 0.2s ease, background-color 0.2s ease;
        }

        .btn:hover {
          transform: translateY(-1px);
        }

        .btn:active {
          transform: translateY(0);
        }

        .btn:focus-visible {
          outline: 2px solid rgba(34, 211, 238, 0.4);
          outline-offset: 2px;
        }

        .btn-secondary {
          background: rgba(15, 23, 42, 0.8);
          color: #cbd5e1;
        }

        .btn-secondary:hover {
          border-color: #334155;
          background: rgba(30, 41, 59, 0.82);
        }

        .btn-primary {
          border-color: rgba(52, 211, 153, 0.55);
          background: linear-gradient(180deg, rgba(52, 211, 153, 0.24), rgba(16, 185, 129, 0.18));
          color: #dcfce7;
        }

        .btn-primary:hover {
          border-color: rgba(52, 211, 153, 0.9);
          background: linear-gradient(180deg, rgba(52, 211, 153, 0.3), rgba(16, 185, 129, 0.22));
        }

        @media (max-width: 680px) {
          .application-card {
            padding: 18px;
          }

          .btn {
            flex: 1 1 100%;
          }
        }
      `}</style>
    </main>
  );
}

function Field({
  label,
  required,
  error,
  children,
}: {
  label: string;
  required?: boolean;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label">
        {label}
        {required ? " *" : ""}
      </span>
      <span className={`field-control ${error ? "field-control-error" : ""}`}>
        {children}
      </span>
      {error && <small className="field-error">{error}</small>}
      <style jsx>{`
        .field {
          display: grid;
          gap: 7px;
        }

        .field-label {
          color: #dbeafe;
          font-weight: 600;
          font-size: 0.96rem;
          letter-spacing: 0.01em;
        }

        .field-control {
          border: 1px solid var(--line);
          border-radius: 10px;
          padding: 11px 12px;
          display: block;
          background: linear-gradient(180deg, rgba(15, 23, 42, 0.74), rgba(2, 6, 23, 0.9));
          box-shadow: 0 0 0 1px rgba(2, 6, 23, 0.28) inset;
          transition: border-color 0.2s ease, box-shadow 0.2s ease, background-color 0.2s ease;
        }

        .field-control:focus-within {
          border-color: rgba(103, 232, 249, 0.85);
          box-shadow: 0 0 0 3px rgba(34, 211, 238, 0.2);
          background: linear-gradient(180deg, rgba(15, 23, 42, 0.86), rgba(2, 6, 23, 0.96));
        }

        .field-control-error {
          border-color: #f87171;
          box-shadow: 0 0 0 1px rgba(248, 113, 113, 0.22) inset;
        }

        .field-error {
          color: #fca5a5;
        }

        .field-control :global(input),
        .field-control :global(select) {
          width: 100%;
          border: 0;
          outline: none;
          background: transparent;
          background-color: transparent;
          color: var(--text);
          font: inherit;
          line-height: 1.45;
          -webkit-appearance: none;
          appearance: none;
        }

        .field-control :global(input:-webkit-autofill),
        .field-control :global(input:-webkit-autofill:hover),
        .field-control :global(input:-webkit-autofill:focus),
        .field-control :global(input:-webkit-autofill:active) {
          -webkit-text-fill-color: var(--text);
          box-shadow: 0 0 0 1000px #0b1220 inset;
          transition: background-color 600000s 0s, color 600000s 0s;
        }

        .field-control :global(input::placeholder) {
          color: #64748b;
        }

        .field-control :global(select) {
          cursor: pointer;
          appearance: none;
        }

        .field-control :global(select option) {
          background: #0f172a;
          color: var(--text);
        }

        .field-control :global(input[type="date"]) {
          color-scheme: dark;
        }

        .field-control :global(input[type="date"]::-webkit-datetime-edit),
        .field-control :global(input[type="date"]::-webkit-datetime-edit-fields-wrapper),
        .field-control :global(input[type="date"]::-webkit-datetime-edit-text),
        .field-control :global(input[type="date"]::-webkit-datetime-edit-month-field),
        .field-control :global(input[type="date"]::-webkit-datetime-edit-day-field),
        .field-control :global(input[type="date"]::-webkit-datetime-edit-year-field) {
          color: var(--text);
          background: transparent;
        }

        .field-control :global(input[type="date"]::-webkit-calendar-picker-indicator) {
          opacity: 0.85;
          cursor: pointer;
          background: transparent;
          filter: invert(87%) sepia(8%) saturate(500%) hue-rotate(161deg) brightness(102%) contrast(95%);
        }
      `}</style>
    </label>
  );
}
