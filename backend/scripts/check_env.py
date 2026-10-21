#!/usr/bin/env python
"""Environment doctor: verify what this deployment can actually do.

    python scripts/check_env.py

Prints a checklist covering Python, dependencies, model weights, detector
backend, audio engine, database, camera connectivity and privacy settings.
Exit code 0 = ready for real detections, 1 = degraded, 2 = misconfigured.
"""

from __future__ import annotations

import importlib
import os
import platform
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The doctor must be fast and offline-safe: never start a weight download while
# probing the detector. Provision weights explicitly with fetch_model.py.
os.environ["SCAI_ALLOW_MODEL_AUTODOWNLOAD"] = "false"

OK = "\033[32m[ OK ]\033[0m"
WARN = "\033[33m[WARN]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"

results: list[tuple[str, str]] = []


def report(status: str, message: str) -> None:
    results.append((status, message))
    print(f"{status} {message}")


def check_imports() -> None:
    required = ["fastapi", "uvicorn", "sqlalchemy", "pydantic", "jwt", "cryptography", "cv2", "numpy", "psutil"]
    for module in required:
        try:
            imported = importlib.import_module(module)
            version = getattr(imported, "__version__", "")
            report(OK, f"{module} {version}")
        except Exception as exc:  # noqa: BLE001
            report(FAIL, f"{module} missing/unusable ({exc})")
    for module in ("ultralytics", "torch", "onnxruntime", "pyttsx3", "onvif"):
        try:
            imported = importlib.import_module(module)
            report(OK, f"optional {module} {getattr(imported, '__version__', '')}")
        except Exception:  # noqa: BLE001
            report(WARN, f"optional {module} not installed")


def check_hardware() -> None:
    report(OK, f"platform {platform.platform()}")
    try:
        import psutil

        report(OK, f"cpu cores {psutil.cpu_count(logical=True)} · ram {psutil.virtual_memory().total / 1e9:.1f} GB")
    except Exception:  # noqa: BLE001
        pass
    try:
        import torch

        if torch.cuda.is_available():
            report(OK, f"CUDA available: {torch.cuda.get_device_name(0)}")
        else:
            report(WARN, "no CUDA GPU detected - CPU inference only (expect ~5-20 FPS at 640px)")
    except Exception:  # noqa: BLE001
        report(WARN, "torch not installed - YOLO backend unavailable")


def check_detector() -> int:
    from app.vision.detector import detector_manager

    report(OK, "model auto-download disabled for this check (use scripts/fetch_model.py)")

    status = detector_manager.load()
    if status.loaded and not status.degraded:
        report(OK, f"detector {status.backend} ({status.model}) on {status.device}")
    elif status.loaded:
        report(WARN, f"degraded detector {status.backend}: {status.extra.get('degraded_reason', '')}")
    else:
        report(FAIL, f"no detector: {status.error}")
    if status.phone_capable:
        report(OK, "mobile-phone detection available")
    else:
        report(WARN, "mobile-phone detection NOT available with this backend")
    return 0 if status.loaded and not status.degraded else 1


def check_audio() -> None:
    from app.services.tts import detect_engine

    engine = detect_engine()
    if engine.available:
        report(OK, f"server TTS engine: {engine.engine} ({engine.detail})")
    else:
        report(WARN, "no server audio engine - use dashboard (browser) announcements")


def check_db() -> int:
    try:
        from app.core.config import settings
        from app.db.session import SessionLocal, init_db

        init_db()
        with SessionLocal() as db:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
        kind = "postgresql" if not settings.is_sqlite else "sqlite"
        report(OK, f"database reachable ({kind})")
        return 0
    except Exception as exc:  # noqa: BLE001
        report(FAIL, f"database error: {exc}")
        return 2


def check_config() -> int:
    from app.core.config import settings

    code = 0
    if not settings.secret_key:
        report(FAIL, "SCAI_SECRET_KEY is empty (credentials cannot be encrypted reliably)")
        code = 2
    else:
        report(OK, "signing/encryption key present")
    if settings.environment == "production":
        if settings.allowed_origins.strip() == "*":
            report(WARN, "SCAI_ALLOWED_ORIGINS=* in production - restrict it to your dashboard origin")
        if not settings.secure_cookies:
            report(WARN, "SCAI_SECURE_COOKIES=false in production - enable behind HTTPS")
    report(OK, f"max visible people per camera: {settings.max_visible_people}")
    report(OK, f"max camera workers: {settings.max_camera_workers}")
    report(OK, f"data directory: {settings.data_dir}")
    if shutil.disk_usage(settings.data_dir).free / 1e9 < 1:
        report(WARN, "less than 1 GB free disk space")
    return code


def main() -> int:
    print("Smart Classroom AI - environment check\n" + "-" * 60)
    check_imports()
    check_hardware()
    detector_code = check_detector()
    check_audio()
    db_code = check_db()
    config_code = check_config()

    print("-" * 60)
    failures = sum(1 for status, _ in results if status == FAIL)
    warnings = sum(1 for status, _ in results if status == WARN)
    print(f"{failures} failure(s), {warnings} warning(s)")
    if failures:
        return 2
    if detector_code or db_code or config_code:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
