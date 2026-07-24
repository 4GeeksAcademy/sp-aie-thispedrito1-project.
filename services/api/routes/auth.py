from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from models import (
    AuthMeResponse,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    ProfileRead,
    ResetPasswordRequest,
    TokenResponse,
    UserRole,
)
from security import (
    authenticate_user,
    create_access_token,
    create_reset_token,
    decode_reset_token,
    get_current_user,
    hash_password,
    verify_password,
)
from auth_repository import AuthRepository
from email_service import send_password_reset_email

router = APIRouter(prefix="/auth", tags=["auth"])
auth_repo = AuthRepository()


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    user = authenticate_user(email=str(payload.email), password=payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    token = create_access_token(subject=user["id"], role=user["role"])
    return TokenResponse(access_token=token, token_type="bearer")


@router.post("/token", response_model=TokenResponse)
def oauth_token(form_data: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    user = authenticate_user(email=form_data.username, password=form_data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    token = create_access_token(subject=user["id"], role=user["role"])
    return TokenResponse(access_token=token, token_type="bearer")


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordRequest) -> MessageResponse:
    # Always respond with the same message to avoid user enumeration.
    generic = MessageResponse(
        message="If that address is registered, you'll receive a reset link shortly."
    )
    user = auth_repo.get_user_by_email(str(payload.email))
    if user and user.get("is_active", True):
        token = create_reset_token(subject=user["id"])
        send_password_reset_email(to_email=user["email"], token=token)
    return generic


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest) -> MessageResponse:
    invalid = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    try:
        claims = decode_reset_token(payload.token)
    except ValueError as exc:
        raise invalid from exc

    jti = claims["jti"]
    if auth_repo.is_reset_token_used(jti):
        raise invalid

    user = auth_repo.get_user_by_id(claims["sub"])
    if not user or not user.get("is_active", True):
        raise invalid

    auth_repo.set_password(user["id"], hash_password(payload.new_password))
    auth_repo.consume_reset_token(jti)
    return MessageResponse(message="Password updated successfully.")


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> MessageResponse:
    if not verify_password(payload.current_password, current_user["hashed_password"]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

    auth_repo.set_password(current_user["id"], hash_password(payload.new_password))
    return MessageResponse(message="Password updated successfully.")


@router.get("/me", response_model=AuthMeResponse)
def me(current_user: dict[str, Any] = Depends(get_current_user)) -> AuthMeResponse:
    profile = auth_repo.get_profile_by_user_id(current_user["id"])
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    return AuthMeResponse(
        id=current_user["id"],
        email=current_user["email"],
        role=UserRole(current_user["role"]),
        profile=ProfileRead.model_validate(profile),
    )
