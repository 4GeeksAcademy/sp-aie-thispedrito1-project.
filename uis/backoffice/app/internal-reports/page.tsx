"use client";

import { useCallback, useState } from "react";

import { AsyncSection } from "../../components/AsyncSection";
import { useAccessProfile } from "../../hooks/useAccessProfile";
import { useAsyncData } from "../../hooks/useAsyncData";
import { internalReportsApi } from "../../services/accessApi";
import { ApiFieldError } from "../../services/http";
import { can, departmentLabel, scopeDescription, type InternalReport } from "../../types/access";

// Informes internos (BRIEFING de Roles y Permisos): la colección con alcance
// departamental. La API ya devuelve solo los del departamento de quien mira
// (Admin: todos); aquí solo se ocultan las acciones que el rol no permite.

function errorText(caught: unknown, fallback: string): string {
  if (caught instanceof ApiFieldError) return caught.fieldErrors.map((item) => item.message).join(" ");
  return caught instanceof Error && caught.message ? caught.message : fallback;
}

export default function InternalReportsPage() {
  const profile = useAccessProfile();
  const fetchReports = useCallback(() => internalReportsApi.list(), []);
  const { data, isLoading, error, reload } = useAsyncData<InternalReport[]>(
    fetchReports,
    "No se pudieron cargar los informes internos. Verifica que la API esté activa e inténtalo de nuevo.",
  );

  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [editing, setEditing] = useState<{ id: string; body: string } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const reports = data ?? [];

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setNotice(null);
    try {
      await internalReportsApi.create({ title: title.trim(), body: body.trim() });
      setTitle("");
      setBody("");
      setNotice({ kind: "ok", text: "Informe creado en tu departamento." });
      reload();
    } catch (caught) {
      setNotice({ kind: "error", text: errorText(caught, "No se pudo crear el informe.") });
    } finally {
      setSaving(false);
    }
  };

  const runAction = async (id: string, action: () => Promise<unknown>, done: string) => {
    setBusyId(id);
    setNotice(null);
    try {
      await action();
      setNotice({ kind: "ok", text: done });
      setEditing(null);
      reload();
    } catch (caught) {
      setNotice({ kind: "error", text: errorText(caught, "No se pudo completar la acción.") });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <main className="shell" style={{ padding: "24px 0 42px", display: "grid", gap: 16 }}>
      <section className="panel">
        <h1 style={{ marginTop: 0 }}>Informes internos</h1>
        <p style={{ color: "var(--muted)", margin: 0 }}>
          Notas operativas por departamento. {scopeDescription(profile)}
        </p>
      </section>

      {can(profile, "records:create") && profile?.department && (
        <section className="panel">
          <h2 style={{ marginTop: 0 }}>Nuevo informe</h2>
          <p role="note" style={{ color: "var(--warning)", fontSize: 14 }}>
            Solo notas operativas: nunca nombres, fechas de nacimiento ni otros datos de pacientes.
          </p>
          <form onSubmit={create} style={{ display: "grid", gap: 12 }}>
            <label>
              Título
              <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} required />
            </label>
            <label>
              Contenido
              <textarea value={body} onChange={(event) => setBody(event.target.value)} maxLength={5000} rows={4} required />
            </label>
            <button type="submit" className="primary" disabled={saving}>
              {saving ? "Guardando…" : `Crear en ${departmentLabel(profile.department)}`}
            </button>
          </form>
        </section>
      )}

      {notice && (
        <p role="status" className={notice.kind === "ok" ? "success-text" : "error-text"}>
          {notice.text}
        </p>
      )}

      <AsyncSection
        isLoading={isLoading}
        error={error}
        onRetry={reload}
        loadingLabel="Cargando informes…"
        isEmpty={reports.length === 0}
        emptyLabel="Tu departamento todavía no tiene informes internos."
      >
        <div style={{ display: "grid", gap: 12 }}>
          {reports.map((report) => (
            <article key={report.id} className="panel" style={{ display: "grid", gap: 8 }}>
              <header style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <strong style={{ flex: "1 1 240px" }}>{report.title}</strong>
                <span className="status-badge">{departmentLabel(report.department)}</span>
                {report.approved_at && (
                  <span className="status-badge" style={{ color: "var(--ok)" }}>
                    Aprobado
                  </span>
                )}
              </header>

              {editing?.id === report.id ? (
                <textarea
                  aria-label="Editar contenido"
                  value={editing.body}
                  onChange={(event) => setEditing({ id: report.id, body: event.target.value })}
                  rows={4}
                />
              ) : (
                <p style={{ margin: 0 }}>{report.body}</p>
              )}

              <small style={{ color: "var(--muted)" }}>
                Creado el {new Date(report.created_at).toLocaleDateString("es-ES")}
              </small>

              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {can(profile, "records:update") &&
                  (editing?.id === report.id ? (
                    <>
                      <button
                        type="button"
                        className="primary"
                        disabled={busyId === report.id}
                        onClick={() =>
                          void runAction(report.id, () => internalReportsApi.update(report.id, { body: editing.body }), "Informe actualizado.")
                        }
                      >
                        Guardar
                      </button>
                      <button type="button" onClick={() => setEditing(null)}>
                        Cancelar
                      </button>
                    </>
                  ) : (
                    <button type="button" onClick={() => setEditing({ id: report.id, body: report.body })}>
                      Editar
                    </button>
                  ))}
                {can(profile, "records:approve") && !report.approved_at && (
                  <button
                    type="button"
                    disabled={busyId === report.id}
                    onClick={() => void runAction(report.id, () => internalReportsApi.approve(report.id), "Informe aprobado.")}
                  >
                    Aprobar
                  </button>
                )}
                {can(profile, "records:delete") && (
                  <button
                    type="button"
                    disabled={busyId === report.id}
                    onClick={() => void runAction(report.id, () => internalReportsApi.remove(report.id), "Informe eliminado.")}
                  >
                    Eliminar
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      </AsyncSection>
    </main>
  );
}
