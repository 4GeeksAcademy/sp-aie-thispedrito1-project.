from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from auth_repository import AuthRepository
from models import ProfileRead, UserCreate, UserPublic, UserRole, UserUpdate, UserWithProfile
from security import ensure_self_or_admin, get_current_user, hash_password, require_admin

router = APIRouter(prefix="/users", tags=["users"])
auth_repo = AuthRepository()


@router.post("", response_model=UserWithProfile, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate) -> UserWithProfile:
    hashed = hash_password(payload.password)
    try:
        user, profile = auth_repo.create_user(
            email=str(payload.email),
            hashed_password=hashed,
            role=UserRole.user,
            profile_data={
                "name": payload.name,
                "phone": payload.phone,
                "address": payload.address,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return UserWithProfile(
        id=user["id"],
        email=user["email"],
        is_active=user["is_active"],
        role=UserRole(user["role"]),
        created_at=user["created_at"],
        profile=ProfileRead.model_validate(profile),
    )


@router.get("", response_model=list[UserPublic])
def list_users(current_user: dict[str, Any] = Depends(get_current_user)) -> list[UserPublic]:
    _ = current_user
    return [UserPublic.model_validate(item) for item in auth_repo.list_users()]


@router.get("/{user_id}", response_model=UserPublic)
def get_user(user_id: str, current_user: dict[str, Any] = Depends(get_current_user)) -> UserPublic:
    _ = current_user
    found = auth_repo.get_user_by_id(user_id)
    if not found:
        raise HTTPException(status_code=404, detail="User not found")
    return UserPublic.model_validate(found)


@router.put("/{user_id}", response_model=UserPublic)
def update_user(user_id: str, payload: UserUpdate, current_user: dict[str, Any] = Depends(get_current_user)) -> UserPublic:
    ensure_self_or_admin(user_id, current_user)

    if payload.role is not None and current_user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can change role")

    try:
        updated = auth_repo.update_user(user_id, email=str(payload.email) if payload.email else None, role=payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if not updated:
        raise HTTPException(status_code=404, detail="User not found")

    return UserPublic.model_validate(updated)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, current_user: dict[str, Any] = Depends(get_current_user)) -> Response:
    require_admin(current_user)

    deleted = auth_repo.delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")

    return Response(status_code=status.HTTP_204_NO_CONTENT)
