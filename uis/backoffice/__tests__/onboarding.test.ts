import { isActivePath } from "../types/navigation";
import {
  actionLabel,
  buildAnswers,
  formatScore,
  isOverdue,
  progressSignature,
  routedLabel,
  shouldPoll,
  type CheckQuestion,
} from "../types/onboarding";

const questions: CheckQuestion[] = [
  { id: "q1", question: "¿Uno?", options: ["a", "b"] },
  { id: "q2", question: "¿Dos?", options: ["a", "b", "c"] },
];

describe("buildAnswers", () => {
  it("devuelve las respuestas cuando todas están contestadas (incluida la opción 0)", () => {
    expect(buildAnswers(questions, { q1: 0, q2: 2 })).toEqual({ answers: { q1: 0, q2: 2 } });
  });

  it("lista las preguntas sin responder en vez de enviar un intento incompleto", () => {
    expect(buildAnswers(questions, { q1: 1 })).toEqual({ missing: ["q2"] });
  });
});

describe("shouldPoll", () => {
  it("consulta mientras la incorporación arranca en el worker", () => {
    expect(shouldPoll("starting", null)).toBe(true);
  });

  it("consulta tras enviar una acción, y deja de hacerlo pasado un minuto", () => {
    expect(shouldPoll("awaiting_check", 1_000, 5_000)).toBe(true);
    expect(shouldPoll("awaiting_check", 1_000, 70_000)).toBe(false);
  });

  it("no consulta en reposo", () => {
    expect(shouldPoll("awaiting_approval", null)).toBe(false);
  });
});

describe("progressSignature", () => {
  const base = {
    phase: "awaiting_check" as const,
    current_stage: 2,
    waiting_for: { kind: "check" as const, stage: 2, attempt: 1 },
    completed_stages: [1],
  };

  it("cambia con un nuevo intento de la misma etapa (suspenso)", () => {
    const failed = { ...base, waiting_for: { ...base.waiting_for, attempt: 2 } };
    expect(progressSignature(failed)).not.toBe(progressSignature(base));
  });

  it("cambia con una marca parcial de trámites aunque la etapa no avance", () => {
    const paperwork = { ...base, phase: "awaiting_paperwork" as const, current_stage: 1, waiting_for: { kind: "paperwork" as const, stage: 1 } };
    expect(progressSignature({ ...paperwork, paperwork: { contract_received: true } })).not.toBe(
      progressSignature({ ...paperwork, paperwork: {} }),
    );
  });

  it("cambia cuando un rechazo devuelve la etapa al inicio", () => {
    const rejected = { ...base, rejection: { stage: 2, comment: "Repasa", round: 1 } };
    expect(progressSignature(rejected)).not.toBe(progressSignature(base));
  });
});

describe("textos", () => {
  it("formatea la nota de una comprobación", () => {
    expect(formatScore({ score: 0.8, passed: true, correct: 4, total: 5, attempt: 1, failed_sections: [] })).toBe("4/5 (80 %)");
  });

  it("explica a quién se derivó una duda", () => {
    expect(routedLabel("compliance")).toContain("Claire Whitfield");
    expect(routedLabel("vp_people")).toContain("Diane Foster");
    expect(routedLabel(null)).toContain("una persona");
  });

  it("traduce las acciones de auditoría, también los avisos", () => {
    expect(actionLabel("patient_data_access_blocked")).toContain("BLOQUEADO");
    expect(actionLabel("notification_reminder")).toBe("Aviso enviado (reminder)");
    expect(actionLabel("accion_nueva")).toBe("accion_nueva");
  });

  it("detecta una etapa vencida", () => {
    expect(isOverdue("2026-11-04T06:00:00Z", Date.parse("2026-11-04T06:00:01Z"))).toBe(true);
    expect(isOverdue("2026-11-04T06:00:00Z", Date.parse("2026-11-04T05:59:59Z"))).toBe(false);
    expect(isOverdue(null)).toBe(false);
  });
});

describe("menú", () => {
  it("marca 'Mi incorporación' en /onboarding/me y 'Incorporaciones' en la ficha de una persona", () => {
    expect(isActivePath("/onboarding/me", "/onboarding/me")).toBe(true);
    expect(isActivePath("/onboarding/me", "/onboarding")).toBe(false);
    expect(isActivePath("/onboarding/123", "/onboarding")).toBe(true);
  });
});
