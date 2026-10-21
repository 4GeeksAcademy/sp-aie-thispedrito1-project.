"use client";

import Link from "next/link";
import { use, useCallback, useState } from "react";

import { AsyncSection } from "../../../components/AsyncSection";
import { useAsyncData } from "../../../hooks/useAsyncData";
import { usePolling } from "../../../hooks/usePolling";
import { ApiFieldError } from "../../../services/http";
import {
  getFinalDocument,
  getRfpTicket,
  getRfpTrace,
  submitArbitrationDecision,
  submitSectionDecision,
} from "../../../services/rfpApi";
import {
  APPROVAL_STATUS_CLASS,
  APPROVAL_STATUS_LABELS,
  POLL_INTERVAL_MS,
  SECTION_STATUS_CLASS,
  SECTION_STATUS_LABELS,
  STATUS_CLASS,
  STATUS_LABELS,
  describeEvaluation,
  describeOutcome,
  formatPhiCategories,
  formatPopulation,
  formatProgramTypes,
  TRACE_PART_LABELS,
  TRIGGER_LABELS,
  describeArbitration,
  finalDocumentFileName,
  isProvisional,
  shouldPollTicket,
  summarizeTraceOutput,
  toDecisionPayload,
  validateDecisionDraft,
  type ApprovalRequest,
  type DecisionDraft,
  type RfpArbitration,
  type RfpDepartmentSection,
  type RfpTicketDetail,
} from "../../../types/rfp";

function fieldErrorsOf(error: unknown): Record<string, string> | null {
  if (!(error instanceof ApiFieldError)) return null;
  return Object.fromEntries(error.fieldErrors.map(({ field, message }) => [field, message]));
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString("es-ES", { dateStyle: "medium", timeStyle: "short" }) : "—";
}

