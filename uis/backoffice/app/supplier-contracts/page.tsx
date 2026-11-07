"use client";

import "./supplier-contracts.css";

import { useCallback, useMemo, useState } from "react";

import { AsyncSection } from "../../components/AsyncSection";
import { useAsyncData } from "../../hooks/useAsyncData";
import {
  askSupervisor,
  clearSupplier,
  createBrief,
  createDraft,
  decideDraft,
  getMyRole,
  listAlerts,
  listDrafts,
  listSuppliers,
  runAlertSweep,
  sendDraft,
} from "../../services/supplierContractsApi";
import {
  AGENT_LABELS,
  ALERT_LABELS,
  EXAMPLE_QUESTIONS,
  PURPOSE_LABELS,
  STATUS_LABELS,
  approvalProgress,
  canDecide,
  canSend,
  sortAlerts,
  type AskResponse,
  type BriefResponse,
  type CommunicationDraft,
  type DraftPurpose,
  type OrgRole,
} from "../../types/supplierContracts";

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

function Refs({ refs }: { refs: string[] }) {
  if (refs.length === 0) return null;
  return (
    <details>
      <summary className="sc-muted">Trazabilidad ({refs.length})</summary>
      <ol className="sc-refs">
        {refs.map((ref) => (
          <li key={ref}>{ref}</li>
        ))}
      </ol>
    </details>
  );
}

/**
 * Proveedores y contratos (Dr. Marcus Reid). Un supervisor coordina a un
 * especialista de precios y a uno de contratos; las cifras las calcula el
 * código y cada una enlaza a su fila del CSV o a su cláusula y página. El
 * sistema prepara borradores, pero nada sale a un proveedor sin aprobación.
 */
