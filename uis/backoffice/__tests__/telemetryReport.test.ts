/**
 * Utilidades puras de la pantalla /telemetry (types/telemetryReport.ts).
 *
 * Los tests de toReportQuery comparan INSTANTES (via Date) y no strings
 * exactos: "2026-09-06T00:00:00Z" y "2026-09-06T00:00:00.000Z" son el mismo
 * momento y el backend acepta ambos (routes/telemetry.py::_parse_query_datetime).
 */
import {
  DEFAULT_WINDOW_DAYS,
  formatRate,
  formatVitalValue,
  getDefaultDateRange,
  toReportQuery,
  totalEventsByType,
  validateDateRange,
} from "../types/telemetryReport";

const instant = (iso: string) => new Date(iso).toISOString();

describe("getDefaultDateRange", () => {
  it("cubre 7 dias naturales contando hoy (camino feliz)", () => {
    const range = getDefaultDateRange(new Date("2026-09-12T15:30:00Z"));
    expect(range).toEqual({ start: "2026-09-06", end: "2026-09-12" });
    expect(DEFAULT_WINDOW_DAYS).toBe(7);
  });

  it("usa el dia UTC, no el local, justo despues de medianoche UTC (limite)", () => {
    const range = getDefaultDateRange(new Date("2026-09-01T00:05:00Z"));
    expect(range).toEqual({ start: "2026-08-26", end: "2026-09-01" });
  });
});

describe("validateDateRange", () => {
  it("acepta un rango normal (camino feliz)", () => {
    expect(validateDateRange({ start: "2026-09-01", end: "2026-09-07" })).toBeNull();
  });

  it("acepta inicio y fin el mismo dia (limite)", () => {
    expect(validateDateRange({ start: "2026-09-07", end: "2026-09-07" })).toBeNull();
  });

  it("rechaza inicio posterior al fin (fallo)", () => {
    expect(validateDateRange({ start: "2026-09-08", end: "2026-09-07" })).toMatch(/posterior/);
  });

  it("rechaza campos vacios (fallo)", () => {
    expect(validateDateRange({ start: "", end: "2026-09-07" })).toMatch(/Selecciona/);
  });
});

describe("toReportQuery", () => {
  it("empieza a medianoche UTC del dia de inicio (camino feliz)", () => {
    const query = toReportQuery({ start: "2026-09-06", end: "2026-09-12" });
    expect(instant(query.start_date)).toBe("2026-09-06T00:00:00.000Z");
  });

  it("incluye el dia de fin completo: fin exclusivo = medianoche del dia siguiente (camino feliz)", () => {
    const query = toReportQuery({ start: "2026-09-06", end: "2026-09-12" });
    expect(instant(query.end_date)).toBe("2026-09-13T00:00:00.000Z");
  });

  it("un solo dia pide exactamente 24 horas (limite)", () => {
    const query = toReportQuery({ start: "2026-09-12", end: "2026-09-12" });
    const hours = (Date.parse(query.end_date) - Date.parse(query.start_date)) / 3_600_000;
    expect(hours).toBe(24);
  });

  it("cruza bien el cambio de mes (limite)", () => {
    const query = toReportQuery({ start: "2026-08-25", end: "2026-08-31" });
    expect(instant(query.end_date)).toBe("2026-09-01T00:00:00.000Z");
  });

  it("envia la zona horaria explicita para que el backend no la adivine (fallo evitado)", () => {
    const query = toReportQuery({ start: "2026-09-06", end: "2026-09-12" });
    expect(query.start_date).toMatch(/(Z|\+00:00)$/);
    expect(query.end_date).toMatch(/(Z|\+00:00)$/);
  });
});

describe("formatRate", () => {
  it("formatea una razon como porcentaje es-ES", () => {
    expect(formatRate(0.125).replace(/\s/g, " ")).toBe("12,5 %");
  });

  it("formatea cero y uno (limites)", () => {
    expect(formatRate(0).replace(/\s/g, " ")).toBe("0 %");
    expect(formatRate(1).replace(/\s/g, " ")).toBe("100 %");
  });
});

describe("totalEventsByType", () => {
  it("suma los dias por tipo y ordena de mayor a menor (camino feliz)", () => {
    const totals = totalEventsByType([
      { date: "2026-09-10", event_type: "page_viewed", count: 3 },
      { date: "2026-09-10", event_type: "login_failed", count: 1 },
      { date: "2026-09-11", event_type: "page_viewed", count: 4 },
    ]);
    expect(totals).toEqual([
      { event_type: "page_viewed", count: 7 },
      { event_type: "login_failed", count: 1 },
    ]);
  });

  it("desempata alfabeticamente para un orden estable (limite)", () => {
    const totals = totalEventsByType([
      { date: "2026-09-10", event_type: "b_event", count: 2 },
      { date: "2026-09-10", event_type: "a_event", count: 2 },
    ]);
    expect(totals.map((row) => row.event_type)).toEqual(["a_event", "b_event"]);
  });

  it("devuelve lista vacia sin filas (fallo controlado)", () => {
    expect(totalEventsByType([])).toEqual([]);
  });
});

describe("formatVitalValue", () => {
  it("muestra milisegundos redondeados para LCP/INP", () => {
    expect(formatVitalValue("LCP", 1234.56)).toBe("1235 ms");
  });

  it("muestra CLS sin unidad y con 3 decimales", () => {
    expect(formatVitalValue("CLS", 0.01234)).toBe("0.012");
  });
});
