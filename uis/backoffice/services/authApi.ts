import type { AuthMeResponse, ForgotPasswordPayload, LoginPayload, MessageResponse, ProfileUpdatePayload, RegisterPayload, ResetPasswordPayload, TokenResponse } from "../types/auth";
import { requestJson } from "./http";

export const authApi = {
  login(payload: LoginPayload): Promise<TokenResponse> {
    return requestJson<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  register(payload: RegisterPayload): Promise<void> {
    return requestJson<void>("/users", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  forgotPassword(payload: ForgotPasswordPayload): Promise<MessageResponse> {
    return requestJson<MessageResponse>("/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  resetPassword(payload: ResetPasswordPayload): Promise<MessageResponse> {
    return requestJson<MessageResponse>("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  getMe(): Promise<AuthMeResponse> {
    return requestJson<AuthMeResponse>("/auth/me", undefined, { authRequired: true });
  },

  updateMyProfile(payload: ProfileUpdatePayload): Promise<AuthMeResponse["profile"]> {
    return requestJson<AuthMeResponse["profile"]>("/profiles/me", {
      method: "PUT",
      body: JSON.stringify(payload),
    }, { authRequired: true });
  },
};
