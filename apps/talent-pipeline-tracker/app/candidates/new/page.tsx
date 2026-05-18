// /app/candidates/new/page.tsx
'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { talentApi } from '@/services/api';
import { CandidateFormData } from '@/types/tracker';

export default function NewCandidatePage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Estados locales controlados para los inputs
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [position, setPosition] = useState('');
  const [years, setYears] = useState<number>(0);
  const [linkedin, setLinkedin] = useState('');
  const [cvUrl, setCvUrl] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!firstName.trim() || !lastName.trim() || !email.trim()) {
      setError('Por favor, rellena todos los campos obligatorios (*).');
      return;
    }

    try {
      setLoading(true);
      setError(null);
      setSuccess(null);

      const payload: CandidateFormData = {
        full_name: `${firstName.trim()} ${lastName.trim()}`,
        email: email.trim().toLowerCase(),
        phone: phone.trim() || '+34000000000',
        position: position.trim() || 'No especificada',
        experience_years: Number(years) || 0,
        linkedin: linkedin.trim() && linkedin.startsWith('http') ? linkedin.trim() : 'https://linkedin.com/in/placeholder',
        cv_url: cvUrl.trim() && cvUrl.startsWith('http') ? cvUrl.trim() : 'https://storage.healthcore.com/cv/placeholder.pdf',
      };

      console.log('Enviando payload del nuevo candidato a la API:', payload);
      const createdCandidate = await talentApi.createRecord(payload);
      if (typeof window !== 'undefined') {
        window.sessionStorage.setItem('latestCandidate', JSON.stringify(createdCandidate));
      }
      setSuccess('Candidatura registrada correctamente. Redirigiendo al dashboard...');
      
      setTimeout(() => {
        router.push('/');
        router.refresh();
      }, 900);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Error inesperado al procesar el registro en el servidor.';
      console.error('Error al guardar el candidato:', err);
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="max-w-2xl mx-auto p-4 md:p-8 font-sans">
      <div className="bg-white p-6 md:p-8 rounded-xl shadow-sm border border-slate-200">
        
        <div className="mb-6">
          <Link href="/" className="text-sm font-medium text-blue-600 hover:text-blue-800 transition-colors">
            &larr; Volver al Dashboard del Panel
          </Link>
          <h1 className="text-2xl font-bold text-slate-900 mt-2">Registrar Candidatura Clínica</h1>
          <p className="text-sm text-slate-500 mt-1">Inserta un nuevo perfil profesional en el sistema de gestión de talento.</p>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-xl text-sm mb-6 shadow-sm">
            <strong>No se pudo guardar:</strong> {error}
          </div>
        )}

        {success && (
          <div className="bg-green-50 border border-green-200 text-green-700 p-4 rounded-xl text-sm mb-6 shadow-sm">
            {success}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">
          
          <div className="bg-slate-50 p-4 rounded-xl border border-slate-200/60 space-y-4">
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Datos Personales</h3>
            
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">Nombre *</label>
                <input
                  type="text"
                  required
                  placeholder="Ej: Pedro"
                  className="w-full p-2.5 border border-slate-300 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium"
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">Apellidos *</label>
                <input
                  type="text"
                  required
                  placeholder="Ej: Sánchez"
                  className="w-full p-2.5 border border-slate-300 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium"
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">Correo Electrónico *</label>
                <input
                  type="email"
                  required
                  placeholder="ejemplo@correo.com"
                  className="w-full p-2.5 border border-slate-300 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">Teléfono Móvil</label>
                <input
                  type="text"
                  placeholder="+34 600 000 000"
                  className="w-full p-2.5 border border-slate-300 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                />
              </div>
            </div>
          </div>

          <div className="bg-slate-50 p-4 rounded-xl border border-slate-200/60 space-y-4">
            <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Perfil Profesional</h3>
            
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">Especialidad / Posición</label>
                <input
                  type="text"
                  placeholder="Ej: Enfermero de Quirófano"
                  className="w-full p-2.5 border border-slate-300 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium"
                  value={position}
                  onChange={(e) => setPosition(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">Años de Experiencia Clínica</label>
                <input
                  type="number"
                  min="0"
                  placeholder="0"
                  className="w-full p-2.5 border border-slate-300 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium"
                  value={years === 0 ? '' : years}
                  onChange={(e) => setYears(Number(e.target.value))}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">URL Perfil LinkedIn</label>
                <input
                  type="url"
                  placeholder="https://linkedin.com/in/..."
                  className="w-full p-2.5 border border-slate-300 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium"
                  value={linkedin}
                  onChange={(e) => setLinkedin(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-700 mb-1">URL Currículum Vitae (PDF)</label>
                <input
                  type="url"
                  placeholder="https://..."
                  className="w-full p-2.5 border border-slate-300 rounded-lg text-sm bg-white text-slate-900 placeholder-slate-400 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-medium"
                  value={cvUrl}
                  onChange={(e) => setCvUrl(e.target.value)}
                />
              </div>
            </div>
          </div>

          <div className="flex gap-4 justify-end pt-2">
            <Link
              href="/"
              className="px-5 py-2.5 rounded-lg text-sm font-semibold text-slate-600 hover:bg-slate-100 border border-slate-200 transition-all text-center"
            >
              Cancelar
            </Link>
            <button
              type="submit"
              disabled={loading}
              className="px-6 py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-sm rounded-lg shadow-sm transition-all disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                  Procesando...
                </>
              ) : (
                'Guardar Candidato'
              )}
            </button>
          </div>

        </form>
      </div>
    </main>
  );
}