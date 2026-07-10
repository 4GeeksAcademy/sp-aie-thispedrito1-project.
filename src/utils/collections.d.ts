import type { Claim, Appointment, AppointmentStatus } from "../types/models.js";
export declare const filterClaims: (claims: Claim[], filters: Partial<Pick<Claim, "locationId" | "status" | "payerName" | "serviceType">>) => Claim[];
export declare const filterAppointmentsByStatus: (appointments: Appointment[], status: AppointmentStatus[]) => Appointment[];
export declare const sortClaimsById: (claims: Claim[], direction: "asc" | "desc") => Claim[];
export declare const sortAppointmentsByDate: (appointments: Appointment[], direction: "asc" | "desc") => Appointment[];
export declare const groupClaimsBy: (claims: Claim[], key: "locationId" | "payerName" | "status" | "serviceType") => Record<string, Claim[]>;
//# sourceMappingURL=collections.d.ts.map