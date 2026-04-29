import type{ Claim, Appointment, Location, Clinician, CMEReport, CMEStatus } from "../types/models.js";

// --- Facturación ---
export const calculateDenialRate = (claims: Claim[]): number => {
  if (claims.length === 0) throw new Error("No claims provided");
  const deniedCount = claims.filter(c => c.status === "denied").length;
  return parseFloat(((deniedCount / claims.length) * 100).toFixed(2));
};

export const denialRateByPayer = (claims: Claim[]): Record<string, number> => {
  const byPayer = claims.reduce((acc, c) => {
    if (!acc[c.payerName]) acc[c.payerName] = [];
    acc[c.payerName]!.push(c);
    return acc;
  }, {} as Record<string, Claim[]>);

  const result: Record<string, number> = {};
  for (const payer in byPayer) {
    result[payer] = calculateDenialRate(byPayer[payer]!);
  }
  return result;
};

export const flagHighDenialPayers = (claims: Claim[], threshold: number = 8): string[] => {
  const rates = denialRateByPayer(claims);
  return Object.keys(rates).filter(payer => rates[payer]! > threshold);
};

// --- No-Show Cost ---
export const calculateNoShowCost = (appointments: Appointment[], location: Location, weekEndingDate: string): number => {
  const end = new Date(weekEndingDate);
  const start = new Date(weekEndingDate);
  start.setDate(end.getDate() - 6); // Rango de 7 días inclusive

  const cost = appointments
    .filter(app => {
      const appDate = new Date(app.scheduledDate);
      return app.locationId === location.locationId && 
             app.status === "no_show" && 
             appDate >= start && appDate <= end;
    })
    .reduce((total, app) => total + location.averageConsultationFee[app.serviceType], 0);

  return parseFloat(cost.toFixed(2));
};

// --- CME Compliance ---
export const generateCMEReport = (clinicians: Clinician[], asOfDate: string): CMEReport[] => {
  const today = new Date(asOfDate);

  return clinicians.map(c => {
    const startDate = new Date(c.cmeYearStartDate);
    const expiryDate = new Date(c.licenceExpiryDate);
    
    // Cálculo de progreso anual
    const msInYear = 365 * 24 * 60 * 60 * 1000;
    const yearProgress = (today.getTime() - startDate.getTime()) / msInYear;
    const percentComplete = parseFloat(((c.cmeHoursLogged / c.cmeHoursRequired) * 100).toFixed(1));
    
    // Lógica de Estado
    let status: CMEStatus = "on_track";
    if (c.cmeHoursLogged >= c.cmeHoursRequired) {
      status = "complete";
    } else if (yearProgress >= 1) {
      status = "overdue";
    } else if ((yearProgress * 100) - percentComplete > 15) {
      status = "at_risk";
    }

    return {
      clinicianId: c.clinicianId,
      fullName: `${c.firstName} ${c.lastName}`,
      role: c.role,
      locationId: c.locationId,
      hoursRequired: c.cmeHoursRequired,
      hoursLogged: c.cmeHoursLogged,
      hoursRemaining: Math.max(0, c.cmeHoursRequired - c.cmeHoursLogged),
      percentComplete,
      daysRemainingInCycle: Math.ceil((new Date(startDate.getFullYear() + 1, 0, 1).getTime() - today.getTime()) / (1000 * 3600 * 24)),
      complianceStatus: status,
      licenceExpiryDate: c.licenceExpiryDate,
      licenceDaysRemaining: Math.ceil((expiryDate.getTime() - today.getTime()) / (1000 * 3600 * 24))
    };
  });
};