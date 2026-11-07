"use client";

import "../onboarding.css";

import Link from "next/link";
import { use, useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { AsyncSection } from "../../../components/AsyncSection";
import { StageTimeline } from "../../../components/onboarding/StageTimeline";
import { useAsyncData } from "../../../hooks/useAsyncData";
import { usePolling } from "../../../hooks/usePolling";
import { ApiFieldError } from "../../../services/http";
import { decideStage, getOnboarding, markPaperwork } from "../../../services/onboardingApi";
import {
  APPROVER_LABELS,
  DEPARTMENT_LABELS,
  FRAMEWORK_LABELS,
  PAPERWORK_LABELS,
  PHASE_LABELS,
  POLL_INTERVAL_MS,
  SITE_LABELS,
  actionLabel,
  formatScore,
  progressSignature,
  shouldPoll,
  type OnboardingDetail,
} from "../../../types/onboarding";

function errorText(err: unknown): string {
  if (err instanceof ApiFieldError) return err.fieldErrors.map((e) => e.message).join(" ");
  return err instanceof Error ? err.message : "No se pudo completar la acción.";
}

function PaperworkForm({ detail, onSent }: { detail: OnboardingDetail; onSent: () => void }) {
  const [marks, setMarks] = useState<Record<string, boolean>>(detail.paperwork);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setMarks(detail.paperwork), [detail.paperwork]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await markPaperwork(detail.hire_id, marks);
      onSent();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="panel" onSubmit={submit}>
      <h3 style={{ marginTop: 0 }}>Etapa 1 · Trámites</h3>
      <p className="onb-muted">
        Solo se registra que cada trámite está recibido o completado. El resultado de la verificación de antecedentes,
        documentos de identidad, datos bancarios o salario <strong>nunca</strong> entran en este sistema.
      </p>
      {Object.entries(PAPERWORK_LABELS).map(([field, label]) => (
        <label key={field} className="onb-option">
          <input
            type="checkbox"
            checked={Boolean(marks[field])}
            disabled={detail.paperwork[field]}
            onChange={(e) => setMarks({ ...marks, [field]: e.target.checked })}
          />
          {label}
        </label>
      ))}
      {error && <p className="error-text">{error}</p>}
      <button className="primary" type="submit" disabled={busy}>
        {busy ? "Guardando…" : "Guardar trámites"}
      </button>
    </form>
  );
}

