import type { Claim, Appointment, Clinician, Location } from "../types/models.js";

export const sampleLocations: Location[] = [
    { locationId: "LOC-001", name: "Austin Central", city: "Austin", stateOrCountry: "TX", country: "US", phone: "512-555-1234", averageConsultationFee: { primary_care: 100, chronic_disease: 120, preventive: 80, specialist: 150, womens_health: 110, paediatric: 90, mental_health: 130 } }
];

export const sampleClinicians: Clinician[] = [
    { clinicianId: "CL-001", firstName: "Sandra", lastName: "Flores", role: "nurse_practitioner", locationId: "LOC-001", licenceState: "TX", licenceExpiryDate: "2025-05-15", cmeHoursRequired: 50, cmeHoursLogged: 10, cmeYearStartDate: "2024-01-01" },
    { clinicianId: "CL-002", firstName: "Marcus", lastName: "Reid", role: "physician", locationId: "LOC-001", licenceState: "TX", licenceExpiryDate: "2026-06-30", cmeHoursRequired: 50, cmeHoursLogged: 35, cmeYearStartDate: "2024-01-01" }
];

export const sampleClaims: Claim[] = [
    { claimId: "HC-100001", patientId: "P-001", locationId: "LOC-001", serviceType: "primary_care", payerName: "Aetna", payerId: "AET-001", submissionDate: "2025-03-01", claimAmount: 150.00, status: "denied", denialReason: "incomplete_documentation", resubmitted: false },
    { claimId: "HC-100002", patientId: "P-002", locationId: "LOC-001", serviceType: "specialist", payerName: "Aetna", payerId: "AET-001", submissionDate: "2025-03-02", claimAmount: 250.00, status: "approved", resubmitted: false }
];

export const sampleAppointments: Appointment[] = [
    { appointmentId: "APP-001", patientId: "P-001", locationId: "LOC-001", serviceType: "primary_care", scheduledDate: "2025-03-10", scheduledTime: "10:00", status: "no_show" },
    { appointmentId: "APP-002", patientId: "P-002", locationId: "LOC-001", serviceType: "chronic_disease", scheduledDate: "2025-03-10", scheduledTime: "11:00", status: "completed" }
];