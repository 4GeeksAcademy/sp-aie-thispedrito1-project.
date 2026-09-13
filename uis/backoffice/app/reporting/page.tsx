"use client";

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";

import { AsyncSection } from "../../components/AsyncSection";
import { BarList } from "../../components/BarList";
import { useAsyncData } from "../../hooks/useAsyncData";
import { getLatestPipelineRun, getMonthlyClinicSupplyPerformance } from "../../services/reportingApi";
import {
  KPI_DEFINITIONS,
  clinicLabel,
  clinicsWithUnrecordedCost,
  countryLabel,
  describeLastUpdate,
  describeReportNotices,
  formatCount,
  formatMoney,
  formatMonthLabel,
  groupCostByCurrency,
  lastDayOfMonth,
  networkTotal,
  rankClinicsByKpi,
  toMonthInputValue,
  toMonthStartParam,
  type ClinicSupplyPerformance,
  type KpiDefinition,
  type MonthlyClinicSupplyPerformance,
  type PipelineRunStatus,
} from "../../types/businessReport";

type DashboardData = {
  report: MonthlyClinicSupplyPerformance | null;
  latestRun: PipelineRunStatus | null;
};

/** Cabecera de la columna de valor y tono de las barras de cada KPI de conteo. */
const COUNT_KPI_PRESENTATION: Record<string, { column: string; tone: "brand" | "critical" | "warning"; none: string }> = {
  supply_consumption_count: {
    column: "Consumos registrados",
    tone: "brand",
    none: "Ninguna clínica registró consumos de insumos este mes.",
  },
  critical_stockout_count: {
    column: "Quiebres críticos",
    tone: "critical",
    none: "Ninguna clínica cayó por debajo del umbral mínimo de un insumo este mes.",
  },
  expiry_risk_count: {
    column: "Lotes en riesgo",
    tone: "warning",
    none: "Ningún lote fue marcado por acercarse a su fecha de vencimiento este mes.",
  },
};

function KpiPanel({ definition, children }: { definition: KpiDefinition; children: ReactNode }) {
  return (
    <section className="panel" aria-labelledby={`kpi-${definition.key}`}>
      <h2 id={`kpi-${definition.key}`} style={{ marginTop: 0, marginBottom: 4, fontSize: 18 }}>
        {definition.name}
      </h2>
      <p style={{ marginTop: 0, color: "var(--muted)", fontSize: 14 }}>{definition.description}</p>
      {children}
    </section>
  );
}

