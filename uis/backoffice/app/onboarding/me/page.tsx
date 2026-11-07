"use client";

import "../onboarding.css";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { AsyncSection } from "../../../components/AsyncSection";
import { NotificationInbox } from "../../../components/onboarding/NotificationInbox";
import { StageTimeline } from "../../../components/onboarding/StageTimeline";
import { useAsyncData } from "../../../hooks/useAsyncData";
import { usePolling } from "../../../hooks/usePolling";
import { ApiFieldError } from "../../../services/http";
import { askQuestion, getMyOnboarding, requestAccess, submitCheck } from "../../../services/onboardingApi";
import {
  APPROVER_LABELS,
  PHASE_LABELS,
  POLL_INTERVAL_MS,
  buildAnswers,
  formatScore,
  progressSignature,
  routedLabel,
  shouldPoll,
  type MyOnboarding,
} from "../../../types/onboarding";

function errorText(err: unknown): string {
  if (err instanceof ApiFieldError) return err.fieldErrors.map((e) => e.message).join(" ");
  return err instanceof Error ? err.message : "No se pudo completar la acción.";
}

function CheckForm({ me, onSent }: { me: MyOnboarding; onSent: () => void }) {
  const check = me.check!;
  const [selected, setSelected] = useState<Record<string, number | undefined>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setSelected({}), [check.set_id, check.attempt]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const built = buildAnswers(check.questions, selected);
    if ("missing" in built) {
      setError(`Faltan ${built.missing.length} pregunta(s) por responder.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await submitCheck(me.waiting_for!.stage, check.attempt, built.answers);
      onSent();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="panel" onSubmit={submit}>
      <h3 style={{ marginTop: 0 }}>
        Comprobación · intento {check.attempt} · necesitas un {Math.round(check.threshold * 100)} %
      </h3>
      {check.questions.map((q, index) => (
        <fieldset key={q.id} className="onb-question">
          <legend>
            {index + 1}. {q.question}
          </legend>
          {q.options.map((option, optionIndex) => (
            <label key={option} className="onb-option">
              <input
                type="radio"
                name={q.id}
                checked={selected[q.id] === optionIndex}
                onChange={() => setSelected({ ...selected, [q.id]: optionIndex })}
              />
              {option}
            </label>
          ))}
        </fieldset>
      ))}
      {error && <p className="error-text">{error}</p>}
      <button className="primary" type="submit" disabled={busy}>
        {busy ? "Enviando…" : "Enviar respuestas"}
      </button>
    </form>
  );
}

function QuestionBox({ me, onAsked }: { me: MyOnboarding; onAsked: () => void }) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) {
      setError("Escribe tu duda.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await askQuestion(text.trim());
      setText("");
      onAsked();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel">
      <h3 style={{ marginTop: 0 }}>¿Dudas?</h3>
      <p className="onb-muted">
        El asistente responde solo con el material oficial y cita la sección. Si no está cubierto, lo dice y se lo pasa a
        Claire (cumplimiento) o a Diane (resto). No escribas datos personales: se eliminan antes de procesar la duda.
      </p>
      <form onSubmit={submit}>
        <textarea rows={3} maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} aria-label="Tu duda" />
        {error && <p className="error-text">{error}</p>}
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Consultando el material…" : "Preguntar"}
        </button>
      </form>
      <ul className="onb-inbox" style={{ marginTop: 12 }}>
        {[...me.questions].reverse().map((q) => (
          <li key={q.id}>
            <strong>{q.question}</strong>
            <div>{q.answer}</div>
            {q.status === "answered" ? (
              <div className="onb-citation">Fuente: {q.citations.join(" · ")}</div>
            ) : (
              <span className="onb-pill onb-pill-warn">{routedLabel(q.routed_to)}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function AccessBox({ me, onRequested }: { me: MyOnboarding; onRequested: () => void }) {
  const [systemKey, setSystemKey] = useState(me.systems[0]?.key ?? "");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await requestAccess(systemKey);
      setMessage({ ok: true, text: "Solicitud registrada." });
    } catch (err) {
      setMessage({ ok: false, text: errorText(err) });
    } finally {
      setBusy(false);
      onRequested();
    }
  };

  return (
    <section className="panel">
      <h3 style={{ marginTop: 0 }}>Accesos</h3>
      {!me.compliance_approved && (
        <p className="onb-banner onb-banner-warn">
          Los sistemas con información de pacientes requieren la etapa de Cumplimiento aprobada. Un intento antes de tiempo
          se rechaza y se registra como evento de seguridad.
        </p>
      )}
      <form onSubmit={submit} className="onb-actions">
        <select value={systemKey} onChange={(e) => setSystemKey(e.target.value)} aria-label="Sistema">
          {me.systems.map((s) => (
            <option key={s.key} value={s.key}>
              {s.name}
              {s.contains_patient_data ? " (datos de pacientes)" : ""}
            </option>
          ))}
        </select>
        <button type="submit" disabled={busy}>
          Solicitar acceso
        </button>
      </form>
      {message && <p className={message.ok ? "success-text" : "error-text"}>{message.text}</p>}
      {me.access_requests.length > 0 && (
        <ul className="onb-inbox" style={{ marginTop: 10 }}>
          {me.access_requests.map((r) => (
            <li key={`${r.system_key}-${r.created_at}`}>
              {r.system_name}{" "}
              <span className={`onb-pill ${r.status === "blocked" ? "onb-pill-danger" : "onb-pill-ok"}`}>
                {r.status === "blocked" ? "Bloqueada" : "Solicitada"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** README: "Ofrece a la persona incorporada una vista de su propio progreso y de lo que le falta". */
export default function MyOnboardingPage() {
  const fetchMe = useCallback(() => getMyOnboarding(), []);
  const { data, isLoading, error, reload } = useAsyncData(fetchMe, "No se pudo cargar tu incorporación.");
  const [live, setLive] = useState<MyOnboarding | null>(null);
  const [pendingSince, setPendingSince] = useState<number | null>(null);
  const [inboxKey, setInboxKey] = useState(0);
  const me = live ?? data;

  const refresh = useCallback(async () => {
    try {
      setLive(await getMyOnboarding());
    } catch {
      // Refresco silencioso.
    }
  }, []);
  usePolling(refresh, shouldPoll(me?.phase, pendingSince), POLL_INTERVAL_MS);

  const signature = me ? progressSignature({ ...me, completed_stages: me.stages.filter((s) => s.status === "completed").map((s) => s.number) }) : "";
  const sentSignature = useRef<string | null>(null);
  useEffect(() => {
    if (pendingSince !== null && sentSignature.current !== null && signature !== sentSignature.current) {
      setPendingSince(null);
      sentSignature.current = null;
      setInboxKey((k) => k + 1);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, [signature, pendingSince]);

  const onSent = () => {
    sentSignature.current = signature;
    setPendingSince(Date.now());
    void refresh();
  };
  const onSideAction = () => {
    void refresh();
    setInboxKey((k) => k + 1);
  };

  const result = me?.last_check_result;
  return (
    <main className="onb-grid">
      <AsyncSection isLoading={isLoading} error={error} onRetry={reload} loadingLabel="Cargando tu incorporación…">
        {me && (
          <>
            <header>
              <h1 style={{ marginBottom: 4 }}>Mi incorporación</h1>
              <p className="onb-muted" style={{ marginTop: 0 }}>
                {me.full_name} · {me.job_title} · {PHASE_LABELS[me.phase]} · te faltan {me.remaining_stages.length} de 7 etapas
              </p>
              {pendingSince !== null && <span className="onb-pill">El agente está procesando tu respuesta…</span>}
            </header>

            {me.status === "completed" && <div className="onb-banner onb-banner-ok">¡Has completado tu incorporación!</div>}
            {me.rejection && (
              <div className="onb-banner onb-banner-danger">
                La etapa {me.rejection.stage} ha vuelto al inicio. Comentario de quien aprueba: {me.rejection.comment}
              </div>
            )}
            {result && !result.passed && me.content?.reinforcement && (
              <div className="onb-banner onb-banner-warn">
                Intento {result.attempt}: {formatScore(result)}. Repasa los puntos de abajo y vuelve a intentarlo con preguntas nuevas.
              </div>
            )}
            {result?.passed && me.waiting_for?.kind !== "check" && result.stage === me.current_stage && (
              <div className="onb-banner onb-banner-ok">Comprobación superada: {formatScore(result)}.</div>
            )}
            {me.waiting_for?.kind === "paperwork" && (
              <div className="onb-banner">El equipo de Personas está confirmando tus trámites. No tienes que hacer nada.</div>
            )}
            {me.waiting_for?.kind === "approval" && (
              <div className="onb-banner">
                Esperando la aprobación de {APPROVER_LABELS[me.waiting_for.approver ?? ""] ?? "una persona"}. Te avisaremos en tu bandeja.
              </div>
            )}

            <div className="onb-two-cols">
              <div className="onb-grid">
                {me.content && (
                  <section className="panel">
                    <h2 style={{ marginTop: 0 }}>{me.content.title}</h2>
                    {me.content.reinforcement && <p className="onb-muted">Refuerzo: solo las secciones de las preguntas falladas.</p>}
                    {me.content.sections.map((section) => (
                      <article key={section.citation} className={`onb-section ${me.content!.reinforcement ? "onb-reinforce" : ""}`}>
                        <h4>{section.title}</h4>
                        <p style={{ margin: "0 0 4px" }}>{section.body}</p>
                        <span className="onb-citation">{section.citation}</span>
                      </article>
                    ))}
                  </section>
                )}
                {me.check && me.waiting_for?.kind === "check" && pendingSince === null && <CheckForm me={me} onSent={onSent} />}
                <QuestionBox me={me} onAsked={onSideAction} />
              </div>
              <div className="onb-grid">
                <section className="panel">
                  <h3 style={{ marginTop: 0 }}>Tu progreso</h3>
                  <StageTimeline stages={me.stages} />
                </section>
                <AccessBox me={me} onRequested={onSideAction} />
                <NotificationInbox refreshKey={inboxKey} />
              </div>
            </div>
          </>
        )}
      </AsyncSection>
    </main>
  );
}
