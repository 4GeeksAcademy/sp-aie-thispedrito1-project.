"use client";

import "./financial-close.css";

import { useCallback, useEffect, useState, type ReactNode } from "react";

import { AsyncSection } from "../../components/AsyncSection";
import { useAsyncData } from "../../hooks/useAsyncData";
import { decideReport, getInbox, getMyRole, getReport, listReports, traceFigure } from "../../services/financialCloseApi";
import {
  INBOX_LABELS,
  STATUS_LABELS,
  TRIGGER_LABELS,
  isFigureId,
  parseReport,
  periodLabel,
  sourceLabel,
  splitInline,
  validateDecision,
  type Block,
  type Decision,
  type Figure,
  type FigureTrace,
  type ReportDetail,
  type ReportLanguage,
} from "../../types/financialClose";

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

/**
 * Cierre financiero mensual (Dra. Sandra Okonkwo; revisa Tom Callahan).
 * El agente prepara el informe con cifras calculadas por código; cada cifra
 * enlaza a sus registros de origen. Nada se distribuye sin la aprobación de Tom.
 */
export default function FinancialClosePage() {
  const role = useAsyncData(useCallback(() => getMyRole(), []), "No se pudo comprobar tu rol.");
  const reports = useAsyncData(useCallback(() => listReports(), []), "No se pudieron cargar los informes.");
  const [selected, setSelected] = useState<string | null>(null);
  const [lang, setLang] = useState<ReportLanguage>("en");

  useEffect(() => {
    if (!selected && reports.data && reports.data.length > 0) setSelected(reports.data[0].id);
  }, [reports.data, selected]);

  const nextRun = role.data?.next_scheduled_run ? new Date(role.data.next_scheduled_run).toLocaleString("es-ES") : null;

  return (
    <main className="fc-grid">
      <header>
        <h1 style={{ marginBottom: 4 }}>Cierre financiero mensual</h1>
        <p className="fc-muted" style={{ marginTop: 0 }}>
          El agente prepara el cierre el tercer día hábil de cada mes a las 06:00 (hora de Austin). Las cifras las calcula el
          código y cada una enlaza a sus registros de origen; las explicaciones son hipótesis. Nada se distribuye sin la
          aprobación de Tom Callahan.
          {role.data?.display_name ? ` Has entrado como ${role.data.display_name}.` : ""}
          {nextRun ? ` Próxima ejecución: ${nextRun}.` : ""}
        </p>
      </header>

      <InboxPanel />

      <AsyncSection
        isLoading={reports.isLoading}
        error={reports.error}
        onRetry={reports.reload}
        loadingLabel="Cargando informes…"
        isEmpty={(reports.data ?? []).length === 0}
        emptyLabel="Todavía no hay informes de cierre (o no formas parte del directorio del cierre)."
      >
        <section className="panel">
          <h2 style={{ marginTop: 0 }}>Versiones</h2>
          <ul className="fc-list">
            {(reports.data ?? []).map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  className={`fc-card ${r.id === selected ? "fc-card-active" : ""}`}
                  style={{ width: "100%", textAlign: "left", background: "transparent", color: "inherit" }}
                  onClick={() => setSelected(r.id)}
                >
                  <span className="fc-row">
                    <strong>{periodLabel(r.period)} · v{r.version}</strong>
                    <span className={`fc-pill fc-pill-${r.status}`}>{STATUS_LABELS[r.status]}</span>
                    <span className="fc-muted">
                      {TRIGGER_LABELS[r.trigger] ?? r.trigger} · texto: {r.narrative_source === "model" ? "modelo" : r.narrative_source === "template" ? "plantilla" : "mixto"} ·
                      cifras <span className="fc-code">{r.figures_sha256.slice(0, 12)}</span>
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </AsyncSection>

      {selected ? <ReportView key={`${selected}-${lang}`} id={selected} lang={lang} onLang={setLang} onDecided={reports.reload} /> : null}
    </main>
  );
}

function InboxPanel() {
  const inbox = useAsyncData(useCallback(() => getInbox(), []), "No se pudo cargar tu bandeja.");
  return (
    <section className="panel">
      <h2 style={{ marginTop: 0 }}>Tu bandeja</h2>
      <AsyncSection
        isLoading={inbox.isLoading}
        error={inbox.error}
        onRetry={inbox.reload}
        loadingLabel="Cargando bandeja…"
        isEmpty={(inbox.data ?? []).length === 0}
        emptyLabel="Sin avisos."
      >
        <ul className="fc-list">
          {(inbox.data ?? []).slice(0, 8).map((item, index) => (
            <li key={`${item.kind}-${item.report_id ?? item.entry_id}-${index}`} className="fc-card">
              <div className="fc-row">
                <strong>{INBOX_LABELS[item.kind] ?? item.kind}</strong>
                <span className="fc-muted">{periodLabel(item.period)}</span>
                {item.entry_id ? <span className="fc-code">{item.entry_id}</span> : null}
              </div>
              <div className="fc-muted">{item.summary}</div>
            </li>
          ))}
        </ul>
      </AsyncSection>
    </section>
  );
}

function ReportView({ id, lang, onLang, onDecided }: { id: string; lang: ReportLanguage; onLang: (l: ReportLanguage) => void; onDecided: () => void }) {
  const report = useAsyncData(useCallback(() => getReport(id, lang), [id, lang]), "No se pudo cargar el informe.");
  const [figureId, setFigureId] = useState<string | null>(null);

  return (
    <AsyncSection isLoading={report.isLoading} error={report.error} onRetry={report.reload} loadingLabel="Cargando informe…">
      {report.data ? (
        <div className="fc-layout">
          <section className="panel">
            <div className="fc-row" style={{ justifyContent: "space-between" }}>
              <span className={`fc-pill fc-pill-${report.data.status}`}>{STATUS_LABELS[report.data.status]}</span>
              <div className="fc-row" role="group" aria-label="Idioma del informe">
                {(["en", "es"] as const).map((l) => (
                  <button key={l} type="button" aria-pressed={lang === l} className={lang === l ? "primary" : undefined} onClick={() => onLang(l)}>
                    {l === "en" ? "English" : "Español"}
                  </button>
                ))}
              </div>
            </div>
            <ReportBody markdown={report.data.markdown} figures={report.data.figures} onFigure={setFigureId} />
          </section>
          <aside className="fc-grid fc-trace">
            <TracePanel reportId={id} figureId={figureId} />
            <ReviewPanel report={report.data} onDecided={() => { report.reload(); onDecided(); }} />
          </aside>
        </div>
      ) : null}
    </AsyncSection>
  );
}

function InlineText({ text, figures, onFigure }: { text: string; figures: Record<string, Figure>; onFigure: (id: string) => void }) {
  return (
    <>
      {splitInline(text).map((part, i) => {
        if (part.kind === "strong") return <strong key={i}>{part.text}</strong>;
        if (part.kind === "code") {
          return isFigureId(part.text, figures) ? (
            <button key={i} type="button" className="fc-figure" title="Ver origen de esta cifra" onClick={() => onFigure(part.text)}>
              {part.text}
            </button>
          ) : (
            <code key={i} className="fc-code">{part.text}</code>
          );
        }
        return <span key={i}>{part.text}</span>;
      })}
    </>
  );
}

function ReportBody({ markdown, figures, onFigure }: { markdown: string; figures: Record<string, Figure>; onFigure: (id: string) => void }) {
  const render = (text: string) => <InlineText text={text} figures={figures} onFigure={onFigure} />;
  const block = (b: Block, i: number): ReactNode => {
    switch (b.kind) {
      case "heading":
        return b.level === 1 ? <h1 key={i}>{render(b.text)}</h1> : b.level === 2 ? <h2 key={i}>{render(b.text)}</h2> : <h3 key={i}>{render(b.text)}</h3>;
      case "quote":
        return <blockquote key={i}>{render(b.text)}</blockquote>;
      case "list":
        return <ul key={i}>{b.items.map((item, j) => <li key={j}>{render(item)}</li>)}</ul>;
      case "table":
        return (
          <div key={i} className="fc-table-wrap">
            <table>
              <thead><tr>{b.header.map((h, j) => <th key={j}>{render(h)}</th>)}</tr></thead>
              <tbody>{b.rows.map((row, r) => <tr key={r}>{row.map((c, j) => <td key={j}>{render(c)}</td>)}</tr>)}</tbody>
            </table>
          </div>
        );
      default:
        return <p key={i}>{render(b.text)}</p>;
    }
  };
  return <article className="fc-report">{parseReport(markdown).map(block)}</article>;
}

function TracePanel({ reportId, figureId }: { reportId: string; figureId: string | null }) {
  const [trace, setTrace] = useState<FigureTrace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    if (!figureId) return;
    setLoading(true);
    setError(null);
    traceFigure(reportId, figureId)
      .then(setTrace)
      .catch((err: unknown) => setError(errorText(err, "No se pudo cargar el origen de la cifra.")))
      .finally(() => setLoading(false));
  }, [reportId, figureId]);

  useEffect(load, [load]);

  return (
    <section className="panel">
      <h2 style={{ marginTop: 0 }}>Origen de la cifra</h2>
      {!figureId ? <p className="fc-muted">Pulsa cualquier identificador de cifra del informe para ver de qué registros sale.</p> : null}
      {loading ? <p className="fc-muted">Cargando…</p> : null}
      {error ? (
        <div className="fc-row">
          <span className="fc-error">{error}</span>
          <button type="button" onClick={load}>Reintentar</button>
        </div>
      ) : null}
      {trace && !loading && !error ? (
        <div className="fc-grid" style={{ gap: 8 }}>
          <div className="fc-code">{trace.figure_id}</div>
          <div>
            <strong>{trace.value ?? "Sin registros"}</strong> <span className="fc-muted">{trace.unit}</span>
          </div>
          <ul className="fc-list">
            {trace.sources.filter((s) => s.kind !== "ledger").map((s, i) => <li key={i} className="fc-muted">{sourceLabel(s)}</li>)}
          </ul>
          {trace.ledger_records.length > 0 ? (
            <div className="fc-table-wrap">
              <table className="fc-report">
                <thead><tr><th>entry_id</th><th>Fila</th><th>Importe</th><th>Descripción</th></tr></thead>
                <tbody>
                  {trace.ledger_records.map((r) => (
                    <tr key={r.entry_id}>
                      <td className="fc-code">{r.entry_id}</td>
                      <td>{r.source_file} {r.source_row}</td>
                      <td>{r.amount ?? "—"} {r.currency}</td>
                      <td>{r.description_withheld ? <em className="fc-muted">Retenida (dato sensible)</em> : r.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function ReviewPanel({ report, onDecided }: { report: ReportDetail; onDecided: () => void }) {
  const [comments, setComments] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = (decision: Decision) => {
    const invalid = validateDecision(decision, comments);
    if (invalid) {
      setError(invalid);
      return;
    }
    setBusy(true);
    setError(null);
    decideReport(report.id, decision, comments)
      .then(() => {
        setComments("");
        onDecided();
      })
      .catch((err: unknown) => setError(errorText(err, "No se pudo registrar la decisión.")))
      .finally(() => setBusy(false));
  };

  return (
    <section className="panel">
      <h2 style={{ marginTop: 0 }}>Revisión</h2>
      <p className="fc-muted">Revisor: {report.reviewer}. {report.status === "rejected" ? "Rechazado: el agente prepara una nueva versión con los comentarios." : ""}</p>
      {report.decisions.map((d, i) => (
        <div key={i} className="fc-card" style={{ marginBottom: 8 }}>
          <strong>{d.decision === "approve" ? "Aprobado" : "Rechazado"}</strong>{" "}
          <span className="fc-muted">{new Date(d.decided_at).toLocaleString("es-ES")}</span>
          {d.comments ? <div>{d.comments}</div> : null}
        </div>
      ))}
      {report.can_decide ? (
        <div className="fc-grid" style={{ gap: 8 }}>
          <label htmlFor="fc-comments" className="fc-muted">
            Comentarios (obligatorios para rechazar; sin datos de pacientes)
          </label>
          <textarea id="fc-comments" className="fc-textarea" value={comments} maxLength={2000} onChange={(e) => setComments(e.target.value)} />
          {error ? <span className="fc-error" role="alert">{error}</span> : null}
          <div className="fc-row">
            <button type="button" className="primary" disabled={busy} onClick={() => submit("approve")}>Aprobar y distribuir</button>
            <button type="button" disabled={busy} onClick={() => submit("reject")}>Rechazar con comentarios</button>
          </div>
        </div>
      ) : null}
      {report.distribution.length > 0 ? (
        <>
          <h3>Distribuido a</h3>
          <ul className="fc-list">
            {report.distribution.map((d) => <li key={d.recipient_role}>{d.recipient_name}</li>)}
          </ul>
        </>
      ) : null}
    </section>
  );
}
