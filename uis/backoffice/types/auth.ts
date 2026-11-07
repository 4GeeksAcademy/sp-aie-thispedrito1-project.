import type { AccessProfile } from "./access";

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

// Refleja ProfilePublic del backend (auditoría de serialización): la API ya
// no devuelve el id de la fila de perfil ni user_id — el primero es detalle
// de almacenamiento y el segundo es redundante, porque tanto /profiles/me
// como /auth/me están siempre acotados al usuario del token.
export interface AuthProfile {
  name?: string | null;
  phone?: string | null;
  address?: string | null;
}

// Roles y Permisos: /auth/me devuelve rol y departamento (dos ejes
// independientes) y las capacidades que el rol concede. Ver types/access.ts.
export interface AuthMeResponse extends AccessProfile {
  id: string;
  email: string;
  profile: AuthProfile;
}

export interface ProfileUpdatePayload {
  name?: string;
  phone?: string;
  address?: string;
}

export interface ForgotPasswordPayload {
  email: string;
}

export interface ResetPasswordPayload {
  token: string;
  new_password: string;
}

export interface MessageResponse {
  message: string;
}