function Field({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div>
      <div style={{ color: "var(--muted)", fontSize: 12, textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</div>
      <div style={{ fontWeight: 600 }}>{value === null || value === undefined || value === "" ? "—" : value}</div>
    </div>
  );
}

/** Parte 2: borrador de la sección + sus tres evaluaciones. */
function DraftBlock({ section }: { section: RfpDepartmentSection }) {
  if (!section.draft_status) return null;
  const provisional = isProvisional(section);
  const evaluation = section.evaluation_results;
  return (
    <div style={{ marginTop: 16, borderTop: "1px solid var(--line)", paddingTop: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <strong>Borrador</strong>
        <span className={`status-badge ${SECTION_STATUS_CLASS[section.draft_status]}`}>
          {SECTION_STATUS_LABELS[section.draft_status]}
        </span>
        {section.draft_iterations > 0 && (
          <span className="mono" style={{ color: "var(--muted)", fontSize: 12 }}>
            {section.draft_iterations === 1 ? "1 iteración" : `${section.draft_iterations} iteraciones`}
          </span>
        )}
      </div>
      {provisional && evaluation && (
        <p className="rfp-check-fail" style={{ fontSize: 13 }}>
          Borrador provisional: no superó la evaluación tras {section.draft_iterations} intentos. No usarlo sin revisar
          estos puntos:
          <br />
          <span style={{ whiteSpace: "pre-wrap" }}>{evaluation.feedback_for_generator}</span>
        </p>
      )}
      {evaluation && (
        <ul className="rfp-list" style={{ fontSize: 13 }}>
          {describeEvaluation(evaluation).map((check) => (
            <li key={check.label}>
              <span className={check.passed ? "rfp-check-pass" : "rfp-check-fail"}>
                {check.passed ? "✓" : "✗"} {check.label}
              </span>
              <span style={{ color: "var(--muted)" }}> — {check.detail}</span>
            </li>
          ))}
        </ul>
      )}
      {section.draft_content && (
        <div className={`rfp-draft${provisional ? " rfp-draft-provisional" : ""}`}>{section.draft_content}</div>
      )}
    </div>
  );
}

/** Formulario del responsable: lo mínimo para decidir sin releer la RFP. */
function DecisionForm({
  ticketId,
  section,
  request,
  onChanged,
}: {
  ticketId: string;
  section: RfpDepartmentSection;
  request: ApprovalRequest;
  onChanged: () => void;
}) {
  const [draft, setDraft] = useState<DecisionDraft>({ decision: "", comments: "", confirmedCapacity: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const facts = request.fixed_facts;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const found = validateDecisionDraft(draft, request);
    setErrors(found);
    setFormError(null);
    if (Object.keys(found).length) return;
    setSubmitting(true);
    try {
      await submitSectionDecision(ticketId, section.department_id, toDecisionPayload(draft, request));
      onChanged();
    } catch (error) {
      const byField = fieldErrorsOf(error);
      if (byField) setErrors(byField);
      else setFormError(error instanceof Error ? error.message : "No se pudo registrar la decisión.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={submit} noValidate style={{ display: "grid", gap: 10, marginTop: 10 }}>
      <p style={{ margin: 0, fontSize: 13 }}>
        Firma en nombre de <strong>{request.approver}</strong> · ronda de cambios {request.revision_round} de{" "}
        {request.max_revision_rounds}
        {facts.currency && (
          <span style={{ color: "var(--muted)" }}>
            {" "}
            · Hechos fijados: {facts.client_country}, {facts.currency}, {facts.data_agreement} ({facts.regulation})
          </span>
        )}
      </p>
      {request.provisional && request.evaluation.feedback && (
        <p className="rfp-check-fail" style={{ margin: 0, fontSize: 13, whiteSpace: "pre-wrap" }}>
          Borrador provisional. Puntos que los evaluadores no dieron por buenos:{"\n"}
          {request.evaluation.feedback}
        </p>
      )}
      {request.last_error && <p className="error-text" style={{ margin: 0 }}>{request.last_error}</p>}
      <fieldset aria-label="Decisión" style={{ border: "none", padding: 0, margin: 0, display: "flex", gap: 14, flexWrap: "wrap" }}>
        {(
          [
            ["approve", "Aprobar"],
            ["request_changes", "Solicitar cambios"],
            ["reject", "Rechazar propuesta"],
          ] as const
        ).map(([value, label]) => (
          <label key={value} style={{ flexDirection: "row", alignItems: "center", gap: 6, color: "var(--text)" }}>
            <input
              type="radio"
              name={`decision-${section.department_id}`}
              value={value}
              checked={draft.decision === value}
              disabled={value === "request_changes" && !request.can_request_changes}
              onChange={() => setDraft({ ...draft, decision: value })}
            />
            {label}
          </label>
        ))}
      </fieldset>
      {errors.decision && <span className="error-text">{errors.decision}</span>}
      {!request.can_request_changes && (
        <span style={{ color: "var(--muted)", fontSize: 12 }}>Ya se usaron todas las rondas de cambios de esta sección.</span>
      )}
      {draft.decision === "approve" && request.asks_capacity && (
        <label>
          Capacidad confirmada (personas que pueden atender las sedes y el personal asignados; el contrato pide{" "}
          {request.covered_population})
          <input
            type="number"
            min={1}
            value={draft.confirmedCapacity}
            onChange={(event) => setDraft({ ...draft, confirmedCapacity: event.target.value })}
          />
          {errors.confirmed_capacity && <span className="error-text">{errors.confirmed_capacity}</span>}
        </label>
      )}
      {(draft.decision === "request_changes" || draft.decision === "reject") && (
        <label>
          {draft.decision === "reject" ? "Motivo del rechazo" : "Qué debe cambiar el redactor"} (no incluyas datos de
          pacientes)
          <textarea
            rows={3}
            value={draft.comments}
            onChange={(event) => setDraft({ ...draft, comments: event.target.value })}
          />
          {errors.comments && <span className="error-text">{errors.comments}</span>}
        </label>
      )}
      {draft.decision === "reject" && (
        <span className="rfp-check-fail" style={{ fontSize: 13 }}>
          Rechazar descarta la propuesta entera para todos los departamentos.
        </span>
      )}
      {formError && <p className="error-text" style={{ margin: 0 }}>{formError}</p>}
      <div>
        <button type="submit" className="primary" disabled={submitting}>
          {submitting ? "Registrando…" : "Registrar decisión"}
        </button>
      </div>
    </form>
  );
}

/** Parte 3: estado de la aprobación de la sección y, si espera, el formulario. */
function ApprovalBlock({ ticketId, section, onChanged }: { ticketId: string; section: RfpDepartmentSection; onChanged: () => void }) {
  if (!section.approval_status) return null;
  const status = section.approval_status;
  return (
    <div style={{ marginTop: 16, borderTop: "1px solid var(--line)", paddingTop: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <strong>Aprobación</strong>
        <span className={`status-badge ${APPROVAL_STATUS_CLASS[status]}`}>{APPROVAL_STATUS_LABELS[status]}</span>
      </div>
      {status === "aprobada" && (
        <p style={{ margin: "6px 0 0", fontSize: 13 }}>
          {section.approver} · {formatDate(section.approved_at)}
          {section.confirmed_capacity !== null && ` · capacidad confirmada: ${section.confirmed_capacity}`}
        </p>
      )}
      {status === "bloqueada" && (
        <p style={{ margin: "6px 0 0", fontSize: 13 }}>
          Un control de Compliance detuvo esta sección antes de enseñarla: el árbitro decide abajo.
        </p>
      )}
      {status === "pendiente" && section.approval_request && (
        <DecisionForm ticketId={ticketId} section={section} request={section.approval_request} onChanged={onChanged} />
      )}
      {section.approval_history.length > 0 && (
        <p style={{ color: "var(--muted)", fontSize: 12, margin: "8px 0 0" }}>
          Historial: {section.approval_history.map((item) => `${item.decision} (${formatDate(item.decided_at)})`).join(" → ")}
        </p>
      )}
    </div>
  );
}

/** Arbitraje: resumen de la última ronda y, si le toca a Tom Callahan, su decisión. */
function ArbitrationPanel({ ticketId, arbitration, onChanged }: { ticketId: string; arbitration: RfpArbitration; onChanged: () => void }) {
  const [option, setOption] = useState<"reduce_population" | "add_sites" | "">("");
  const [population, setPopulation] = useState("");
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const summary = describeArbitration(arbitration);
  const request = arbitration.pending ? arbitration.request : null;
  if (!summary && !request) return null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!option) {
      setErrors({ option: "Elige cómo resolver el conflicto." });
      return;
    }
    setErrors({});
    setFormError(null);
    setSubmitting(true);
    try {
      await submitArbitrationDecision(ticketId, {
        option,
        new_population: option === "reduce_population" ? Number(population) : null,
        note: option === "add_sites" ? note.trim() : null,
      });
      onChanged();
    } catch (error) {
      const byField = fieldErrorsOf(error);
      if (byField) setErrors(byField);
      else setFormError(error instanceof Error ? error.message : "No se pudo registrar la decisión.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="panel" style={{ margin: "16px 0" }}>
      <h2 style={{ marginTop: 0 }}>Arbitraje entre departamentos</h2>
      {summary && <p>{summary}</p>}
      {request && (
        <form onSubmit={submit} noValidate style={{ display: "grid", gap: 10 }}>
          <p style={{ margin: 0 }}>
            <strong>{TRIGGER_LABELS[request.trigger_id] ?? request.trigger_id}.</strong> El contrato cubre{" "}
            {request.covered_population} personas y Clinical Operations confirmó capacidad para {request.confirmed_capacity}.
            Decide <strong>{request.arbiter}</strong> (Revenue Cycle), con Compliance ya conforme · ronda {request.round} de{" "}
            {request.max_rounds}.
          </p>
          {arbitration.last_error && <p className="error-text" style={{ margin: 0 }}>{arbitration.last_error}</p>}
          {arbitration.decision_registered ? (
            <p style={{ margin: 0 }}>Decisión registrada: aplicándola…</p>
          ) : (
            <>
              <label style={{ flexDirection: "row", alignItems: "center", gap: 6, color: "var(--text)" }}>
                <input type="radio" name="arbitration" checked={option === "reduce_population"} onChange={() => setOption("reduce_population")} />
                Reducir la población cubierta
              </label>
              {option === "reduce_population" && (
                <label>
                  Nueva población (máximo {request.confirmed_capacity})
                  <input type="number" min={1} max={request.confirmed_capacity} value={population} onChange={(e) => setPopulation(e.target.value)} />
                  {errors.new_population && <span className="error-text">{errors.new_population}</span>}
                </label>
              )}
              <label style={{ flexDirection: "row", alignItems: "center", gap: 6, color: "var(--text)" }}>
                <input type="radio" name="arbitration" checked={option === "add_sites"} onChange={() => setOption("add_sites")} />
                Añadir sedes
              </label>
              {option === "add_sites" && (
                <label>
                  Qué sedes o refuerzos (sin datos de pacientes)
                  <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
                  {errors.note && <span className="error-text">{errors.note}</span>}
                </label>
              )}
              {errors.option && <span className="error-text">{errors.option}</span>}
              {formError && <p className="error-text" style={{ margin: 0 }}>{formError}</p>}
              <div>
                <button type="submit" className="primary" disabled={submitting}>
                  {submitting ? "Registrando…" : "Registrar decisión del árbitro"}
                </button>
              </div>
            </>
          )}
        </form>
      )}
    </section>
  );
}

function FinalDocumentPanel({ ticketId }: { ticketId: string }) {
  const fetchDocument = useCallback(() => getFinalDocument(ticketId), [ticketId]);
  const { data, isLoading, error, reload } = useAsyncData(fetchDocument, "No se pudo cargar el documento final.");

  const download = () => {
    if (!data) return;
    const url = URL.createObjectURL(new Blob([data.content_markdown], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = finalDocumentFileName(data);
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section className="panel" style={{ margin: "16px 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0 }}>Documento final</h2>
        {data && (
          <button type="button" onClick={download}>
            Descargar (.md)
          </button>
        )}
      </div>
      <AsyncSection isLoading={isLoading} error={error} onRetry={reload} loadingLabel="Cargando documento…">
        {data && (
          <>
            <p style={{ color: "var(--muted)" }}>
              Generado {formatDate(data.generated_at)} · moneda {data.currency} · aprobado por{" "}
              {data.approvals.map((a) => a.approver).join(", ")}
            </p>
            <div className="rfp-draft">{data.content_markdown}</div>
          </>
        )}
      </AsyncSection>
    </section>
  );
}

function TracePanel({ ticketId }: { ticketId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="panel" style={{ margin: "16px 0" }}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "Ocultar trazabilidad" : "Ver trazabilidad (qué agente hizo qué)"}
      </button>
      {open && <TraceTable ticketId={ticketId} />}
    </section>
  );
}

function TraceTable({ ticketId }: { ticketId: string }) {
  const fetchTrace = useCallback(() => getRfpTrace(ticketId), [ticketId]);
  const { data, isLoading, error, reload } = useAsyncData(fetchTrace, "No se pudo cargar la trazabilidad.");
  return (
    <AsyncSection isLoading={isLoading} error={error} onRetry={reload} isEmpty={data?.length === 0} emptyLabel="Sin pasos registrados." loadingLabel="Cargando trazabilidad…">
      {data && (
        <div style={{ overflowX: "auto", marginTop: 12 }}>
          <table className="table">
            <thead>
              <tr>
                <th>Hora (UTC)</th>
                <th>Parte</th>
                <th>Paso</th>
                <th>Departamento</th>
                <th>Agente</th>
                <th>Resultado</th>
              </tr>
            </thead>
            <tbody>
              {data.map((event) => (
                <tr key={event.event_id}>
                  <td className="mono">{event.timestamp.slice(11, 19)}</td>
                  <td>{TRACE_PART_LABELS[event.part] ?? event.part}</td>
                  <td className="mono">{event.node}</td>
                  <td>{event.department_id ?? "—"}</td>
                  <td className="mono">{event.agent}</td>
                  <td style={{ fontSize: 12 }}>{summarizeTraceOutput(event.output)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </AsyncSection>
  );
}

function TicketView({ ticket, onChanged }: { ticket: RfpTicketDetail; onChanged: () => void }) {
  const metadata = ticket.metadata;
  return (
    <>
      <div className="panel" style={{ margin: "16px 0" }}>
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <span className={`status-badge ${STATUS_CLASS[ticket.status]}`}>{STATUS_LABELS[ticket.status]}</span>
          {ticket.requires_compliance_review && (
            <span className="status-badge rfp-status-error">Revisión Compliance</span>
          )}
          <span className="mono" style={{ color: "var(--muted)" }}>
            {ticket.rfp_id}
          </span>
        </div>
        <p style={{ marginBottom: 0 }}>{describeOutcome(ticket)}</p>
        {ticket.phi_categories.length > 0 && (
          <p style={{ color: "var(--muted)", marginBottom: 0 }}>
            Detectado: {formatPhiCategories(ticket.phi_categories)}. El contenido no se muestra ni se ha enviado a
            ningún modelo.
          </p>
        )}
      </div>

      {ticket.has_final_document && <FinalDocumentPanel ticketId={ticket.ticket_id} />}
      {ticket.arbitration && <ArbitrationPanel ticketId={ticket.ticket_id} arbitration={ticket.arbitration} onChanged={onChanged} />}

      {metadata?.who_to_ask && metadata.who_to_ask.length > 0 && (
        <section className="panel" style={{ margin: "16px 0" }}>
          <h2 style={{ marginTop: 0 }}>Qué pedir a quién</h2>
          {metadata.summary && <p>{metadata.summary}</p>}
          <table className="table">
            <thead>
              <tr>
                <th>Departamento</th>
                <th>Responsable</th>
                <th>Qué pedirle</th>
              </tr>
            </thead>
            <tbody>
              {metadata.who_to_ask.map((ask) => (
                <tr key={ask.department_id}>
                  <td>{ask.department}</td>
                  <td>{ask.owner}</td>
                  <td>{ask.request}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {ticket.sections.length > 0 && (
        <div className="rfp-departments" style={{ margin: "16px 0" }}>
          {ticket.sections.map((section) => (
            <section key={section.department_id} className="panel">
              <h3 style={{ margin: 0 }}>{section.department}</h3>
              <p style={{ color: "var(--muted)", margin: "4px 0 0" }}>{section.owner}</p>
              <ul className="rfp-list">
                {section.key_aspects.map((aspect) => (
                  <li key={aspect}>{aspect}</li>
                ))}
              </ul>
              {section.open_questions.length > 0 && (
                <>
                  <p style={{ fontWeight: 600, marginBottom: 0 }}>Preguntas abiertas</p>
                  <ul className="rfp-list">
                    {section.open_questions.map((question) => (
                      <li key={question}>{question}</li>
                    ))}
                  </ul>
                </>
              )}
              {section.unsupported_claims_removed > 0 && (
                <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 0 }}>
                  {section.unsupported_claims_removed === 1
                    ? "Se descartó 1 afirmación con cifras que no aparecen en la RFP."
                    : `Se descartaron ${section.unsupported_claims_removed} afirmaciones con cifras que no aparecen en la RFP.`}
                </p>
              )}
              <DraftBlock section={section} />
              <ApprovalBlock ticketId={ticket.ticket_id} section={section} onChanged={onChanged} />
            </section>
          ))}
        </div>
      )}

      {metadata && (metadata.open_questions.length > 0 || metadata.conflicts.length > 0 || metadata.unmapped_departments.length > 0) && (
        <section className="panel" style={{ margin: "16px 0" }}>
          <h2 style={{ marginTop: 0 }}>Pendiente de confirmar</h2>
          <ul className="rfp-list">
            {metadata.open_questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
            {metadata.conflicts.map((conflict) => (
              <li key={conflict.description}>
                Contradicción ({conflict.department_ids.join(", ")}): {conflict.description}
              </li>
            ))}
            {metadata.unmapped_departments.length > 0 && (
              <li>
                La RFP pide áreas que no son departamentos de la propuesta: {metadata.unmapped_departments.join(", ")}.
                Revenue Cycle decide a quién derivarlas.
              </li>
            )}
          </ul>
        </section>
      )}

      {metadata?.client_name && (
        <section className="panel" style={{ margin: "16px 0" }}>
          <h2 style={{ marginTop: 0 }}>Datos de la RFP</h2>
          <div className="form-grid">
            <Field label="Cliente" value={metadata.client_name} />
            <Field label="País" value={metadata.client_country} />
            <Field label="Moneda" value={metadata.currency} />
            <Field label="Programa" value={formatProgramTypes(metadata.program_type)} />
            <Field label="Población cubierta" value={formatPopulation(metadata.covered_population, metadata.covered_population_unit)} />
            <Field label="Fecha límite" value={metadata.deadline_text ?? metadata.deadline} />
            <Field label="Presupuesto" value={metadata.budget_range ?? "No indicado"} />
          </div>
          <h3>Legibilidad (coste estimado de procesamiento)</h3>
          <div className="form-grid">
            <Field label="Palabras" value={metadata.word_count} />
            <Field label="Tokens estimados" value={metadata.estimated_tokens} />
            <Field label="Flesch-Kincaid (grado)" value={metadata.flesch_kincaid_grade} />
            <Field label="Gunning Fog" value={metadata.gunning_fog} />
            <Field label="Flesch Reading Ease" value={metadata.flesch_reading_ease} />
          </div>
          {metadata.readability_note && <p style={{ color: "var(--muted)", marginBottom: 0 }}>{metadata.readability_note}</p>}
        </section>
      )}

      <TracePanel ticketId={ticket.ticket_id} />
    </>
  );
}

export default function RfpTicketPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const fetchTicket = useCallback(() => getRfpTicket(id), [id]);
  const { data, isLoading, error, reload } = useAsyncData(fetchTicket, "No se pudo cargar la RFP.");
  const [live, setLive] = useState<RfpTicketDetail | null>(null);
  const ticket = live ?? data;

  const refresh = useCallback(async () => {
    try {
      setLive(await getRfpTicket(id));
    } catch {
      // Refresco silencioso: un fallo puntual no tapa el último estado bueno.
    }
  }, [id]);
  usePolling(refresh, ticket ? shouldPollTicket(ticket) : false, POLL_INTERVAL_MS);

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <Link href="/rfps" className="nav-link" style={{ color: "var(--brand)" }}>
        ← Volver a RFPs
      </Link>
      <h1 style={{ marginBottom: 0 }}>{ticket?.client_name ?? "RFP"}</h1>
      <AsyncSection
        isLoading={isLoading}
        error={error}
        onRetry={() => {
          setLive(null);
          reload();
        }}
        loadingLabel="Cargando RFP…"
      >
        {ticket && <TicketView ticket={ticket} onChanged={refresh} />}
      </AsyncSection>
    </main>
  );
}
