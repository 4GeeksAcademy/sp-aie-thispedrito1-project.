from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from dotenv import load_dotenv

from auth_repository import AuthRepository

load_dotenv(Path(__file__).resolve().parent / ".env")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")
auth_repo = AuthRepository()


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _get_secret_key() -> str:
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY environment variable is required")
    return secret


def _get_algorithm() -> str:
    return os.getenv("JWT_ALGORITHM", "HS256")


def _get_expiry_minutes() -> int:
    raw = os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES must be an integer") from exc


def _get_reset_expiry_minutes() -> int:
    raw = os.getenv("RESET_TOKEN_EXPIRE_MINUTES", "30")
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("RESET_TOKEN_EXPIRE_MINUTES must be an integer") from exc


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=_get_expiry_minutes())
    payload = {"sub": subject, "role": role, "exp": expire}
    return jwt.encode(payload, _get_secret_key(), algorithm=_get_algorithm())


def create_reset_token(subject: str) -> str:
    """Create a short-lived signed reset token with a unique jti for single-use tracking."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=_get_reset_expiry_minutes())
    payload = {"sub": subject, "type": "reset", "jti": str(uuid4()), "exp": expire}
    return jwt.encode(payload, _get_secret_key(), algorithm=_get_algorithm())


def decode_reset_token(token: str) -> dict[str, Any]:
    """Validate signature, expiry and shape of a reset token. Raise ValueError if invalid."""
    try:
        payload = jwt.decode(token, _get_secret_key(), algorithms=[_get_algorithm()])
    except JWTError as exc:
        raise ValueError("Invalid or expired reset token") from exc

    if payload.get("type") != "reset" or not payload.get("sub") or not payload.get("jti"):
        raise ValueError("Invalid reset token")

    return payload


def authenticate_user(email: str, password: str) -> dict[str, Any] | None:
    user = auth_repo.get_user_by_email(email)
    if not user:
        return None

    if not verify_password(password, user["hashed_password"]):
        return None

    if not user.get("is_active", True):
        return None

    return user


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, _get_secret_key(), algorithms=[_get_algorithm()])
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise _credentials_exception()
    except JWTError as exc:
        raise _credentials_exception() from exc

    user = auth_repo.get_user_by_id(user_id)
    if not user or not user.get("is_active", True):
        raise _credentials_exception()

    return user


def require_admin(user: dict[str, Any]) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")


def ensure_self_or_admin(target_user_id: str, user: dict[str, Any]) -> None:
    if user.get("id") == target_user_id:
        return
    if user.get("role") == "admin":
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
