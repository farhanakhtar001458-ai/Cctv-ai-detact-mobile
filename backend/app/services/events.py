"""Event lifecycle management (stage 5 persistence): open/close/annotate events.

One *open* event per (camera, anonymous tracking id, category) is maintained in
the database. When a person's state changes, the previous event is closed with
its measured duration and a new one is opened. This keeps the event table small
and produces meaningful durations instead of one row per frame.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select

from app.core.config import settings
from app.db.models import AlertStatus, Camera, CameraMetric, DetectionEvent, EventCategory, EventSource
from app.db.session import SessionLocal
from app.services.broadcast import broadcaster
from app.services.settings_store import settings_store
from app.vision.pipeline import FrameAnalysis, TrackSummary
from app.vision.temporal import PhoneUseState

log = logging.getLogger(__name__)

STATE_TO_CATEGORY: dict[PhoneUseState, EventCategory | None] = {
    PhoneUseState.NONE: None,
    PhoneUseState.PHONE_DETECTED: EventCategory.PHONE_DETECTED,
    PhoneUseState.POSSIBLE_USE: EventCategory.POSSIBLE_PHONE_USE,
    PhoneUseState.SUSTAINED: EventCategory.SUSTAINED_PHONE_USE,
    PhoneUseState.UNCERTAIN: EventCategory.UNCERTAIN,
}


@dataclass
class OpenEvent:
    event_id: int
    event_uuid: str
    category: str
    started_at: datetime
    track_id: int
    peak_confidence: float = 0.0


class EventService:
    """Per-camera event bookkeeping (one instance per worker)."""

    def __init__(self, camera_id: int, source: str = EventSource.MODEL.value) -> None:
        self.camera_id = camera_id
        self.source = source
        self._open: dict[tuple[int, str], OpenEvent] = {}
        self._last_person_event: dict[int, float] = {}
        self._lock = threading.Lock()
        self._announced_devices: set[tuple[int, str]] = set()

    def close_all(self) -> None:
        with self._lock:
            for key, ev in list(self._open.items()):
                self._close_event(ev)
                self._open.pop(key, None)

    def _close_event(self, ev: OpenEvent) -> None:
        ended = datetime.now(timezone.utc)
        started = ev.started_at if ev.started_at.tzinfo else ev.started_at.replace(tzinfo=timezone.utc)
        duration = max(0.0, (ended - started).total_seconds())
        try:
            with SessionLocal() as db:
                row = db.get(DetectionEvent, ev.event_id)
                if row is None:
                    return
                row.ended_at = ended
                row.duration_s = round(duration, 2)
                row.confidence = round(max(row.confidence, ev.peak_confidence), 4)
                db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to close event %s: %s", ev.event_id, exc)

    def _open_event(self, track_id: int, category: EventCategory, confidence: float, evidence: dict[str, Any], tracking_label: str) -> OpenEvent:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            row = DetectionEvent(
                camera_id=self.camera_id,
                tracking_id=tracking_label,
                category=category.value,
                confidence=round(confidence, 4),
                started_at=now,
                alert_status=(
                    AlertStatus.PENDING.value
                    if category == EventCategory.SUSTAINED_PHONE_USE
                    else AlertStatus.NONE.value
                ),
                source=self.source,
                evidence=json.dumps(evidence, default=str)[:4000],
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            event = OpenEvent(
                event_id=row.id,
                event_uuid=row.event_uuid,
                category=category.value,
                started_at=now,
                track_id=track_id,
                peak_confidence=confidence,
            )
        broadcaster.publish_threadsafe(
            {
                "topic": "event",
                "type": "event_opened",
                "camera_id": self.camera_id,
                "event": {
                    "id": row.id,
                    "event_uuid": row.event_uuid,
                    "camera_id": self.camera_id,
                    "tracking_id": tracking_label,
                    "category": category.value,
                    "confidence": round(confidence, 3),
                    "started_at": now.isoformat(),
                    "alert_status": row.alert_status,
                    "source": self.source,
                },
            }
        )
        return event

    def sync(self, analysis: FrameAnalysis) -> list[dict[str, Any]]:
        """Reconcile DB events with the current per-track states.

        Returns newly opened events (the worker turns sustained ones into
        voice announcements).
        """
        created: list[dict[str, Any]] = []
        now_ts = time.time()
        with self._lock:
            visible_tracks = {t.track_id for t in analysis.tracks}
            # Close events for people who left the frame entirely.
            for key in list(self._open):
                if key[0] not in visible_tracks:
                    self._close_event(self._open.pop(key))

            for track in analysis.tracks:
                if not analysis.phone_capable:
                    continue
                category = STATE_TO_CATEGORY.get(track.state)
                if track.state == PhoneUseState.NONE and self._should_record_person(track, now_ts):
                    category = EventCategory.PERSON_DETECTED
                key = (track.track_id, category.value) if category else None

                # Close any open event whose category no longer matches.
                for other_key in [k for k in self._open if k[0] == track.track_id and k != key]:
                    self._close_event(self._open.pop(other_key))

                if category is None or key is None:
                    continue
                existing = self._open.get(key)
                if existing is not None:
                    existing.peak_confidence = max(existing.peak_confidence, track.use_score)
                    continue
                confidence = track.use_score if category != EventCategory.PERSON_DETECTED else track.conf
                evidence = {
                    "state": track.state.value,
                    "use_score": round(track.use_score, 3),
                    "association_score": round(track.association_score, 3),
                    "phone_conf": round(track.phone_conf, 3),
                    "sustained_s": round(track.sustained_s, 2),
                    "ambiguous": track.ambiguous,
                    "occluded": track.occluded,
                    "reason": track.reason,
                    "simulated": analysis.simulated,
                    "model": analysis.model_backend,
                }
                ev = self._open_event(track.track_id, category, confidence, evidence, track.label)
                self._open[key] = ev
                created.append(
                    {
                        "event_id": ev.event_id,
                        "event_uuid": ev.event_uuid,
                        "category": category.value,
                        "track_id": track.track_id,
                        "tracking_id": track.label,
                        "confidence": confidence,
                        "started_at": ev.started_at.isoformat(),
                        "alert_status": (
                            AlertStatus.PENDING.value
                            if category == EventCategory.SUSTAINED_PHONE_USE
                            else AlertStatus.NONE.value
                        ),
                        "source": self.source,
                        "evidence": evidence,
                        "state": track.state.value,
                    }
                )
            return created

    def _should_record_person(self, track: TrackSummary, now_ts: float) -> bool:
        if not settings_store.get("record_person_events", False):
            return False
        last = self._last_person_event.get(track.track_id)
        if last is None or now_ts - last > 300:
            self._last_person_event[track.track_id] = now_ts
            return True
        return False

    def mark_announced(self, event_id: int, status: AlertStatus) -> None:
        try:
            with SessionLocal() as db:
                row = db.get(DetectionEvent, event_id)
                if row is None:
                    return
                row.alert_status = status.value
                db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to update alert status for event %s: %s", event_id, exc)


# ---------------------------------------------------------------------------
# Maintenance helpers
# ---------------------------------------------------------------------------


def purge_expired(retention_days: int | None = None, metrics_days: int | None = None) -> dict[str, int]:
    retention_days = retention_days or int(settings_store.get("event_retention_days", settings.event_retention_days))
    metrics_days = metrics_days or settings.metrics_retention_days
    now = datetime.now(timezone.utc)
    ev_cutoff = now - timedelta(days=retention_days)
    mt_cutoff = now - timedelta(days=metrics_days)
    removed_events = removed_metrics = 0
    with SessionLocal() as db:
        res = db.execute(delete(DetectionEvent).where(DetectionEvent.started_at < ev_cutoff))
        removed_events = res.rowcount or 0
        res = db.execute(delete(CameraMetric).where(CameraMetric.recorded_at < mt_cutoff))
        removed_metrics = res.rowcount or 0
        db.commit()
    if removed_events or removed_metrics:
        log.info("Retention cleanup: %d events, %d metric samples removed", removed_events, removed_metrics)
    return {"events_removed": removed_events, "metrics_removed": removed_metrics}


def acknowledge_event(event_id: int, username: str, note: str = "") -> dict[str, Any] | None:
    with SessionLocal() as db:
        row = db.get(DetectionEvent, event_id)
        if row is None:
            return None
        row.acknowledged_by = username
        row.acknowledged_at = datetime.now(timezone.utc)
        row.alert_status = AlertStatus.ACKNOWLEDGED.value
        if note:
            row.review_note = note[:2000]
        db.commit()
        return {"id": row.id, "alert_status": row.alert_status, "acknowledged_by": row.acknowledged_by}


def event_to_dict(row: DetectionEvent, camera_name: str = "") -> dict[str, Any]:
    return {
        "id": row.id,
        "event_uuid": row.event_uuid,
        "camera_id": row.camera_id,
        "camera_name": camera_name,
        "tracking_id": row.tracking_id,
        "category": row.category,
        "confidence": round(row.confidence, 3),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "duration_s": round(row.duration_s, 2),
        "alert_status": row.alert_status,
        "source": row.source,
        "simulated": row.source == EventSource.SIMULATED.value,
        "evidence": _safe_json(row.evidence),
        "acknowledged_by": row.acknowledged_by,
        "acknowledged_at": row.acknowledged_at.isoformat() if row.acknowledged_at else None,
        "review_note": row.review_note,
    }


def _safe_json(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def list_events(
    limit: int = 100,
    offset: int = 0,
    camera_id: int | None = None,
    category: str | None = None,
    source: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    tracking_id: str | None = None,
) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        stmt = select(DetectionEvent).order_by(DetectionEvent.started_at.desc())
        if camera_id:
            stmt = stmt.where(DetectionEvent.camera_id == camera_id)
        if category:
            stmt = stmt.where(DetectionEvent.category == category)
        if source:
            stmt = stmt.where(DetectionEvent.source == source)
        if tracking_id:
            stmt = stmt.where(DetectionEvent.tracking_id == tracking_id)
        if since:
            stmt = stmt.where(DetectionEvent.started_at >= since)
        if until:
            stmt = stmt.where(DetectionEvent.started_at <= until)
        rows = db.execute(stmt.offset(max(0, offset)).limit(min(limit, 1000))).scalars().all()
        cameras = {c.id: c.name for c in db.execute(select(Camera)).scalars()}
        return [event_to_dict(r, cameras.get(r.camera_id, "")) for r in rows]
