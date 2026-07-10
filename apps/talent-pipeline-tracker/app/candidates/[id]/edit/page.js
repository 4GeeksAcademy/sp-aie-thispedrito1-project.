'use client';
"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.default = EditCandidatePage;
const jsx_runtime_1 = require("react/jsx-runtime");
const react_1 = require("react");
const link_1 = __importDefault(require("next/link"));
const navigation_1 = require("next/navigation");
const CandidateForm_1 = __importDefault(require("@/components/CandidateForm"));
const api_1 = require("@/services/api");
const tracker_1 = require("@/types/tracker");
function EditCandidatePage() {
    const params = (0, navigation_1.useParams)();
    const candidateId = params?.id;
    const [candidate, setCandidate] = (0, react_1.useState)(null);
    const [loading, setLoading] = (0, react_1.useState)(true);
    const [error, setError] = (0, react_1.useState)(null);
    (0, react_1.useEffect)(() => {
        const loadCandidate = async () => {
            if (!candidateId) {
                setError('No se recibió un ID de candidato válido.');
                setLoading(false);
                return;
            }
            try {
                setLoading(true);
                setError(null);
                const candidateData = await api_1.talentApi.getRecordById(candidateId);
                setCandidate(candidateData);
            }
            catch (err) {
                const message = err instanceof Error ? err.message : 'No se pudo cargar la candidatura.';
                setError(message);
            }
            finally {
                setLoading(false);
            }
        };
        void loadCandidate();
    }, [candidateId]);
    return ((0, jsx_runtime_1.jsx)("main", { className: "min-h-screen bg-slate-50 p-6 md:p-12 font-sans", children: (0, jsx_runtime_1.jsxs)("div", { className: "max-w-4xl mx-auto space-y-6", children: [(0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)(link_1.default, { href: candidateId ? `/candidates/${candidateId}` : '/', className: "text-sm font-medium text-blue-600 hover:text-blue-800 transition-colors", children: "\u2190 Volver al detalle" }), (0, jsx_runtime_1.jsx)("h1", { className: "text-2xl font-bold text-slate-900 mt-2", children: "Editar Candidatura" }), (0, jsx_runtime_1.jsx)("p", { className: "text-sm text-slate-500 mt-1", children: "Actualiza la informaci\u00F3n del perfil profesional." })] }), loading && ((0, jsx_runtime_1.jsxs)("div", { className: "text-center py-12 bg-white rounded-xl border border-slate-200", children: [(0, jsx_runtime_1.jsx)("div", { className: "animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto mb-3" }), (0, jsx_runtime_1.jsx)("p", { className: "text-slate-500 text-sm", children: "Cargando candidatura..." })] })), error && !loading && ((0, jsx_runtime_1.jsxs)("div", { className: "bg-red-50 border border-red-200 text-red-700 p-4 rounded-xl text-sm", children: [(0, jsx_runtime_1.jsx)("strong", { children: "No se pudo cargar:" }), " ", error] })), !loading && !error && candidate && ((0, jsx_runtime_1.jsx)(CandidateForm_1.default, { initialData: candidate, isEditing: true }))] }) }));
}
//# sourceMappingURL=page.js.map