"""Authentication, session identity and user administration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    LoginRequest,
    MessageOut,
    PasswordChange,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.core.rate_limit import enforce
from app.core.security import (
    AdminUser,
    ViewerUser,
    create_access_token,
    hash_password,
    verify_password,
)
from app.db.models import User
from app.db.session import get_db
from app.services.audit import record as audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _authenticate(db: Session, username: str, password: str, request: Request) -> User:
    enforce(request, "login", "10/minute")
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        audit("login_failed", actor=username[:64], ip=request.client.host if request.client else "")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    audit("login", actor=user.username, ip=request.client.host if request.client else "")
    return user


@router.post("/token", response_model=TokenResponse, summary="OAuth2 password flow (form)")
def token(
    request: Request,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    user = _authenticate(db, form.username, form.password, request)
    token_value, expires = create_access_token(user.username, user.role)
    return TokenResponse(access_token=token_value, expires_at=expires, user=_user_out(user))


@router.post("/login", response_model=TokenResponse, summary="JSON login")
def login(request: Request, payload: LoginRequest, db: Annotated[Session, Depends(get_db)]) -> TokenResponse:
    user = _authenticate(db, payload.username, payload.password, request)
    token_value, expires = create_access_token(user.username, user.role)
    return TokenResponse(access_token=token_value, expires_at=expires, user=_user_out(user))


@router.get("/me", response_model=UserOut)
def me(user: ViewerUser) -> UserOut:
    return _user_out(user)


@router.post("/change-password", response_model=MessageOut)
def change_password(
    payload: PasswordChange,
    user: ViewerUser,
    db: Annotated[Session, Depends(get_db)],
    request: Request,
) -> MessageOut:
    enforce(request, "password", "5/minute")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    db_user = db.get(User, user.id)
    assert db_user is not None
    db_user.password_hash = hash_password(payload.new_password)
    db.commit()
    audit("password_changed", actor=user.username)
    return MessageOut(message="Password updated")


@router.get("/users", response_model=list[UserOut], dependencies=[])
def list_users(user: AdminUser, db: Annotated[Session, Depends(get_db)]) -> list[UserOut]:
    rows = db.execute(select(User).order_by(User.id)).scalars().all()
    return [_user_out(u) for u in rows]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, user: AdminUser, db: Annotated[Session, Depends(get_db)]) -> UserOut:
    existing = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
    new_user = User(
        username=payload.username,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    audit("user_created", actor=user.username, target=payload.username, detail=f"role={payload.role}")
    return _user_out(new_user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    user: AdminUser,
    db: Annotated[Session, Depends(get_db)],
) -> UserOut:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    changes = payload.model_dump(exclude_none=True)
    if target.username == user.username and changes.get("is_active") is False:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account")
    for key, value in changes.items():
        setattr(target, key, value)
    db.commit()
    db.refresh(target)
    audit("user_updated", actor=user.username, target=target.username, detail=str(changes))
    return _user_out(target)


@router.delete("/users/{user_id}", response_model=MessageOut)
def delete_user(user_id: int, user: AdminUser, db: Annotated[Session, Depends(get_db)]) -> MessageOut:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if target.username == user.username:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    db.delete(target)
    db.commit()
    audit("user_deleted", actor=user.username, target=target.username)
    return MessageOut(message="User deleted")


@router.post("/stream-token")
def stream_token(user: ViewerUser) -> dict[str, object]:
    """Short-lived token for MJPEG/snapshot <img> URLs (headers unsupported)."""
    value, expires = create_access_token(user.username, user.role, {"scope": "stream"}, ttl_minutes=10)
    return {"token": value, "expires_at": expires.isoformat(), "ttl_minutes": 10}


@router.get("/permissions")
def permissions(user: ViewerUser) -> dict[str, Any]:
    """What the current role may do - the UI uses this to hide controls."""
    return {
        "role": user.role,
        "can_acknowledge": user.role in ("admin", "supervisor"),
        "can_configure_cameras": user.role in ("admin", "supervisor"),
        "can_control_cameras": user.role in ("admin", "supervisor"),
        "can_change_settings": user.role == "admin",
        "can_manage_users": user.role == "admin",
        "can_export": user.role in ("admin", "supervisor"),
    }
