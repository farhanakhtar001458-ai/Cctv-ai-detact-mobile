"""Pydantic request/response models for the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: "UserOut"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    id: int
    username: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime | None = None
    last_login_at: datetime | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=8, max_length=200)
    full_name: str = Field(default="", max_length=128)
    role: Literal["admin", "supervisor", "viewer"] = "viewer"


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=128)
    role: Literal["admin", "supervisor", "viewer"] | None = None
    is_active: bool | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class CameraBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    location: str = Field(default="", max_length=128)
    source_type: Literal["rtsp", "http", "file", "webcam", "simulated"] = "rtsp"
    url: str = Field(default="", max_length=1024)
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=256)
    enabled: bool = True
    auto_start: bool = False
    inference_width: int = Field(default=960, ge=160, le=3840)
    inference_height: int = Field(default=540, ge=120, le=2160)
    frame_sample: int = Field(default=3, ge=1, le=30)
    detect_conf: float = Field(default=0.35, ge=0.05, le=0.95)
    phone_conf: float = Field(default=0.30, ge=0.05, le=0.95)
    persistence_threshold: float = Field(default=0.65, ge=0.3, le=0.99)
    alert_cooldown_s: int = Field(default=60, ge=5, le=3600)
    phone_detection_enabled: bool = True

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return v
        lowered = v.lower()
        allowed = ("rtsp://", "rtsps://", "http://", "https://", "sim://", "file://")
        if lowered.startswith(allowed):
            return v
        # allow bare file paths and webcam indices
        if lowered.isdigit() or v.startswith("/") or v.startswith("./") or v.startswith(".."):
            return v
        raise ValueError("URL must start with rtsp://, rtsps://, http://, https://, sim:// or be a file path/webcam index")


class CameraCreate(CameraBase):
    pass


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    location: str | None = Field(default=None, max_length=128)
    source_type: Literal["rtsp", "http", "file", "webcam", "simulated"] | None = None
    url: str | None = Field(default=None, max_length=1024)
    username: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)
    enabled: bool | None = None
    auto_start: bool | None = None
    inference_width: int | None = Field(default=None, ge=160, le=3840)
    inference_height: int | None = Field(default=None, ge=120, le=2160)
    frame_sample: int | None = Field(default=None, ge=1, le=30)
    detect_conf: float | None = Field(default=None, ge=0.05, le=0.95)
    phone_conf: float | None = Field(default=None, ge=0.05, le=0.95)
    persistence_threshold: float | None = Field(default=None, ge=0.3, le=0.99)
    alert_cooldown_s: int | None = Field(default=None, ge=5, le=3600)
    phone_detection_enabled: bool | None = None


class CameraOut(BaseModel):
    id: int
    name: str
    location: str
    source_type: str
    url_display: str
    has_credentials: bool
    enabled: bool
    auto_start: bool
    inference_width: int
    inference_height: int
    frame_sample: int
    detect_conf: float
    phone_conf: float
    persistence_threshold: float
    alert_cooldown_s: int
    phone_detection_enabled: bool
    runtime: dict[str, Any] | None = None
    created_at: datetime | None = None


class CameraTestRequest(CameraBase):
    pass


class EventOut(BaseModel):
    id: int
    event_uuid: str
    camera_id: int
    camera_name: str = ""
    tracking_id: str
    category: str
    confidence: float
    started_at: str | None
    ended_at: str | None
    duration_s: float
    alert_status: str
    source: str
    simulated: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)
    acknowledged_by: str | None = None
    review_note: str = ""


class AcknowledgeRequest(BaseModel):
    note: str = Field(default="", max_length=2000)


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


class SettingOut(BaseModel):
    key: str
    label: str
    category: str
    description: str
    default: Any
    value: Any


class MessageOut(BaseModel):
    ok: bool = True
    message: str = ""
    detail: Any | None = None


TokenResponse.model_rebuild()
