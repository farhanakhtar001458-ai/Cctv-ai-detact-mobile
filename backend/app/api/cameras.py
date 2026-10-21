"""Camera management, stream control, preview endpoints and ONVIF discovery."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import CameraCreate, CameraOut, CameraTestRequest, CameraUpdate, MessageOut
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.rate_limit import enforce
from app.core.security import AdminUser, SupervisorUser, ViewerUser, get_current_user_flexible
from app.db.models import Camera, CameraSource
from app.db.session import get_db
from app.services import onvif
from app.services.audit import record as audit
from app.services.camera_manager import camera_manager
from app.vision.sources import probe_stream

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cameras", tags=["cameras"])

SENSITIVE_SCHEMES = ("rtsp://", "rtsps://", "http://", "https://")


def _split_credentials(url: str) -> tuple[str, str, str]:
    """Return (url_without_userinfo, username, password)."""
    if not url:
        return "", "", ""
    parts = urlsplit(url)
    if not parts.scheme and not parts.netloc:
        return url, "", ""
    username = unquote(parts.username or "")
    password = unquote(parts.password or "")
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)), username, password


def _compose(url: str, username: str, password: str) -> str:
    """Inject credentials into a stream URL (stored encrypted only)."""
    if not username and not password:
        # If the operator pasted credentials inline, keep them (they are encrypted).
        return url
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return url
    host = parts.hostname
    if parts.port:
        host += f":{parts.port}"
    userinfo = quote(username, safe="")
    if password:
        userinfo += ":" + quote(password, safe="")
    netloc = f"{userinfo}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _display_url(url: str) -> str:
    clean, _, _ = _split_credentials(url)
    return clean[:500]


def _to_out(camera: Camera, include_runtime: bool = True) -> CameraOut:
    runtime = camera_manager.status(camera.id) if include_runtime else None
    return CameraOut(
        id=camera.id,
        name=camera.name,
        location=camera.location,
        source_type=camera.source_type,
        url_display=camera.url_display,
        has_credentials=bool(camera.url_encrypted),
        enabled=camera.enabled,
        auto_start=camera.auto_start,
        inference_width=camera.inference_width,
        inference_height=camera.inference_height,
        frame_sample=camera.frame_sample,
        detect_conf=camera.detect_conf,
        phone_conf=camera.phone_conf,
        persistence_threshold=camera.persistence_threshold,
        alert_cooldown_s=camera.alert_cooldown_s,
        phone_detection_enabled=camera.phone_detection_enabled,
        runtime=runtime,
        created_at=camera.created_at,
    )


def _apply_payload(camera: Camera, payload: CameraCreate | CameraUpdate, is_update: bool) -> None:
    data = payload.model_dump(exclude_unset=is_update)
    url: str | None = data.pop("url", None)
    username: str | None = data.pop("username", None)
    password: str | None = data.pop("password", None)

    for key, value in data.items():
        setattr(camera, key, value)

    if url is not None and url != "":
        clean, embedded_user, embedded_pass = _split_credentials(url)
        username = username or embedded_user
        password = password or embedded_pass
        full = _compose(clean, username, password)
        camera.url_encrypted = encrypt_secret(full)
        camera.url_display = _display_url(full)
    elif url == "":
        camera.url_encrypted = ""
        camera.url_display = ""
    elif username is not None or password is not None:
        # Credentials updated without a new URL: rebuild from the stored URL.
        try:
            current = decrypt_secret(camera.url_encrypted)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        clean, current_user, current_pass = _split_credentials(current)
        full = _compose(clean, username if username is not None else current_user,
                        password if password is not None else current_pass)
        camera.url_encrypted = encrypt_secret(full)
        camera.url_display = _display_url(full)


@router.get("", response_model=list[CameraOut])
def list_cameras(user: ViewerUser, db: Annotated[Session, Depends(get_db)]) -> list[CameraOut]:
    rows = db.execute(select(Camera).order_by(Camera.id)).scalars().all()
    return [_to_out(c) for c in rows]


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: int, user: ViewerUser, db: Annotated[Session, Depends(get_db)]) -> CameraOut:
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    return _to_out(camera)


@router.post("", response_model=CameraOut, status_code=201)
def create_camera(
    payload: CameraCreate,
    user: SupervisorUser,
    db: Annotated[Session, Depends(get_db)],
    request: Request,
) -> CameraOut:
    enforce(request, "camera_write")
    camera = Camera(
        name=payload.name,
        location=payload.location,
        source_type=payload.source_type,
        url_encrypted="",
        url_display="",
    )
    _apply_payload(camera, payload, is_update=False)
    if camera.source_type != CameraSource.SIMULATED.value and not camera.url_encrypted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A stream URL is required for this source type")
    db.add(camera)
    db.commit()
    db.refresh(camera)
    audit("camera_created", actor=user.username, target=str(camera.id),
          detail=f"{camera.name} ({camera.source_type})", ip=request.client.host if request.client else "")
    return _to_out(camera)


@router.patch("/{camera_id}", response_model=CameraOut)
def update_camera(
    camera_id: int,
    payload: CameraUpdate,
    user: SupervisorUser,
    db: Annotated[Session, Depends(get_db)],
    request: Request,
) -> CameraOut:
    enforce(request, "camera_write")
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    was_running = camera_manager.is_running(camera.id)
    _apply_payload(camera, payload, is_update=True)
    db.commit()
    db.refresh(camera)
    audit("camera_updated", actor=user.username, target=str(camera.id),
          detail=f"fields={list(payload.model_dump(exclude_unset=True).keys())}",
          ip=request.client.host if request.client else "")
    if was_running:
        camera_manager.restart(camera.id)
    return _to_out(camera)


@router.delete("/{camera_id}", response_model=MessageOut)
def delete_camera(camera_id: int, user: AdminUser, db: Annotated[Session, Depends(get_db)]) -> MessageOut:
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    if camera_manager.is_running(camera_id):
        camera_manager.stop(camera_id)
    name = camera.name
    db.delete(camera)
    db.commit()
    audit("camera_deleted", actor=user.username, target=str(camera_id), detail=name)
    return MessageOut(message=f"Camera '{name}' deleted")


@router.post("/{camera_id}/start", response_model=MessageOut)
def start_camera(camera_id: int, user: SupervisorUser, db: Annotated[Session, Depends(get_db)]) -> MessageOut:
    if db.get(Camera, camera_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    try:
        state = camera_manager.start(camera_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    audit("camera_started", actor=user.username, target=str(camera_id))
    return MessageOut(message="Camera worker started", detail=state)


@router.post("/{camera_id}/stop", response_model=MessageOut)
def stop_camera(camera_id: int, user: SupervisorUser) -> MessageOut:
    state = camera_manager.stop(camera_id)
    audit("camera_stopped", actor=user.username, target=str(camera_id))
    return MessageOut(message="Camera worker stopped", detail=state)


@router.post("/{camera_id}/restart", response_model=MessageOut)
def restart_camera(camera_id: int, user: SupervisorUser) -> MessageOut:
    try:
        state = camera_manager.restart(camera_id)
    except (LookupError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    audit("camera_restarted", actor=user.username, target=str(camera_id))
    return MessageOut(message="Camera worker restarted", detail=state)


@router.get("/{camera_id}/status")
def camera_status(camera_id: int, user: ViewerUser, db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    state = camera_manager.status(camera_id)
    if state is None:
        return {
            "camera_id": camera_id,
            "name": camera.name,
            "status": "stopped",
            "message": "Worker not running",
            "occupancy": 0,
            "active_alerts": 0,
            "simulated": camera.source_type == "simulated",
        }
    return state


@router.get("/{camera_id}/analysis")
def latest_analysis(camera_id: int, user: ViewerUser) -> dict[str, Any]:
    analysis = camera_manager.analysis(camera_id)
    if analysis is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No analysis available (camera not running)")
    return analysis


@router.post("/test", response_model=MessageOut)
def test_connection(payload: CameraTestRequest, user: SupervisorUser, request: Request) -> MessageOut:
    """Probe a stream without saving it. Credentials are never logged or returned."""
    enforce(request, "camera_test", "20/minute")
    url = payload.url
    if payload.username or payload.password:
        url = _compose(url, payload.username, payload.password)
    result = probe_stream(url, payload.source_type, timeout_s=6.0)
    audit("camera_test", actor=user.username, detail=f"{payload.source_type} ok={result.get('ok')}")
    return MessageOut(ok=bool(result.get("ok")), message="Connection OK" if result.get("ok") else "Connection failed",
                      detail=result)


@router.get("/{camera_id}/snapshot.jpg")
def snapshot(camera_id: int, user: Annotated[Any, Depends(get_current_user_flexible)]) -> Response:
    jpeg = camera_manager.snapshot(camera_id)
    if not jpeg:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No frame available yet")
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0", "X-Content-Type-Options": "nosniff"},
    )


def _mjpeg_generator(camera_id: int, fps: float):
    boundary = b"--frame\r\n"
    last = b""
    while True:
        jpeg = camera_manager.snapshot(camera_id)
        if jpeg and jpeg != last:
            last = jpeg
            yield boundary + b"Content-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"
        yield b""
        # Sleeping here keeps CPU flat for many concurrent viewers.
        probe = 1.0 / max(1.0, min(fps, 20.0))
        if not jpeg:
            probe = 0.5
        time_sleep(probe)


def time_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


@router.get("/{camera_id}/stream.mjpg")
def stream_mjpeg(camera_id: int, user: Annotated[Any, Depends(get_current_user_flexible)]) -> StreamingResponse:
    """Annotated MJPEG preview.

    MJPEG is used deliberately: it needs no re-encoding of the source stream,
    works in every browser, and keeps the RTSP URL and credentials server-side.
    """
    if camera_manager.status(camera_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera is not running")
    return StreamingResponse(
        _mjpeg_generator(camera_id, 8.0),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/discover/onvif", response_model=MessageOut)
async def discover_onvif(user: SupervisorUser, timeout_s: float = 3.0) -> MessageOut:
    devices = await asyncio.to_thread(onvif.discover, max(1.0, min(timeout_s, 10.0)))
    audit("onvif_discovery", actor=user.username, detail=f"{len(devices)} devices")
    return MessageOut(
        ok=True,
        message=f"{len(devices)} ONVIF device(s) responded" if devices else "No ONVIF devices responded",
        detail={"devices": devices},
    )


@router.post("/discover/onvif/stream", response_model=MessageOut)
def onvif_stream(
    host: str,
    username: str,
    password: str,
    user: SupervisorUser,
    request: Request,
    port: int = 80,
) -> MessageOut:
    """Resolve an RTSP URL from an ONVIF device. Requires optional onvif-zeep."""
    enforce(request, "onvif", "10/minute")
    result = onvif.probe_onvif_media(host, username, password, port)
    audit("onvif_stream_resolved", actor=user.username, detail=f"host={host} ok={result.get('ok')}")
    return MessageOut(ok=bool(result.get("ok")), message="Resolved" if result.get("ok") else "Failed",
                      detail=result)
