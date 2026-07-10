import type { Claim, Appointment, Location, Clinician, CMEReport } from "../types/models.js";
export declare const calculateDenialRate: (claims: Claim[]) => number;
export declare const denialRateByPayer: (claims: Claim[]) => Record<string, number>;
export declare const flagHighDenialPayers: (claims: Claim[], threshold?: number) => string[];
export declare const calculateNoShowCost: (appointments: Appointment[], location: Location, weekEndingDate: string) => number;
export declare const generateCMEReport: (clinicians: Clinician[], asOfDate: string) => CMEReport[];
//# sourceMappingURL=transformations.d.ts.map