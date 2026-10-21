"""SQLAlchemy models: users, cameras, events, metrics, audit log, settings."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    VIEWER = "viewer"


class CameraSource(str, enum.Enum):
    RTSP = "rtsp"
    HTTP = "http"
    FILE = "file"
    WEBCAM = "webcam"
    SIMULATED = "simulated"


class EventCategory(str, enum.Enum):
    """Detection states surfaced to the dashboard.

    ``PERSON_DETECTED`` and ``PHONE_DETECTED`` are informational; only
    ``SUSTAINED_PHONE_USE`` triggers the audible alert by default.
    """

    PERSON_DETECTED = "person_detected"
    PHONE_DETECTED = "phone_detected"
    POSSIBLE_PHONE_USE = "possible_phone_use"
    SUSTAINED_PHONE_USE = "sustained_phone_use"
    UNCERTAIN = "uncertain"


class EventSource(str, enum.Enum):
    MODEL = "model"  # real inference output
    SIMULATED = "simulated"  # synthetic demo feed - never a real detection


class AlertStatus(str, enum.Enum):
    NONE = "none"
    PENDING = "pending"
    ANNOUNCED = "announced"
    ACKNOWLEDGED = "acknowledged"
    SUPPRESSED = "suppressed"


class CameraStatus(str, enum.Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    ONLINE = "online"
    ERROR = "error"
    RECONNECTING = "reconnecting"


def _uuid() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[UserRole] = mapped_column(String(16), default=UserRole.VIEWER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    location: Mapped[str] = mapped_column(String(128), default="")
    source_type: Mapped[str] = mapped_column(String(16), default=CameraSource.RTSP.value)
    # Full stream URL (may embed credentials) - always encrypted at rest.
    url_encrypted: Mapped[str] = mapped_column(Text, default="")
    # Safe display form with username/password stripped - the only form the API returns.
    url_display: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_start: Mapped[bool] = mapped_column(Boolean, default=False)

    inference_width: Mapped[int] = mapped_column(Integer, default=960)
    inference_height: Mapped[int] = mapped_column(Integer, default=540)
    frame_sample: Mapped[int] = mapped_column(Integer, default=3)
    detect_conf: Mapped[float] = mapped_column(Float, default=0.35)
    phone_conf: Mapped[float] = mapped_column(Float, default=0.30)
    persistence_threshold: Mapped[float] = mapped_column(Float, default=0.65)
    alert_cooldown_s: Mapped[int] = mapped_column(Integer, default=60)
    phone_detection_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Deleting a camera removes its events in the database (ON DELETE CASCADE on
    # the FK) without SQLAlchemy trying to NULL the required camera_id first.
    events: Mapped[list["DetectionEvent"]] = relationship(
        back_populates="camera",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DetectionEvent(Base):
    __tablename__ = "detection_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_uuid: Mapped[str] = mapped_column(String(32), default=_uuid, unique=True, index=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    tracking_id: Mapped[str] = mapped_column(String(24), default="", index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    alert_status: Mapped[str] = mapped_column(String(16), default=AlertStatus.NONE.value)
    source: Mapped[str] = mapped_column(String(16), default=EventSource.MODEL.value, index=True)
    evidence: Mapped[str] = mapped_column(Text, default="{}")  # JSON: geometry + reasoning
    acknowledged_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, default="")

    camera: Mapped[Camera] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_events_camera_time", "camera_id", "started_at"),
        Index("ix_events_category_time", "category", "started_at"),
    )


class CameraMetric(Base):
    """Lightweight periodic health/performance samples (no video stored)."""

    __tablename__ = "camera_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    fps: Mapped[float] = mapped_column(Float, default=0.0)
    inference_ms: Mapped[float] = mapped_column(Float, default=0.0)
    occupancy: Mapped[int] = mapped_column(Integer, default=0)
    active_alerts: Mapped[int] = mapped_column(Integer, default=0)
    cpu_percent: Mapped[float] = mapped_column(Float, default=0.0)
    memory_mb: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default=CameraStatus.STOPPED.value)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")


class AppSetting(Base):
    """Key/value runtime configuration editable by admins (voice, retention...)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (UniqueConstraint("key", name="uq_app_settings_key"),)
