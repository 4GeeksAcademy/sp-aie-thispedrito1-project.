import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HealthCore Digital | Atencion ambulatoria moderna y segura",
  description:
    "HealthCore Digital moderniza la atencion ambulatoria con citas online, IA clinica y cumplimiento HIPAA y UK GDPR.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}