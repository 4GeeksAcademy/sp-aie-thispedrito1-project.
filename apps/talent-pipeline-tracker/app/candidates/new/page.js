// /app/candidates/new/page.tsx
'use client';
"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.default = NewCandidatePage;
const jsx_runtime_1 = require("react/jsx-runtime");
const react_1 = require("react");
const navigation_1 = require("next/navigation");
const link_1 = __importDefault(require("next/link"));
const api_1 = require("@/services/api");
const tracker_1 = require("@/types/tracker");
function NewCandidatePage() {
    const router = (0, navigation_1.useRouter)();
    const [loading, setLoading] = (0, react_1.useState)(false);
    const [error, setError] = (0, react_1.useState)(null);
    const [success, setSuccess] = (0, react_1.useState)(null);
    // Estados locales controlados para los inputs
    const [firstName, setFirstName] = (0, react_1.useState)('');
    const [lastName, setLastName] = (0, react_1.useState)('');
    const [email, setEmail] = (0, react_1.useState)('');
    const [phone, setPhone] = (0, react_1.useState)('');
    const [position, setPosition] = (0, react_1.useState)('');
    const [years, setYears] = (0, react_1.useState)(0);
    const [linkedin, setLinkedin] = (0, react_1.useState)('');
    const [cvUrl, setCvUrl] = (0, react_1.useState)('');
    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!firstName.trim() || !lastName.trim() || !email.trim()) {
            setError('Por favor, rellena todos los campos obligatorios (*).');
            return;
        }
        try {
            setLoading(true);
            setError(null);
            setSuccess(null);
            const payload = {
                full_name: `${firstName.trim()} ${lastName.trim()}`,
                email: email.trim().toLowerCase(),
                phone: phone.trim() || '+34000000000',
                position: position.trim() || 'No especificada',
                experience_years: Number(years) || 0,
                linkedin: linkedin.trim() && linkedin.startsWith('http') ? linkedin.trim() : 'https://linkedin.com/in/placeholder',
                cv_url: cvUrl.trim() && cvUrl.startsWith('http') ? cvUrl.trim() : 'https://storage.healthcore.com/cv/placeholder.pdf',
            };
            console.log('Enviando payload del nuevo candidato a la API:', payload);
            const createdCandidate = await api_1.talentApi.createRecord(payload);
            if (typeof window !== 'undefined') {
                window.sessionStorage.setItem('latestCandidate', JSON.stringify(createdCandidate));
            }
            setSuccess('Candidatura registrada correctamente. Redirigiendo al dashboard...');
            setTimeout(() => {
                router.push('/');
                router.refresh();
            }, 900);
        }
        catch (err) {
            const message = err instanceof Error ? err.message : 'Error inesperado al procesar el registro en el servidor.';
            console.error('Error al guardar el candidato:', err);
            setError(message);
        }
        finally {
            setLoading(false);
        }
    };
    return ((0, jsx_runtime_1.jsx)("main", { className: "max-w-2xl mx-auto p-4 md:p-8 font-sans", children: (0, jsx_runtime_1.jsxs)("div", { className: "bg-white p-6 md:p-8 rounded-xl shadow-sm border border-slate-200", children: [(0, jsx_runtime_1.jsxs)("div", { className: "mb-6", children: [(0, jsx_runtime_1.jsx)(link_1.default, { href: "/", className: "text-sm font-medium text-blue-600 hover:text-blue-800 transition-colors", children: "\u2190 Volver al Dashboard del Panel" }), (0, jsx_runtime_1.jsx)("h1", { className: "text-2xl font-bold text-slate-900 mt-2", children: "Registrar Candidatura Cl\u00EDnica" }), (0, jsx_runtime_1.jsx)("p", { className: "text-sm text-slate-500 mt-1", children: "Inserta un nuevo perfil profesional en el sistema de gesti\u00F3n de talento." })] }), error && ((0, jsx_runtime_1.jsxs)("div", { className: "bg-red-50 border border-red-200 text-red-700 p-4 rounded-xl text-sm mb-6 shadow-sm", children: [(0, jsx_runtime_1.jsx)("strong", { children: "No se pudo guardar:" }), " ", error] })), success && ((0, jsx_runtime_1.jsx)("div", { className: "bg-green-50 border border-green-200 text-green-700 p-4 rounded-xl text-sm mb-6 shadow-sm", children: success })), (0, jsx_runtime_1.jsxs)("form", { onSubmit: handleSubmit, className: "space-y-6", children: [(0, jsx_runtime_1.jsxs)("div", { className: "bg-slate-50 p-4 rounded-xl border border-slate-200/60 space-y-4", children: [(0, jsx_runtime_1.jsx)("h3", { className: "text-xs font-bold text-slate-500 uppercase tracking-wider", children: "Datos Personales" }), (0, jsx_runtime_1.jsxs)("div", { className: "grid grid-cols-1 sm:grid-cols-2 gap-4", children: [(0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-xs font-semibold text-slate-700 mb-1", children: "Nombre *" }), (0, jsx_runtime_1.jsx)("input", { type: "text", required: true, placeholder: "Ej: Pedro", className: "w-full p-2.5 border border-slate-600 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-600 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium", value: firstName, onChange: (e) => setFirstName(e.target.value) })] }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-xs font-semibold text-slate-700 mb-1", children: "Apellidos *" }), (0, jsx_runtime_1.jsx)("input", { type: "text", required: true, placeholder: "Ej: S\u00E1nchez", className: "w-full p-2.5 border border-slate-600 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-600 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium", value: lastName, onChange: (e) => setLastName(e.target.value) })] })] }), (0, jsx_runtime_1.jsxs)("div", { className: "grid grid-cols-1 sm:grid-cols-2 gap-4", children: [(0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-xs font-semibold text-slate-700 mb-1", children: "Correo Electr\u00F3nico *" }), (0, jsx_runtime_1.jsx)("input", { type: "email", required: true, placeholder: "ejemplo@correo.com", className: "w-full p-2.5 border border-slate-600 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-600 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium", value: email, onChange: (e) => setEmail(e.target.value) })] }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-xs font-semibold text-slate-700 mb-1", children: "Tel\u00E9fono M\u00F3vil" }), (0, jsx_runtime_1.jsx)("input", { type: "text", placeholder: "+34 600 000 000", className: "w-full p-2.5 border border-slate-600 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-600 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium", value: phone, onChange: (e) => setPhone(e.target.value) })] })] })] }), (0, jsx_runtime_1.jsxs)("div", { className: "bg-slate-50 p-4 rounded-xl border border-slate-200/60 space-y-4", children: [(0, jsx_runtime_1.jsx)("h3", { className: "text-xs font-bold text-slate-500 uppercase tracking-wider", children: "Perfil Profesional" }), (0, jsx_runtime_1.jsxs)("div", { className: "grid grid-cols-1 sm:grid-cols-2 gap-4", children: [(0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-xs font-semibold text-slate-700 mb-1", children: "Especialidad / Posici\u00F3n" }), (0, jsx_runtime_1.jsx)("input", { type: "text", placeholder: "Ej: Enfermero de Quir\u00F3fano", className: "w-full p-2.5 border border-slate-600 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-600 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium", value: position, onChange: (e) => setPosition(e.target.value) })] }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-xs font-semibold text-slate-700 mb-1", children: "A\u00F1os de Experiencia Cl\u00EDnica" }), (0, jsx_runtime_1.jsx)("input", { type: "number", min: "0", placeholder: "0", className: "w-full p-2.5 border border-slate-600 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-600 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium", value: years === 0 ? '' : years, onChange: (e) => setYears(Number(e.target.value)) })] })] }), (0, jsx_runtime_1.jsxs)("div", { className: "grid grid-cols-1 sm:grid-cols-2 gap-4", children: [(0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-xs font-semibold text-slate-700 mb-1", children: "URL Perfil LinkedIn" }), (0, jsx_runtime_1.jsx)("input", { type: "url", placeholder: "https://linkedin.com/in/...", className: "w-full p-2.5 border border-slate-600 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-600 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium", value: linkedin, onChange: (e) => setLinkedin(e.target.value) })] }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-xs font-semibold text-slate-700 mb-1", children: "URL Curr\u00EDculum Vitae (PDF)" }), (0, jsx_runtime_1.jsx)("input", { type: "url", placeholder: "https://...", className: "w-full p-2.5 border border-slate-600 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-600 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium", value: cvUrl, onChange: (e) => setCvUrl(e.target.value) })] })] })] }), (0, jsx_runtime_1.jsxs)("div", { className: "flex gap-4 justify-end pt-2", children: [(0, jsx_runtime_1.jsx)(link_1.default, { href: "/", className: "px-5 py-2.5 rounded-lg text-sm font-semibold text-slate-600 hover:bg-slate-100 border border-slate-200 transition-all text-center", children: "Cancelar" }), (0, jsx_runtime_1.jsx)("button", { type: "submit", disabled: loading, className: "px-6 py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-sm rounded-lg shadow-sm transition-all disabled:opacity-50 flex items-center justify-center gap-2", children: loading ? ((0, jsx_runtime_1.jsxs)(jsx_runtime_1.Fragment, { children: [(0, jsx_runtime_1.jsx)("div", { className: "animate-spin rounded-full h-4 w-4 border-b-2 border-white" }), "Procesando..."] })) : ('Guardar Candidato') })] })] })] }) }));
}
//# sourceMappingURL=page.js.map