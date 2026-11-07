import {
  EMPTY_FILTERS,
  actorKey,
  describeDetails,
  describeIntegrity,
  toAuditQuery,
  validateFilters,
  type IntegrityResult,
} from "../types/audit";
import { NAV_SECTIONS, visibleSections } from "../types/navigation";
import { parseAmountToCents } from "../types/patients";

describe("toAuditQuery", () => {
  it("convierte el último día elegido en fin exclusivo UTC", () => {
    const params = toAuditQuery({ ...EMPTY_FILTERS, dateFrom: "2026-11-01", dateTo: "2026-11-07" }, { limit: 25 });
    expect(params.get("date_from")).toBe("2026-11-01T00:00:00.000Z");
    expect(params.get("date_to")).toBe("2026-11-08T00:00:00.000Z");
  });

  it("cruza bien el fin de mes y de año", () => {
    const params = toAuditQuery({ ...EMPTY_FILTERS, dateTo: "2026-12-31" }, { limit: 25 });
    expect(params.get("date_to")).toBe("2027-01-01T00:00:00.000Z");
  });

  it("separa el actor en tipo e id y normaliza la referencia de paciente", () => {
    const params = toAuditQuery(
      { ...EMPTY_FILTERS, actorKey: "process:clinical_documentation_assistant", patientRef: " pt-0a1b2c3d " },
      { limit: 10, before: 42 },
    );
    expect(params.get("actor_type")).toBe("process");
    expect(params.get("actor_id")).toBe("clinical_documentation_assistant");
    expect(params.get("patient_ref")).toBe("PT-0A1B2C3D");
    expect(params.get("before")).toBe("42");
    expect(params.get("limit")).toBe("10");
  });

  it("un actor anónimo filtra solo por tipo", () => {
    const params = toAuditQuery({ ...EMPTY_FILTERS, actorKey: actorKey({ actor_type: "anonymous", actor_id: null }) }, { limit: 5 });
    expect(params.get("actor_type")).toBe("anonymous");
    expect(params.has("actor_id")).toBe(false);
  });

  it("no envía filtros vacíos", () => {
    expect([...toAuditQuery(EMPTY_FILTERS, { limit: 25 }).keys()]).toEqual(["limit"]);
  });
});

describe("validateFilters", () => {
  it("rechaza un rango invertido y una referencia con otra forma", () => {
    expect(validateFilters({ ...EMPTY_FILTERS, dateFrom: "2026-11-08", dateTo: "2026-11-01" })).toMatch(/posterior/);
    expect(validateFilters({ ...EMPTY_FILTERS, patientRef: "Jordan" })).toMatch(/PT-XXXXXXXX/);
    expect(validateFilters({ ...EMPTY_FILTERS, dateFrom: "2026-11-01", dateTo: "2026-11-01" })).toBeNull();
  });
});

describe("describeIntegrity", () => {
  const base: IntegrityResult = {
    ok: true,
    checked: 12,
    head_sequence: 12,
    head_hash: "a".repeat(64),
    broken_at: null,
    reason: null,
    verified_at: "2026-11-07T10:00:00.000000Z",
  };

  it("explica una cadena íntegra y una vacía", () => {
    expect(describeIntegrity(base)).toMatch(/12 entradas verificadas/);
    expect(describeIntegrity({ ...base, checked: 0, head_sequence: null })).toMatch(/vacío/);
  });

  it("dice dónde y por qué se rompió", () => {
    const text = describeIntegrity({ ...base, ok: false, checked: 1, broken_at: 2, reason: "tampered_entry" });
    expect(text).toMatch(/#2/);
    expect(text).toMatch(/modificó/);
  });
});

describe("describeDetails", () => {
  it("resume la lista de pacientes exportados y oculta el lote de la semilla", () => {
    const text = describeDetails({
      record_count: 3,
      patient_refs: ["PT-00000001", "PT-00000002", "PT-00000003"],
      seed_batch: "audit-log-v1",
    });
    expect(text).toBe("record_count: 3 · 3 pacientes");
    expect(text).not.toMatch(/seed_batch/);
  });
});

describe("parseAmountToCents", () => {
  it("acepta coma o punto decimal y rechaza lo demás", () => {
    expect(parseAmountToCents("120,50")).toBe(12050);
    expect(parseAmountToCents("7")).toBe(700);
    expect(parseAmountToCents("-3")).toBeNull();
    expect(parseAmountToCents("1,234")).toBeNull();
  });
});

describe("menú", () => {
  it("muestra el registro de auditoría también al empleado (ve sus pacientes)", () => {
    const employee = { role: "employee", role_rank: 1, department: "clinical_operations", department_id: "x", capabilities: [] };
    const hrefs = visibleSections(NAV_SECTIONS, employee).flatMap((section) => section.items.map((item) => item.href));
    expect(hrefs).toEqual(expect.arrayContaining(["/audit-log", "/patients"]));
  });
});
