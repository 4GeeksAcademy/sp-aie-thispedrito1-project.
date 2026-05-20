// /components/CandidateDashboard.tsx
'use client';

import { useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { talentApi } from '@/services/api';

const STATUS_LABELS: Record<string, string> = {
  received: 'Recibido',
  in_progress: 'En Progreso',
  selected: 'Seleccionado',
  discarded: 'Descartado',
};

const STAGE_LABELS: Record<string, string> = {
  pending: 'Pendiente',
  review: 'Revisión',
  personal_interview: 'Entrevista Personal',
  technical_interview: 'Entrevista Técnica',
  offer_presented: 'Oferta Presentada',
};

type DashboardCandidate = {
  id: string;
  full_name: string;
  email: string;
  position?: string;
  experience_years?: number;
  status?: string;
  step?: string;
  stage?: string;
};

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null
);

const normalizeCandidate = (candidate: unknown): DashboardCandidate | null => {
  if (!isRecord(candidate)) return null;

  const fullName = String(candidate.full_name || '').trim();
  if (!fullName || fullName.includes('#')) return null;

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

const mergeUniqueById = (items: DashboardCandidate[]): DashboardCandidate[] => {
  const map = new Map<string, DashboardCandidate>();
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

export default function CandidateDashboard() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Estados locales para los datos de la API
  const [candidates, setCandidates] = useState<DashboardCandidate[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Filtros de la URL de Next.js
  const currentStatus = searchParams.get('status') || 'all';
  const currentStage = searchParams.get('step') || 'all';
  const searchQuery = searchParams.get('search') || '';

  // Función de carga de datos pura y limpia
  const loadCandidates = async () => {
    try {
      setLoading(true);
      setError(null);
      
      const responseData = await talentApi.getRecords();
      console.log("Datos frescos recuperados del servidor:", responseData);

      let serverRecords: unknown[] = [];
      if (Array.isArray(responseData)) {
        serverRecords = responseData;
      } else if (responseData && typeof responseData === 'object') {
        const payload = responseData as Record<string, unknown>;
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
        .filter((candidate): candidate is DashboardCandidate => candidate !== null);

      const latestCandidateRaw = typeof window !== 'undefined' ? window.sessionStorage.getItem('latestCandidate') : null;
      let latestCandidate: DashboardCandidate | null = null;
      if (latestCandidateRaw) {
        try {
          latestCandidate = normalizeCandidate(JSON.parse(latestCandidateRaw));
        } catch {
          latestCandidate = null;
        }
      }

      // ESTRATEGIA DE HIDRATACIÓN PERFECCIONADA:
      // Combinamos siempre los datos reales del servidor (los nuevos que agregas) 
      // junto con los de respaldo para mantener el Dashboard interactivo.
      const normalizedBackup = BACKUP_CANDIDATES
        .map(normalizeCandidate)
        .filter((candidate): candidate is DashboardCandidate => candidate !== null);

      setCandidates(mergeUniqueById([
        ...(latestCandidate ? [latestCandidate] : []),
        ...validServerRecords,
        ...normalizedBackup,
      ]));

    } catch (err: unknown) {
      console.error("Error al conectar con la API en Dashboard:", err);
      setCandidates(BACKUP_CANDIDATES);
      setError("Modo Demo Activo: Cargando datos de contingencia local.");
    } finally {
      setLoading(false);
    }
  };

  // Forzar recarga cada vez que cambien los parámetros de búsqueda o filtros
  useEffect(() => {
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
  const updateFilters = (key: string, value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    if (value && value !== 'all') {
      params.set(key, value);
    } else {
      params.delete(key);
    }
    router.push(`/?${params.toString()}`);
  };

  // Lógica de filtrado en cliente tolerante a nulos
  const filteredCandidates = candidates.filter(candidate => {
    if (!candidate) return false;

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

  return (
    <div className="space-y-6">
      {/* SECCIÓN DE CONTROLES Y FILTROS */}
      <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200 space-y-4">
        <div className="flex flex-col md:flex-row gap-4 justify-between items-center">
          <div className="w-full md:w-1/3 relative">
            <input
              type="text"
              placeholder="Buscar por nombre o email..."
              className="w-full pl-4 pr-4 py-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-sm text-slate-900 font-medium"
              value={searchQuery}
              onChange={(e) => updateFilters('search', e.target.value)}
            />
          </div>

          <div className="w-full md:w-auto flex flex-wrap gap-3 items-center">
            <button 
              onClick={handleManualRefresh}
              className="p-2 text-slate-700 hover:text-blue-600 border border-slate-600 rounded-lg bg-white text-sm font-semibold transition-colors mt-5 flex items-center gap-1 shadow-sm"
            >
              🔄 Sincronizar
            </button>

            <div>
              <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-1">Estado</label>
              <select
                className="p-2 border border-slate-600 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none font-medium text-slate-800"
                value={currentStatus}
                onChange={(e) => updateFilters('status', e.target.value)}
              >
                <option value="all">Todos los Estados</option>
                <option value="received">Recibido (received)</option>
                <option value="in_progress">En Progreso (in_progress)</option>
                <option value="selected">Seleccionado (selected)</option>
                <option value="discarded">Descartado (discarded)</option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-1">Etapa Clínica</label>
              <select
                className="p-2 border border-slate-600 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none font-medium text-slate-800"
                value={currentStage}
                onChange={(e) => updateFilters('step', e.target.value)}
              >
                <option value="all">Todas las Etapas</option>
                <option value="pending">Pendiente</option>
                <option value="review">Revisión</option>
                <option value="personal_interview">Entrevista Personal</option>
                <option value="technical_interview">Entrevista Técnica</option>
                <option value="offer_presented">Oferta Presentada</option>
              </select>
            </div>

            <Link
              href="/candidates/new"
              className="mt-5 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-sm rounded-lg transition-colors shadow-sm"
            >
              + Nuevo Candidato
            </Link>
          </div>
        </div>
      </div>

      {/* MANEJO DE AVISOS ASÍNCRONOS */}
      {loading && (
        <div className="text-center py-12 bg-white rounded-xl border border-slate-200">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto mb-3"></div>
          <p className="text-slate-500 text-sm">Actualizando base de datos en tiempo real...</p>
        </div>
      )}

      {error && !loading && (
        <div className="bg-amber-50 border border-amber-200 text-amber-800 p-4 rounded-xl text-sm">
          ⚠️ {error}
        </div>
      )}

      {/* TABLA PRINCIPAL DE EXTRACCIÓN */}
      {!loading && (
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                <th className="p-4">Candidato</th>
                <th className="p-4">Posición</th>
                <th className="p-4">Años Exp.</th>
                <th className="p-4">Estado</th>
                <th className="p-4">Etapa</th>
                <th className="p-4 text-right">Acciones</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-sm text-slate-700">
              {filteredCandidates.length === 0 ? (
                <tr>
                  <td colSpan={6} className="text-center py-12 text-slate-400 italic">
                    No se encontraron expedientes clínicos con los filtros seleccionados.
                  </td>
                </tr>
              ) : (
                filteredCandidates.map((candidate) => (
                  <tr key={candidate.id} className="hover:bg-slate-50/70 transition-colors">
                    <td className="p-4">
                      <div className="font-semibold text-slate-900">{candidate.full_name}</div>
                      <div className="text-xs text-slate-400">{candidate.email}</div>
                    </td>
                    <td className="p-4 font-medium text-slate-600">{candidate.position || 'No especificada'}</td>
                    <td className="p-4">{candidate.experience_years || 0} años</td>
                    <td className="p-4">
                      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                        candidate.status === 'selected' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' :
                        candidate.status === 'in_progress' ? 'bg-amber-50 text-amber-700 border border-amber-200' :
                        candidate.status === 'discarded' ? 'bg-red-50 text-red-700 border border-red-200' :
                        'bg-blue-50 text-blue-700 border border-blue-200'
                      }`}>
                        {STATUS_LABELS[String(candidate.status || 'received')] || String(candidate.status || 'received')}
                      </span>
                    </td>
                    <td className="p-4 text-slate-500">
                      {STAGE_LABELS[String(candidate.stage || candidate.step || '')] || 'Sin asignar'}
                    </td>
                    <td className="p-4 text-right">
                      <Link
                        href={`/candidates/${candidate.id}`}
                        className="text-blue-600 hover:text-blue-800 font-semibold hover:underline"
                      >
                        Revisar perfil &rarr;
                      </Link>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}