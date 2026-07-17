export type Country = "USA" | "UK";

export type Currency = "USD" | "GBP";

export type SupplierStatus = "active" | "suspended";

export type ComplianceAgreement = "BAA" | "DPA" | "both" | null;

export type Supplier = {
  id: number;
  name: string;
  country: Country;
  categories: string[];
  monthly_rate: number;
  currency: Currency;
  updated_at: string;
  status: SupplierStatus;
  compliance_agreement: ComplianceAgreement;
  contract_renewal_date?: string | null;
  contact_email?: string | null;
  notes?: string | null;
};

export type SupplierCreateInput = {
  name: string;
  country: Country;
  categories: string[];
  monthly_rate: number;
  currency: Currency;
  status: SupplierStatus;
  compliance_agreement: ComplianceAgreement;
  contract_renewal_date?: string;
  contact_email?: string;
  notes?: string;
};

export type SupplierFilters = {
  country?: string;
  category?: string;
};
