/**
 * Utilidades puras del dashboard de negocio /reporting
 * (Reporte Mensual de Desempeño de Insumos por Clínica).
 */
import {
  KPI_DEFINITIONS,
  clinicLabel,
  clinicsWithUnrecordedCost,
  countryLabel,
  describeLastUpdate,
  describeReportNotices,
  formatMonthLabel,
  groupCostByCurrency,
  lastDayOfMonth,
  networkTotal,
  rankClinicsByKpi,
  toMonthInputValue,
  toMonthStartParam,
  type ClinicSupplyPerformance,
  type PipelineRunStatus,
} from "../types/businessReport";

const CLINICS: ClinicSupplyPerformance[] = [
  { clinic_id: "1", country: "US", total_supply_cost: 18420.5, supply_consumption_count: 340, critical_stockout_count: 1, expiry_risk_count: 4, currency: "USD" },
  { clinic_id: "7", country: "US", total_supply_cost: 0.1, supply_consumption_count: 12, critical_stockout_count: 3, expiry_risk_count: 0, currency: "USD" },
  { clinic_id: "10", country: "UK", total_supply_cost: 9210, supply_consumption_count: 190, critical_stockout_count: 0, expiry_risk_count: 2, currency: "GBP" },
];

const RUN: PipelineRunStatus = {
  run_id: "run-1",
  status: "completed",
  month_start: "2026-08-01",
  started_at: "2026-09-01T02:00:00Z",
  finished_at: "2026-09-01T02:00:05Z",
  records_processed: 4,
  warnings: [],
  clinics_with_unrecorded_cost: [],
  is_stale: false,
};

describe("KPI_DEFINITIONS", () => {
  it("usa exactamente los nombres de la sección 'KPIs a medir' del CONTEXT, en su orden", () => {
    expect(KPI_DEFINITIONS.map((kpi) => [kpi.key, kpi.name])).toEqual([
      ["total_supply_cost", "Costo de insumos por clínica"],
      ["supply_consumption_count", "Volumen de consumo de insumos"],
      ["critical_stockout_count", "Frecuencia de quiebre crítico"],
      ["expiry_risk_count", "Conteo de riesgo de vencimiento"],
    ]);
  });
});

describe("etiquetas legibles", () => {
  it("formatea el mes del informe en español con mayúscula (camino feliz)", () => {
    expect(formatMonthLabel("2026-08-01")).toBe("Agosto de 2026");
  });

  it("calcula el último día del mes, incluido febrero bisiesto (límite)", () => {
    expect(lastDayOfMonth("2026-08-01")).toBe(31);
    expect(lastDayOfMonth("2028-02-01")).toBe(29);
  });

  it("nombra clínicas y países sin códigos técnicos", () => {
    expect(clinicLabel("7")).toBe("Clínica 7");
    expect(countryLabel("US")).toBe("EE. UU.");
    expect(countryLabel("UK")).toBe("Reino Unido");
  });

  it("convierte entre el selector de mes y el parámetro de la API, rechazando valores inválidos (fallo)", () => {
    expect(toMonthStartParam("2026-08")).toBe("2026-08-01");
    expect(toMonthStartParam("")).toBeNull();
    expect(toMonthStartParam("2026-13")).toBeNull();
    expect(toMonthInputValue("2026-08-01")).toBe("2026-08");
  });
});

describe("groupCostByCurrency", () => {
  it("separa USD y GBP con su propio total y nunca los suma entre sí", () => {
    const groups = groupCostByCurrency(CLINICS);

    expect(groups.map((group) => [group.currency, group.country, group.total])).toEqual([
      ["USD", "US", 18420.6],
      ["GBP", "UK", 9210],
    ]);
    expect(groups[0].entries.map((entry) => entry.clinicId)).toEqual(["1", "7"]);
  });

  it("no crea un grupo vacío si todas las clínicas son de un mismo país (límite)", () => {
    expect(groupCostByCurrency(CLINICS.filter((clinic) => clinic.country === "UK")).map((group) => group.currency)).toEqual(["GBP"]);
  });
});

describe("KPIs de conteo", () => {
  it("ordena clínicas de mayor a menor y suma el total de la red", () => {
    expect(rankClinicsByKpi(CLINICS, "critical_stockout_count").map((entry) => [entry.clinicId, entry.value])).toEqual([
      ["7", 3],
      ["1", 1],
      ["10", 0],
    ]);
    expect(networkTotal(CLINICS, "expiry_risk_count")).toBe(6);
  });
});

describe("describeReportNotices", () => {
  it("no muestra avisos si la última actualización del mes fue limpia (camino feliz)", () => {
    expect(describeReportNotices(RUN, "2026-08-01")).toEqual([]);
  });

  it("avisa en lenguaje llano si el último mes cerrado no está calculado (alerta de silencio)", () => {
    const notices = describeReportNotices({ ...RUN, is_stale: true }, "2026-07-01");
    expect(notices).toHaveLength(1);
    expect(notices[0].message).toMatch(/todavía no se ha generado/);
  });

  it("avisa de captura incompleta solo en el mes de esa actualización y oculta avisos técnicos", () => {
    const run = { ...RUN, status: "completed_with_warnings" as const, warnings: ["low_capture_ratio:1:inbound", "eval_snapshot_failed"] };

    expect(describeReportNotices(run, "2026-08-01").map((notice) => notice.message)).toEqual([
      "Parte de la actividad de inventario de este mes no quedó registrada en el sistema de medición: algunas cifras pueden estar por debajo de la realidad.",
    ]);
    expect(describeReportNotices(run, "2026-07-01")).toEqual([]);
    expect(JSON.stringify(describeReportNotices(run, "2026-08-01"))).not.toMatch(/low_capture|eval_snapshot/);
  });

  it("avisa de compras sin coste registrado: ese costo está incompleto, no es cero (límite)", () => {
    const run = { ...RUN, clinics_with_unrecorded_cost: ["1", "3"] };

    expect(describeReportNotices(run, "2026-08-01").map((notice) => notice.message)).toEqual([
      "Algunas compras de este mes se registraron sin coste (Clínica 1, Clínica 3): el costo de insumos de esas clínicas está incompleto, no es un gasto cero.",
    ]);
    expect(clinicsWithUnrecordedCost(run, "2026-07-01")).toEqual([]);
  });

  it("marca como crítica una actualización fallida del mes mostrado (fallo)", () => {
    const notices = describeReportNotices({ ...RUN, status: "failed", finished_at: null }, "2026-08-01");
    expect(notices).toEqual([expect.objectContaining({ tone: "critical" })]);
  });
});

describe("describeLastUpdate", () => {
  it("da la fecha de la última actualización correcta del mes mostrado, y nada si es de otro mes o falló", () => {
    expect(describeLastUpdate(RUN, "2026-08-01")).toMatch(/1 de septiembre de 2026.*\(UTC\)/);
    expect(describeLastUpdate(RUN, "2026-07-01")).toBeNull();
    expect(describeLastUpdate({ ...RUN, status: "failed" }, "2026-08-01")).toBeNull();
  });
});
