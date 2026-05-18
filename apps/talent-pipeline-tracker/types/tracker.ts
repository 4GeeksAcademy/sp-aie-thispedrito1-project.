// /types/tracker.ts

export type ApplicationStatus = 'received' | 'in_progress' | 'selected' | 'discarded';
export type ApplicationStep = 'pending' | 'review' | 'personal_interview' | 'technical_interview' | 'offer_presented';

export interface Candidate {
  id: string; // Tipado como string para soportar UUID
  full_name: string;
  email: string;
  phone: string;
  position: string; 
  linkedin: string;
  cv_url: string;
  experience_years: number;
  status: ApplicationStatus;
  step?: ApplicationStep;
  stage?: ApplicationStep;
  application_date: string;
  first_name?: string;
  last_name?: string;
  years_of_experience?: number;
}

export interface CandidateNote {
  id: string | number; 
  candidate_id?: string;
  record_id?: string;
  content: string;
  created_at: string;
}

export interface CandidateFormData {
  full_name: string;
  email: string;
  phone: string;
  position: string;
  linkedin: string;
  cv_url: string;
  experience_years: number;
}