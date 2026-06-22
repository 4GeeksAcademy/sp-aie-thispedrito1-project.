'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import CandidateForm from '@/components/CandidateForm';
import { talentApi } from '@/services/api';
import { Candidate } from '@/types/tracker';

export default function EditCandidatePage() {
  const params = useParams<{ id: string }>();
  const candidateId = params?.id;

  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadCandidate = async () => {
      if (!candidateId) {
        setError('No se recibió un ID de candidato válido.');
        setLoading(false);
        return;
      }

      try {
        setLoading(true);
        setError(null);
        const candidateData = await talentApi.getRecordById(candidateId);
        setCandidate(candidateData);
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : 'No se pudo cargar la candidatura.';
        setError(message);
      } finally {
        setLoading(false);
      }
    };

    void loadCandidate();
  }, [candidateId]);

  return (
    <main className="min-h-screen bg-slate-50 p-6 md:p-12 font-sans">
      <div className="max-w-4xl mx-auto space-y-6">
        <div>
          <Link href={candidateId ? `/candidates/${candidateId}` : '/'} className="text-sm font-medium text-blue-600 hover:text-blue-800 transition-colors">
            &larr; Volver al detalle
          </Link>
          <h1 className="text-2xl font-bold text-slate-900 mt-2">Editar Candidatura</h1>
          <p className="text-sm text-slate-500 mt-1">Actualiza la información del perfil profesional.</p>
        </div>

        {loading && (
          <div className="text-center py-12 bg-white rounded-xl border border-slate-200">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto mb-3"></div>
            <p className="text-slate-500 text-sm">Cargando candidatura...</p>
          </div>
        )}

        {error && !loading && (
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-xl text-sm">
            <strong>No se pudo cargar:</strong> {error}
          </div>
        )}

        {!loading && !error && candidate && (
          <CandidateForm initialData={candidate} isEditing />
        )}
      </div>
    </main>
  );
}
