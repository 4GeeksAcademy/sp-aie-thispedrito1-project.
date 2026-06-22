// /app/page.tsx
import { Suspense } from 'react';
import CandidateDashboard from '@/components/CandidateDashboard';

export default function Home() {
  return (
    <main className="min-h-screen bg-slate-50 p-6 md:p-12 font-sans">
      <div className="max-w-7xl mx-auto space-y-8">
        <header className="border-b border-slate-200 pb-6">
          <h1 className="text-3xl font-bold text-blue-900 tracking-tight">
            HealthCore Digital
          </h1>
          <p className="text-slate-500 mt-2 text-lg">
            Talent Pipeline Tracker · Personas y Fuerza Laboral
          </p>
        </header>

        {/* El Suspense es obligatorio en Next.js App Router cuando usamos useSearchParams en el cliente */}
        <Suspense fallback={
          <div className="flex justify-center items-center h-64">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
          </div>
        }>
          <CandidateDashboard />
        </Suspense>
      </div>
    </main>
  );
}