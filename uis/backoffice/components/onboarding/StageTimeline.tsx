import { APPROVER_LABELS, formatDue, type OnboardingStage } from "../../types/onboarding";

const STATUS_TEXT = { completed: "Completada", current: "En curso", upcoming: "Pendiente" } as const;

/** Las 7 etapas del BRIEFING con su plazo (Día N, en días hábiles) y quién aprueba. */
export function StageTimeline({ stages }: { stages: OnboardingStage[] }) {
  return (
    <ol className="onb-stages" aria-label="Etapas del onboarding">
      {stages.map((stage) => (
        <li key={stage.number} className={`onb-stage onb-stage-${stage.status}`}>
          <span className="onb-stage-number">{stage.number}</span>
          <div>
            <strong>{stage.title}</strong>
            <div className="onb-stage-meta">
              Día {stage.deadline_day} · vence {formatDue(stage.due_at)}
              {stage.approver ? ` · aprueba ${APPROVER_LABELS[stage.approver] ?? stage.approver}` : " · automática"}
              {stage.pass_threshold ? ` · umbral ${Math.round(stage.pass_threshold * 100)} %` : ""}
            </div>
          </div>
          <span className={`onb-pill ${stage.status === "completed" ? "onb-pill-ok" : stage.status === "current" ? "onb-pill-brand" : ""}`}>
            {STATUS_TEXT[stage.status]}
          </span>
        </li>
      ))}
    </ol>
  );
}
