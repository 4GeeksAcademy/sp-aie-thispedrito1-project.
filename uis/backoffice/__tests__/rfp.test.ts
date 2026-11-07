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
  isInProgress,
  validatePdfFile,
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

  it("solo `analizando` sigue haciendo poll", () => {
    expect(isInProgress("analizando")).toBe(true);
    expect(["descartado", "analisis_completo", "error"].map((s) => isInProgress(s as never))).toEqual([false, false, false]);
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
