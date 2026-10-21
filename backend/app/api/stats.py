"""Dashboard statistics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.security import ViewerUser
from app.services import stats
from app.services.camera_manager import camera_manager

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/overview")
def overview(user: ViewerUser) -> dict:
    return stats.overview()


@router.get("/daily")
def daily(user: ViewerUser, days: int = Query(default=14, ge=1, le=365)) -> list[dict]:
    return stats.daily_summary(days)


@router.get("/cameras")
def cameras(user: ViewerUser, days: int = Query(default=7, ge=1, le=365)) -> list[dict]:
    return stats.camera_summary(days)


@router.get("/hourly")
def hourly(user: ViewerUser, days: int = Query(default=1, ge=1, le=30)) -> list[dict]:
    return stats.hourly_activity(days)


@router.get("/live")
def live(user: ViewerUser) -> dict:
    """Per-camera live runtime state for the dashboard grid."""
    return {"cameras": camera_manager.all_status(), "totals": camera_manager.totals()}
