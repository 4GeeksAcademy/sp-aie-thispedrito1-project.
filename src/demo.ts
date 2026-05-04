import { sampleClaims, sampleAppointments, sampleClinicians, sampleLocations } from "./data/sampleData.js";
import { 
    calculateDenialRate, 
    denialRateByPayer, 
    calculateNoShowCost, 
    generateCMEReport 
} from "./utils/transformations.js";
import { validateClaim } from "./utils/validations.js";

console.log("================================================");
console.log("📊 PRUEBAS DE SISTEMA - HEALTHCORE");
console.log("================================================\n");

// 1. Métricas de Facturación
console.log("1️⃣  REVENUE CYCLE:");
const totalRate = calculateDenialRate(sampleClaims);
const ratesByPayer = denialRateByPayer(sampleClaims);

console.log(`- Tasa Global de Denegación: ${totalRate}%`);
console.log("- Desglose por Pagador:");
console.table(ratesByPayer);

// 2. Impacto de Inasistencias (No-Shows)
console.log("\n2️⃣  IMPACTO FINANCIERO:");
const location = sampleLocations[0]!; // Austin Central
const lostRevenue = calculateNoShowCost(sampleAppointments, location, "2025-03-14");

console.log(`- Sede: ${location.name}`);
console.log(`- Ingresos perdidos (Semana 14/03): $${lostRevenue}`);

// 3. Reporte de Cumplimiento Médico
console.log("\n3️⃣  RECURSOS HUMANOS (CME):");
const report = generateCMEReport(sampleClinicians, "2025-04-29");
console.table(report.map(r => ({
    Nombre: r.fullName,
    Estado: r.complianceStatus,
    "Progreso %": r.percentComplete,
    "Días Restantes": r.licenceDaysRemaining
})));

// 4. Validación de Datos
console.log("\n4️⃣  VALIDACIÓN DE REGISTROS:");
const claimToTest = sampleClaims[0]!;
const locationIds = sampleLocations.map(l => l.locationId);
const validation = validateClaim(claimToTest, locationIds);

console.log(`- Validando Claim ${claimToTest.claimId}:`, validation.valid ? "✅ OK" : "❌ ERROR");

console.log("\n================================================");
console.log("✅ FIN DE LAS PRUEBAS");
console.log("================================================");