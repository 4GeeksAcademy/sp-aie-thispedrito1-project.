export interface LoginPayload {
  email: string;
  password: string;
}

export interface RegisterPayload {
  email: string;
  password: string;
  name?: string;
  phone?: string;
  address?: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer" | string;
}

export interface AuthProfile {
  id: string;
  user_id: string;
  name?: string | null;
  phone?: string | null;
  address?: string | null;
}

export interface AuthMeResponse {
  id: string;
  email: string;
  role: "admin" | "manager" | "user";
  profile: AuthProfile;
}

export interface ProfileUpdatePayload {
  name?: string;
  phone?: string;
  address?: string;
}
