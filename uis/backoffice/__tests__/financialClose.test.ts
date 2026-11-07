import { isActivePath } from "../types/navigation";
import {
  decisionBody,
  isFigureId,
  parseReport,
  periodLabel,
  sourceLabel,
  splitInline,
  validateDecision,
  type Figure,
} from "../types/financialClose";

const REPORT = [
  "# HealthCore — Monthly financial close, October 2026",
  "",
  "_Version 1 · status: pending review · reviewer: Tom Callahan_",
  "",
  "> Draft pending review.",
  "",
  "## 2. Consolidated result (USD)",
  "",
  "| | Budget | Actual |",
  "|---|---:|---:|",
  "| Revenue | 2,095,450.00 | 2,027,420.00 |",
  "| Expenses * | 1,705,142.00 | 1,709,135.81 |",
  "",
  "\\* Comparable budget excludes lines with no records.",
  "",
  "Supporting figures:",
  "- `line:US.n/a.laboratory:actual` (Actual): 48,150.00 USD — `JE-2026OCT-0027`",
  "- **no records** in ledger.csv",
].join("\n");

const FIGURES: Record<string, Figure> = {
  "line:US.n/a.laboratory:actual": { value: "48150.00", unit: "USD", sources: [] },
};

describe("parseReport", () => {
  it("convierte el informe en bloques sin HTML", () => {
    const blocks = parseReport(REPORT);
    expect(blocks.map((b) => b.kind)).toEqual(["heading", "paragraph", "quote", "heading", "table", "paragraph", "paragraph", "list"]);
    const table = blocks[4];
    expect(table.kind === "table" && table.rows).toEqual([
      ["Revenue", "2,095,450.00", "2,027,420.00"],
      ["Expenses *", "1,705,142.00", "1,709,135.81"],
    ]);
    const note = blocks[5];
    expect(note.kind === "paragraph" && note.text.startsWith("* Comparable")).toBe(true);
  });
});

describe("splitInline", () => {
  it("separa código, negrita y texto", () => {
    expect(splitInline("- `line:a` (Actual): **no records** fin")).toEqual([
      { kind: "text", text: "- " },
      { kind: "code", text: "line:a" },
      { kind: "text", text: " (Actual): " },
      { kind: "strong", text: "no records" },
      { kind: "text", text: " fin" },
    ]);
  });

  it("solo los ids del informe se vuelven enlaces a su origen", () => {
    expect(isFigureId("line:US.n/a.laboratory:actual", FIGURES)).toBe(true);
    expect(isFigureId("JE-2026OCT-0027", FIGURES)).toBe(false);
    expect(isFigureId("toString", FIGURES)).toBe(false);
  });
});

describe("decisión del revisor", () => {
  it("rechazar exige comentarios; aprobar no", () => {
    expect(validateDecision("reject", "   ")).toMatch(/escribe qué debe cambiar/);
    expect(validateDecision("reject", "Acorta el resumen")).toBeNull();
    expect(validateDecision("approve", "")).toBeNull();
    expect(validateDecision("approve", "x".repeat(2001))).toMatch(/2000/);
  });

  it("no envía comentarios vacíos", () => {
    expect(decisionBody("approve", "  ")).toEqual({ decision: "approve" });
    expect(decisionBody("reject", " Más detalle ")).toEqual({ decision: "reject", comments: "Más detalle" });
  });
});

describe("etiquetas", () => {
  it("periodo sin zona horaria", () => {
    expect(periodLabel("2026-10-01")).toBe("octubre de 2026");
    expect(periodLabel("raro")).toBe("raro");
  });

  it("origen legible", () => {
    expect(sourceLabel({ kind: "budget", source_file: "budget.csv", source_row: 7 })).toBe("budget.csv fila 7");
    expect(sourceLabel({ kind: "ledger", entry_id: "JE-1" })).toBe("ledger.csv · JE-1");
    expect(sourceLabel({ kind: "calculation" })).toBe("Cálculo");
  });

  it("el menú ilumina el cierre financiero", () => {
    expect(isActivePath("/financial-close", "/financial-close")).toBe(true);
  });
});
