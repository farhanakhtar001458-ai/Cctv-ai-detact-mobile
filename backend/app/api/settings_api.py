"""Runtime configuration API (detection, voice, privacy/retention)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool

from app.api.schemas import MessageOut, SettingOut, SettingsUpdate
from app.core.rate_limit import enforce
from app.core.security import AdminUser, SupervisorUser, ViewerUser
from app.services.audit import record as audit
from app.services.camera_manager import camera_manager
from app.services.settings_store import settings_store
from app.services.tts import Announcement, voice_service
from app.vision.detector import detector_manager

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=list[SettingOut])
def get_settings(user: ViewerUser) -> list[SettingOut]:
    return [SettingOut(**item) for item in settings_store.describe()]


@router.put("", response_model=MessageOut)
def update_settings(payload: SettingsUpdate, user: AdminUser, request: Request) -> MessageOut:
    enforce(request, "settings_write", "30/minute")
    try:
        applied = settings_store.set_many(payload.values)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid setting value(s): {exc}") from exc
    audit("settings_updated", actor=user.username, detail=str(applied),
          ip=request.client.host if request.client else "")
    return MessageOut(message="Settings saved", detail=applied)


@router.post("/reset", response_model=MessageOut)
def reset_settings(user: AdminUser) -> MessageOut:
    settings_store.reset()
    audit("settings_reset", actor=user.username)
    return MessageOut(message="Settings restored to defaults")


@router.post("/voice/test", response_model=MessageOut)
def test_voice(user: SupervisorUser, text: str | None = None) -> MessageOut:
    """Queue a test announcement through the configured output path."""
    enabled = bool(settings_store.get("voice_enabled", True))
    output = str(settings_store.get("voice_output", "dashboard"))
    message = (text or str(settings_store.get("voice_message_sustained", ""))).strip()
    if not message:
        message = "Mobile phone detected. This is a system test."
    announcement = Announcement(
        text=f"[TEST] {message}",
        camera_id=None,
        camera_name="System test",
        event_id=None,
        category="test",
        output=output if enabled else "dashboard",
    )
    voice_service.announce(announcement)
    audit("voice_test", actor=user.username, detail=output)
    return MessageOut(
        message="Test announcement queued",
        detail={
            "output": output if enabled else "dashboard",
            "voice_enabled": enabled,
            "engine": voice_service.engine.as_dict(),
        },
    )


@router.get("/voice/status")
def voice_status(user: ViewerUser) -> dict[str, Any]:
    return {
        **voice_service.status(),
        "enabled": bool(settings_store.get("voice_enabled", True)),
        "output": settings_store.get("voice_output", "dashboard"),
        "message_sustained": settings_store.get("voice_message_sustained", ""),
        "language": settings_store.get("voice_language", "en"),
        "rate": settings_store.get("voice_rate", 175),
        "volume": settings_store.get("voice_volume", 0.8),
        "history": voice_service.history()[:10],
    }


@router.get("/model")
def model_status(user: ViewerUser) -> dict[str, Any]:
    return detector_manager.status.as_dict()


@router.post("/model/reload", response_model=MessageOut)
async def reload_model(user: AdminUser) -> MessageOut:
    started = time.perf_counter()
    status_obj = await run_in_threadpool(detector_manager.load, True)
    audit("model_reloaded", actor=user.username, detail=str(status_obj.as_dict()))
    for camera_id in camera_manager.running_ids():
        camera_manager.restart(camera_id)
    return MessageOut(
        message="Detector reloaded" if status_obj.loaded else "Detector unavailable - see detail",
        detail={**status_obj.as_dict(), "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)},
    )
