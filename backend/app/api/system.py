"""Health, system information and the administrative audit trail."""

from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from app.core.config import settings
from app.core.security import AdminUser, ViewerUser
from app.services import stats
from app.services.audit import list_logs
from app.vision.detector import detector_manager

router = APIRouter(tags=["system"])


@router.get("/api/health")
def health() -> dict[str, Any]:
    """Unauthenticated liveness probe (no sensitive fields)."""
    return {
        "status": "ok",
        "version": settings.version,
        "time": datetime.now(timezone.utc).isoformat(),
        "detector_loaded": detector_manager.status.loaded,
    }


@router.get("/api/system/health")
def detailed_health(user: ViewerUser) -> dict[str, Any]:
    return stats.health()


@router.get("/api/system/info")
def info(user: ViewerUser) -> dict[str, Any]:
    return {
        "app": settings.app_name,
        "version": settings.version,
        "environment": settings.environment,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "detector": detector_manager.status.as_dict(),
        "features": {
            "phone_detection": detector_manager.phone_capable,
            "degraded_mode": detector_manager.status.degraded,
            "simulated_sources": settings.allow_simulated_sources,
            "tracker": settings.tracker_type,
            "database": "postgresql" if not settings.is_sqlite else "sqlite",
            "max_visible_people": settings.max_visible_people,
            "max_camera_workers": settings.max_camera_workers,
        },
        "privacy": {
            "facial_recognition": False,
            "biometric_profiles": False,
            "stores_video": False,
            "stores_audio": False,
            "anonymous_ids": True,
        },
    }


@router.get("/api/audit")
def audit_log(
    user: AdminUser,
    limit: int = Query(default=100, ge=1, le=500),
    action: str | None = None,
    actor: str | None = None,
) -> list[dict[str, Any]]:
    return list_logs(limit=limit, action=action, actor=actor)
