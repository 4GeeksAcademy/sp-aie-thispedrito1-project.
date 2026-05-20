// /components/CandidateForm.tsx
'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { talentApi } from '@/services/api';
import { CandidateFormData, Candidate } from '@/types/tracker';

interface CandidateFormProps {
  initialData?: Candidate;
  isEditing?: boolean;
}

export default function CandidateForm({ initialData, isEditing = false }: CandidateFormProps) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<boolean>(false);

  const nameParts = (initialData?.full_name || '').trim().split(/\s+/);
  const defaultFirstName = initialData?.first_name || nameParts[0] || '';
  const defaultLastName = initialData?.last_name || nameParts.slice(1).join(' ') || '';

  // Estado del formulario inicializado con datos existentes o vacío
  const [formData, setFormData] = useState({
    firstName: defaultFirstName,
    lastName: defaultLastName,
    email: initialData?.email || '',
    phone: initialData?.phone || '',
    position: initialData?.position || '',
    linkedin: initialData?.linkedin || '',
    cv_url: initialData?.cv_url || '',
    experience_years: initialData?.experience_years ?? initialData?.years_of_experience ?? 0,
  });

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: name === 'experience_years' ? parseInt(value) || 0 : value
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setSuccess(false);

    try {
      const payload: CandidateFormData = {
        full_name: `${formData.firstName.trim()} ${formData.lastName.trim()}`.trim(),
        email: formData.email,
        phone: formData.phone,
        position: formData.position,
        linkedin: formData.linkedin,
        cv_url: formData.cv_url,
        experience_years: formData.experience_years,
      };

      if (isEditing && initialData) {
        await talentApi.updateRecord(initialData.id.toString(), payload);
      } else {
        await talentApi.createRecord(payload);
      }
      
      setSuccess(true);
      
      // Si es exitoso, redirigimos al dashboard después de un breve momento
      setTimeout(() => {
        router.push('/');
        router.refresh(); // Forzamos a Next.js a revalidar los datos del servidor
      }, 1500);

    } catch (err: any) {
      setError(err.message || 'Ocurrió un error al procesar el expediente.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="bg-white p-8 rounded-xl shadow-sm border border-slate-200">
      {error && (
        <div className="mb-6 bg-red-50 text-red-700 p-4 rounded-lg border border-red-200">
          <strong>Error de validación:</strong> {error}
        </div>
      )}
      
      {success && (
        <div className="mb-6 bg-green-50 text-green-700 p-4 rounded-lg border border-green-200">
          Expediente {isEditing ? 'actualizado' : 'registrado'} correctamente. Redirigiendo...
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Información Personal */}
        <div className="space-y-4">
          <h3 className="text-lg font-semibold text-slate-800 border-b border-slate-100 pb-2">Datos Personales</h3>
          
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Nombre *</label>
            <input required type="text" name="firstName" value={formData.firstName} onChange={handleChange} className="w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600" placeholder="Ej. Sandra" />
          </div>
          
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Apellidos *</label>
            <input required type="text" name="lastName" value={formData.lastName} onChange={handleChange} className="w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600" placeholder="Ej. Okonkwo" />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Correo Electrónico *</label>
            <input required type="email" name="email" value={formData.email} onChange={handleChange} className="w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600" placeholder="sandra@ejemplo.com" />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Teléfono</label>
            <input type="tel" name="phone" value={formData.phone} onChange={handleChange} className="w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600" placeholder="+1 (555) 000-0000" />
          </div>
        </div>

        {/* Información Profesional */}
        <div className="space-y-4">
          <h3 className="text-lg font-semibold text-slate-800 border-b border-slate-100 pb-2">Perfil Profesional</h3>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Posición Clínica / Admin *</label>
            <input required type="text" name="position" value={formData.position} onChange={handleChange} className="w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600" placeholder="Ej. Enfermera de Práctica Avanzada" />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Años de Experiencia *</label>
            <input required type="number" min="0" name="experience_years" value={formData.experience_years} onChange={handleChange} className="w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600" />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">URL de LinkedIn</label>
            <input type="url" name="linkedin" value={formData.linkedin} onChange={handleChange} className="w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600" placeholder="https://linkedin.com/in/perfil" />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">URL del CV Documentado</label>
            <input type="url" name="cv_url" value={formData.cv_url} onChange={handleChange} className="w-full p-2 border border-slate-600 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none text-black placeholder-slate-600" placeholder="https://enlace-al-documento.com/cv.pdf" />
          </div>
        </div>
      </div>

      <div className="mt-8 pt-6 border-t border-slate-100 flex justify-end gap-4">
        <button type="button" onClick={() => router.back()} className="px-6 py-2 border border-slate-300 text-slate-700 rounded-lg hover:bg-slate-50 transition-colors">
          Cancelar
        </button>
        <button type="submit" disabled={loading || success} className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">
          {loading ? 'Procesando...' : (isEditing ? 'Actualizar Expediente' : 'Registrar Candidatura')}
        </button>
      </div>
    </form>
  );
}