import type { Metadata } from "next";
import { Manrope } from "next/font/google";
import "./globals.css";

const manrope = Manrope({
  subsets: ["latin"],
  variable: "--font-manrope",
});

export const metadata: Metadata = {
  title: "HealthCore Digital | Atencion ambulatoria moderna y segura",
  description:
    "HealthCore Digital moderniza la atencion ambulatoria con citas online, IA clinica y cumplimiento HIPAA y UK GDPR.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es" className={manrope.variable}>
      <body>{children}</body>
    </html>
  );
}
