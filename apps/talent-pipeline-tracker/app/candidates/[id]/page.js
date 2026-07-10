"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.default = CandidatePage;
const jsx_runtime_1 = require("react/jsx-runtime");
// /app/candidates/[id]/page.tsx
const CandidateDetail_1 = __importDefault(require("@/components/CandidateDetail"));
// Convertimos la función en una función asíncrona (async)
async function CandidatePage({ params }) {
    // Esperamos de forma asíncrona a que Next.js resuelva el ID de la URL
    const resolvedParams = await params;
    return ((0, jsx_runtime_1.jsx)("main", { className: "min-h-screen bg-slate-50 p-6 md:p-12 font-sans", children: (0, jsx_runtime_1.jsx)("div", { className: "max-w-5xl mx-auto", children: (0, jsx_runtime_1.jsx)(CandidateDetail_1.default, { candidateId: resolvedParams.id }) }) }));
}
//# sourceMappingURL=page.js.map