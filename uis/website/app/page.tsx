import { BenefitsSection } from "../components/BenefitsSection";
import { ContactSection } from "../components/ContactSection";
import { CtaSection } from "../components/CtaSection";
import { HeroSection } from "../components/HeroSection";
import { ServicesSection } from "../components/ServicesSection";
import { SiteFooter } from "../components/SiteFooter";
import { SiteHeader } from "../components/SiteHeader";

export default function WebsiteHomePage() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "LocalBusiness",
    name: "HealthCore Digital",
    description:
      "Red internacional de salud ambulatoria con plataforma digital para reservas, documentacion asistida por IA y cumplimiento regulatorio.",
    foundingDate: "2011",
    numberOfEmployees: 200,
    areaServed: ["Texas", "Florida", "Georgia", "Londres", "Manchester"],
    slogan: "Atencion el mismo dia con estandares clinicos y digitales de clase internacional",
    url: "https://healthcore.example",
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <SiteHeader />
      <main>
        <HeroSection />
        <BenefitsSection />
        <ServicesSection />
        <CtaSection />
        <ContactSection />
      </main>
      <SiteFooter />
    </>
  );
}
