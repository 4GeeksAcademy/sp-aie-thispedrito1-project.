"""Contratos de la API de autenticación federada (`/auth/federated`).

Ningún esquema lleva tokens de Microsoft: solo el JWT propio de HealthCore
cuando un login federado tiene éxito (el mismo que emite /auth/login).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class FederatedProvider(BaseModel):
    provider: str
    enabled: bool
    mode: Optional[str] = None  # "microsoft" | "mock" (el backoffice avisa en modo simulado)


class FederatedProvidersResponse(BaseModel):
    providers: list[FederatedProvider]


class FederatedStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    redirect_uri: str = Field(min_length=1, max_length=2048)


class FederatedStartResponse(BaseModel):
    authorization_url: str
    # El navegador lo guarda en sessionStorage y lo compara en el callback:
    # liga el flujo a la pestaña que lo empezó (anti login-CSRF).
    state: str
    expires_in: int


class FederatedCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=512)
    redirect_uri: str = Field(min_length=1, max_length=2048)


class FederatedLinkPublic(BaseModel):
    provider: str
    provider_user_id: str
    email_at_link_time: Optional[str] = None
    linked_at: str


class FederatedCallbackResponse(BaseModel):
    purpose: Literal["login", "link"]
    message: str
    access_token: Optional[str] = None
    token_type: Optional[str] = None
    link: Optional[FederatedLinkPublic] = None


class FederatedAccountStatus(BaseModel):
    links: list[FederatedLinkPublic]
    has_password: bool
    # Código de aviso si desvincular dejaría la cuenta sin acceso
    # ("last_access_method"); None = se puede desvincular.
    unlink_warning: Optional[str] = None


class FederatedUnlinkResponse(BaseModel):
    message: str
    provider: str
    account: FederatedAccountStatus


class FederatedAuditEvent(BaseModel):
    id: str
    event: str
    at: str
    user_id: Optional[str] = None
    provider: str
    provider_user_id: Optional[str] = None
    reason: Optional[str] = None


class FederatedAuditResponse(BaseModel):
    events: list[FederatedAuditEvent]
