/**
 * Pantalla /reporting: dashboard de negocio del pipeline de desempeño.
 *
 * Mismo enfoque que telemetryDashboard.test.tsx: sin @testing-library,
 * montando con `act` + `createRoot` en jsdom y con la API sustituida por
 * jest.mock (sin backend ni Supabase).
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import BusinessReportingDashboardPage from "../app/reporting/page";
import { getLatestPipelineRun, getMonthlyClinicSupplyPerformance } from "../services/reportingApi";
import type { MonthlyClinicSupplyPerformance, PipelineRunStatus } from "../types/businessReport";

jest.mock("../services/reportingApi", () => ({
  getMonthlyClinicSupplyPerformance: jest.fn(),
  getLatestPipelineRun: jest.fn(),
}));

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const mockedGetReport = getMonthlyClinicSupplyPerformance as jest.MockedFunction<typeof getMonthlyClinicSupplyPerformance>;
const mockedGetLatestRun = getLatestPipelineRun as jest.MockedFunction<typeof getLatestPipelineRun>;

const REPORT: MonthlyClinicSupplyPerformance = {
  month_start: "2026-08-01",
  clinics: [
    { clinic_id: "3", country: "US", total_supply_cost: 18420.5, supply_consumption_count: 340, critical_stockout_count: 1, expiry_risk_count: 4, currency: "USD" },
    { clinic_id: "10", country: "UK", total_supply_cost: 9210, supply_consumption_count: 190, critical_stockout_count: 0, expiry_risk_count: 0, currency: "GBP" },
  ],
};

const RUN: PipelineRunStatus = {
  run_id: "run-1",
  status: "completed_with_warnings",
  month_start: "2026-08-01",
  started_at: "2026-09-01T02:00:00Z",
  finished_at: "2026-09-01T02:00:05Z",
  records_processed: 530,
  warnings: ["low_capture_ratio:3:inbound"],
  clinics_with_unrecorded_cost: ["3"],
  is_stale: false,
};

let container: HTMLDivElement;
let root: Root;

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

async function renderPage() {
  await act(async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    root.render(<BusinessReportingDashboardPage />);
  });
  await flush();
}

function typeInto(input: HTMLInputElement, value: string) {
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setValue?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function buttonByText(text: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === text);
  if (!button) throw new Error(`No hay botón "${text}"`);
  return button;
}

function panelFor(kpiName: string): HTMLElement {
  const panel = Array.from(container.querySelectorAll("section")).find((section) => section.querySelector("h2")?.textContent === kpiName);
  if (!panel) throw new Error(`No hay panel "${kpiName}"`);
  return panel;
}

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  mockedGetReport.mockReset();
  mockedGetLatestRun.mockReset();
});

describe("BusinessReportingDashboardPage", () => {
  it("muestra un panel por cada KPI del CONTEXT con su nombre exacto y el período (camino feliz)", async () => {
    mockedGetReport.mockResolvedValue(REPORT);
    mockedGetLatestRun.mockResolvedValue(RUN);
    await renderPage();

    expect(mockedGetReport).toHaveBeenCalledWith(undefined);
    const text = container.textContent ?? "";
    expect(text).toContain("Reporte Mensual de Desempeño de Insumos por Clínica");
    expect(text).toContain("Agosto de 2026");
    expect(text).toContain("del 1 al 31");
    expect(Array.from(container.querySelectorAll("section h2")).map((h2) => h2.textContent)).toEqual([
      "Costo de insumos por clínica",
      "Volumen de consumo de insumos",
      "Frecuencia de quiebre crítico",
      "Conteo de riesgo de vencimiento",
    ]);
    expect((container.querySelector('input[type="month"]') as HTMLInputElement).value).toBe("2026-08");
  });

  it("muestra el costo por moneda sin sumar USD y GBP, y los conteos con total de red", async () => {
    mockedGetReport.mockResolvedValue(REPORT);
    mockedGetLatestRun.mockResolvedValue(RUN);
    await renderPage();

    const cost = panelFor("Costo de insumos por clínica").textContent ?? "";
    expect(cost).toContain("Clínicas de EE. UU. · importes en USD");
    expect(cost).toContain("Clínicas de Reino Unido · importes en GBP");
    expect(cost).toContain("no se suman entre sí");
    expect(cost).not.toContain("27.630"); // 18.420,50 + 9.210 nunca aparece
    // Clínica 3 tiene compras sin coste: su importe se marca como incompleto; la 10 no.
    const costRows = Array.from(panelFor("Costo de insumos por clínica").querySelectorAll("tbody tr"));
    expect(costRows.find((row) => row.textContent?.startsWith("Clínica 3"))?.textContent).toContain("Incompleto");
    expect(costRows.find((row) => row.textContent?.startsWith("Clínica 10"))?.textContent).not.toContain("Incompleto");

    const consumption = panelFor("Volumen de consumo de insumos").textContent ?? "";
    expect(consumption).toContain("Clínica 3");
    expect(consumption).toContain("Total de la red");
    expect(consumption).toContain("530");

    expect(panelFor("Conteo de riesgo de vencimiento").textContent).toContain("Clínica 3");
  });

  it("avisa en lenguaje de negocio de la captura incompleta, sin códigos técnicos", async () => {
    mockedGetReport.mockResolvedValue(REPORT);
    mockedGetLatestRun.mockResolvedValue(RUN);
    await renderPage();

    const status = container.querySelector('[role="status"]')?.textContent ?? "";
    expect(status).toMatch(/algunas cifras pueden estar por debajo de la realidad/);
    expect(container.textContent).not.toMatch(/low_capture_ratio|run-1|completed_with_warnings/);
  });

  it("explica que un mes no tiene informe en vez de mostrar un error (límite)", async () => {
    mockedGetReport.mockResolvedValueOnce(REPORT).mockResolvedValueOnce(null);
    mockedGetLatestRun.mockResolvedValue(RUN);
    await renderPage();

    await act(async () => {
      typeInto(container.querySelector('input[type="month"]') as HTMLInputElement, "2026-06");
    });
    await act(async () => {
      buttonByText("Ver mes").click();
    });
    await flush();

    expect(mockedGetReport).toHaveBeenLastCalledWith("2026-06-01");
    expect(container.textContent).toContain("Todavía no hay un informe calculado para Junio de 2026.");
    expect(container.textContent).not.toContain("Reintentar");
  });

  it("muestra un error legible con Reintentar si la API falla (fallo)", async () => {
    mockedGetReport.mockRejectedValueOnce(new Error("socket hang up")).mockResolvedValueOnce(REPORT);
    mockedGetLatestRun.mockResolvedValue(null);
    await renderPage();

    expect(container.textContent).toContain("No se pudo cargar el informe mensual");
    expect(container.textContent).not.toContain("socket hang up");

    await act(async () => {
      buttonByText("Reintentar").click();
    });
    await flush();

    expect(container.textContent).toContain("Costo de insumos por clínica");
  });

  it("sigue mostrando el informe aunque falle la consulta del estado de actualización (límite)", async () => {
    mockedGetReport.mockResolvedValue(REPORT);
    mockedGetLatestRun.mockRejectedValue(new Error("down"));
    await renderPage();

    expect(container.textContent).toContain("Frecuencia de quiebre crítico");
    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  it("no pide nada a la API si no se elige un mes y lo explica (fallo)", async () => {
    mockedGetReport.mockResolvedValue(REPORT);
    mockedGetLatestRun.mockResolvedValue(RUN);
    await renderPage();

    await act(async () => {
      typeInto(container.querySelector('input[type="month"]') as HTMLInputElement, "");
    });
    await act(async () => {
      buttonByText("Ver mes").click();
    });

    expect(container.querySelector('[role="alert"]')?.textContent).toBe("Elige un mes para ver su informe.");
    expect(mockedGetReport).toHaveBeenCalledTimes(1);
  });
});
