"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { authApi } from "../../services/authApi";
import { ApiFieldError } from "../../services/http";
import { setAuthToken } from "../../services/session";

type RegisterField = "email" | "password" | "confirmPassword" | "name" | "phone" | "address";
type FieldErrors = Partial<Record<RegisterField, string>>;

const REGISTER_FIELDS: RegisterField[] = ["email", "password", "name", "phone", "address"];

function mapApiFieldErrors(error: ApiFieldError): FieldErrors {
  const nextErrors: FieldErrors = {};
  for (const { field, message } of error.fieldErrors) {
    if ((REGISTER_FIELDS as string[]).includes(field)) {
      nextErrors[field as RegisterField] = message;
    }
  }
  return nextErrors;
}

export default function RegisterPage() {
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [address, setAddress] = useState("");

  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const validate = (): FieldErrors => {
    const errors: FieldErrors = {};

    if (!email.trim()) errors.email = "El email es obligatorio.";
    if (!password) errors.password = "La contrasena es obligatoria.";
    if (password.length > 0 && password.length < 8) errors.password = "La contrasena debe tener al menos 8 caracteres.";
    if (confirmPassword !== password) errors.confirmPassword = "Las contrasenas no coinciden.";

    return errors;
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);

    const validationErrors = validate();
    setFieldErrors(validationErrors);
    if (Object.keys(validationErrors).length > 0) {
      return;
    }

    setSubmitting(true);

    try {
      await authApi.register({
        email: email.trim(),
        password,
        name: name.trim() || undefined,
        phone: phone.trim() || undefined,
        address: address.trim() || undefined,
      });

      const token = await authApi.login({
        email: email.trim(),
        password,
      });

      setAuthToken(token.access_token);
      router.replace("/");
    } catch (requestError: unknown) {
      if (requestError instanceof ApiFieldError) {
        const mappedErrors = mapApiFieldErrors(requestError);
        if (Object.keys(mappedErrors).length > 0) {
          setFieldErrors(mappedErrors);
        } else {
          setFormError("Algunos datos no son válidos. Revisa el formulario e inténtalo de nuevo.");
        }
      } else {
        const message = requestError instanceof Error ? requestError.message : "No se pudo completar el registro.";
        if (message === "Email already registered") {
          setFieldErrors({ email: "Este correo ya esta registrado." });
        } else {
          setFormError(message);
        }
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="shell" style={{ padding: "24px 0 42px" }}>
      <section className="panel" style={{ maxWidth: 640, margin: "0 auto" }}>
        <h1 style={{ marginTop: 0 }}>Crear cuenta</h1>
        <p style={{ color: "var(--muted)" }}>Registra tu usuario para acceder al backoffice.</p>

        {formError && <p className="error-text">{formError}</p>}

        <form onSubmit={handleSubmit} style={{ display: "grid", gap: 12 }}>
          <Field label="Email" type="email" value={email} onChange={setEmail} error={fieldErrors.email} required />

          <div className="form-grid">
            <Field label="Contrasena" type="password" value={password} onChange={setPassword} error={fieldErrors.password} required />
            <Field label="Confirmar contrasena" type="password" value={confirmPassword} onChange={setConfirmPassword} error={fieldErrors.confirmPassword} required />
          </div>

          <Field label="Nombre" value={name} onChange={setName} error={fieldErrors.name} />

          <div className="form-grid">
            <Field label="Telefono" value={phone} onChange={setPhone} error={fieldErrors.phone} />
            <Field label="Direccion" value={address} onChange={setAddress} error={fieldErrors.address} />
          </div>

          <button type="submit" disabled={submitting}>
            {submitting ? "Creando cuenta..." : "Registrarme"}
          </button>
        </form>

        <p style={{ marginBottom: 0 }}>
          Ya tienes cuenta?{" "}
          <Link href="/login" style={{ color: "var(--brand)", fontWeight: 700 }}>
            Inicia sesion
          </Link>
        </p>
      </section>
    </main>
  );
}

function Field({
  label,
  value,
  onChange,
  error,
  type = "text",
  required = false,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  error?: string;
  type?: React.HTMLInputTypeAttribute;
  required?: boolean;
}) {
  return (
    <label>
      {label}
      <input
        type={type}
        required={required}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        style={error ? { borderColor: "var(--critical)" } : undefined}
      />
      {error && <span className="error-text">{error}</span>}
    </label>
  );
}
