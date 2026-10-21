"""First-run bootstrap: admin accounts, default settings and demo camera."""

from __future__ import annotations

import logging
import os
import secrets

from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.core.security import hash_password
from app.db.models import Camera, CameraSource, User, UserRole
from app.db.session import SessionLocal
from app.services.settings_store import settings_store

log = logging.getLogger(__name__)


def _write_credential_file(username: str, password: str) -> None:
    path = settings.data_dir / "initial_credentials.txt"
    try:
        path.write_text(
            "Smart Classroom AI - initial credentials\n"
            f"username: {username}\npassword: {password}\n"
            "Change this password immediately after the first login.\n",
            encoding="utf-8",
        )
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover
        pass


def _ensure_user(db, username: str, password: str, role: UserRole, full_name: str) -> bool:
    existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if existing:
        return False
    db.add(
        User(
            username=username,
            full_name=full_name,
            password_hash=hash_password(password),
            role=role.value,
        )
    )
    return True


def seed() -> dict[str, str]:
    """Create bootstrap data if missing. Returns generated credentials (if any)."""
    created: dict[str, str] = {}
    with SessionLocal() as db:
        admin_password = settings.bootstrap_admin_password or secrets.token_urlsafe(12)
        if _ensure_user(db, settings.bootstrap_admin_username, admin_password, UserRole.ADMIN, "System Administrator"):
            created["admin"] = admin_password

        supervisor_password = settings.bootstrap_supervisor_password or secrets.token_urlsafe(12)
        if _ensure_user(db, "supervisor", supervisor_password, UserRole.SUPERVISOR, "Classroom Supervisor"):
            created["supervisor"] = supervisor_password

        cam_count = db.execute(select(Camera)).scalars().first()
        if cam_count is None:
            demo = Camera(
                name="Demo Classroom (Simulated)",
                location="Sandbox / demonstration only",
                source_type=CameraSource.SIMULATED.value,
                url_encrypted=encrypt_secret("sim://classroom"),
                url_display="sim://classroom",
                enabled=True,
                auto_start=True,
                inference_width=960,
                inference_height=540,
                frame_sample=2,
                detect_conf=0.35,
                phone_conf=0.30,
                persistence_threshold=0.62,
                alert_cooldown_s=45,
            )
            room = Camera(
                name="Room 101 - Front CCTV",
                location="Block A, Room 101",
                source_type=CameraSource.RTSP.value,
                url_encrypted="",
                url_display="",
                enabled=False,
                auto_start=False,
                inference_width=1280,
                inference_height=720,
                frame_sample=3,
                detect_conf=0.35,
                phone_conf=0.30,
                persistence_threshold=0.65,
                alert_cooldown_s=60,
            )
            db.add_all([demo, room])
        db.commit()

    for username, password in created.items():
        _write_credential_file(username, password)
        if settings.environment == "development":
            log.warning(
                "Created user '%s' with initial password '%s' (development mode only)",
                username, password,
            )
        else:
            log.warning(
                "Created user '%s'. Initial password written to %s (change it now).",
                username, settings.data_dir / "initial_credentials.txt",
            )
    settings_store.refresh()
    return created
