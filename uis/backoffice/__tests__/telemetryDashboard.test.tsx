/**
 * Pantalla /telemetry (actividad adicional "Dashboard visual simple").
 *
 * Mismo enfoque que useAsyncData.test.tsx: sin @testing-library, montando con
 * `act` + `createRoot` en jsdom. La API se sustituye con jest.mock para no
 * depender de un backend ni de Supabase.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import TelemetryDashboardPage from "../app/telemetry/page";
import { getTelemetryReport } from "../services/telemetryReportApi";
import type { TelemetryReport } from "../types/telemetryReport";

jest.mock("../services/telemetryReportApi", () => ({
  getTelemetryReport: jest.fn(),
}));

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const mockedGetReport = getTelemetryReport as jest.MockedFunction<typeof getTelemetryReport>;

const REPORT: TelemetryReport = {
  period: { from: "2026-09-06T00:00:00+00:00", to: "2026-09-13T00:00:00+00:00" },
  metrics: {
    events_per_day: [
      { date: "2026-09-10", event_type: "page_viewed", count: 5 },
      { date: "2026-09-11", event_type: "login_failed", count: 2 },
    ],
    error_rate_by_day: [{ date: "2026-09-11", total_events: 4, error_events: 1, error_rate: 0.25 }],
    web_vital_latency_by_day: [],
    auth_failure_rate: [{ date: "2026-09-11", total_attempts: 3, failed_attempts: 2, failure_rate: 2 / 3 }],
  },
};

let container: HTMLDivElement;
let root: Root;

async function renderPage() {
  await act(async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    root.render(<TelemetryDashboardPage />);
  });
  // Deja resolver la promesa del fetcher y el re-render posterior.
  await act(async () => {
    await Promise.resolve();
  });
}

/** Cambia un <input> controlado por React como lo haria el navegador. */
function typeInto(input: HTMLInputElement, value: string) {
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setValue?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function buttonByText(text: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === text);
  if (!button) throw new Error(`No hay boton "${text}"`);
  return button;
}

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  mockedGetReport.mockReset();
});

describe("TelemetryDashboardPage", () => {
  it("muestra el periodo devuelto por el servidor y un panel por metrica (camino feliz)", async () => {
    mockedGetReport.mockResolvedValue(REPORT);
    await renderPage();

    expect(mockedGetReport).toHaveBeenCalledTimes(1);
    const text = container.textContent ?? "";
    expect(text).toContain("Período del reporte");
    expect(text).toContain("6 sept 2026");
    expect(text).toContain("Volumen de eventos");
    expect(text).toContain("Tasa de error por día");
    expect(text).toContain("Fallos de login por día");
    expect(text).toContain("page_viewed");

    // Los event_type largos se cortan con ellipsis: el nombre completo debe
    // quedar accesible al pasar el raton.
    expect(container.querySelector('[title="page_viewed"]')?.textContent).toBe("page_viewed");
  });

  it("marca como vacia una metrica sin filas sin ocultar las demas (limite)", async () => {
    mockedGetReport.mockResolvedValue(REPORT);
    await renderPage();

    const latencyPanel = Array.from(container.querySelectorAll("section")).find((section) =>
      section.textContent?.includes("Latencia percibida"),
    );
    expect(latencyPanel?.textContent).toContain("Sin eventos de este tipo");
    expect(container.textContent).toContain("page_viewed");
  });

  it("muestra error legible con Reintentar y vuelve a pedir el reporte (fallo)", async () => {
    mockedGetReport.mockRejectedValueOnce(new Error("network")).mockResolvedValueOnce(REPORT);
    await renderPage();

    expect(container.textContent).toContain("No se pudo cargar el reporte técnico");
    expect(container.textContent).not.toContain("network");

    await act(async () => {
      buttonByText("Reintentar").click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(mockedGetReport).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("Volumen de eventos");
  });

  it("no llama a la API si el rango es invalido y avisa del motivo (fallo)", async () => {
    mockedGetReport.mockResolvedValue(REPORT);
    await renderPage();

    const [startInput, endInput] = Array.from(container.querySelectorAll<HTMLInputElement>('input[type="date"]'));
    await act(async () => {
      typeInto(endInput, "2026-09-01");
      typeInto(startInput, "2026-09-10");
    });
    await act(async () => {
      buttonByText("Aplicar").click();
    });

    expect(container.querySelector('[role="alert"]')?.textContent).toMatch(/posterior/);
    expect(mockedGetReport).toHaveBeenCalledTimes(1);
  });

  it("pide el nuevo rango al aplicar fechas validas (camino feliz)", async () => {
    mockedGetReport.mockResolvedValue(REPORT);
    await renderPage();

    const [startInput, endInput] = Array.from(container.querySelectorAll<HTMLInputElement>('input[type="date"]'));
    await act(async () => {
      typeInto(startInput, "2026-08-01");
      typeInto(endInput, "2026-08-31");
    });
    await act(async () => {
      buttonByText("Aplicar").click();
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(mockedGetReport).toHaveBeenCalledTimes(2);
    const lastQuery = mockedGetReport.mock.calls[1][0];
    expect(new Date(lastQuery.start_date).toISOString()).toBe("2026-08-01T00:00:00.000Z");
    expect(new Date(lastQuery.end_date).toISOString()).toBe("2026-09-01T00:00:00.000Z");
  });
});
