"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.default = Home;
const jsx_runtime_1 = require("react/jsx-runtime");
// /app/page.tsx
const react_1 = require("react");
const CandidateDashboard_1 = __importDefault(require("@/components/CandidateDashboard"));
function Home() {
    return ((0, jsx_runtime_1.jsx)("main", { className: "min-h-screen bg-slate-50 p-6 md:p-12 font-sans", children: (0, jsx_runtime_1.jsxs)("div", { className: "max-w-7xl mx-auto space-y-8", children: [(0, jsx_runtime_1.jsxs)("header", { className: "border-b border-slate-200 pb-6", children: [(0, jsx_runtime_1.jsx)("h1", { className: "text-3xl font-bold text-blue-900 tracking-tight", children: "HealthCore Digital" }), (0, jsx_runtime_1.jsx)("p", { className: "text-slate-500 mt-2 text-lg", children: "Talent Pipeline Tracker \u00B7 Personas y Fuerza Laboral" })] }), (0, jsx_runtime_1.jsx)(react_1.Suspense, { fallback: (0, jsx_runtime_1.jsx)("div", { className: "flex justify-center items-center h-64", children: (0, jsx_runtime_1.jsx)("div", { className: "animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600" }) }), children: (0, jsx_runtime_1.jsx)(CandidateDashboard_1.default, {}) })] }) }));
}
//# sourceMappingURL=page.js.map