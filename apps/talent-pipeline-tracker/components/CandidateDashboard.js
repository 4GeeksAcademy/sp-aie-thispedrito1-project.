// /components/CandidateDashboard.tsx
'use client';
"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.default = CandidateDashboard;
const jsx_runtime_1 = require("react/jsx-runtime");
const react_1 = require("react");
const navigation_1 = require("next/navigation");
const link_1 = __importDefault(require("next/link"));
const api_1 = require("@/services/api");
const STATUS_LABELS = {
    received: 'Recibido',
    in_progress: 'En Progreso',
    selected: 'Seleccionado',
    discarded: 'Descartado',
};
const STAGE_LABELS = {
    pending: 'Pendiente',
    review: 'Revisión',
    personal_interview: 'Entrevista Personal',
    technical_interview: 'Entrevista Técnica',
    offer_presented: 'Oferta Presentada',
};
const isRecord = (value) => (typeof value === 'object' && value !== null);
const normalizeCandidate = (candidate) => {
    if (!isRecord(candidate))
        return null;
    const fullName = String(candidate.full_name || '').trim();
    if (!fullName || fullName.includes('#'))
        return null;
    return {
        ...candidate,
        id: String(candidate.id),
        full_name: fullName,
        email: String(candidate.email || ''),
        stage: String(candidate.stage || candidate.step || '').trim() || 'pending',
        step: String(candidate.step || candidate.stage || '').trim() || 'pending',
        experience_years: Number(candidate.experience_years || candidate.years_of_experience || 0),
    };
};
const mergeUniqueById = (items) => {
    const map = new Map();
    for (const item of items) {
        if (item?.id && !map.has(item.id)) {
            map.set(item.id, item);
        }
    }
    return Array.from(map.values());
};
// Datos de semilla locales para que la interfaz nunca se vea vacía en la primera carga
const BACKUP_CANDIDATES = [
    {
        id: "mock-1",
        full_name: "Dr. Michael Smith",
        email: "m.smith@healthcore.com",
        position: "Cardiólogo Clínico",
        experience_years: 8,
        status: "in_progress",
        stage: "review"
    },
    {
        id: "mock-2",
        full_name: "Enf. Sandra Ortega",
        email: "sandra.o@healthcore.com",
        position: "Enfermera Instrumentista",
        experience_years: 4,
        status: "received",
        stage: "pending"
    }
];
function CandidateDashboard() {
    const router = (0, navigation_1.useRouter)();
    const searchParams = (0, navigation_1.useSearchParams)();
    // Estados locales para los datos de la API
    const [candidates, setCandidates] = (0, react_1.useState)([]);
    const [loading, setLoading] = (0, react_1.useState)(true);
    const [error, setError] = (0, react_1.useState)(null);
    // Filtros de la URL de Next.js
    const currentStatus = searchParams.get('status') || 'all';
    const currentStage = searchParams.get('step') || 'all';
    const searchQuery = searchParams.get('search') || '';
    // Función de carga de datos pura y limpia
    const loadCandidates = async () => {
        try {
            setLoading(true);
            setError(null);
            const responseData = await api_1.talentApi.getRecords();
            console.log("Datos frescos recuperados del servidor:", responseData);
            let serverRecords = [];
            if (Array.isArray(responseData)) {
                serverRecords = responseData;
            }
            else if (responseData && typeof responseData === 'object') {
                const payload = responseData;
                const records = payload.records;
                const data = payload.data;
                serverRecords = Array.isArray(records)
                    ? records
                    : Array.isArray(data)
                        ? data
                        : [];
            }
            const validServerRecords = serverRecords
                .map(normalizeCandidate)
                .filter((candidate) => candidate !== null);
            const latestCandidateRaw = typeof window !== 'undefined' ? window.sessionStorage.getItem('latestCandidate') : null;
            let latestCandidate = null;
            if (latestCandidateRaw) {
                try {
                    latestCandidate = normalizeCandidate(JSON.parse(latestCandidateRaw));
                }
                catch {
                    latestCandidate = null;
                }
            }
            // ESTRATEGIA DE HIDRATACIÓN PERFECCIONADA:
            // Combinamos siempre los datos reales del servidor (los nuevos que agregas) 
            // junto con los de respaldo para mantener el Dashboard interactivo.
            const normalizedBackup = BACKUP_CANDIDATES
                .map(normalizeCandidate)
                .filter((candidate) => candidate !== null);
            setCandidates(mergeUniqueById([
                ...(latestCandidate ? [latestCandidate] : []),
                ...validServerRecords,
                ...normalizedBackup,
            ]));
        }
        catch (err) {
            console.error("Error al conectar con la API en Dashboard:", err);
            setCandidates(BACKUP_CANDIDATES);
            setError("Modo Demo Activo: Cargando datos de contingencia local.");
        }
        finally {
            setLoading(false);
        }
    };
    // Forzar recarga cada vez que cambien los parámetros de búsqueda o filtros
    (0, react_1.useEffect)(() => {
        const timeoutId = window.setTimeout(() => {
            void loadCandidates();
        }, 0);
        return () => {
            window.clearTimeout(timeoutId);
        };
    }, [searchParams]);
    // Manejo de actualización manual (Botón Sincronizar)
    const handleManualRefresh = () => {
        router.refresh();
        loadCandidates();
    };
    // Actualizar parámetros en la barra de direcciones de Next.js
    const updateFilters = (key, value) => {
        const params = new URLSearchParams(searchParams.toString());
        if (value && value !== 'all') {
            params.set(key, value);
        }
        else {
            params.delete(key);
        }
        router.push(`/?${params.toString()}`);
    };
    // Lógica de filtrado en cliente tolerante a nulos
    const filteredCandidates = candidates.filter(candidate => {
        if (!candidate)
            return false;
        // Filtro por Estado (received, in_progress, discarded)
        const candidateStatus = candidate.status ? String(candidate.status).toLowerCase().trim() : 'received';
        const matchStatus = currentStatus === 'all' || candidateStatus === currentStatus.toLowerCase().trim();
        // Filtro por Etapa
        const candidateStage = String(candidate.stage || candidate.step || 'pending').toLowerCase().trim();
        const matchStage = currentStage === 'all' || candidateStage === currentStage.toLowerCase().trim();
        // Búsqueda selectiva por Texto
        const fullName = String(candidate.full_name || '').toLowerCase().trim();
        const email = String(candidate.email || '').toLowerCase().trim();
        const search = searchQuery.toLowerCase().trim();
        const matchSearch = search === '' || fullName.includes(search) || email.includes(search);
        return matchStatus && matchStage && matchSearch;
    });
    return ((0, jsx_runtime_1.jsxs)("div", { className: "space-y-6", children: [(0, jsx_runtime_1.jsx)("div", { className: "bg-white p-6 rounded-xl shadow-sm border border-slate-200 space-y-4", children: (0, jsx_runtime_1.jsxs)("div", { className: "flex flex-col md:flex-row gap-4 justify-between items-center", children: [(0, jsx_runtime_1.jsx)("div", { className: "w-full md:w-1/3 relative", children: (0, jsx_runtime_1.jsx)("input", { type: "text", placeholder: "Buscar por nombre o email...", className: "w-full pl-4 pr-4 py-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-sm text-slate-900 font-medium", value: searchQuery, onChange: (e) => updateFilters('search', e.target.value) }) }), (0, jsx_runtime_1.jsxs)("div", { className: "w-full md:w-auto flex flex-wrap gap-3 items-center", children: [(0, jsx_runtime_1.jsx)("button", { onClick: handleManualRefresh, className: "p-2 text-slate-700 hover:text-blue-600 border border-slate-600 rounded-lg bg-white text-sm font-semibold transition-colors mt-5 flex items-center gap-1 shadow-sm", children: "\uD83D\uDD04 Sincronizar" }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-xs font-bold text-slate-500 uppercase tracking-wider mb-1", children: "Estado" }), (0, jsx_runtime_1.jsxs)("select", { className: "p-2 border border-slate-600 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none font-medium text-slate-800", value: currentStatus, onChange: (e) => updateFilters('status', e.target.value), children: [(0, jsx_runtime_1.jsx)("option", { value: "all", children: "Todos los Estados" }), (0, jsx_runtime_1.jsx)("option", { value: "received", children: "Recibido (received)" }), (0, jsx_runtime_1.jsx)("option", { value: "in_progress", children: "En Progreso (in_progress)" }), (0, jsx_runtime_1.jsx)("option", { value: "selected", children: "Seleccionado (selected)" }), (0, jsx_runtime_1.jsx)("option", { value: "discarded", children: "Descartado (discarded)" })] })] }), (0, jsx_runtime_1.jsxs)("div", { children: [(0, jsx_runtime_1.jsx)("label", { className: "block text-xs font-bold text-slate-500 uppercase tracking-wider mb-1", children: "Etapa Cl\u00EDnica" }), (0, jsx_runtime_1.jsxs)("select", { className: "p-2 border border-slate-600 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none font-medium text-slate-800", value: currentStage, onChange: (e) => updateFilters('step', e.target.value), children: [(0, jsx_runtime_1.jsx)("option", { value: "all", children: "Todas las Etapas" }), (0, jsx_runtime_1.jsx)("option", { value: "pending", children: "Pendiente" }), (0, jsx_runtime_1.jsx)("option", { value: "review", children: "Revisi\u00F3n" }), (0, jsx_runtime_1.jsx)("option", { value: "personal_interview", children: "Entrevista Personal" }), (0, jsx_runtime_1.jsx)("option", { value: "technical_interview", children: "Entrevista T\u00E9cnica" }), (0, jsx_runtime_1.jsx)("option", { value: "offer_presented", children: "Oferta Presentada" })] })] }), (0, jsx_runtime_1.jsx)(link_1.default, { href: "/candidates/new", className: "mt-5 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-sm rounded-lg transition-colors shadow-sm", children: "+ Nuevo Candidato" })] })] }) }), loading && ((0, jsx_runtime_1.jsxs)("div", { className: "text-center py-12 bg-white rounded-xl border border-slate-200", children: [(0, jsx_runtime_1.jsx)("div", { className: "animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto mb-3" }), (0, jsx_runtime_1.jsx)("p", { className: "text-slate-500 text-sm", children: "Actualizando base de datos en tiempo real..." })] })), error && !loading && ((0, jsx_runtime_1.jsxs)("div", { className: "bg-amber-50 border border-amber-200 text-amber-800 p-4 rounded-xl text-sm", children: ["\u26A0\uFE0F ", error] })), !loading && ((0, jsx_runtime_1.jsx)("div", { className: "bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden", children: (0, jsx_runtime_1.jsxs)("table", { className: "w-full text-left border-collapse", children: [(0, jsx_runtime_1.jsx)("thead", { children: (0, jsx_runtime_1.jsxs)("tr", { className: "bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-500 uppercase tracking-wider", children: [(0, jsx_runtime_1.jsx)("th", { className: "p-4", children: "Candidato" }), (0, jsx_runtime_1.jsx)("th", { className: "p-4", children: "Posici\u00F3n" }), (0, jsx_runtime_1.jsx)("th", { className: "p-4", children: "A\u00F1os Exp." }), (0, jsx_runtime_1.jsx)("th", { className: "p-4", children: "Estado" }), (0, jsx_runtime_1.jsx)("th", { className: "p-4", children: "Etapa" }), (0, jsx_runtime_1.jsx)("th", { className: "p-4 text-right", children: "Acciones" })] }) }), (0, jsx_runtime_1.jsx)("tbody", { className: "divide-y divide-slate-100 text-sm text-slate-700", children: filteredCandidates.length === 0 ? ((0, jsx_runtime_1.jsx)("tr", { children: (0, jsx_runtime_1.jsx)("td", { colSpan: 6, className: "text-center py-12 text-slate-400 italic", children: "No se encontraron expedientes cl\u00EDnicos con los filtros seleccionados." }) })) : (filteredCandidates.map((candidate) => ((0, jsx_runtime_1.jsxs)("tr", { className: "hover:bg-slate-50/70 transition-colors", children: [(0, jsx_runtime_1.jsxs)("td", { className: "p-4", children: [(0, jsx_runtime_1.jsx)("div", { className: "font-semibold text-slate-900", children: candidate.full_name }), (0, jsx_runtime_1.jsx)("div", { className: "text-xs text-slate-400", children: candidate.email })] }), (0, jsx_runtime_1.jsx)("td", { className: "p-4 font-medium text-slate-600", children: candidate.position || 'No especificada' }), (0, jsx_runtime_1.jsxs)("td", { className: "p-4", children: [candidate.experience_years || 0, " a\u00F1os"] }), (0, jsx_runtime_1.jsx)("td", { className: "p-4", children: (0, jsx_runtime_1.jsx)("span", { className: `inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${candidate.status === 'selected' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' :
                                                candidate.status === 'in_progress' ? 'bg-amber-50 text-amber-700 border border-amber-200' :
                                                    candidate.status === 'discarded' ? 'bg-red-50 text-red-700 border border-red-200' :
                                                        'bg-blue-50 text-blue-700 border border-blue-200'}`, children: STATUS_LABELS[String(candidate.status || 'received')] || String(candidate.status || 'received') }) }), (0, jsx_runtime_1.jsx)("td", { className: "p-4 text-slate-500", children: STAGE_LABELS[String(candidate.stage || candidate.step || '')] || 'Sin asignar' }), (0, jsx_runtime_1.jsx)("td", { className: "p-4 text-right", children: (0, jsx_runtime_1.jsx)(link_1.default, { href: `/candidates/${candidate.id}`, className: "text-blue-600 hover:text-blue-800 font-semibold hover:underline", children: "Revisar perfil \u2192" }) })] }, candidate.id)))) })] }) }))] }));
}
//# sourceMappingURL=CandidateDashboard.js.map