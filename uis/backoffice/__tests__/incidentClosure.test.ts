/**
 * Cierre de incidencias con confirmación explícita (clase NIST, acciones irreversibles).
 *
 * `resolved` y `discarded` no tienen vuelta atrás: la API exige `confirm: true`
 * y la pantalla /incidents pide confirmación en línea antes de enviarlo.
 * Estas funciones puras deciden cuándo hace falta y qué viaja en el PATCH.
 */
import { STATUS_TRANSITIONS, isTerminalStatus, statusChangeBody, type IncidentStatus } from "../types/incident";

describe("isTerminalStatus", () => {
  it("marca como finales justo los estados sin transición de salida", () => {
    const statuses = Object.keys(STATUS_TRANSITIONS) as IncidentStatus[];
    expect(statuses.filter(isTerminalStatus).sort()).toEqual(["discarded", "resolved"]);
  });

  it("no pide confirmación para pasar a en curso", () => {
    expect(isTerminalStatus("in_progress")).toBe(false);
  });
});

describe("statusChangeBody", () => {
  it("solo envía confirm cuando la persona confirmó", () => {
    expect(statusChangeBody("discarded", true)).toEqual({ status: "discarded", confirm: true });
  });

  it("sin confirmar, el cuerpo no lleva confirm (y la API rechaza un cierre)", () => {
    expect(statusChangeBody("discarded", false)).toEqual({ status: "discarded" });
    expect("confirm" in statusChangeBody("in_progress", false)).toBe(false);
  });
});