function SupplyCostPanel({
  definition,
  clinics,
  unrecordedCost,
}: {
  definition: KpiDefinition;
  clinics: ClinicSupplyPerformance[];
  /** Clínicas con compras sin coste registrado: su importe está incompleto. */
  unrecordedCost: string[];
}) {
  const groups = groupCostByCurrency(clinics);

  return (
    <KpiPanel definition={definition}>
      {groups.map((group) => (
        <div key={group.currency} style={{ marginBottom: 18 }}>
          <h3 style={{ fontSize: 15, margin: "0 0 10px" }}>
            Clínicas de {countryLabel(group.country)} · importes en {group.currency}
          </h3>
          <BarList
            tone="brand"
            monoLabels={false}
            max={group.entries[0]?.value ?? 0}
            items={group.entries.map((entry) => ({
              id: entry.clinicId,
              label: clinicLabel(entry.clinicId),
              value: entry.value,
              display: formatMoney(entry.value, group.currency),
            }))}
          />
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Clínica</th>
                  <th>Costo de insumos ({group.currency})</th>
                </tr>
              </thead>
              <tbody>
                {group.entries.map((entry) => (
                  <tr key={entry.clinicId}>
                    <td>{clinicLabel(entry.clinicId)}</td>
                    <td>
                      <span className="mono">{formatMoney(entry.value, group.currency)}</span>
                      {unrecordedCost.includes(entry.clinicId) && (
                        <span style={{ display: "block", color: "var(--warning)", fontSize: 12 }}>
                          Incompleto: incluye compras sin coste registrado
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
                <tr>
                  <td>
                    <strong>Total {countryLabel(group.country)}</strong>
                  </td>
                  <td className="mono">
                    <strong>{formatMoney(group.total, group.currency)}</strong>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      ))}
      {groups.length > 1 && (
        <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
          Los importes en USD y en GBP se muestran por separado y no se suman entre sí.
        </p>
      )}
    </KpiPanel>
  );
}

function CountKpiPanel({ definition, clinics }: { definition: KpiDefinition; clinics: ClinicSupplyPerformance[] }) {
  const presentation = COUNT_KPI_PRESENTATION[definition.key];
  const entries = rankClinicsByKpi(clinics, definition.key);
  const total = networkTotal(clinics, definition.key);

  return (
    <KpiPanel definition={definition}>
      {total === 0 ? (
        <p style={{ marginTop: 0 }}>{presentation.none}</p>
      ) : (
        <BarList
          tone={presentation.tone}
          monoLabels={false}
          max={entries[0]?.value ?? 0}
          items={entries.map((entry) => ({
            id: entry.clinicId,
            label: `${clinicLabel(entry.clinicId)} · ${countryLabel(entry.country)}`,
            value: entry.value,
            display: formatCount(entry.value),
          }))}
        />
      )}
      <div style={{ overflowX: "auto" }}>
        <table className="table">
          <thead>
            <tr>
              <th>Clínica</th>
              <th>País</th>
              <th>{presentation.column}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.clinicId}>
                <td>{clinicLabel(entry.clinicId)}</td>
                <td>{countryLabel(entry.country)}</td>
                <td className="mono">{formatCount(entry.value)}</td>
              </tr>
            ))}
            <tr>
              <td colSpan={2}>
                <strong>Total de la red</strong>
              </td>
              <td className="mono">
                <strong>{formatCount(total)}</strong>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </KpiPanel>
  );
}

function NoticeList({ run, monthStart }: { run: PipelineRunStatus | null; monthStart: string | null }) {
  const notices = describeReportNotices(run, monthStart);
  if (notices.length === 0) return null;

  return (
    <div role="status" style={{ display: "grid", gap: 8, marginBottom: 16 }}>
      {notices.map((notice) => (
        <p
          key={notice.message}
          className="panel"
          style={{
            margin: 0,
            borderLeft: `4px solid ${notice.tone === "critical" ? "var(--critical)" : "var(--warning)"}`,
          }}
        >
          <strong>Aviso: </strong>
          {notice.message}
        </p>
      ))}
    </div>
  );
}

function ReportView({ report, latestRun }: { report: MonthlyClinicSupplyPerformance; latestRun: PipelineRunStatus | null }) {
  const monthLabel = formatMonthLabel(report.month_start);
  const lastUpdate = describeLastUpdate(latestRun, report.month_start);

  return (
    <>
      <div className="panel" style={{ marginBottom: 16, display: "grid", gap: 4 }}>
        <span>
          <span style={{ color: "var(--muted)" }}>Período del informe: </span>
          <strong>{monthLabel}</strong>
          <span style={{ color: "var(--muted)" }}>
            {" "}
            · del 1 al {lastDayOfMonth(report.month_start)} (hora UTC)
          </span>
        </span>
        <span style={{ color: "var(--muted)", fontSize: 13 }}>
          {report.clinics.length === 1 ? "1 clínica" : `${report.clinics.length} clínicas`} con actividad registrada
          en el mes. Las clínicas sin actividad registrada no aparecen.
          {lastUpdate ? ` Última actualización: ${lastUpdate}.` : ""}
        </span>
      </div>

      <NoticeList run={latestRun} monthStart={report.month_start} />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 420px), 1fr))",
          gap: 16,
          alignItems: "start",
        }}
      >
        {KPI_DEFINITIONS.map((definition) =>
          definition.kind === "money" ? (
            <SupplyCostPanel
              key={definition.key}
              definition={definition}
              clinics={report.clinics}
              unrecordedCost={clinicsWithUnrecordedCost(latestRun, report.month_start)}
            />
          ) : (
            <CountKpiPanel key={definition.key} definition={definition} clinics={report.clinics} />
          ),
        )}
      </div>
    </>
  );
}

