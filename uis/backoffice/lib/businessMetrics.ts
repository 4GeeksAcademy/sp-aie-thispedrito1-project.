import { sampleAppointments, sampleClaims, sampleClinicians, sampleLocations } from "../../../src/data/sampleData";
import { calculateDenialRate, calculateNoShowCost, denialRateByPayer, generateCMEReport } from "../../../src/utils/transformations";
import { validateClaim } from "../../../src/utils/validations";

export interface BackofficeSummary {
  denialRate: number;
  deniedByPayer: Array<{ payer: string; rate: number }>;
  lostRevenueWeek: number;
  cmeSnapshot: Array<{ fullName: string; complianceStatus: string; percentComplete: number; licenceDaysRemaining: number }>;
  claimValidation: { valid: boolean; errors: string[] };
}

export function getBackofficeSummary(): BackofficeSummary {
  const denialRate = calculateDenialRate(sampleClaims);
  const deniedByPayer = Object.entries(denialRateByPayer(sampleClaims)).map(([payer, rate]) => ({ payer, rate }));

  const location = sampleLocations[0];
  const lostRevenueWeek = location ? calculateNoShowCost(sampleAppointments, location, "2025-03-14") : 0;

  const cmeSnapshot = generateCMEReport(sampleClinicians, "2025-04-29").slice(0, 5).map((item) => ({
    fullName: item.fullName,
    complianceStatus: item.complianceStatus,
    percentComplete: item.percentComplete,
    licenceDaysRemaining: item.licenceDaysRemaining,
  }));

  const knownLocationIds = sampleLocations.map((item) => item.locationId);
  const claimValidation = sampleClaims[0]
    ? validateClaim(sampleClaims[0], knownLocationIds)
    : { valid: false, errors: ["No hay claims disponibles para validar."] };

  return {
    denialRate,
    deniedByPayer,
    lostRevenueWeek,
    cmeSnapshot,
    claimValidation,
  };
}
