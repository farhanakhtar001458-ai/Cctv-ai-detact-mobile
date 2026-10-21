"""FastAPI application entrypoint.

Run with::

    uvicorn app.main:app --host 0.0.0.0 --port 8000

The API, WebSocket hub, camera workers and (optionally) the built React
dashboard are served from this single process.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.api import auth, cameras, events, settings_api, stats as stats_api, system, ws
from app.core.config import PROJECT_DIR, settings
from app.core.logging import configure_logging
from app.db.models import Camera
from app.db.seed import seed
from app.db.session import SessionLocal, init_db
from app.services.broadcast import broadcaster
from app.services.camera_manager import camera_manager
from app.services.events import purge_expired
from app.services.settings_store import settings_store
from app.services.tts import voice_service
from app.vision.detector import detector_manager

configure_logging()
log = logging.getLogger("app.main")


async def _retention_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(purge_expired)
        except Exception as exc:  # noqa: BLE001
            log.warning("Retention task failed: %s", exc)
        await asyncio.sleep(3600)


async def _load_detector() -> None:
    try:
        await asyncio.to_thread(detector_manager.load)
    except Exception as exc:  # noqa: BLE001
        log.error("Detector initialisation failed: %s", exc)


async def _autostart_cameras() -> None:
    if not settings_store.get("auto_start_cameras", False) and settings.environment != "development":
        # Individual cameras may still opt in through their own auto_start flag.
        pass
    try:
        with SessionLocal() as db:
            cams = list(db.execute(select(Camera)).scalars())
        for camera in cams:
            if not camera.auto_start or not camera.enabled:
                continue
            try:
                camera_manager.start(camera.id)
                log.info("Auto-started camera '%s'", camera.name)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not auto-start camera '%s': %s", camera.name, exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("Camera auto-start skipped: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings_store.refresh()
    seed()
    broadcaster.bind_loop(asyncio.get_running_loop())
    voice_service.start()
    tasks = [asyncio.create_task(_retention_loop())]
    tasks.append(asyncio.create_task(_load_detector()))
    tasks.append(asyncio.create_task(_autostart_cameras()))
    log.info(
        "Smart Classroom AI v%s started (env=%s, db=%s)",
        settings.version,
        settings.environment,
        "sqlite" if settings.is_sqlite else "postgres",
    )
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        camera_manager.stop_all()
        voice_service.shutdown()
        detector_manager.shutdown()
        log.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "AI-powered classroom monitoring: anonymous person tracking, mobile-phone "
            "detection, phone-use association and privacy-conscious event reporting."
        ),
        lifespan=lifespan,
        docs_url="/api/docs" if settings.environment != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.environment != "production" else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        if settings.environment == "production":
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: blob:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                "connect-src 'self' ws: wss:; frame-ancestors 'self'",
            )
        if settings.secure_cookies:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        # Never cache API responses that may contain event data.
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception):  # pragma: no cover - safety net
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    for router in (
        auth.router,
        cameras.router,
        events.router,
        stats_api.router,
        settings_api.router,
        system.router,
        ws.router,
    ):
        app.include_router(router)

    @app.get("/api/voice/clip/{name}", include_in_schema=False)
    def voice_clip(name: str, token: str | None = None) -> Any:
        from fastapi import HTTPException

        from app.core.security import decode_access_token

        if not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token required")
        decode_access_token(token)
        safe_name = Path(name).name
        path = settings.data_dir / "voice" / safe_name
        if not path.exists() or path.suffix not in (".wav", ".mp3", ".aiff"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Clip not found")
        return FileResponse(path, media_type="audio/wav", filename=safe_name)

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built React dashboard when present (single-container deploy)."""
    dist = Path(PROJECT_DIR) / "frontend" / "dist"
    if not dist.exists():
        @app.get("/", include_in_schema=False)
        def dev_root() -> dict[str, str]:
            return {
                "message": "Smart Classroom AI API is running.",
                "dashboard": "Build the frontend (cd frontend && npm run build) or run the Vite dev server.",
                "docs": "/api/docs",
            }

        return
    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        from fastapi import HTTPException

        if full_path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")


app = create_app()