function ApprovalForm({ detail, onSent }: { detail: OnboardingDetail; onSent: () => void }) {
  const stage = detail.waiting_for?.stage ?? detail.current_stage;
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const send = async (decision: "approve" | "reject") => {
    if (decision === "reject" && !comment.trim()) {
      setError("Para rechazar, escribe un comentario: la persona lo verá.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await decideStage(detail.hire_id, stage, decision, comment);
      setComment("");
      onSent();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const title = detail.stages.find((s) => s.number === stage)?.title ?? `Etapa ${stage}`;
  return (
    <section className="panel">
      <h3 style={{ marginTop: 0 }}>Aprobación · {title}</h3>
      {detail.last_check_result?.passed && detail.last_check_result.stage === stage && (
        <p className="onb-muted">Comprobación superada: {formatScore(detail.last_check_result)}.</p>
      )}
      <label htmlFor="onb-comment">Comentario (obligatorio si rechazas; lo verá la persona)</label>
      <textarea id="onb-comment" rows={3} value={comment} maxLength={1000} onChange={(e) => setComment(e.target.value)} />
      <p className="onb-muted">No incluyas datos de identidad, bancarios, salariales ni de la verificación de antecedentes.</p>
      {error && <p className="error-text">{error}</p>}
      <div className="onb-actions">
        <button className="primary" type="button" disabled={busy} onClick={() => send("approve")}>
          Aprobar
        </button>
        <button type="button" disabled={busy} onClick={() => send("reject")}>
          Rechazar y devolver al inicio de la etapa
        </button>
      </div>
    </section>
  );
}

export default function OnboardingDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const fetchDetail = useCallback(() => getOnboarding(id), [id]);
  const { data, isLoading, error, reload } = useAsyncData(fetchDetail, "No se pudo cargar la incorporación.");
  const [live, setLive] = useState<OnboardingDetail | null>(null);
  const [pendingSince, setPendingSince] = useState<number | null>(null);
  const detail = live ?? data;

  const refresh = useCallback(async () => {
    try {
      setLive(await getOnboarding(id));
    } catch {
      // Refresco silencioso: el error visible es el de la carga inicial.
    }
  }, [id]);
  usePolling(refresh, shouldPoll(detail?.phase, pendingSince), POLL_INTERVAL_MS);

  // Cuando la firma cambia respecto a la del envío, el agente ya aplicó la acción.
  const signature = detail ? progressSignature(detail) : "";
  const sentSignature = useRef<string | null>(null);
  useEffect(() => {
    if (pendingSince !== null && sentSignature.current !== null && signature !== sentSignature.current) {
      setPendingSince(null);
      sentSignature.current = null;
    }
  }, [signature, pendingSince]);

  const onSent = () => {
    sentSignature.current = signature;
    setPendingSince(Date.now());
    void refresh();
  };

  return (
    <main className="onb-grid">
      <p style={{ margin: 0 }}>
        <Link href="/onboarding">← Incorporaciones</Link>
      </p>
      <AsyncSection isLoading={isLoading} error={error} onRetry={reload} loadingLabel="Cargando incorporación…">
        {detail && (
          <>
            <header>
              <h1 style={{ marginBottom: 4 }}>{detail.full_name}</h1>
              <p className="onb-muted" style={{ marginTop: 0 }}>
                {detail.job_title} · {DEPARTMENT_LABELS[detail.department] ?? detail.department} · {SITE_LABELS[detail.site]} ·{" "}
                {FRAMEWORK_LABELS[detail.framework]} · inicio {new Date(detail.start_date).toLocaleDateString("es-ES")}
              </p>
              <div className="onb-actions">
                <span className={`onb-pill ${detail.status === "completed" ? "onb-pill-ok" : detail.blocked ? "onb-pill-danger" : "onb-pill-brand"}`}>
                  {PHASE_LABELS[detail.phase]}
                </span>
                <span className={`onb-pill ${detail.compliance_approved ? "onb-pill-ok" : "onb-pill-warn"}`}>
                  {detail.compliance_approved ? "Cumplimiento aprobado" : "Sin acceso a datos de pacientes"}
                </span>
                {pendingSince !== null && <span className="onb-pill">El agente está aplicando tu acción…</span>}
              </div>
            </header>

            {detail.blocked_reasons.length > 0 && (
              <div className="onb-banner onb-banner-warn">
                <strong>Bloqueada:</strong> {detail.blocked_reasons.join(" · ")}
              </div>
            )}

            <div className="onb-two-cols">
              <div className="onb-grid">
                {detail.can_mark_paperwork && <PaperworkForm detail={detail} onSent={onSent} />}
                {detail.can_decide && <ApprovalForm detail={detail} onSent={onSent} />}
                {detail.waiting_for?.kind === "approval" && !detail.can_decide && (
                  <div className="onb-banner">
                    Esperando la aprobación de {APPROVER_LABELS[detail.waiting_for.approver ?? ""] ?? "una persona"}.
                    {detail.decide_refusal ? ` ${detail.decide_refusal}` : ""}
                  </div>
                )}
                {detail.rejection && (
                  <div className="onb-banner onb-banner-danger">
                    Etapa {detail.rejection.stage} devuelta al inicio. Comentario: {detail.rejection.comment}
                  </div>
                )}
                <section className="panel">
                  <h3 style={{ marginTop: 0 }}>Etapas</h3>
                  <StageTimeline stages={detail.stages} />
                  {detail.last_check_result && (
                    <p className="onb-muted">
                      Última comprobación (etapa {detail.last_check_result.stage}, intento {detail.last_check_result.attempt}):{" "}
                      {formatScore(detail.last_check_result)} — {detail.last_check_result.passed ? "superada" : "no superada"}.
                    </p>
                  )}
                </section>
              </div>
              <div className="onb-grid">
                <section className="panel">
                  <h3 style={{ marginTop: 0 }}>Accesos</h3>
                  {detail.access_requests.length === 0 ? (
                    <p className="onb-muted">Sin solicitudes. Las del departamento las emite el agente en la etapa 7.</p>
                  ) : (
                    <ul className="onb-inbox">
                      {detail.access_requests.map((r) => (
                        <li key={`${r.system_key}-${r.created_at}`}>
                          {r.system_name}{" "}
                          <span className={`onb-pill ${r.status === "blocked" ? "onb-pill-danger" : "onb-pill-ok"}`}>
                            {r.status === "blocked" ? "Bloqueada" : "Solicitada"}
                          </span>
                          <div className="onb-muted">
                            {r.contains_patient_data ? "Datos de pacientes · " : ""}
                            {r.requested_by_type === "agent" ? "emitida por el agente" : "pedida por la persona"}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </div>
            </div>

            {detail.audit && (
              <section className="panel">
                <h3 style={{ marginTop: 0 }}>Auditoría</h3>
                <p className="onb-muted">
                  Cada acción con su actor: <span className="mono">agent</span> = proceso automatizado
                  (onboarding-orchestrator); <span className="mono">human</span> = una persona, en seudónimo.
                </p>
                <div className="onb-table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Cuándo</th>
                        <th>Etapa</th>
                        <th>Acción</th>
                        <th>Actor</th>
                        <th>Resultado</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.audit.map((event, index) => (
                        <tr key={`${event.created_at}-${index}`}>
                          <td className="mono">{new Date(event.created_at).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "medium" })}</td>
                          <td>{event.stage ?? "—"}</td>
                          <td>
                            {actionLabel(event.action)}
                            {event.security_event && <span className="onb-pill onb-pill-danger" style={{ marginLeft: 6 }}>Seguridad</span>}
                          </td>
                          <td className="mono">
                            {event.actor_type} · {event.actor_ref}
                          </td>
                          <td className="mono">{event.outcome ?? ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
          </>
        )}
      </AsyncSection>
    </main>
  );
}
