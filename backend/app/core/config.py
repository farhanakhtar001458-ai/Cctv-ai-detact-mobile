"""Application configuration.

All settings can be provided via environment variables prefixed with ``SCAI_``
(for example ``SCAI_PORT=9000``) or via a ``.env`` file next to the backend
package. Command line style single underscores are also accepted.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]  # backend/
PROJECT_DIR = BASE_DIR.parent
DATA_DIR = Path(os.environ.get("SCAI_DATA_DIR", BASE_DIR / "data")).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCAI_",
        env_file=(str(BASE_DIR / ".env"), str(PROJECT_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- General ---------------------------------------------------------
    app_name: str = "Smart Classroom AI Monitor"
    version: str = "1.0.0"
    environment: Literal["development", "production", "test"] = "development"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    root_path: str = ""

    # --- Security --------------------------------------------------------
    secret_key: str = ""
    access_token_ttl_minutes: int = 480
    jwt_algorithm: str = "HS256"
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = ""  # generated + printed once if empty
    bootstrap_supervisor_password: str = ""
    allowed_origins: str = "*"
    secure_cookies: bool = False

    # --- Database --------------------------------------------------------
    database_url: str = ""
    sql_echo: bool = False
    event_retention_days: int = 90
    metrics_retention_days: int = 14
    # Anonymous tracking ids reset after this many minutes of a person being
    # absent from a camera (privacy: ids are session scoped, never biometric).
    tracking_id_reset_minutes: int = 30

    # --- Vision ----------------------------------------------------------
    model_path: str = "yolo11n.pt"
    model_device: str = "auto"  # auto|cpu|cuda|cuda:0|mps
    model_imgsz: int = 640
    model_half: bool = False
    detector_backend: Literal["auto", "ultralytics", "onnx", "hog", "none"] = "auto"
    allow_model_autodownload: bool = True
    onnx_model_path: str = ""
    tracker_type: Literal["iou", "bytetrack", "botsort"] = "iou"
    tracker_max_age: int = 45  # frames a lost track is kept alive
    tracker_min_hits: int = 3
    tracker_iou_threshold: float = 0.3
    detect_conf_default: float = 0.35
    phone_conf_default: float = 0.30
    max_visible_people: int = 50
    max_camera_workers: int = 8
    frame_sample_default: int = 3  # process 1 of every N decoded frames
    inference_width_default: int = 960
    inference_height_default: int = 540
    phone_use_window_seconds: float = 4.0
    phone_use_persistence_default: float = 0.65
    alert_cooldown_default: int = 60
    stream_read_timeout_s: float = 8.0
    allow_simulated_sources: bool = True
    snapshot_quality: int = 75
    snapshot_fps: float = 8.0

    # --- Rate limiting ---------------------------------------------------
    rate_limit_enabled: bool = True
    login_rate_limit: str = "10/minute"
    write_rate_limit: str = "120/minute"
    trust_proxy_headers: bool = False

    # --- Audit / logging -------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("secret_key")
    @classmethod
    def _no_empty_secret(cls, v: str) -> str:
        return v

    @property
    def data_dir(self) -> Path:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return DATA_DIR

    @property
    def models_dir(self) -> Path:
        p = self.data_dir / "models"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def snapshots_dir(self) -> Path:
        p = self.data_dir / "snapshots"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'classroom.db').as_posix()}"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.resolved_database_url.startswith("sqlite")


def _load_or_create_secret() -> str:
    """Load the signing key from env or the data dir, generating one once.

    A generated key is stored with 0600 permissions and never committed.
    """
    env_secret = os.environ.get("SCAI_SECRET_KEY", "").strip()
    if env_secret:
        return env_secret
    secret_file = DATA_DIR / ".secret_key"
    if secret_file.exists():
        value = secret_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    secret_file.write_text(value, encoding="utf-8")
    try:
        os.chmod(secret_file, 0o600)
    except OSError:  # pragma: no cover - best effort on odd filesystems
        pass
    return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if not settings.secret_key:
        settings = settings.model_copy(update={"secret_key": _load_or_create_secret()})
    return settings


settings = get_settings()
