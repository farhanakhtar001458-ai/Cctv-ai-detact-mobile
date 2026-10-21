"""Typed runtime settings persisted in the database (admin editable).

Environment/settings-file values provide defaults; anything written via the
settings API is stored in ``app_settings`` and survives restarts. Values are
validated and coerced, so a bad admin input cannot silently disable detection.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select

from app.core.config import settings as env_settings
from app.db.models import AppSetting
from app.db.session import SessionLocal

log = logging.getLogger(__name__)


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _as_int(lo: int, hi: int) -> Callable[[Any], int]:
    def conv(v: Any) -> int:
        n = int(float(v))
        if n < lo or n > hi:
            raise ValueError(f"value must be between {lo} and {hi}")
        return n

    return conv


def _as_float(lo: float, hi: float) -> Callable[[Any], float]:
    def conv(v: Any) -> float:
        f = float(v)
        if not (lo <= f <= hi):
            raise ValueError(f"value must be between {lo} and {hi}")
        return round(f, 4)

    return conv


def _as_str(max_len: int, allowed: tuple[str, ...] | None = None) -> Callable[[Any], str]:
    def conv(v: Any) -> str:
        s = str(v)
        if len(s) > max_len:
            raise ValueError(f"value longer than {max_len} characters")
        if allowed is not None and s not in allowed:
            raise ValueError(f"value must be one of {allowed}")
        return s

    return conv


@dataclass(frozen=True)
class SettingSpec:
    key: str
    default: Any
    coerce: Callable[[Any], Any]
    label: str
    category: str
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "default": self.default,
            "label": self.label,
            "category": self.category,
            "description": self.description,
        }


SPECS: list[SettingSpec] = [
    # Detection
    SettingSpec("detection_enabled", True, _as_bool, "Detection enabled", "detection",
                "Master switch for running detection on all cameras"),
    SettingSpec("phone_detection_enabled", True, _as_bool, "Phone-use detection", "detection",
                "Enable mobile-phone association and phone-use alerts"),
    SettingSpec("person_conf", 0.35, _as_float(0.05, 0.95), "Person confidence", "detection",
                "Minimum confidence for a person detection"),
    SettingSpec("phone_conf", 0.30, _as_float(0.05, 0.95), "Phone confidence", "detection",
                "Minimum confidence for a mobile-phone detection"),
    SettingSpec("persistence_threshold", 0.65, _as_float(0.3, 0.99), "Alert persistence", "detection",
                "Sustained use-confidence required before an alert"),
    SettingSpec("min_sustained_seconds", 2.0, _as_float(0.5, 30.0), "Minimum sustained time", "detection",
                "How long evidence must hold before an alert"),
    SettingSpec("analysis_window_seconds", 4.0, _as_float(1.0, 20.0), "Analysis window", "detection",
                "Sliding evidence window per person"),
    SettingSpec("alert_cooldown_seconds", 60, _as_int(5, 3600), "Alert cooldown", "detection",
                "Minimum gap between alerts for the same person"),
    SettingSpec("max_visible_people", 50, _as_int(1, 50), "Max visible people", "detection",
                "Cap on simultaneously tracked people per camera (product limit 50)"),
    SettingSpec("record_person_events", False, _as_bool, "Record person-entry events", "detection",
                "Store an event when a new anonymous person ID appears (higher volume)"),

    # Privacy / retention
    SettingSpec("event_retention_days", 90, _as_int(1, 3650), "Event retention (days)", "privacy",
                "Detection events older than this are purged"),
    SettingSpec("session_reset_minutes", 30, _as_int(1, 1440), "Anonymous ID reset", "privacy",
                "Anonymous person IDs are recycled after this much absence"),
    SettingSpec("privacy_notice", (
        "This classroom is monitored by an AI assistance system for classroom-management "
        "purposes. It detects mobile-phone use and counts visible people using anonymous "
        "tracking IDs. No facial recognition is used and no biometric profiles are created."
    ), _as_str(2000), "Monitoring notice", "privacy",
        "Notice displayed on the dashboard and available for signage"),

    # Voice
    SettingSpec("voice_enabled", True, _as_bool, "Voice alerts enabled", "voice",
                "Announce sustained phone-use alerts"),
    SettingSpec("voice_output", "dashboard", _as_str(20, ("dashboard", "server", "both")), "Announcement output",
                "voice", "dashboard = browser on the monitoring PC; server = backend audio device"),
    SettingSpec("voice_message_sustained", "Mobile phone detected. Please follow classroom rules.",
                _as_str(400), "Sustained alert message", "voice"),
    SettingSpec("voice_message_possible", "", _as_str(400), "Possible-use message", "voice",
                "Optional softer message for possible use (usually empty)"),
    SettingSpec("voice_language", "en", _as_str(16), "Language", "voice", "BCP-47 language tag"),
    SettingSpec("voice_rate", 175, _as_int(60, 400), "Speaking rate", "voice", "Words per minute"),
    SettingSpec("voice_volume", 0.8, _as_float(0.0, 1.0), "Volume", "voice"),
    SettingSpec("voice_cooldown_seconds", 60, _as_int(5, 3600), "Announcement cooldown", "voice"),
    SettingSpec("voice_max_per_minute", 4, _as_int(1, 30), "Max announcements / minute", "voice"),

    # Performance
    SettingSpec("snapshot_fps", 8.0, _as_float(1.0, 20.0), "Preview stream FPS", "performance"),
    SettingSpec("default_frame_sample", 3, _as_int(1, 30), "Frame sampling", "performance",
                "Process 1 of every N decoded frames (camera overrides allowed)"),
    SettingSpec("auto_start_cameras", False, _as_bool, "Auto-start cameras on boot", "performance"),
]

SPEC_INDEX: dict[str, SettingSpec] = {s.key: s for s in SPECS}


class SettingsStore:
    """Thread-safe cached settings store backed by the database."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: dict[str, Any] | None = None

    # -- internal ---------------------------------------------------------
    def _db_defaults(self) -> dict[str, Any]:
        values = {s.key: s.default for s in SPECS}
        # Environment-provided defaults (still overridable via the UI).
        values["event_retention_days"] = env_settings.event_retention_days
        values["session_reset_minutes"] = env_settings.tracking_id_reset_minutes
        values["person_conf"] = env_settings.detect_conf_default
        values["phone_conf"] = env_settings.phone_conf_default
        values["persistence_threshold"] = env_settings.phone_use_persistence_default
        values["analysis_window_seconds"] = env_settings.phone_use_window_seconds
        values["alert_cooldown_seconds"] = env_settings.alert_cooldown_default
        values["max_visible_people"] = env_settings.max_visible_people
        values["default_frame_sample"] = env_settings.frame_sample_default
        values["snapshot_fps"] = env_settings.snapshot_fps
        return values

    def refresh(self) -> dict[str, Any]:
        with self._lock:
            values = self._db_defaults()
            try:
                with SessionLocal() as db:
                    for row in db.execute(select(AppSetting)).scalars():
                        spec = SPEC_INDEX.get(row.key)
                        if not spec:
                            continue
                        try:
                            values[row.key] = spec.coerce(json.loads(row.value))
                        except Exception:  # noqa: BLE001 - ignore corrupted rows
                            log.warning("Ignoring invalid stored setting %s", row.key)
            except Exception as exc:  # noqa: BLE001 - DB may not be initialised yet
                log.warning("Could not load stored settings: %s", exc)
            self._values = values
            return dict(values)

    def all(self) -> dict[str, Any]:
        with self._lock:
            if self._values is None:
                self.refresh()
            assert self._values is not None
            return dict(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        values = self.all()
        return values.get(key, SPEC_INDEX[key].default if key in SPEC_INDEX else default)

    def describe(self) -> list[dict[str, Any]]:
        values = self.all()
        out = []
        for spec in SPECS:
            item = spec.as_dict()
            item["value"] = values.get(spec.key, spec.default)
            out.append(item)
        return out

    def set_many(self, updates: dict[str, Any]) -> dict[str, Any]:
        coerced: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for key, raw in updates.items():
            spec = SPEC_INDEX.get(key)
            if spec is None:
                errors[key] = "unknown setting"
                continue
            try:
                coerced[key] = spec.coerce(raw)
            except (TypeError, ValueError) as exc:
                errors[key] = str(exc)
        if errors:
            raise ValueError(json.dumps(errors))
        with self._lock:
            with SessionLocal() as db:
                for key, value in coerced.items():
                    row = db.get(AppSetting, key)
                    payload = json.dumps(value)
                    if row is None:
                        db.add(AppSetting(key=key, value=payload))
                    else:
                        row.value = payload
                db.commit()
            self.refresh()
        return coerced

    def reset(self) -> None:
        with self._lock:
            with SessionLocal() as db:
                for row in db.execute(select(AppSetting)).scalars():
                    db.delete(row)
                db.commit()
            self.refresh()


settings_store = SettingsStore()
