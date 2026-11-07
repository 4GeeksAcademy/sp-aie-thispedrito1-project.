import { ALL_NAV_HREFS, isActivePath } from "../types/navigation";
import {
  approvalProgress,
  canDecide,
  canSend,
  sortAlerts,
  type CommunicationDraft,
  type SupplierAlert,
} from "../types/supplierContracts";

function alert(kind: string, due: string | null): SupplierAlert {
  return {
    alert_key: `${kind}:${due}`, kind, supplier_id: "HC-S03", contract_id: null, recipient_role: "clinical_ops_director",
    recipient: "Dr. Marcus Reid", due_date: due, summary: "", refs: [], raised_by: "agent:supplier-supervisor", created_at: "",
  };
}

function draft(overrides: Partial<CommunicationDraft> = {}): CommunicationDraft {
  return {
    draft_id: "d1", supplier_id: "HC-S03", contract_id: "CT-HC-02", purpose: "non_renewal_notice", subject: "s", body: "b",
    status: "pending_approval", draft_source: "template", drafted_by: "agent:supplier-supervisor", refs: [], approval_reasons: [],
    approvals: [
      { role: "clinical_ops_director", approver: "Dr. Marcus Reid", decision: "approved", comment: null },
      { role: "compliance_officer", approver: "Claire Whitfield", decision: "pending", comment: null },
    ],
    created_at: "", sent_at: null, ...overrides,
  };
}

describe("supplier contracts helpers", () => {
  it("sorts data-protection and notice alerts before price and 90-day alerts", () => {
    const sorted = sortAlerts([alert("expiry_90", "2027-01-06"), alert("price_increase", null),
      alert("notice_deadline", "2026-11-22"), alert("data_protection_missing", null), alert("notice_deadline", "2026-11-20")]);
    expect(sorted.map((a) => `${a.kind}:${a.due_date}`)).toEqual([
      "data_protection_missing:null", "notice_deadline:2026-11-20", "notice_deadline:2026-11-22",
      "price_increase:null", "expiry_90:2027-01-06",
    ]);
  });

  it("only lets a pending approver decide", () => {
    expect(canDecide(draft(), "compliance_officer")).toBe(true);
    expect(canDecide(draft(), "clinical_ops_director")).toBe(false); // ya decidió
    expect(canDecide(draft(), "ceo")).toBe(false); // no figura entre los aprobadores
    expect(canDecide(draft({ status: "rejected" }), "compliance_officer")).toBe(false);
    expect(canDecide(draft(), null)).toBe(false);
  });

  it("only lets clinical operations send a fully approved draft", () => {
    const approved = draft({
      status: "approved",
      approvals: draft().approvals.map((a) => ({ ...a, decision: "approved" as const })),
    });
    expect(canSend(approved, "clinical_ops_director")).toBe(true);
    expect(canSend(approved, "compliance_officer")).toBe(false);
    expect(canSend(draft(), "clinical_ops_director")).toBe(false);
    expect(approvalProgress(draft())).toBe("1/2 aprobaciones");
  });

  it("menu: the supplier-contracts page does not light up the suppliers link", () => {
    const active = ALL_NAV_HREFS.filter((href) => isActivePath("/supplier-contracts", href));
    expect(active).toEqual(["/supplier-contracts"]);
  });
});
