import type { BenefitItem, ContactItem, ServiceItem, StatItem } from "../types/content";

export const stats: StatItem[] = [
  { value: "12", label: "clinicas activas" },
  { value: "200", label: "profesionales" },
  { value: "$28M", label: "facturacion anual" },
];

export const benefits: BenefitItem[] = [
  {
    title: "Citas en el mismo dia",
    description:
      "Canal digital para reducir dependencia telefonica y consolidar una agenda compartida entre sedes.",
  },
  {
    title: "Horarios ampliados",
    description:
      "Mayor disponibilidad para pacientes activos, con experiencia de reserva simple desde movil o escritorio.",
  },
  {
    title: "Atencion bilingue",
    description:
      "Comunicacion clara en mercados de EE.UU., reforzando adherencia y satisfaccion durante todo el proceso.",
  },
];

export const services: ServiceItem[] = [
  {
    title: "Atencion primaria coordinada",
    description:
      "Evaluacion inicial, seguimiento continuo y derivacion eficiente entre sedes para una experiencia de atencion integrada.",
    image:
      "https://images.unsplash.com/photo-1584982751601-97dcc096659c?auto=format&fit=crop&w=900&q=80",
    alt: "Medica de atencion primaria conversando con una paciente durante una consulta",
  },
  {
    title: "Consultas especializadas",
    description:
      "Acceso rapido a especialistas con historial estructurado y trazabilidad completa para decisiones basadas en evidencia.",
    image:
      "https://images.unsplash.com/photo-1579154204601-01588f351e67?auto=format&fit=crop&w=900&q=80",
    alt: "Profesional sanitario revisando datos clinicos en una pantalla",
  },
  {
    title: "Cronicos y medicina preventiva",
    description:
      "Protocolos de control y prevencion con recordatorios inteligentes para mejorar adherencia y continuidad asistencial.",
    image:
      "https://images.unsplash.com/photo-1532938911079-1b06ac7ceec7?auto=format&fit=crop&w=900&q=80",
    alt: "Paciente y profesional revisando un plan de medicina preventiva",
  },
];

export const contactInfo: ContactItem[] = [
  { label: "Correo", value: "growth@healthcore.example" },
  { label: "Telefono", value: "+1 (800) 555-0124" },
  { label: "Horario de atencion", value: "Lunes a Viernes, 08:00 - 19:00" },
];
