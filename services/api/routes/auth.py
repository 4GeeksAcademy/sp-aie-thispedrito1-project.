from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from models import AuthMeResponse, LoginRequest, ProfileRead, TokenResponse, UserRole
from security import authenticate_user, create_access_token, get_current_user
from auth_repository import AuthRepository

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
