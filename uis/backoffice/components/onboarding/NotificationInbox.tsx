"use client";

import { useCallback } from "react";

import { useAsyncData } from "../../hooks/useAsyncData";
import { listMyNotifications } from "../../services/onboardingApi";
import { AsyncSection } from "../AsyncSection";

/** Bandeja de salida del agente para la persona que mira (sin correos reales). */
export function NotificationInbox({ refreshKey = 0 }: { refreshKey?: number }) {
  // refreshKey cambia tras cada acción: vuelve a pedir la bandeja.
  const fetchInbox = useCallback(() => {
    void refreshKey;
    return listMyNotifications();
  }, [refreshKey]);
  const { data, isLoading, error, reload } = useAsyncData(fetchInbox, "No se pudo cargar tu bandeja.");
  return (
    <section className="panel">
      <h3 style={{ marginTop: 0 }}>Tu bandeja</h3>
      <p className="onb-muted">Avisos que el agente te ha enviado (remitente: onboarding-orchestrator).</p>
      <AsyncSection isLoading={isLoading} error={error} onRetry={reload} loadingLabel="Cargando avisos…" isEmpty={(data ?? []).length === 0} emptyLabel="Sin avisos.">
        <ul className="onb-inbox">
          {(data ?? []).map((note) => (
            <li key={note.id}>
              <strong>{note.subject}</strong>
              <div>{note.body}</div>
              <div className="onb-muted">{new Date(note.created_at).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" })}</div>
            </li>
          ))}
        </ul>
      </AsyncSection>
    </section>
  );
}
