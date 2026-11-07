import type {
  MyOnboarding,
  OnboardingDetail,
  OnboardingListItem,
  OnboardingNotification,
  OnboardingQuestion,
  OnboardingRole,
} from "../types/onboarding";
import { requestJson } from "./http";

/**
 * Orquestador de incorporaciones (services/onboarding). Las acciones que
 * avanzan el flujo (trámites, respuestas, aprobaciones) responden 202: la API
 * las valida y las guarda, y el agente las aplica en el worker. Las pantallas
 * consultan unos segundos después. Un 400 llega como ApiFieldError (por campo).
 */
const auth = { authRequired: true };

export function getOnboardingRole(): Promise<OnboardingRole> {
  return requestJson<OnboardingRole>("/onboarding/whoami", undefined, auth);
}

export function listOnboardings(): Promise<OnboardingListItem[]> {
  return requestJson<OnboardingListItem[]>("/onboarding/hires", undefined, auth);
}

export function getOnboarding(hireId: string): Promise<OnboardingDetail> {
  return requestJson<OnboardingDetail>(`/onboarding/hires/${encodeURIComponent(hireId)}`, undefined, auth);
}

export function markPaperwork(hireId: string, marks: Record<string, boolean>): Promise<unknown> {
  return requestJson(`/onboarding/hires/${encodeURIComponent(hireId)}/paperwork`, { method: "POST", body: JSON.stringify(marks) }, auth);
}

export function decideStage(hireId: string, stage: number, decision: "approve" | "reject", comment: string): Promise<unknown> {
  const body = { stage, decision, comment: comment.trim() || null };
  return requestJson(`/onboarding/hires/${encodeURIComponent(hireId)}/approvals`, { method: "POST", body: JSON.stringify(body) }, auth);
}

export function getMyOnboarding(): Promise<MyOnboarding> {
  return requestJson<MyOnboarding>("/onboarding/me", undefined, auth);
}

export function submitCheck(stage: number, attempt: number, answers: Record<string, number>): Promise<unknown> {
  return requestJson("/onboarding/me/check", { method: "POST", body: JSON.stringify({ stage, attempt, answers }) }, auth);
}

export function askQuestion(question: string): Promise<OnboardingQuestion> {
  return requestJson<OnboardingQuestion>("/onboarding/me/questions", { method: "POST", body: JSON.stringify({ question }) }, auth);
}

export function requestAccess(systemKey: string): Promise<{ system_key: string; status: string }> {
  return requestJson("/onboarding/me/access-requests", { method: "POST", body: JSON.stringify({ system_key: systemKey }) }, auth);
}

export function listMyNotifications(): Promise<OnboardingNotification[]> {
  return requestJson<OnboardingNotification[]>("/onboarding/notifications", undefined, auth);
}
