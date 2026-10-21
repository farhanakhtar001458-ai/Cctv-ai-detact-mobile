"""Stage 6 - alert gating, duplicate suppression and voice announcement dispatch."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.db.models import AlertStatus, EventCategory
from app.services.broadcast import broadcaster
from app.services.events import EventService
from app.services.settings_store import settings_store
from app.services.tts import Announcement, voice_service
from app.vision.temporal import AlertSuppressor

log = logging.getLogger(__name__)


class AlertCoordinator:
    """Per-camera alerting policy shared by the worker and the voice service."""

    def __init__(self, camera_id: int) -> None:
        self.camera_id = camera_id
        self.suppressor = AlertSuppressor(cooldown_s=float(settings_store.get("alert_cooldown_seconds", 60)))

    def configure(self) -> None:
        self.suppressor.configure(float(settings_store.get("alert_cooldown_seconds", 60)))

    def handle(
        self,
        camera_name: str,
        new_events: list[dict[str, Any]],
        event_service: EventService,
        simulated: bool = False,
    ) -> None:
        voice_enabled = bool(settings_store.get("voice_enabled", True))
        output = str(settings_store.get("voice_output", "dashboard"))
        if not voice_enabled:
            output = "off"

        for ev in new_events:
            category = ev["category"]
            if category not in (
                EventCategory.SUSTAINED_PHONE_USE.value,
                EventCategory.POSSIBLE_PHONE_USE.value,
            ):
                continue
            if category == EventCategory.POSSIBLE_PHONE_USE.value:
                message_key = "voice_message_possible"
                if not str(settings_store.get(message_key, "")).strip():
                    continue
            else:
                message_key = "voice_message_sustained"

            if simulated:
                # Simulated feeds may exercise the pipeline, but announcements
                # are labelled so nobody mistakes them for a real detection.
                prefix = "[SIMULATED] "
            else:
                prefix = ""

            allowed = self.suppressor.should_alert(
                self.camera_id, int(ev["track_id"]), category
            )
            inside_cooldown = not allowed
            if allowed and output != "off":
                max_per_min = int(settings_store.get("voice_max_per_minute", 4))
                if not voice_slot_free():
                    allowed = False
                    inside_cooldown = True
                elif voice_service.rate_limited(max_per_min):
                    allowed = False
                    inside_cooldown = True

            if not allowed:
                event_service.mark_announced(int(ev["event_id"]), AlertStatus.SUPPRESSED)
                broadcaster.publish_threadsafe(
                    {
                        "topic": "alert",
                        "type": "alert_suppressed",
                        "camera_id": self.camera_id,
                        "camera_name": camera_name,
                        "tracking_id": ev["tracking_id"],
                        "event_id": ev["event_id"],
                        "category": category,
                        "reason": "cooldown" if inside_cooldown else "rate_limited",
                    }
                )
                continue

            message = str(settings_store.get(message_key, "")).strip() or "Mobile phone detected."
            announcement = Announcement(
                text=f"{prefix}{message}",
                camera_id=self.camera_id,
                camera_name=camera_name,
                event_id=int(ev["event_id"]),
                category=category,
                output=output,
            )
            if output != "off":
                voice_service.announce(announcement)
            event_service.mark_announced(
                int(ev["event_id"]),
                AlertStatus.ANNOUNCED if output != "off" else AlertStatus.NONE,
            )
            broadcaster.publish_threadsafe(
                {
                    "topic": "alert",
                    "type": "phone_use_alert",
                    "camera_id": self.camera_id,
                    "camera_name": camera_name,
                    "tracking_id": ev["tracking_id"],
                    "event_id": ev["event_id"],
                    "event_uuid": ev.get("event_uuid"),
                    "category": category,
                    "confidence": ev.get("confidence"),
                    "source": ev.get("source"),
                    "simulated": simulated,
                    "message": message,
                    "voice_output": output,
                    "timestamp": time.time(),
                }
            )
            log.warning(
                "Phone-use alert camera=%s person=%s confidence=%.2f simulated=%s",
                camera_name, ev["tracking_id"], float(ev.get("confidence") or 0.0), simulated,
            )


_last_voice_ok = [0.0]
VOICE_GAP_S = 1.5


def voice_slot_free() -> bool:
    """Simple global spacing so two cameras never speak over each other."""
    now = time.time()
    if now - _last_voice_ok[0] < VOICE_GAP_S:
        return False
    _last_voice_ok[0] = now
    return True
