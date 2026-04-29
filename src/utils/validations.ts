import type { Claim, Clinician } from "../types/models.js";

export const validateClaim = (claim: Claim, knownLocationIds: string[]): { valid: boolean, errors: string[] } => {
  const errors: string[] = [];
  const patientIdRegex = /^HC-[a-zA-Z0-9]{6}$/;

  if (claim.claimAmount <= 0) errors.push("claimAmount debe ser > 0");
  if (new Date(claim.submissionDate) > new Date()) errors.push("submissionDate no debe ser futura");
  if (!knownLocationIds.includes(claim.locationId)) errors.push("locationId no válida");
  if (claim.status === "denied" && !claim.denialReason) errors.push("Falta denialReason en claim denegado");
  if (!patientIdRegex.test(claim.patientId)) errors.push("Formato de patientId inválido (HC-XXXXXX)");

  return { valid: errors.length === 0, errors };
};

export const validateClinician = (clinician: Clinician): { valid: boolean, errors: string[] } => {
  const errors: string[] = [];
  const validRoles = ["physician", "nurse_practitioner", "nurse", "medical_assistant"];

  if (clinician.cmeHoursRequired < 0) errors.push("cmeHoursRequired debe ser >= 0");
  if (clinician.cmeHoursLogged < 0) errors.push("cmeHoursLogged debe ser >= 0");
  if (!validRoles.includes(clinician.role)) errors.push("Rol de clínico no válido");
  
  const expiry = new Date(clinician.licenceExpiryDate);
  if (isNaN(expiry.getTime())) errors.push("Fecha de expiración de licencia inválida");

  return { valid: errors.length === 0, errors };
};