import type { Claim, Clinician } from "../types/models.js";
export declare const findClaimById: (claims: Claim[], claimId: string) => Claim | null;
export declare const findClinicianById: (clinicians: Clinician[], clinicianId: string) => Clinician | null;
export declare const binarySearchClaimById: (sortedClaims: Claim[], targetId: string) => number;
//# sourceMappingURL=search.d.ts.map