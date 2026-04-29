import type{ Claim, Clinician } from "../types/models.js";

export const findClaimById = (claims: Claim[], claimId: string): Claim | null => {
  return claims.find(c => c.claimId === claimId) || null;
};

export const findClinicianById = (clinicians: Clinician[], clinicianId: string): Clinician | null => {
  return clinicians.find(c => c.clinicianId === clinicianId) || null;
};

export const binarySearchClaimById = (sortedClaims: Claim[], targetId: string): number => {
  let low = 0;
  let high = sortedClaims.length - 1;

  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    const midId = sortedClaims[mid]!.claimId;

    if (midId === targetId) return mid;
    if (midId < targetId) low = mid + 1;
    else high = mid - 1;
  }
  return -1;
};