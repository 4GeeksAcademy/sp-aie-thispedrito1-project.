"use client";

import "./onboarding.css";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { AsyncSection } from "../../components/AsyncSection";
import { NotificationInbox } from "../../components/onboarding/NotificationInbox";
import { useAsyncData } from "../../hooks/useAsyncData";
import { getOnboardingRole, listOnboardings } from "../../services/onboardingApi";
import {
  DEPARTMENT_LABELS,
  FRAMEWORK_LABELS,
  PHASE_LABELS,
  SITE_LABELS,
  formatDue,
  isOverdue,
  type OnboardingListItem,
} from "../../types/onboarding";

type Filter = "active" | "blocked" | "completed" | "all";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "active", label: "Activas" },
  { key: "blocked", label: "Bloqueadas" },
  { key: "completed", label: "Completadas" },
  { key: "all", label: "Todas" },
];

function matches(item: OnboardingListItem, filter: Filter): boolean {
  if (filter === "active") return item.status === "active";
  if (filter === "blocked") return item.blocked;
  if (filter === "completed") return item.status === "completed";
  return true;
}

/**
 * Vista del equipo de Personas (README: "qué incorporaciones están activas,
 * en qué etapa está cada una y cuáles están bloqueadas"). Un responsable
 * directo ve solo su equipo; una persona incorporada, el enlace a la suya.
 */
export default function OnboardingTeamPage() {
  const fetchRole = useCallback(() => getOnboardingRole(), []);
  const role = useAsyncData(fetchRole, "No se pudo comprobar tu rol en el onboarding.");
  const canList = Boolean(role.data && (role.data.can_view_all || role.data.can_view_reports));
  const fetchList = useCallback(() => (canList ? listOnboardings() : Promise.resolve([] as OnboardingListItem[])), [canList]);
  const list = useAsyncData(fetchList, "No se pudieron cargar las incorporaciones.");
  const [filter, setFilter] = useState<Filter>("active");

  const items = useMemo(() => (list.data ?? []).filter((item) => matches(item, filter)), [list.data, filter]);
  const counts = useMemo(() => {
    const all = list.data ?? [];
    return { active: all.filter((i) => i.status === "active").length, blocked: all.filter((i) => i.blocked).length };
  }, [list.data]);

  return (
    <main className="onb-grid">
      <header>
        <h1 style={{ marginBottom: 4 }}>Incorporaciones</h1>
        <p className="onb-muted" style={{ marginTop: 0 }}>
          Onboarding corporativo en 7 etapas (plazo objetivo: 10 días hábiles). El agente presenta el material, corrige las
          comprobaciones y avisa; las aprobaciones son siempre de una persona.
        </p>
      </header>

      <AsyncSection isLoading={role.isLoading} error={role.error} onRetry={role.reload} loadingLabel="Comprobando tu rol…">
        {role.data?.is_hire && (
          <div className="onb-banner onb-banner-ok">
            Tienes una incorporación en curso. <Link href="/onboarding/me">Ver mi incorporación →</Link>
          </div>
        )}
        {!canList && !role.data?.is_hire && (
          <div className="onb-banner">Solo el equipo de Personas, Cumplimiento y los responsables directos ven las incorporaciones.</div>
        )}
      </AsyncSection>

      {canList && (
        <section className="panel">
          <div className="onb-actions" style={{ justifyContent: "space-between", marginBottom: 12 }}>
            <div className="onb-actions" role="tablist" aria-label="Filtrar incorporaciones">
              {FILTERS.map((f) => (
                <button key={f.key} type="button" role="tab" aria-selected={filter === f.key} className={filter === f.key ? "primary" : ""} onClick={() => setFilter(f.key)}>
                  {f.label}
                  {f.key === "active" ? ` (${counts.active})` : f.key === "blocked" ? ` (${counts.blocked})` : ""}
                </button>
              ))}
            </div>
            <button type="button" onClick={list.reload}>
              Actualizar
            </button>
          </div>
          <AsyncSection
            isLoading={list.isLoading}
            error={list.error}
            onRetry={list.reload}
            loadingLabel="Cargando incorporaciones…"
            isEmpty={items.length === 0}
            emptyLabel="No hay incorporaciones en este filtro."
          >
            <div className="onb-table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Persona</th>
                    <th>Departamento · sede</th>
                    <th>Etapa actual</th>
                    <th>Vence</th>
                    <th>Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.hire_id}>
                      <td>
                        <Link href={`/onboarding/${item.hire_id}`}>
                          <strong>{item.full_name}</strong>
                        </Link>
                        <div className="onb-muted">{item.job_title}</div>
                      </td>
                      <td>
                        {DEPARTMENT_LABELS[item.department] ?? item.department}
                        <div className="onb-muted">
                          {SITE_LABELS[item.site] ?? item.site} · {FRAMEWORK_LABELS[item.framework] ?? item.framework} · {item.language.toUpperCase()}
                        </div>
                      </td>
                      <td>
                        {item.status === "completed" ? "—" : `${item.current_stage}. ${item.current_stage_title}`}
                        <div className="onb-muted">{item.completed_stages.length}/7 completadas</div>
                      </td>
                      <td className={isOverdue(item.current_due_at) ? "error-text" : ""}>{formatDue(item.current_due_at)}</td>
                      <td>
                        <span className={`onb-pill ${item.status === "completed" ? "onb-pill-ok" : item.blocked ? "onb-pill-danger" : "onb-pill-brand"}`}>
                          {item.blocked ? "Bloqueada" : PHASE_LABELS[item.phase]}
                        </span>
                        {item.blocked_reasons.map((reason) => (
                          <div key={reason} className="onb-muted">
                            {reason}
                          </div>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </AsyncSection>
        </section>
      )}

      <NotificationInbox />
    </main>
  );
}