export default function SupplierContractsPage() {
  const role = useAsyncData(useCallback(() => getMyRole(), []), "No se pudo comprobar tu rol.");
  const orgRole: OrgRole | null = role.data?.org_role ?? null;
  const suppliers = useAsyncData(useCallback(() => listSuppliers(), []), "No se pudieron cargar los proveedores.");

  return (
    <main className="sc-grid">
      <header>
        <h1 style={{ marginBottom: 4 }}>Proveedores y contratos</h1>
        <p className="sc-muted" style={{ marginTop: 0 }}>
          Un supervisor coordina a un especialista de precios y a uno de contratos. Las cifras las calcula el código y cada
          una enlaza a su origen. Ninguna comunicación sale a un proveedor sin aprobación.
          {role.data?.display_name ? ` Has entrado como ${role.data.display_name}.` : ""}
        </p>
      </header>

      <AlertsPanel orgRole={orgRole} />

      <div className="sc-two-cols">
        <AskPanel />
        <BriefPanel orgRole={orgRole} suppliers={suppliers.data ?? []} />
      </div>

      <DraftsPanel orgRole={orgRole} suppliers={suppliers.data ?? []} />

      <section className="panel">
        <h2 style={{ marginTop: 0 }}>Proveedores</h2>
        <AsyncSection isLoading={suppliers.isLoading} error={suppliers.error} onRetry={suppliers.reload} loadingLabel="Cargando proveedores…"
          isEmpty={(suppliers.data ?? []).length === 0} emptyLabel="Sin proveedores cargados: ejecuta scripts/seed_supplier_contracts.py.">
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Proveedor</th>
                  <th>Categoría · país</th>
                  <th>Último precio</th>
                  <th>Contrato</th>
                  <th>Datos de pacientes</th>
                </tr>
              </thead>
              <tbody>
                {(suppliers.data ?? []).map((s) => (
                  <tr key={s.supplier_id}>
                    <td>
                      <strong>{s.name}</strong>
                      <div className="sc-muted">{s.supplier_id} · {s.source}</div>
                    </td>
                    <td>{s.category_label} · {s.country}</td>
                    <td>
                      {s.latest_price ? `${s.latest_price} ${s.currency}/${s.unit}` : "—"}
                      <div className="sc-muted">{s.latest_month} · {s.monthly_volume === 0 ? "solo cotización" : `${s.monthly_volume} ${s.unit}/mes`}</div>
                    </td>
                    <td>{s.contract_id ?? <span className="sc-muted">Sin contrato</span>}</td>
                    <td>{s.processes_patient_data ? <span className="sc-pill sc-pill-brand">Sí</span> : "No"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </AsyncSection>
      </section>
    </main>
  );
}

function AlertsPanel({ orgRole }: { orgRole: OrgRole | null }) {
  const [mine, setMine] = useState(false);
  const fetchAlerts = useCallback(() => listAlerts(mine), [mine]);
  const alerts = useAsyncData(fetchAlerts, "No se pudieron cargar las alertas.");
  const [sweepState, setSweepState] = useState<string | null>(null);
  const sorted = useMemo(() => sortAlerts(alerts.data ?? []), [alerts.data]);
  const canSweep = orgRole === "clinical_ops_director" || orgRole === "compliance_officer";

  async function onSweep() {
    setSweepState("Revisando precios, contratos y acuerdos…");
    try {
      const result = await runAlertSweep();
      setSweepState(result.created === 0 ? "Sin alertas nuevas." : `${result.created} alertas nuevas.`);
      alerts.reload();
    } catch (err) {
      setSweepState(errorText(err, "No se pudo ejecutar la revisión."));
    }
  }

  return (
    <section className="panel">
      <div className="sc-row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Alertas</h2>
        <div className="sc-row">
          {orgRole && (
            <label className="sc-row sc-muted">
              <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Solo las mías
            </label>
          )}
          {canSweep && <button type="button" onClick={onSweep}>Revisar ahora</button>}
          <button type="button" onClick={alerts.reload}>Actualizar</button>
        </div>
      </div>
      {sweepState && <p className="sc-muted" role="status">{sweepState}</p>}
      <AsyncSection isLoading={alerts.isLoading} error={alerts.error} onRetry={alerts.reload} loadingLabel="Cargando alertas…"
        isEmpty={sorted.length === 0} emptyLabel="No hay alertas.">
        <ul className="sc-list" style={{ marginTop: 12 }}>
          {sorted.map((alert) => (
            <li key={alert.alert_key} className={`sc-card ${alert.kind === "data_protection_missing" ? "sc-card-danger" : ""}`}>
              <div className="sc-row">
                <span className="sc-pill sc-pill-brand">{ALERT_LABELS[alert.kind] ?? alert.kind}</span>
                <strong>{alert.supplier_id}</strong>
                {alert.due_date && <span className="sc-muted">Fecha: {alert.due_date}</span>}
                <span className="sc-muted">Para: {alert.recipient}</span>
              </div>
              <p className="sc-answer" style={{ marginTop: 6 }}>{alert.summary}</p>
              <Refs refs={alert.refs} />
            </li>
          ))}
        </ul>
      </AsyncSection>
    </section>
  );
}

function AskPanel() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function ask(text: string) {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setAnswer(await askSupervisor(text));
    } catch (err) {
      setError(errorText(err, "No se pudo consultar al supervisor."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <h2 style={{ marginTop: 0 }}>Preguntar al supervisor</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask(question);
        }}
      >
        <textarea className="sc-textarea" value={question} maxLength={1000} onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ej.: ¿ClearPath Labs puede subirnos un 7%?" aria-label="Pregunta" />
        <div className="sc-row" style={{ marginTop: 8 }}>
          <button type="submit" className="primary" disabled={busy || !question.trim()}>{busy ? "Consultando…" : "Preguntar"}</button>
        </div>
      </form>
      <div className="sc-row" style={{ marginTop: 8 }}>
        {EXAMPLE_QUESTIONS.map((q) => (
          <button key={q} type="button" className="sc-chip" disabled={busy} onClick={() => { setQuestion(q); void ask(q); }}>{q}</button>
        ))}
      </div>
      {error && (
        <div className="sc-banner sc-banner-danger" role="alert" style={{ marginTop: 12 }}>
          {error} <button type="button" onClick={() => void ask(question)}>Reintentar</button>
        </div>
      )}
      {answer && (
        <div style={{ marginTop: 12 }}>
          <p className="sc-answer">{answer.answer}</p>
          <p className="sc-muted">
            Plan: {answer.plan_source === "model" ? "elegido por el modelo" : "por reglas (respaldo)"} ·{" "}
            {answer.consultations.map((c) => `${AGENT_LABELS[c.agent_id] ?? c.agent_id}: ${c.intent}`).join(" → ") || "sin consultas"}
          </p>
          <Refs refs={answer.citations} />
        </div>
      )}
    </section>
  );
}

function BriefPanel({ orgRole, suppliers }: { orgRole: OrgRole | null; suppliers: { supplier_id: string; name: string }[] }) {
  const [supplierId, setSupplierId] = useState("HC-S03");
  const [brief, setBrief] = useState<BriefResponse | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function generate() {
    setBusy(true);
    setBlocked(null);
    setBrief(null);
    try {
      setBrief(await createBrief(supplierId));
    } catch (err) {
      setBlocked(errorText(err, "No se pudo preparar el brief."));
    } finally {
      setBusy(false);
    }
  }

  async function clearAndRetry() {
    try {
      await clearSupplier(supplierId);
      await generate();
    } catch (err) {
      setBlocked(errorText(err, "No se pudo registrar la autorización."));
    }
  }

  function download() {
    if (!brief) return;
    const url = URL.createObjectURL(new Blob([brief.markdown], { type: "text/markdown" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `brief-${brief.supplier_id}.md`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section className="panel">
      <h2 style={{ marginTop: 0 }}>Brief de renegociación</h2>
      <div className="sc-row">
        <select value={supplierId} onChange={(e) => setSupplierId(e.target.value)} aria-label="Proveedor">
          {suppliers.map((s) => (
            <option key={s.supplier_id} value={s.supplier_id}>{s.name}</option>
          ))}
        </select>
        <button type="button" className="primary" onClick={generate} disabled={busy}>{busy ? "Preparando…" : "Preparar brief"}</button>
        {brief && <button type="button" onClick={download}>Descargar .md</button>}
      </div>
      {blocked && (
        <div className="sc-banner sc-banner-danger" role="alert" style={{ marginTop: 12 }}>
          <p style={{ margin: 0 }}>{blocked}</p>
          <div className="sc-row" style={{ marginTop: 8 }}>
            <button type="button" onClick={generate}>Reintentar</button>
            {orgRole === "compliance_officer" && (
              <button type="button" onClick={clearAndRetry}>Autorizar a continuar (Cumplimiento)</button>
            )}
          </div>
        </div>
      )}
      {brief && (
        <div style={{ marginTop: 12 }}>
          {brief.message && <p className="sc-muted">{brief.message}</p>}
          <pre className="sc-doc">{brief.markdown}</pre>
        </div>
      )}
    </section>
  );
}

function DraftsPanel({ orgRole, suppliers }: { orgRole: OrgRole | null; suppliers: { supplier_id: string; name: string }[] }) {
  const drafts = useAsyncData(useCallback(() => listDrafts(), []), "No se pudieron cargar los borradores.");
  const [supplierId, setSupplierId] = useState("HC-S03");
  const [purpose, setPurpose] = useState<DraftPurpose>("price_increase_objection");
  const [message, setMessage] = useState<string | null>(null);
  const [comments, setComments] = useState<Record<string, string>>({});

  async function run(action: () => Promise<unknown>, okText: string) {
    setMessage(null);
    try {
      await action();
      setMessage(okText);
      drafts.reload();
    } catch (err) {
      setMessage(errorText(err, "No se pudo completar la acción."));
    }
  }

  return (
    <section className="panel">
      <h2 style={{ marginTop: 0 }}>Comunicaciones a proveedores</h2>
      <p className="sc-muted" style={{ marginTop: 0 }}>
        El supervisor redacta; aprueba el Dr. Marcus Reid, también Claire Whitfield si el proveedor procesa datos de pacientes, y
        también la Dra. Sandra Okonkwo si el contrato supera 500.000 USD al año.
      </p>
      <div className="sc-row">
        <select value={supplierId} onChange={(e) => setSupplierId(e.target.value)} aria-label="Proveedor del borrador">
          {suppliers.map((s) => (
            <option key={s.supplier_id} value={s.supplier_id}>{s.name}</option>
          ))}
        </select>
        <select value={purpose} onChange={(e) => setPurpose(e.target.value as DraftPurpose)} aria-label="Propósito">
          {(Object.keys(PURPOSE_LABELS) as DraftPurpose[]).map((p) => (
            <option key={p} value={p}>{PURPOSE_LABELS[p]}</option>
          ))}
        </select>
        <button type="button" className="primary" onClick={() => run(() => createDraft(supplierId, purpose), "Borrador preparado: pendiente de aprobación.")}>
          Redactar borrador
        </button>
      </div>
      {message && <p className="sc-muted" role="status">{message}</p>}
      <AsyncSection isLoading={drafts.isLoading} error={drafts.error} onRetry={drafts.reload} loadingLabel="Cargando borradores…"
        isEmpty={(drafts.data ?? []).length === 0} emptyLabel="Todavía no hay borradores.">
        <ul className="sc-list" style={{ marginTop: 12 }}>
          {(drafts.data ?? []).map((draft: CommunicationDraft) => (
            <li key={draft.draft_id} className="sc-card">
              <div className="sc-row">
                <span className={`sc-pill ${draft.status === "sent" || draft.status === "approved" ? "sc-pill-ok" : draft.status === "rejected" ? "sc-pill-danger" : "sc-pill-brand"}`}>
                  {STATUS_LABELS[draft.status]}
                </span>
                <strong>{draft.subject}</strong>
                <span className="sc-muted">{draft.supplier_id} · {PURPOSE_LABELS[draft.purpose]} · {approvalProgress(draft)} · redactado por {draft.draft_source === "model" ? "el modelo" : "plantilla"}</span>
              </div>
              <pre className="sc-doc" style={{ maxHeight: 200, marginTop: 8 }}>{draft.body}</pre>
              <div className="sc-row" style={{ marginTop: 6 }}>
                {draft.approvals.map((a) => (
                  <span key={a.role} className={`sc-pill ${a.decision === "approved" ? "sc-pill-ok" : a.decision === "rejected" ? "sc-pill-danger" : ""}`}>
                    {a.approver}: {a.decision === "pending" ? "pendiente" : a.decision === "approved" ? "aprobado" : "rechazado"}
                  </span>
                ))}
              </div>
              {canDecide(draft, orgRole) && (
                <div className="sc-row" style={{ marginTop: 8 }}>
                  <input type="text" placeholder="Comentario (opcional)" maxLength={500} value={comments[draft.draft_id] ?? ""}
                    onChange={(e) => setComments({ ...comments, [draft.draft_id]: e.target.value })} aria-label="Comentario" />
                  <button type="button" className="primary" onClick={() => run(() => decideDraft(draft.draft_id, "approved", comments[draft.draft_id] ?? ""), "Aprobación registrada.")}>Aprobar</button>
                  <button type="button" onClick={() => run(() => decideDraft(draft.draft_id, "rejected", comments[draft.draft_id] ?? ""), "Borrador rechazado.")}>Rechazar</button>
                </div>
              )}
              {canSend(draft, orgRole) && (
                <div className="sc-row" style={{ marginTop: 8 }}>
                  <button type="button" className="primary" onClick={() => run(() => sendDraft(draft.draft_id), "Marcado como enviado.")}>Marcar como enviado</button>
                </div>
              )}
              <Refs refs={draft.refs} />
            </li>
          ))}
        </ul>
      </AsyncSection>
    </section>
  );
}
