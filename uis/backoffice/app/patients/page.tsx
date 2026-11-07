"use client";

import { useCallback, useState } from "react";

import { AsyncSection } from "../../components/AsyncSection";
import { useAccessProfile } from "../../hooks/useAccessProfile";
import { useAsyncData } from "../../hooks/useAsyncData";
import { patientsApi } from "../../services/auditApi";
import { ApiFieldError } from "../../services/http";
import { can, departmentLabel } from "../../types/access";
import {
  CLAIM_STATUS_LABELS,
  REASON_CODE_LABELS,
  formatMoney,
  parseAmountToCents,
  type PatientCatalog,
  type PatientDetail,
  type PatientListItem,
} from "../../types/patients";

// Pacientes SINTÉTICOS (Registro de Auditoría). Cada apertura de ficha pide un
// motivo y queda registrada; la API decide qué secciones ve cada persona
// (clínica: solo el equipo de cuidado; facturación: también Revenue Cycle).

function errorText(caught: unknown, fallback: string): string {
  if (caught instanceof ApiFieldError) return caught.fieldErrors.map((item) => item.message).join(" ");
  return caught instanceof Error && caught.message ? caught.message : fallback;
}

export default function PatientsPage() {
  const profile = useAccessProfile();
  const fetchAll = useCallback(() => Promise.all([patientsApi.list(), patientsApi.catalog()]), []);
  const { data, isLoading, error, reload } = useAsyncData<[PatientListItem[], PatientCatalog]>(
    fetchAll,
    "No se pudieron cargar los pacientes. Verifica que la API esté activa e inténtalo de nuevo.",
  );
  const [patients, catalog] = data ?? [[], null];

  const [purpose, setPurpose] = useState("");
  const [detail, setDetail] = useState<PatientDetail | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const run = async (key: string, action: () => Promise<PatientDetail | void>, done: string) => {
    setBusy(key);
    setNotice(null);
    try {
      const result = await action();
      if (result) setDetail(result);
      setNotice({ kind: "ok", text: result ? `${done} (entrada de auditoría #${result.audit_sequence}).` : done });
    } catch (caught) {
      setNotice({ kind: "error", text: errorText(caught, "No se pudo completar la acción.") });
    } finally {
      setBusy(null);
    }
  };

  const open = (ref: string) => {
    if (!purpose) {
      setNotice({ kind: "error", text: "Elige primero el motivo del acceso: queda en el registro de auditoría." });
      return;
    }
    void run(ref, () => patientsApi.open(ref, purpose), `Ficha ${ref} abierta. Tu acceso quedó registrado`);
  };

  const exportCsv = () => {
    if (!purpose) {
      setNotice({ kind: "error", text: "Elige primero el motivo de la exportación." });
      return;
    }
    void run(
      "export",
      async () => {
        const csv = await patientsApi.exportCsv(purpose);
        const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
        const link = document.createElement("a");
        link.href = url;
        link.download = "pacientes-sinteticos.csv";
        link.click();
        URL.revokeObjectURL(url);
      },
      "Exportación descargada y registrada en la auditoría.",
    );
  };

  return (
    <main className="shell" style={{ padding: "24px 0 42px", display: "grid", gap: 16 }}>
      <section className="panel" style={{ display: "grid", gap: 8 }}>
        <h1 style={{ margin: 0 }}>Pacientes (datos sintéticos)</h1>
        <p style={{ margin: 0, color: "var(--muted)" }}>
          Todos los pacientes son ficticios. Abrir una ficha, cambiarla o exportarla queda en el registro de auditoría
          con tu cuenta, la hora, tu IP y el motivo que elijas.
        </p>
        <label style={{ maxWidth: 360 }}>
          Motivo del acceso
          <select value={purpose} onChange={(event) => setPurpose(event.target.value)}>
            <option value="">Elige un motivo…</option>
            {catalog?.purposes.map((item) => (
              <option key={item.code} value={item.code}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        {can(profile, "records:approve") && (
          <div>
            <button type="button" onClick={exportCsv} disabled={busy === "export"}>
              {busy === "export" ? "Exportando…" : "Exportar mis pacientes (CSV)"}
            </button>
          </div>
        )}
      </section>

      {notice && (
        <p role="status" className={notice.kind === "ok" ? "success-text" : "error-text"}>
          {notice.text}
        </p>
      )}

      {detail && catalog && (
        <PatientPanel detail={detail} catalog={catalog} purpose={purpose} busy={busy} run={run} canEdit={can(profile, "records:update")} canApprove={can(profile, "records:approve")} />
      )}

      <AsyncSection
        isLoading={isLoading}
        error={error}
        onRetry={reload}
        loadingLabel="Cargando pacientes…"
        isEmpty={patients.length === 0}
        emptyLabel="No hay pacientes en tu alcance (o aún no se ejecutó scripts/seed_audit_log.py)."
      >
        <section className="panel" style={{ overflowX: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Referencia</th>
                <th>Departamento</th>
                <th>País</th>
                <th>Secciones visibles</th>
                <th>Reclamaciones pendientes</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {patients.map((patient) => (
                <tr key={patient.patient_ref}>
                  <td className="mono">
                    {patient.patient_ref}
                    {patient.assigned_to_me && <span className="status-badge"> asignado a mí</span>}
                  </td>
                  <td>{departmentLabel(patient.department)}</td>
                  <td>{patient.country}</td>
                  <td>{patient.sections.map((s) => (s === "clinical" ? "Clínica" : "Facturación")).join(" + ")}</td>
                  <td>{patient.pending_claims}</td>
                  <td>
                    <button type="button" onClick={() => open(patient.patient_ref)} disabled={busy === patient.patient_ref}>
                      Abrir ficha
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </AsyncSection>
    </main>
  );
}

interface PanelProps {
  detail: PatientDetail;
  catalog: PatientCatalog;
  purpose: string;
  busy: string | null;
  canEdit: boolean;
  canApprove: boolean;
  run: (key: string, action: () => Promise<PatientDetail | void>, done: string) => Promise<void>;
}

function PatientPanel({ detail, catalog, purpose, busy, canEdit, canApprove, run }: PanelProps) {
  const ref = detail.patient_ref;
  const [notes, setNotes] = useState(detail.clinical?.clinical_notes ?? "");
  const [balance, setBalance] = useState("");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const needPurpose = !purpose;

  return (
    <section className="panel" style={{ display: "grid", gap: 12 }}>
      <header style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <h2 style={{ margin: 0, flex: "1 1 240px" }}>
          Ficha <span className="mono">{ref}</span>
        </h2>
        <span className="status-badge">{departmentLabel(detail.department)}</span>
        <span className="status-badge">Lectura registrada: #{detail.audit_sequence}</span>
      </header>

      {detail.clinical ? (
        <div style={{ display: "grid", gap: 6 }}>
          <h3 style={{ margin: 0 }}>Datos clínicos</h3>
          <p style={{ margin: 0 }}>
            <strong>{detail.clinical.full_name}</strong> · nacimiento {detail.clinical.date_of_birth}
          </p>
          <p style={{ margin: 0 }}>Diagnóstico: {detail.clinical.diagnosis}</p>
          {canEdit ? (
            <label>
              Notas clínicas
              <textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={3} maxLength={4000} />
            </label>
          ) : (
            <p style={{ margin: 0 }}>Notas: {detail.clinical.clinical_notes}</p>
          )}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {canEdit && (
              <button
                type="button"
                className="primary"
                disabled={busy === "clinical" || needPurpose}
                onClick={() => void run("clinical", () => patientsApi.updateClinical(ref, { purpose, clinical_notes: notes }), "Notas clínicas guardadas")}
              >
                Guardar notas
              </button>
            )}
            <button
              type="button"
              disabled={busy === "draft" || needPurpose}
              onClick={() => void run("draft", () => patientsApi.draftDocumentation(ref, purpose), "El asistente de documentación preparó un borrador")}
            >
              Pedir borrador al asistente de documentación
            </button>
          </div>
          {detail.clinical.documentation_draft && (
            <pre style={{ whiteSpace: "pre-wrap", margin: 0, padding: 8, border: "1px solid var(--line)" }}>
              {detail.clinical.documentation_draft}
            </pre>
          )}
        </div>
      ) : (
        <p style={{ margin: 0, color: "var(--muted)" }}>
          No ves el contenido clínico: solo lo ve el equipo de cuidado del paciente.
        </p>
      )}

      {detail.billing && (
        <div style={{ display: "grid", gap: 6 }}>
          <h3 style={{ margin: 0 }}>Facturación</h3>
          <p style={{ margin: 0 }}>
            Seguro: {detail.billing.insurance_plan} · saldo pendiente{" "}
            {formatMoney(detail.billing.outstanding_balance_cents, detail.billing.currency)}
          </p>
          {canEdit && !detail.clinical && (
            <div style={{ display: "flex", gap: 8, alignItems: "end", flexWrap: "wrap" }}>
              <label>
                Nuevo saldo ({detail.billing.currency})
                <input value={balance} onChange={(event) => setBalance(event.target.value)} inputMode="decimal" placeholder="0,00" />
              </label>
              <button
                type="button"
                disabled={busy === "billing" || needPurpose}
                onClick={() => {
                  const cents = parseAmountToCents(balance);
                  if (cents === null) return void run("billing", async () => { throw new Error("Escribe un importe válido, por ejemplo 120,50."); }, "");
                  void run("billing", () => patientsApi.updateBilling(ref, { purpose, outstanding_balance_cents: cents }), "Saldo actualizado");
                }}
              >
                Actualizar saldo
              </button>
            </div>
          )}
          {detail.billing.claims.length > 0 && (
            <ul style={{ margin: 0, display: "grid", gap: 6 }}>
              {detail.billing.claims.map((claim) => (
                <li key={claim.claim_ref}>
                  <span className="mono">{claim.claim_ref}</span> · {formatMoney(claim.amount_cents, claim.currency)} ·{" "}
                  {CLAIM_STATUS_LABELS[claim.status]}
                  {claim.reason_code && ` (${REASON_CODE_LABELS[claim.reason_code] ?? claim.reason_code})`}
                  {canApprove && !detail.clinical && claim.status === "pending" && (
                    <span style={{ display: "inline-flex", gap: 6, marginLeft: 8 }}>
                      <select
                        aria-label={`Motivo para ${claim.claim_ref}`}
                        value={reasons[claim.claim_ref] ?? ""}
                        onChange={(event) => setReasons({ ...reasons, [claim.claim_ref]: event.target.value })}
                      >
                        <option value="">Motivo…</option>
                        {(["approve", "deny"] as const).map((decision) =>
                          catalog.claim_reason_codes[decision].map((code) => (
                            <option key={code} value={`${decision}:${code}`}>
                              {decision === "approve" ? "Aprobar" : "Denegar"}: {REASON_CODE_LABELS[code] ?? code}
                            </option>
                          )),
                        )}
                      </select>
                      <button
                        type="button"
                        disabled={!reasons[claim.claim_ref] || busy === claim.claim_ref}
                        onClick={() => {
                          const [decision, reason_code] = reasons[claim.claim_ref].split(":") as ["approve" | "deny", string];
                          void run(claim.claim_ref, () => patientsApi.decideClaim(ref, claim.claim_ref, { decision, reason_code }), "Reclamación decidida");
                        }}
                      >
                        Decidir
                      </button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {needPurpose && <small style={{ color: "var(--warning)" }}>Elige un motivo arriba para poder actuar sobre la ficha.</small>}
    </section>
  );
}
