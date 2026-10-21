"""Password hashing, JWT issuing/validation and role based access control."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Iterable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import User, UserRole
from app.db.session import get_db

PBKDF2_ITERATIONS = 260_000
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)

ROLE_RANK = {UserRole.VIEWER: 1, UserRole.SUPERVISOR: 2, UserRole.ADMIN: 3}


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 with per-password salt (stdlib only, no native deps)."""
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_access_token(
    subject: str,
    role: str,
    extra: dict | None = None,
    ttl_minutes: int | None = None,
) -> tuple[str, datetime]:
    ttl = ttl_minutes if ttl_minutes is not None else settings.access_token_ttl_minutes
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl)
    payload: dict = {
        "sub": subject,
        "role": role,
        "iat": datetime.now(timezone.utc),
        "exp": expires,
        "jti": secrets.token_hex(8),
        "iss": "smart-classroom-ai",
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, expires


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer="smart-classroom-ai",
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc


def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(token)
    user = db.execute(select(User).where(User.username == payload.get("sub"))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    return user


def get_current_user_flexible(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
) -> User:
    """Authenticate from the Authorization header or a ``token`` query param.

    The query-parameter form exists for MJPEG previews: ``<img>`` tags cannot
    set headers. The UI therefore requests a short-lived stream token.
    """
    candidate = token or request.query_params.get("token") or request.query_params.get("access_token")
    if not candidate:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(candidate)
    if payload.get("scope") not in (None, "stream"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token scope")
    user = db.execute(select(User).where(User.username == payload.get("sub"))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    return user


def require_roles(*roles: UserRole):
    """Dependency factory enforcing a minimum role."""

    minimum = max(ROLE_RANK[r] for r in roles)

    def _checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        if ROLE_RANK[user.role] < minimum:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires role {'/'.join(r.value for r in roles)}",
            )
        return user

    return _checker


AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
SupervisorUser = Annotated[User, Depends(require_roles(UserRole.SUPERVISOR))]
ViewerUser = Annotated[User, Depends(get_current_user)]


def user_has_role(user: User, roles: Iterable[UserRole]) -> bool:
    return ROLE_RANK[user.role] >= max(ROLE_RANK[r] for r in roles)
