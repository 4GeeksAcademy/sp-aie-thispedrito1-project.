from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from auth_repository import AuthRepository
from models import ProfilePublic, ProfileUpdate
from security import get_current_user

router = APIRouter(prefix="/profiles", tags=["profiles"])
auth_repo = AuthRepository()


@router.get("/me", response_model=ProfilePublic)
def get_my_profile(current_user: dict[str, Any] = Depends(get_current_user)) -> ProfilePublic:
    found = auth_repo.get_profile_by_user_id(current_user["id"])
    if not found:
        raise HTTPException(status_code=404, detail="Profile not found")
    return ProfilePublic.model_validate(found)


@router.put("/me", response_model=ProfilePublic)
def update_my_profile(payload: ProfileUpdate, current_user: dict[str, Any] = Depends(get_current_user)) -> ProfilePublic:
    updated = auth_repo.update_profile(current_user["id"], payload.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Profile not found")
    return ProfilePublic.model_validate(updated)
