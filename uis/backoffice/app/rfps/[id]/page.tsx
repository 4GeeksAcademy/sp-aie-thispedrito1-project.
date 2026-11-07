"use client";

import Link from "next/link";
import { use, useCallback, useState } from "react";

import { AsyncSection } from "../../../components/AsyncSection";
import { useAsyncData } from "../../../hooks/useAsyncData";
import { usePolling } from "../../../hooks/usePolling";
import { getRfpTicket } from "../../../services/rfpApi";
import {
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
  isProvisional,
  shouldPoll,
  type RfpDepartmentSection,
  type RfpTicketDetail,
} from "../../../types/rfp";

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

function TicketView({ ticket }: { ticket: RfpTicketDetail }) {
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
  usePolling(refresh, ticket ? shouldPoll(ticket.status) : false, POLL_INTERVAL_MS);

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
        {ticket && <TicketView ticket={ticket} />}
      </AsyncSection>
    </main>
  );
}
