/**
 * Recepción de RFPs: ayudas puras de types/rfp.ts, la subida de services/rfpApi.ts
 * y el arreglo de http.ts para cuerpos FormData.
 */
import { requestJson } from "../services/http";
import {
  MAX_PDF_BYTES,
  describeOutcome,
  formatPhiCategories,
  formatPopulation,
  formatProgramTypes,
  describeEvaluation,
  isInProgress,
  isProvisional,
  shouldPoll,
  validatePdfFile,
  type EvaluationResult,
} from "../types/rfp";

function fileOf(name: string, type: string, size: number): File {
  const file = new File(["x"], name, { type });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

describe("validatePdfFile", () => {
  it("acepta un PDF normal (camino feliz)", () => {
    expect(validatePdfFile(fileOf("rfp.pdf", "application/pdf", 4000))).toBeNull();
  });

  it("acepta un PDF de exactamente 10 MB (límite)", () => {
    expect(validatePdfFile(fileOf("rfp.pdf", "application/pdf", MAX_PDF_BYTES))).toBeNull();
  });

  it("rechaza sin archivo, otro formato, vacío o demasiado grande (fallo)", () => {
    expect(validatePdfFile(null)).toBe("Selecciona un PDF.");
    expect(validatePdfFile(fileOf("rfp.docx", "application/msword", 10))).toBe("El archivo debe ser un PDF.");
    expect(validatePdfFile(fileOf("rfp.pdf", "application/pdf", 0))).toBe("El archivo está vacío.");
    expect(validatePdfFile(fileOf("rfp.pdf", "application/pdf", MAX_PDF_BYTES + 1))).toMatch(/10 MB/);
  });
});

describe("describeOutcome", () => {
  const base = { discard_reason: null, classifier_reason: null, error_message: null };

  it("explica el descarte por PHI y que lo revisa Compliance", () => {
    const text = describeOutcome({ ...base, status: "descartado", discard_reason: "phi_detected" });
    expect(text).toMatch(/datos de pacientes/);
    expect(text).toMatch(/Claire Whitfield/);
  });

  it("muestra el motivo del clasificador cuando no es una RFP", () => {
    const text = describeOutcome({ ...base, status: "descartado", discard_reason: "not_rfp", classifier_reason: "Pitch de un proveedor" });
    expect(text).toBe("No es una RFP: Pitch de un proveedor");
  });

  it("un error técnico muestra su mensaje, no un descarte", () => {
    expect(describeOutcome({ ...base, status: "error", error_message: "El modelo no respondió" })).toBe("El modelo no respondió");
  });
});

describe("formateadores", () => {
  it("población: nunca un número inventado si no está confirmada", () => {
    expect(formatPopulation(800, "employees")).toBe("800 empleados");
    expect(formatPopulation(null, "students")).toBe("Sin confirmar");
  });

  it("programa y PHI en lenguaje de negocio", () => {
    expect(formatProgramTypes(["occupational_health", "corporate_wellness"])).toBe("Salud ocupacional + Bienestar corporativo");
    expect(formatProgramTypes([])).toBe("—");
    expect(formatPhiCategories(["patient_name", "date_of_birth"])).toBe("nombre de paciente, fecha de nacimiento");
  });

  it("los estados de trabajo de las Partes 1 y 2 están en curso; los finales no", () => {
    expect(["analizando", "generando_borrador", "en_evaluación"].map((s) => isInProgress(s as never))).toEqual([true, true, true]);
    expect(
      ["descartado", "analisis_completo", "error", "needs_human_review", "evaluacion_completa"].map((s) => isInProgress(s as never)),
    ).toEqual([false, false, false, false, false]);
  });

  it("analisis_completo sigue haciendo poll porque la Parte 2 arranca sola", () => {
    expect(shouldPoll("analisis_completo")).toBe(true);
    expect(shouldPoll("needs_human_review")).toBe(false);
    expect(shouldPoll("evaluacion_completa")).toBe(false);
  });
});

describe("Parte 2: borradores y evaluación", () => {
  const failing: EvaluationResult = {
    section_id: "t:compliance",
    department_id: "compliance",
    iteration: 3,
    readability: { pass: true, score: 62.4, details: { problems: [] } },
    relevance: { pass: false, missing_aspects: ["Minimización de datos"] },
    compliance: {
      pass: false,
      rule_ids: ["no-phi", "no-invented-figures", "currency-by-country", "baa-us"],
      violations: [{ rule_id: "baa-us", message: "Incluye una cláusula BAA (HIPAA)." }],
      contains_phi: false,
    },
    overall_pass: false,
    feedback_for_generator: "1. [baa-us] Incluye una cláusula BAA (HIPAA).",
    retryable: true,
    evaluated_at: "2026-10-21T10:00:00Z",
  };

  it("describe los tres evaluadores en lenguaje de negocio", () => {
    const checks = describeEvaluation(failing);
    expect(checks.map((c) => [c.label, c.passed])).toEqual([
      ["Legibilidad", true],
      ["Pertinencia", false],
      ["Cumplimiento", false],
    ]);
    expect(checks[0].detail).toBe("Fernández Huerta 62");
    expect(checks[1].detail).toContain("Minimización de datos");
    expect(checks[2].detail).toBe("Incluye una cláusula BAA (HIPAA).");
  });

  it("una sección aprobada lista las reglas verificadas", () => {
    const passing = { ...failing, compliance: { ...failing.compliance, pass: true, violations: [] } };
    expect(describeEvaluation(passing)[2].detail).toBe("Reglas verificadas: no-phi, no-invented-figures, currency-by-country, baa-us");
  });

  it("solo needs_human_review es provisional", () => {
    expect(isProvisional({ draft_status: "needs_human_review" })).toBe(true);
    expect(isProvisional({ draft_status: "aprobada_por_evaluadores" })).toBe(false);
    expect(isProvisional({ draft_status: null })).toBe(false);
  });

  it("explica needs_human_review sin tecnicismos", () => {
    const text = describeOutcome({ status: "needs_human_review", discard_reason: null, classifier_reason: null, error_message: null });
    expect(text).toContain("provisional");
  });
});

describe("requestJson con FormData", () => {
  afterEach(() => jest.restoreAllMocks());

  it("no fuerza Content-Type JSON en una subida de archivo", async () => {
    const fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 202, json: async () => ({ ticket_id: "t" }) });
    global.fetch = fetchMock as unknown as typeof fetch;
    const body = new FormData();
    body.append("file", new File(["%PDF-"], "rfp.pdf", { type: "application/pdf" }));

    await requestJson("/rfp-tickets", { method: "POST", body });

    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.has("Content-Type")).toBe(false);
  });

  it("sigue poniendo JSON en los cuerpos de texto (regresión)", async () => {
    const fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    global.fetch = fetchMock as unknown as typeof fetch;

    await requestJson("/x", { method: "POST", body: JSON.stringify({ a: 1 }) });

    expect((fetchMock.mock.calls[0][1].headers as Headers).get("Content-Type")).toBe("application/json");
  });
});
