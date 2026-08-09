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

export const SUPPLIER_CATEGORY_LABELS: Record<string, string> = {
  medical_supplies: "Material médico",
  laboratory_services: "Servicios de laboratorio",
  pharmaceutical: "Farmacéutico",
  clinical_software: "Software clínico",
  it_infrastructure: "Infraestructura TI",
  hr_and_payroll_software: "RRHH y nómina",
  cleaning_and_facilities: "Limpieza e instalaciones",
  patient_communication: "Comunicación con pacientes",
  billing_and_coding_software: "Facturación y codificación",
  training_platforms: "Plataformas de formación",
};

export const SUPPLIER_STATUS_LABELS: Record<SupplierStatus, string> = {
  active: "Activo",
  suspended: "Suspendido",
};
