import type{ Claim, Appointment, AppointmentStatus } from "../types/models.js";

export const filterClaims = (
  claims: Claim[], 
  filters: Partial<Pick<Claim, "locationId" | "status" | "payerName" | "serviceType">>
): Claim[] => {
  return claims.filter(claim => {
    return Object.entries(filters).every(([key, value]) => {
      return value === undefined || claim[key as keyof Claim] === value;
    });
  });
};

export const filterAppointmentsByStatus = (
  appointments: Appointment[], 
  status: AppointmentStatus[]
): Appointment[] => {
  return appointments.filter(app => status.includes(app.status));
};

export const sortClaimsById = (claims: Claim[], direction: "asc" | "desc"): Claim[] => {
  return [...claims].sort((a, b) => {
    return direction === "asc" 
      ? a.claimId.localeCompare(b.claimId) 
      : b.claimId.localeCompare(a.claimId);
  });
};

export const sortAppointmentsByDate = (appointments: Appointment[], direction: "asc" | "desc"): Appointment[] => {
  return [...appointments].sort((a, b) => {
    const dateA = new Date(a.scheduledDate).getTime();
    const dateB = new Date(b.scheduledDate).getTime();
    return direction === "asc" ? dateA - dateB : dateB - dateA;
  });
};

export const groupClaimsBy = (
  claims: Claim[], 
  key: "locationId" | "payerName" | "status" | "serviceType"
): Record<string, Claim[]> => {
  return claims.reduce((acc, claim) => {
    const groupKey = claim[key] as string;
    if (!acc[groupKey]) acc[groupKey] = [];
    acc[groupKey].push(claim);
    return acc;
  }, {} as Record<string, Claim[]>);
};