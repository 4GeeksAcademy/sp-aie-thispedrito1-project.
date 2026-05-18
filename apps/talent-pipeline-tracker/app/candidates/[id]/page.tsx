// /app/candidates/[id]/page.tsx
import CandidateDetail from '@/components/CandidateDetail';

interface PageProps {
  params: Promise<{ id: string }>;
}

// Convertimos la función en una función asíncrona (async)
export default async function CandidatePage({ params }: PageProps) {
  // Esperamos de forma asíncrona a que Next.js resuelva el ID de la URL
  const resolvedParams = await params;
  
  return (
    <main className="min-h-screen bg-slate-50 p-6 md:p-12 font-sans">
      <div className="max-w-5xl mx-auto">
        {/* Ahora sí pasamos el ID de texto limpio y verificado */}
        <CandidateDetail candidateId={resolvedParams.id} />
      </div>
    </main>
  );
}