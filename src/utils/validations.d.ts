import type { Claim, Clinician } from "../types/models.js";
/**
 * Valida los datos de una factura (Claim)
 */
export declare const validateClaim: (claim: Claim, knownLocationIds: string[]) => {
    valid: boolean;
    errors: string[];
};
/**
 * Valida los datos de un clínico
 */
export declare const validateClinician: (clinician: Clinician) => {
    valid: boolean;
    errors: string[];
};
//# sourceMappingURL=validations.d.ts.map