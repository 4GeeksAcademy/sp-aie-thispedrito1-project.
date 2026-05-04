import type { Claim, Clinician } from "../types/models.js";

/**
 * Valida los datos de una factura (Claim)
 */
export const validateClaim = (claim: Claim, knownLocationIds: string[]): { valid: boolean, errors: string[] } => {
  const errors: string[] = [];
  
  // 1. Formato de ID: HC- seguido de al menos un número (ej: HC-100001)
  const claimIdRegex = /^HC-\d+/;

  // Validación de monto (en tu interfaz es 'claimAmount')
  if (claim.claimAmount <= 0) {
    errors.push("El monto (claimAmount) debe ser mayor a 0.");
  }

  // Validación de fecha
  if (new Date(claim.submissionDate) > new Date()) {
    errors.push("La fecha de envío (submissionDate) no puede ser futura.");
  }

  // Validación de sede
  if (!knownLocationIds.includes(claim.locationId)) {
    errors.push(`La sede '${claim.locationId}' no es válida o no está registrada.`);
  }

  // Validación de motivo de denegación
  if (claim.status === "denied" && !claim.denialReason) {
    errors.push("Las facturas denegadas deben incluir un motivo (denialReason).");
  }

  // Validación de formato de ID de factura
  if (!claimIdRegex.test(claim.claimId)) {
    errors.push("Formato de claimId inválido. Debe empezar con 'HC-' seguido de números.");
  }

  return { 
    valid: errors.length === 0, 
    errors 
  };
};

/**
 * Valida los datos de un clínico
 */
export const validateClinician = (clinician: Clinician): { valid: boolean, errors: string[] } => {
  const errors: string[] = [];
  const validRoles = ["physician", "nurse_practitioner", "nurse", "medical_assistant"];

  // Validación de horas CME (en tu interfaz es 'cmeHoursLogged' y 'cmeHoursRequired')
  if (clinician.cmeHoursRequired < 0) {
    errors.push("Las horas requeridas no pueden ser negativas.");
  }
  
  if (clinician.cmeHoursLogged < 0) {
    errors.push("Las horas logradas no pueden ser negativas.");
  }

  // Validación de rol (normalizamos a minúsculas para comparar)
  if (!validRoles.includes(clinician.role.toLowerCase().replace(" ", "_"))) {
    errors.push(`El rol '${clinician.role}' no es un rol operativo válido.`);
  }
  
  // Validación de fecha de licencia
  const expiry = new Date(clinician.licenceExpiryDate);
  if (isNaN(expiry.getTime())) {
    errors.push("La fecha de expiración de la licencia no es válida.");
  }

  return { 
    valid: errors.length === 0, 
    errors 
  };
};