export default function BusinessReportingDashboardPage() {
  // `applied` null = "el mes calculado más reciente" (lo decide la API).
  // `draft` es lo que hay escrito en el selector; solo se pide al pulsar "Ver mes".
  const [applied, setApplied] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [monthError, setMonthError] = useState<string | null>(null);

  const fetchDashboard = useCallback(async (): Promise<DashboardData> => {
    // El estado de la última actualización es información secundaria: si
    // falla, el informe se muestra igual, solo sin avisos ni fecha.
    const [report, latestRun] = await Promise.all([
      getMonthlyClinicSupplyPerformance(applied ?? undefined),
      getLatestPipelineRun().catch(() => null),
    ]);
    return { report, latestRun };
  }, [applied]);

  const { data, isLoading, error, reload } = useAsyncData<DashboardData>(
    fetchDashboard,
    "No se pudo cargar el informe mensual. Inténtalo de nuevo en unos minutos.",
  );

  // Cuando llega un informe, el selector muestra el mes que realmente se está
  // viendo (importa sobre todo al abrir la pantalla con "el más reciente").
  // Depende solo de `data` a propósito: si dependiera también de `draft`,
  // borrar el campo lo volvería a rellenar al instante y nunca se podría
  // vaciar (lo detectó el test "no pide nada a la API si no se elige un mes").
  useEffect(() => {
    if (data?.report) {
      setDraft(toMonthInputValue(data.report.month_start));
    }
  }, [data]);

  const showMonth = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const monthStart = toMonthStartParam(draft);
    if (!monthStart) {
      setMonthError("Elige un mes para ver su informe.");
      return;
    }
    setMonthError(null);
    setApplied(monthStart);
  };

  const showLatest = () => {
    setMonthError(null);
    setDraft("");
    setApplied(null);
  };

  const emptyLabel = applied
    ? `Todavía no hay un informe calculado para ${formatMonthLabel(applied)}.`
    : "Todavía no se ha calculado ningún informe mensual.";

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <h1>Reporte Mensual de Desempeño de Insumos por Clínica</h1>
      <p style={{ color: "var(--muted)", maxWidth: 820 }}>
        Consolidado mensual, por clínica y por país, del costo de insumos, la actividad de quiebre de stock y el
        riesgo de vencimiento en las clínicas de la red en EE. UU. y Reino Unido.
      </p>

      {/* noValidate: mismo motivo que en /telemetry, el mensaje de error lo
          muestra la pantalla en español y no un globo nativo del navegador. */}
      <form
        noValidate
        onSubmit={showMonth}
        className="panel"
        style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap", marginBottom: 16 }}
      >
        <label>
          Mes del informe
          <input type="month" value={draft} onChange={(event) => setDraft(event.target.value)} />
        </label>
        <button type="submit" className="primary">
          Ver mes
        </button>
        <button type="button" onClick={showLatest}>
          Último mes calculado
        </button>
        {monthError && (
          <span className="error-text" role="alert" style={{ flexBasis: "100%" }}>
            {monthError}
          </span>
        )}
      </form>

      <AsyncSection
        isLoading={isLoading}
        error={error}
        onRetry={reload}
        loadingLabel="Cargando el informe mensual…"
        isEmpty={!data?.report}
        emptyLabel={
          <>
            {emptyLabel}
            {data && describeReportNotices(data.latestRun, null).map((notice) => ` ${notice.message}`)}
          </>
        }
      >
        {data?.report && <ReportView report={data.report} latestRun={data.latestRun} />}
      </AsyncSection>
    </main>
  );
}
