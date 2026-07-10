// /components/CandidateForm.tsx
'use client';
"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.default = CandidateForm;
const jsx_runtime_1 = require("react/jsx-runtime");
const react_1 = require("react");
const navigation_1 = require("next/navigation");
const api_1 = require("@/services/api");
const tracker_1 = require("@/types/tracker");
function CandidateForm({ initialData, isEditing = false }) {
    const router = (0, navigation_1.useRouter)();
    const [loading, setLoading] = (0, react_1.useState)(false);
    const [error, setError] = (0, react_1.useState)(null);
    const [success, setSuccess] = (0, react_1.useState)(false);
    const nameParts = (initialData?.full_name || '').trim().split(/\s+/);
    const defaultFirstName = initialData?.first_name || nameParts[0] || '';
    const defaultLastName = initialData?.last_name || nameParts.slice(1).join(' ') || '';
    // Estado del formulario inicializado con datos existentes o vacío
    const [formData, setFormData] = (0, react_1.useState)({
        firstName: defaultFirstName,
        lastName: defaultLastName,
        email: initialData?.email || '',
        phone: initialData?.phone || '',
        position: initialData?.position || '',
        linkedin: initialData?.linkedin || '',
        cv_url: initialData?.cv_url || '',
        experience_years: initialData?.experience_years ?? initialData?.years_of_experience ?? 0,
    });
    const handleChange = (e) => {
        const { name, value } = e.target;
        setFormData(prev => ({
            ...prev,
            [name]: name === 'experience_years' ? parseInt(value) || 0 : value
        }));
    };
    const handleSubmit = async (e) => {
        e.preventDefault();
        setLoading(true);
        setError(null);
        setSuccess(false);
        try {
            const payload = {
                full_name: `${formData.firstName.trim()} ${formData.lastName.trim()}`.trim(),
                email: formData.email,
                phone: formData.phone,
                position: formData.position,
                linkedin: formData.linkedin,
                cv_url: formData.cv_url,
                experience_years: formData.experience_years,
            };
            if (isEditing && initialData) {
                await api_1.talentApi.updateRecord(initialData.id.toString(), payload);
            }
            else {
                await api_1.talentApi.createRecord(payload);
            }
            setSuccess(true);
            // Si es exitoso, redirigimos al dashboard después de un breve momento
            setTimeout(() => {
                router.push('/');
                router.refresh(); // Forzamos a Next.js a revalidar los datos del servidor
            }, 1500);
        }
        catch (err) {
            setError(err.message || 'Ocurrió un error al procesar el expediente.');
        }
        finally {
            setLoading(false);
        }
    };
    return ((0, jsx_runtime_1.jsxs)("form", { onSubmit: handleSubmit, className: "bg-white p-8 rounded-xl shadow-sm border border-slate-200", children: [error && ((0, jsx_runtime_1.jsxs)("div", { className: "mb-6 bg-red-50 text-red-700 p-4 rounded-lg border border-red-200", children: [(0, jsx_runtime_1.jsx)("strong", { children: "Error de validaci\u00F3n:" }), " ", error] })), success && ((0, jsx_runtime_1.jsxs)("div", { className: "mb-6 bg-green-50 text-green-700 p-4 rounded-lg border border-green-200", children: ["Expediente ", isEditing ? 'actualizado' : 'registrado', " correctamente. Redirigiendo..."] })), (0, jsx_runtime_1.jsxs)("div", { className: "grid grid-cols-1 md:grid-cols-2 gap-6", children: [(0, jsx_runtime_1.jsxs)("div", { className: "space-y-4", children: [(0, jsx_runtime_1.jsx)("h3", { className: "text-lg font-semibold text-slate-800 border-b border-slate-100 pb-2", children: "Datos Personales" }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-sm font-medium text-slate-700 mb-1", children: "Nombre *" }), (0, jsx_runtime_1.jsx)("input", { required: true, type: "text", name: "firstName", value: formData.firstName, onChange: handleChange, className: "w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600", placeholder: "Ej. Sandra" })] }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-sm font-medium text-slate-700 mb-1", children: "Apellidos *" }), (0, jsx_runtime_1.jsx)("input", { required: true, type: "text", name: "lastName", value: formData.lastName, onChange: handleChange, className: "w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600", placeholder: "Ej. Okonkwo" })] }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-sm font-medium text-slate-700 mb-1", children: "Correo Electr\u00F3nico *" }), (0, jsx_runtime_1.jsx)("input", { required: true, type: "email", name: "email", value: formData.email, onChange: handleChange, className: "w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600", placeholder: "sandra@ejemplo.com" })] }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-sm font-medium text-slate-700 mb-1", children: "Tel\u00E9fono" }), (0, jsx_runtime_1.jsx)("input", { type: "tel", name: "phone", value: formData.phone, onChange: handleChange, className: "w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600", placeholder: "+1 (555) 000-0000" })] })] }), (0, jsx_runtime_1.jsxs)("div", { className: "space-y-4", children: [(0, jsx_runtime_1.jsx)("h3", { className: "text-lg font-semibold text-slate-800 border-b border-slate-100 pb-2", children: "Perfil Profesional" }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-sm font-medium text-slate-700 mb-1", children: "Posici\u00F3n Cl\u00EDnica / Admin *" }), (0, jsx_runtime_1.jsx)("input", { required: true, type: "text", name: "position", value: formData.position, onChange: handleChange, className: "w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600", placeholder: "Ej. Enfermera de Pr\u00E1ctica Avanzada" })] }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-sm font-medium text-slate-700 mb-1", children: "A\u00F1os de Experiencia *" }), (0, jsx_runtime_1.jsx)("input", { required: true, type: "number", min: "0", name: "experience_years", value: formData.experience_years, onChange: handleChange, className: "w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600" })] }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-sm font-medium text-slate-700 mb-1", children: "URL de LinkedIn" }), (0, jsx_runtime_1.jsx)("input", { type: "url", name: "linkedin", value: formData.linkedin, onChange: handleChange, className: "w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600", placeholder: "https://linkedin.com/in/perfil" })] }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-sm font-medium text-slate-700 mb-1", children: "URL del CV Documentado" }), (0, jsx_runtime_1.jsx)("input", { type: "url", name: "cv_url", value: formData.cv_url, onChange: handleChange, className: "w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600", placeholder: "https://enlace-al-documento.com/cv.pdf" })] })] })] }), (0, jsx_runtime_1.jsxs)("div", { className: "mt-8 pt-6 border-t border-slate-100 flex justify-end gap-4", children: [(0, jsx_runtime_1.jsx)("button", { type: "button", onClick: () => router.back(), className: "px-6 py-2 border border-slate-300 text-slate-700 rounded-lg hover:bg-slate-50 transition-colors", children: "Cancelar" }), (0, jsx_runtime_1.jsx)("button", { type: "submit", disabled: loading || success, className: "px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50", children: loading ? 'Procesando...' : (isEditing ? 'Actualizar Expediente' : 'Registrar Candidatura') })] })] }));
}
//# sourceMappingURL=CandidateForm.js.map