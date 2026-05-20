// /components/CandidateDetail.tsx
'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { talentApi } from '@/services/api';
import { Candidate, CandidateNote, ApplicationStatus, ApplicationStep } from '@/types/tracker';

const STATUS_OPTIONS: Array<{ value: ApplicationStatus; label: string }> = [
  { value: 'received', label: 'Recibido' },
  { value: 'in_progress', label: 'En Progreso' },
  { value: 'selected', label: 'Seleccionado' },
  { value: 'discarded', label: 'Descartado' },
];

const STAGE_OPTIONS: Array<{ value: ApplicationStep; label: string }> = [
  { value: 'pending', label: 'Pendiente' },
  { value: 'review', label: 'Revisión' },
  { value: 'personal_interview', label: 'Entrevista Personal' },
  { value: 'technical_interview', label: 'Entrevista Técnica' },
  { value: 'offer_presented', label: 'Oferta Presentada' },
];

export default function CandidateDetail({ candidateId }: { candidateId: string }) {
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [notes, setNotes] = useState<CandidateNote[]>([]);
  
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<boolean>(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  
  const [newNote, setNewNote] = useState<string>('');
  const [noteSubmitting, setNoteSubmitting] = useState<boolean>(false);
  const [deletingNoteId, setDeletingNoteId] = useState<string | number | null>(null);

  useEffect(() => {
    const fetchDetailData = async () => {
      try {
        setLoading(true);
        setError(null);
        
        const [candidateData, notesData] = await Promise.all([
          talentApi.getRecordById(candidateId).catch(err => { throw err; }),
          talentApi.getNotes(candidateId).catch(() => [])
        ]);
        
        setCandidate({
          ...candidateData,
          status: (candidateData.status || 'received') as ApplicationStatus,
          stage: (candidateData.stage || candidateData.step || 'pending') as ApplicationStep,
          step: (candidateData.step || candidateData.stage || 'pending') as ApplicationStep,
        });
        setNotes(Array.isArray(notesData) ? notesData : []);

      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : 'Error al obtener el expediente del candidato.';
        setError(message);
      } finally {
        setLoading(false);
      }
    };

    fetchDetailData();
  }, [candidateId]);

  // 2. Actualización de Estado (Estrategia Dual para Diagnóstico)
  const handleUpdatePipeline = async (type: 'status' | 'stage', value: string) => {
    if (!candidate) return;
    
    try {
      setActionLoading(true);
      setActionError(null);
      setActionMessage(null);
      
      // Intentamos primero la forma más estándar posible para un PATCH clásico: enviar SOLO la llave que cambió.
      // Si la API arroja 422, nuestro catch atrapará la respuesta exacta del servidor.
      const minimalPayload = {
        [type]: value
      };

      const updatedCandidate = await talentApi.patchRecordStatus(candidateId, minimalPayload);
      setCandidate({
        ...updatedCandidate,
        status: (updatedCandidate.status || candidate.status || 'received') as ApplicationStatus,
        stage: (updatedCandidate.stage || updatedCandidate.step || candidate.stage || candidate.step || 'pending') as ApplicationStep,
        step: (updatedCandidate.step || updatedCandidate.stage || candidate.step || candidate.stage || 'pending') as ApplicationStep,
      });
      setActionMessage(type === 'status' ? 'Estado actualizado correctamente.' : 'Etapa actualizada correctamente.');
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Error al actualizar el expediente.';
      if (message.startsWith('API_422_DETAIL:')) {
        // Extraemos y formateamos el JSON interno para que sea legible en el navegador
        const rawDetail = message.replace('API_422_DETAIL: ', '');
        setActionError(`La API rechazó el cambio (422): ${rawDetail}`);
      } else {
        setActionError(`Error al actualizar el expediente: ${message}`);
      }
    } finally {
      setActionLoading(false);
    }
  };

  const handleAddNote = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newNote.trim()) return;

    try {
      setNoteSubmitting(true);
      setActionError(null);
      setActionMessage(null);
      const addedNote = await talentApi.addNote(candidateId, newNote.trim());
      setNotes(prev => [...prev, addedNote]);
      setNewNote(''); 
      setActionMessage('Nota guardada correctamente.');
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Error al guardar la nota.';
      setActionError(`Error al guardar la nota: ${message}`);
    } finally {
      setNoteSubmitting(false);
    }
  };

  const handleDeleteNote = async (noteId: string | number) => {
    const confirmDelete = window.confirm("¿Seguro que deseas eliminar esta nota del expediente?");
    if (!confirmDelete) return;

    try {
      setDeletingNoteId(noteId);
      setActionError(null);
      setActionMessage(null);
      await talentApi.deleteNote(candidateId, noteId);
      setNotes(prev => prev.filter(note => String(note.id) !== String(noteId)));
      setActionMessage('Nota eliminada correctamente.');
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Error al eliminar la nota.';
      setActionError(`Error al eliminar la nota: ${message}`);
    } finally {
      setDeletingNoteId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 bg-white rounded-xl border border-slate-200">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 mb-4"></div>
        <p className="text-slate-500">Recuperando expediente médico/administrativo...</p>
      </div>
    );
  }

  if (error || !candidate) {
    return (
      <div className="bg-red-50 border-l-4 border-red-500 p-6 rounded-r-xl shadow-sm">
        <h3 className="text-red-800 font-bold text-lg mb-2">Expediente no accesible</h3>
        <p className="text-red-700">{error || 'Candidato no encontrado'}</p>
        <Link href="/" className="inline-block mt-4 text-blue-600 hover:underline">
          &larr; Volver
        </Link>
      </div>
    );
  }

  const safeNotes = Array.isArray(notes) ? notes : [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between mb-8">
        <Link href="/" className="text-blue-600 hover:text-blue-800 font-medium transition-colors flex items-center gap-2">
          <span>&larr;</span> Volver
        </Link>
        <div className="flex items-center gap-3">
          <Link
            href={`/candidates/${candidate.id}/edit`}
            className="text-sm px-3 py-1.5 rounded-lg border border-slate-600 text-slate-700 hover:bg-slate-100"
          >
            Editar candidatura
          </Link>
          {actionLoading && <span className="text-sm text-slate-500 animate-pulse">Sincronizando cambios...</span>}
        </div>
      </div>

      {actionMessage && (
        <div className="bg-green-50 border border-green-200 text-green-700 p-3 rounded-lg text-sm">
          {actionMessage}
        </div>
      )}

      {actionError && (
        <div className="bg-red-50 border border-red-200 text-red-700 p-3 rounded-lg text-sm whitespace-pre-wrap">
          {actionError}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <div className="bg-white p-8 rounded-xl shadow-sm border border-slate-200">
            <div className="flex flex-col md:flex-row md:justify-between md:items-start gap-4 mb-8">
              <div>
                <h1 className="text-3xl font-bold text-slate-900">
                  {candidate.full_name || `${candidate.first_name || ''} ${candidate.last_name || ''}`.trim()}
                </h1>
                <p className="text-xl text-blue-700 font-medium mt-1">{candidate.position || 'Posición no especificada'}</p>
              </div>
              <div className="text-right text-sm text-slate-500">
                <p>Aplicación: {candidate.application_date ? new Date(candidate.application_date).toLocaleDateString('es-ES') : 'Fecha de registro'}</p>
                <p>ID HealthCore: #{candidate.id}</p>
              </div>
            </div>

            <div className="bg-slate-50 p-6 rounded-lg border border-slate-100 mb-8 flex flex-col sm:flex-row gap-6">
              <div className="flex-1">
                <label className="block text-sm font-semibold text-slate-700 mb-2">Estado del Proceso</label>
                <select 
                  className="w-full p-2.5 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none disabled:opacity-50 bg-white text-black"
                  value={candidate.status || 'received'}
                  onChange={(e) => handleUpdatePipeline('status', e.target.value)}
                  disabled={actionLoading}
                >
                  {STATUS_OPTIONS.map((statusOption) => (
                    <option key={statusOption.value} value={statusOption.value}>
                      {statusOption.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex-1">
                <label className="block text-sm font-semibold text-slate-700 mb-2">Etapa Actual</label>
                <select 
                  className="w-full p-2.5 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none disabled:opacity-50 bg-white text-black"
                  value={candidate.stage || candidate.step || 'pending'}
                  onChange={(e) => handleUpdatePipeline('stage', e.target.value)}
                  disabled={actionLoading}
                >
                  {STAGE_OPTIONS.map((stageOption) => (
                    <option key={stageOption.value} value={stageOption.value}>
                      {stageOption.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
              <div>
                <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Contacto</h3>
                <ul className="space-y-3 text-slate-700">
                  <li><strong className="font-medium">Email:</strong> {candidate.email}</li>
                  <li><strong className="font-medium">Teléfono:</strong> {candidate.phone || 'No proporcionado'}</li>
                </ul>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Perfil Profesional</h3>
                <ul className="space-y-3 text-slate-700">
                  <li><strong className="font-medium">Experiencia:</strong> {candidate.experience_years ?? candidate.years_of_experience ?? 0} años</li>
                  {candidate.linkedin && !candidate.linkedin.includes('no-proporcionado.com') && (
                    <li>
                      <a href={candidate.linkedin} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
                        Perfil de LinkedIn &#8599;
                      </a>
                    </li>
                  )}
                  {candidate.cv_url && !candidate.cv_url.includes('no-proporcionado.com') && (
                    <li>
                      <a href={candidate.cv_url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
                        Ver CV Documentado &#8599;
                      </a>
                    </li>
                  )}
                </ul>
              </div>
            </div>
          </div>
        </div>

        <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200 h-fit">
          <h2 className="text-lg font-bold text-slate-900 mb-4 flex items-center gap-2">
            Notas del Expediente
            <span className="bg-blue-100 text-blue-800 text-xs py-0.5 px-2 rounded-full">
              {safeNotes.length}
            </span>
          </h2>

          <form onSubmit={handleAddNote} className="mb-6">
            <textarea
              className="w-full p-3 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-sm resize-none text-black placeholder-slate-600"
              rows={3}
              placeholder="Añadir nota de entrevista, compliance, o requerimientos..."
              value={newNote}
              onChange={(e) => setNewNote(e.target.value)}
              disabled={noteSubmitting}
            />
            <button
              type="submit"
              disabled={noteSubmitting || !newNote.trim()}
              className="mt-2 w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {noteSubmitting ? 'Guardando...' : 'Guardar Nota'}
            </button>
          </form>

          <div className="space-y-4 max-h-[400px] overflow-y-auto pr-2">
            {safeNotes.length === 0 ? (
              <p className="text-slate-500 text-sm text-center py-4 italic">No hay notas registradas.</p>
            ) : (
              safeNotes.map((note) => (
                <div key={note.id} className="bg-slate-50 p-4 rounded-lg border border-slate-100 group relative">
                  <p className="text-sm text-black whitespace-pre-wrap">{note.content}</p>
                  <div className="mt-2 flex justify-between items-center text-xs text-slate-400">
                    <span>{note.created_at ? new Date(note.created_at).toLocaleString('es-ES') : 'Fecha de nota'}</span>
                    <button
                      type="button"
                      onClick={() => handleDeleteNote(note.id)}
                      disabled={deletingNoteId === note.id}
                      className="text-red-500 hover:text-red-700 opacity-0 group-hover:opacity-100 transition-opacity disabled:opacity-60"
                      title="Eliminar nota"
                    >
                      {deletingNoteId === note.id ? 'Eliminando...' : 'Eliminar'}
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}