/**
 * Parte 3 (aprobación humana y cierre): ayudas puras de types/rfp.ts que usa
 * /rfps/[id] para el formulario de decisión, el poll y el arbitraje.
 */
import {
  describeArbitration,
  describeOutcome,
  finalDocumentFileName,
  shouldPollTicket,
  summarizeTraceOutput,
  toDecisionPayload,
  validateDecisionDraft,
  type RfpArbitration,
  type RfpDepartmentSection,
} from "../types/rfp";

const CLINICAL_REQUEST = { asks_capacity: true, can_request_changes: true, draft_digest: "abc123" };
const OTHER_REQUEST = { asks_capacity: false, can_request_changes: false, draft_digest: "def456" };

function section(approval_status: RfpDepartmentSection["approval_status"]): RfpDepartmentSection {
  return { approval_status } as RfpDepartmentSection;
}

describe("validateDecisionDraft", () => {
  it("exige elegir una decisión", () => {
    expect(validateDecisionDraft({ decision: "", comments: "", confirmedCapacity: "" }, OTHER_REQUEST)).toHaveProperty("decision");
  });

  it("pide motivo para solicitar cambios o rechazar", () => {
    const errors = validateDecisionDraft({ decision: "reject", comments: "No", confirmedCapacity: "" }, OTHER_REQUEST);
    expect(errors).toHaveProperty("comments");
  });

  it("no ofrece cambios cuando ya no quedan rondas", () => {
    const errors = validateDecisionDraft(
      { decision: "request_changes", comments: "Concretad el arranque.", confirmedCapacity: "" },
      OTHER_REQUEST,
    );
    expect(errors.decision).toMatch(/rondas/);
  });

  it("Clinical debe confirmar una capacidad entera y positiva para aprobar", () => {
    expect(validateDecisionDraft({ decision: "approve", comments: "", confirmedCapacity: "" }, CLINICAL_REQUEST)).toHaveProperty(
      "confirmed_capacity",
    );
    expect(validateDecisionDraft({ decision: "approve", comments: "", confirmedCapacity: "2.5" }, CLINICAL_REQUEST)).toHaveProperty(
      "confirmed_capacity",
    );
    expect(validateDecisionDraft({ decision: "approve", comments: "", confirmedCapacity: "800" }, CLINICAL_REQUEST)).toEqual({});
  });
});

describe("toDecisionPayload", () => {
  it("manda la huella del borrador leído y solo la capacidad que corresponde", () => {
    expect(toDecisionPayload({ decision: "approve", comments: " ", confirmedCapacity: "500" }, CLINICAL_REQUEST)).toEqual({
      decision: "approve",
      comments: null,
      confirmed_capacity: 500,
      draft_digest: "abc123",
    });
    expect(
      toDecisionPayload({ decision: "request_changes", comments: "Más concreto.", confirmedCapacity: "500" }, CLINICAL_REQUEST)
        .confirmed_capacity,
    ).toBeNull();
  });
});

describe("shouldPollTicket", () => {
  it("consulta mientras el worker aplica una decisión, no mientras se espera a una persona", () => {
    const waiting = { status: "esperando_aprobación" as const, sections: [section("pendiente"), section("aprobada")], arbitration: null };
    expect(shouldPollTicket(waiting)).toBe(false);
    expect(shouldPollTicket({ ...waiting, sections: [section("en_revisión")] })).toBe(true);
    expect(shouldPollTicket({ ...waiting, arbitration: { decision_registered: true } as RfpArbitration })).toBe(true);
    expect(shouldPollTicket({ status: "terminado", sections: [], arbitration: null })).toBe(false);
    // Reintento de Celery tras un fallo del proveedor durante una revisión.
    expect(shouldPollTicket({ status: "error", sections: [section("en_revisión")], arbitration: null })).toBe(true);
    expect(shouldPollTicket({ status: "error", sections: [section("pendiente")], arbitration: null })).toBe(false);
  });
});

describe("arbitraje y cierre", () => {
  it("resume la última ronda del árbitro en lenguaje de negocio", () => {
    const arbitration = {
      last: {
        round: 1,
        resolution: "human",
        conflicts: [{ trigger_id: "capacity-vs-population", departments: ["revenue", "clinical"], arbiter: "Tom Callahan", arbiter_kind: "human", details: {} }],
        forced_departments: ["clinical", "revenue"],
      },
    } as RfpArbitration;
    expect(describeArbitration(arbitration)).toBe(
      "Ronda 1: La capacidad clínica no cubre la población. Tom Callahan decidió; se revisan: clinical, revenue.",
    );
    expect(describeArbitration({ last: { round: 0, resolution: "none", conflicts: [] } } as unknown as RfpArbitration)).toBeNull();
  });

  it("explica por qué se descartó una propuesta en la Parte 3", () => {
    const base = { classifier_reason: null, error_message: null };
    expect(describeOutcome({ ...base, status: "descartado", discard_reason: "rejected_in_approval" })).toMatch(/rechazó/);
    expect(describeOutcome({ ...base, status: "descartado", discard_reason: "arbitration_limit" })).toMatch(/arbitraje/);
    expect(describeOutcome({ ...base, status: "terminado", discard_reason: null })).toMatch(/documento final/);
  });

  it("nombra el archivo del documento final y resume la salida del trace", () => {
    expect(finalDocumentFileName({ rfp_id: "RFP-20261021-ABC123" })).toBe("propuesta-RFP-20261021-ABC123.md");
    expect(summarizeTraceOutput({ decision: "approve", has_comments: false, draft: { sha256: "x" }, nada: null })).toBe(
      "decision: approve · has_comments: false",
    );
  });
});
