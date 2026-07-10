// /components/CandidateDetail.tsx
'use client';
"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.default = CandidateDetail;
const jsx_runtime_1 = require("react/jsx-runtime");
const react_1 = require("react");
const link_1 = __importDefault(require("next/link"));
const api_1 = require("@/services/api");
const tracker_1 = require("@/types/tracker");
const STATUS_OPTIONS = [
    { value: 'received', label: 'Recibido' },
    { value: 'in_progress', label: 'En Progreso' },
    { value: 'selected', label: 'Seleccionado' },
    { value: 'discarded', label: 'Descartado' },
];
const STAGE_OPTIONS = [
    { value: 'pending', label: 'Pendiente' },
    { value: 'review', label: 'Revisión' },
    { value: 'personal_interview', label: 'Entrevista Personal' },
    { value: 'technical_interview', label: 'Entrevista Técnica' },
    { value: 'offer_presented', label: 'Oferta Presentada' },
];
function CandidateDetail({ candidateId }) {
    const [candidate, setCandidate] = (0, react_1.useState)(null);
    const [notes, setNotes] = (0, react_1.useState)([]);
    const [loading, setLoading] = (0, react_1.useState)(true);
    const [error, setError] = (0, react_1.useState)(null);
    const [actionLoading, setActionLoading] = (0, react_1.useState)(false);
    const [actionMessage, setActionMessage] = (0, react_1.useState)(null);
    const [actionError, setActionError] = (0, react_1.useState)(null);
    const [newNote, setNewNote] = (0, react_1.useState)('');
    const [noteSubmitting, setNoteSubmitting] = (0, react_1.useState)(false);
    const [deletingNoteId, setDeletingNoteId] = (0, react_1.useState)(null);
    (0, react_1.useEffect)(() => {
        const fetchDetailData = async () => {
            try {
                setLoading(true);
                setError(null);
                const [candidateData, notesData] = await Promise.all([
                    api_1.talentApi.getRecordById(candidateId).catch(err => { throw err; }),
                    api_1.talentApi.getNotes(candidateId).catch(() => [])
                ]);
                setCandidate({
                    ...candidateData,
                    status: (candidateData.status || 'received'),
                    stage: (candidateData.stage || candidateData.step || 'pending'),
                    step: (candidateData.step || candidateData.stage || 'pending'),
                });
                setNotes(Array.isArray(notesData) ? notesData : []);
            }
            catch (err) {
                const message = err instanceof Error ? err.message : 'Error al obtener el expediente del candidato.';
                setError(message);
            }
            finally {
                setLoading(false);
            }
        };
        fetchDetailData();
    }, [candidateId]);
    // 2. Actualización de Estado (Estrategia Dual para Diagnóstico)
    const handleUpdatePipeline = async (type, value) => {
        if (!candidate)
            return;
        try {
            setActionLoading(true);
            setActionError(null);
            setActionMessage(null);
            // Intentamos primero la forma más estándar posible para un PATCH clásico: enviar SOLO la llave que cambió.
            // Si la API arroja 422, nuestro catch atrapará la respuesta exacta del servidor.
            const minimalPayload = {
                [type]: value
            };
            const updatedCandidate = await api_1.talentApi.patchRecordStatus(candidateId, minimalPayload);
            setCandidate({
                ...updatedCandidate,
                status: (updatedCandidate.status || candidate.status || 'received'),
                stage: (updatedCandidate.stage || updatedCandidate.step || candidate.stage || candidate.step || 'pending'),
                step: (updatedCandidate.step || updatedCandidate.stage || candidate.step || candidate.stage || 'pending'),
            });
            setActionMessage(type === 'status' ? 'Estado actualizado correctamente.' : 'Etapa actualizada correctamente.');
        }
        catch (err) {
            const message = err instanceof Error ? err.message : 'Error al actualizar el expediente.';
            if (message.startsWith('API_422_DETAIL:')) {
                // Extraemos y formateamos el JSON interno para que sea legible en el navegador
                const rawDetail = message.replace('API_422_DETAIL: ', '');
                setActionError(`La API rechazó el cambio (422): ${rawDetail}`);
            }
            else {
                setActionError(`Error al actualizar el expediente: ${message}`);
            }
        }
        finally {
            setActionLoading(false);
        }
    };
    const handleAddNote = async (e) => {
        e.preventDefault();
        if (!newNote.trim())
            return;
        try {
            setNoteSubmitting(true);
            setActionError(null);
            setActionMessage(null);
            const addedNote = await api_1.talentApi.addNote(candidateId, newNote.trim());
            setNotes(prev => [...prev, addedNote]);
            setNewNote('');
            setActionMessage('Nota guardada correctamente.');
        }
        catch (err) {
            const message = err instanceof Error ? err.message : 'Error al guardar la nota.';
            setActionError(`Error al guardar la nota: ${message}`);
        }
        finally {
            setNoteSubmitting(false);
        }
    };
    const handleDeleteNote = async (noteId) => {
        const confirmDelete = window.confirm("¿Seguro que deseas eliminar esta nota del expediente?");
        if (!confirmDelete)
            return;
        try {
            setDeletingNoteId(noteId);
            setActionError(null);
            setActionMessage(null);
            await api_1.talentApi.deleteNote(candidateId, noteId);
            setNotes(prev => prev.filter(note => String(note.id) !== String(noteId)));
            setActionMessage('Nota eliminada correctamente.');
        }
        catch (err) {
            const message = err instanceof Error ? err.message : 'Error al eliminar la nota.';
            setActionError(`Error al eliminar la nota: ${message}`);
        }
        finally {
            setDeletingNoteId(null);
        }
    };
    if (loading) {
        return ((0, jsx_runtime_1.jsxs)("div", { className: "flex flex-col items-center justify-center h-64 bg-white rounded-xl border border-slate-200", children: [(0, jsx_runtime_1.jsx)("div", { className: "animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 mb-4" }), (0, jsx_runtime_1.jsx)("p", { className: "text-slate-500", children: "Recuperando expediente m\u00E9dico/administrativo..." })] }));
    }
    if (error || !candidate) {
        return ((0, jsx_runtime_1.jsxs)("div", { className: "bg-red-50 border-l-4 border-red-500 p-6 rounded-r-xl shadow-sm", children: [(0, jsx_runtime_1.jsx)("h3", { className: "text-red-800 font-bold text-lg mb-2", children: "Expediente no accesible" }), (0, jsx_runtime_1.jsx)("p", { className: "text-red-700", children: error || 'Candidato no encontrado' }), (0, jsx_runtime_1.jsx)(link_1.default, { href: "/", className: "inline-block mt-4 text-blue-600 hover:underline", children: "\u2190 Volver" })] }));
    }
    const safeNotes = Array.isArray(notes) ? notes : [];
    return ((0, jsx_runtime_1.jsxs)("div", { className: "space-y-6", children: [(0, jsx_runtime_1.jsxs)("div", { className: "flex items-center justify-between mb-8", children: [(0, jsx_runtime_1.jsxs)(link_1.default, { href: "/", className: "text-blue-600 hover:text-blue-800 font-medium transition-colors flex items-center gap-2", children: [(0, jsx_runtime_1.jsx)("span", { children: "\u2190" }), " Volver"] }), (0, jsx_runtime_1.jsxs)("div", { className: "flex items-center gap-3", children: [(0, jsx_runtime_1.jsx)(link_1.default, { href: `/candidates/${candidate.id}/edit`, className: "text-sm px-3 py-1.5 rounded-lg border border-slate-600 text-slate-700 hover:bg-slate-100", children: "Editar candidatura" }), actionLoading && (0, jsx_runtime_1.jsx)("span", { className: "text-sm text-slate-500 animate-pulse", children: "Sincronizando cambios..." })] })] }), actionMessage && ((0, jsx_runtime_1.jsx)("div", { className: "bg-green-50 border border-green-200 text-green-700 p-3 rounded-lg text-sm", children: actionMessage })), actionError && ((0, jsx_runtime_1.jsx)("div", { className: "bg-red-50 border border-red-200 text-red-700 p-3 rounded-lg text-sm whitespace-pre-wrap", children: actionError })), (0, jsx_runtime_1.jsxs)("div", { className: "grid grid-cols-1 lg:grid-cols-3 gap-6", children: [(0, jsx_runtime_1.jsx)("div", { className: "lg:col-span-2 space-y-6", children: (0, jsx_runtime_1.jsxs)("div", { className: "bg-white p-8 rounded-xl shadow-sm border border-slate-200", children: [(0, jsx_runtime_1.jsxs)("div", { className: "flex flex-col md:flex-row md:justify-between md:items-start gap-4 mb-8", children: [(0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("h1", { className: "text-3xl font-bold text-slate-900", children: candidate.full_name || `${candidate.first_name || ''} ${candidate.last_name || ''}`.trim() }), (0, jsx_runtime_1.jsx)("p", { className: "text-xl text-blue-700 font-medium mt-1", children: candidate.position || 'Posición no especificada' })] }), (0, jsx_runtime_1.jsxs)("div", { className: "text-right text-sm text-slate-500", children: [(0, jsx_runtime_1.jsxs)("p", { children: ["Aplicaci\u00F3n: ", candidate.application_date ? new Date(candidate.application_date).toLocaleDateString('es-ES') : 'Fecha de registro'] }), (0, jsx_runtime_1.jsxs)("p", { children: ["ID HealthCore: #", candidate.id] })] })] }), (0, jsx_runtime_1.jsxs)("div", { className: "bg-slate-50 p-6 rounded-lg border border-slate-100 mb-8 flex flex-col sm:flex-row gap-6", children: [(0, jsx_runtime_1.jsxs)("div", { className: "flex-1", children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-sm font-semibold text-slate-700 mb-2", children: "Estado del Proceso" }), (0, jsx_runtime_1.jsx)("select", { className: "w-full p-2.5 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none disabled:opacity-50 bg-white text-black", value: candidate.status || 'received', onChange: (e) => handleUpdatePipeline('status', e.target.value), disabled: actionLoading, children: STATUS_OPTIONS.map((statusOption) => ((0, jsx_runtime_1.jsx)("option", { value: statusOption.value, children: statusOption.label }, statusOption.value))) })] }), (0, jsx_runtime_1.jsxs)("div", { className: "flex-1", children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-sm font-semibold text-slate-700 mb-2", children: "Etapa Actual" }), (0, jsx_runtime_1.jsx)("select", { className: "w-full p-2.5 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none disabled:opacity-50 bg-white text-black", value: candidate.stage || candidate.step || 'pending', onChange: (e) => handleUpdatePipeline('stage', e.target.value), disabled: actionLoading, children: STAGE_OPTIONS.map((stageOption) => ((0, jsx_runtime_1.jsx)("option", { value: stageOption.value, children: stageOption.label }, stageOption.value))) })] })] }), (0, jsx_runtime_1.jsxs)("div", { className: "grid grid-cols-1 sm:grid-cols-2 gap-6", children: [(0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("h3", { className: "text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3", children: "Contacto" }), (0, jsx_runtime_1.jsxs)("ul", { className: "space-y-3 text-slate-700", children: [(0, jsx_runtime_1.jsxs)("li", { children: [(0, jsx_runtime_1.jsx)("strong", { className: "font-medium", children: "Email:" }), " ", candidate.email] }), (0, jsx_runtime_1.jsxs)("li", { children: [(0, jsx_runtime_1.jsx)("strong", { className: "font-medium", children: "Tel\u00E9fono:" }), " ", candidate.phone || 'No proporcionado'] })] })] }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("h3", { className: "text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3", children: "Perfil Profesional" }), (0, jsx_runtime_1.jsxs)("ul", { className: "space-y-3 text-slate-700", children: [(0, jsx_runtime_1.jsxs)("li", { children: [(0, jsx_runtime_1.jsx)("strong", { className: "font-medium", children: "Experiencia:" }), " ", candidate.experience_years ?? candidate.years_of_experience ?? 0, " a\u00F1os"] }), candidate.linkedin && !candidate.linkedin.includes('no-proporcionado.com') && ((0, jsx_runtime_1.jsx)("li", { children: (0, jsx_runtime_1.jsx)("a", { href: candidate.linkedin, target: "_blank", rel: "noreferrer", className: "text-blue-600 hover:underline", children: "Perfil de LinkedIn \u2197" }) })), candidate.cv_url && !candidate.cv_url.includes('no-proporcionado.com') && ((0, jsx_runtime_1.jsx)("li", { children: (0, jsx_runtime_1.jsx)("a", { href: candidate.cv_url, target: "_blank", rel: "noreferrer", className: "text-blue-600 hover:underline", children: "Ver CV Documentado \u2197" }) }))] })] })] })] }) }), (0, jsx_runtime_1.jsxs)("div", { className: "bg-white p-6 rounded-xl shadow-sm border border-slate-200 h-fit", children: [(0, jsx_runtime_1.jsxs)("h2", { className: "text-lg font-bold text-slate-900 mb-4 flex items-center gap-2", children: ["Notas del Expediente", (0, jsx_runtime_1.jsx)("span", { className: "bg-blue-100 text-blue-800 text-xs py-0.5 px-2 rounded-full", children: safeNotes.length })] }), (0, jsx_runtime_1.jsxs)("form", { onSubmit: handleAddNote, className: "mb-6", children: [(0, jsx_runtime_1.jsx)("textarea", { className: "w-full p-3 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-sm resize-none text-black placeholder-slate-600", rows: 3, placeholder: "A\u00F1adir nota de entrevista, compliance, o requerimientos...", value: newNote, onChange: (e) => setNewNote(e.target.value), disabled: noteSubmitting }), (0, jsx_runtime_1.jsx)("button", { type: "submit", disabled: noteSubmitting || !newNote.trim(), className: "mt-2 w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed", children: noteSubmitting ? 'Guardando...' : 'Guardar Nota' })] }), (0, jsx_runtime_1.jsx)("div", { className: "space-y-4 max-h-[400px] overflow-y-auto pr-2", children: safeNotes.length === 0 ? ((0, jsx_runtime_1.jsx)("p", { className: "text-slate-500 text-sm text-center py-4 italic", children: "No hay notas registradas." })) : (safeNotes.map((note) => ((0, jsx_runtime_1.jsxs)("div", { className: "bg-slate-50 p-4 rounded-lg border border-slate-100 group relative", children: [(0, jsx_runtime_1.jsx)("p", { className: "text-sm text-black whitespace-pre-wrap", children: note.content }), (0, jsx_runtime_1.jsxs)("div", { className: "mt-2 flex justify-between items-center text-xs text-slate-400", children: [(0, jsx_runtime_1.jsx)("span", { children: note.created_at ? new Date(note.created_at).toLocaleString('es-ES') : 'Fecha de nota' }), (0, jsx_runtime_1.jsx)("button", { type: "button", onClick: () => handleDeleteNote(note.id), disabled: deletingNoteId === note.id, className: "text-red-500 hover:text-red-700 opacity-0 group-hover:opacity-100 transition-opacity disabled:opacity-60", title: "Eliminar nota", children: deletingNoteId === note.id ? 'Eliminando...' : 'Eliminar' })] })] }, note.id)))) })] })] })] }));
}
//# sourceMappingURL=CandidateDetail.js.map