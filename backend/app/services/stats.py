"""Dashboard statistics, summaries and report generation."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.db.models import Camera, CameraMetric, DetectionEvent, EventCategory, EventSource, User
from app.db.session import SessionLocal
from app.services.camera_manager import camera_manager
from app.services.settings_store import settings_store
from app.vision.detector import detector_manager


def _start_of_day(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def overview() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    today = _start_of_day(now.date())
    with SessionLocal() as db:
        def count(category: str | None = None, source: str | None = None, after: datetime | None = None) -> int:
            stmt = select(func.count(DetectionEvent.id))
            if category:
                stmt = stmt.where(DetectionEvent.category == category)
            if source:
                stmt = stmt.where(DetectionEvent.source == source)
            if after:
                stmt = stmt.where(DetectionEvent.started_at >= after)
            return int(db.execute(stmt).scalar() or 0)

        cameras = list(db.execute(select(Camera)).scalars())
        users = int(db.execute(select(func.count(User.id))).scalar() or 0)
        latest_metric = db.execute(select(CameraMetric).order_by(CameraMetric.recorded_at.desc()).limit(1)).scalar_one_or_none()

    totals = camera_manager.totals()
    statuses = camera_manager.all_status()
    return {
        "server_time": now.isoformat(),
        "cameras_total": len(cameras),
        "cameras_enabled": sum(1 for c in cameras if c.enabled),
        "cameras_running": totals["cameras_running"],
        "occupancy_now": totals["occupancy"],
        "active_alerts_now": totals["active_alerts"],
        "possible_alerts_now": totals["possible_alerts"],
        "phones_visible_now": totals["phones_visible"],
        "events_24h": count(after=since),
        "events_today": count(after=today),
        "sustained_24h": count(EventCategory.SUSTAINED_PHONE_USE.value, after=since),
        "possible_24h": count(EventCategory.POSSIBLE_PHONE_USE.value, after=since),
        "phone_detections_24h": count(EventCategory.PHONE_DETECTED.value, after=since),
        "uncertain_24h": count(EventCategory.UNCERTAIN.value, after=since),
        "person_events_24h": count(EventCategory.PERSON_DETECTED.value, after=since),
        "simulated_events_24h": count(source=EventSource.SIMULATED.value, after=since),
        "model_events_24h": count(source=EventSource.MODEL.value, after=since),
        "users": users,
        "detector": detector_manager.status.as_dict(),
        "voice_enabled": bool(settings_store.get("voice_enabled", True)),
        "voice_output": settings_store.get("voice_output", "dashboard"),
        "retention_days": settings_store.get("event_retention_days", 90),
        "camera_statuses": statuses,
        "avg_fps": round(
            sum(s["fps"] for s in statuses.values()) / len(statuses), 2
        ) if statuses else 0.0,
        "avg_inference_ms": round(
            sum(s["inference_ms"] for s in statuses.values()) / len(statuses), 2
        ) if statuses else 0.0,
        "last_metric_at": latest_metric.recorded_at.isoformat() if latest_metric else None,
    }


def daily_summary(days: int = 14) -> list[dict[str, Any]]:
    days = max(1, min(days, 365))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with SessionLocal() as db:
        day_col = func.date(DetectionEvent.started_at)
        rows = db.execute(
            select(
                day_col.label("day"),
                DetectionEvent.category,
                DetectionEvent.source,
                func.count(DetectionEvent.id),
            )
            .where(DetectionEvent.started_at >= since)
            .group_by(day_col, DetectionEvent.category, DetectionEvent.source)
            .order_by(day_col)
        ).all()
    buckets: dict[str, dict[str, Any]] = {}
    for day, category, source, count in rows:
        key = str(day)
        bucket = buckets.setdefault(
            key,
            {
                "date": key,
                "total": 0,
                "sustained": 0,
                "possible": 0,
                "phone_detected": 0,
                "uncertain": 0,
                "person_detected": 0,
                "simulated": 0,
                "model": 0,
            },
        )
        bucket["total"] += int(count)
        if category == EventCategory.SUSTAINED_PHONE_USE.value:
            bucket["sustained"] += int(count)
        elif category == EventCategory.POSSIBLE_PHONE_USE.value:
            bucket["possible"] += int(count)
        elif category == EventCategory.PHONE_DETECTED.value:
            bucket["phone_detected"] += int(count)
        elif category == EventCategory.UNCERTAIN.value:
            bucket["uncertain"] += int(count)
        elif category == EventCategory.PERSON_DETECTED.value:
            bucket["person_detected"] += int(count)
        if source == EventSource.SIMULATED.value:
            bucket["simulated"] += int(count)
        else:
            bucket["model"] += int(count)
    return list(buckets.values())


def camera_summary(days: int = 7) -> list[dict[str, Any]]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    with SessionLocal() as db:
        cameras = {c.id: c for c in db.execute(select(Camera)).scalars()}
        rows = db.execute(
            select(
                DetectionEvent.camera_id,
                DetectionEvent.category,
                func.count(DetectionEvent.id),
                func.avg(DetectionEvent.confidence),
                func.max(DetectionEvent.started_at),
            )
            .where(DetectionEvent.started_at >= since)
            .group_by(DetectionEvent.camera_id, DetectionEvent.category)
        ).all()
        metric_rows = db.execute(
            select(
                CameraMetric.camera_id,
                func.avg(CameraMetric.fps),
                func.avg(CameraMetric.inference_ms),
            ).where(CameraMetric.recorded_at >= since).group_by(CameraMetric.camera_id)
        ).all()
    metrics = {cid: (fps, inf) for cid, fps, inf in metric_rows}
    out: dict[int, dict[str, Any]] = {}
    for cid, camera in cameras.items():
        out[cid] = {
            "camera_id": cid,
            "camera_name": camera.name,
            "source_type": camera.source_type,
            "total": 0,
            "sustained": 0,
            "possible": 0,
            "uncertain": 0,
            "mean_confidence": 0.0,
            "last_event_at": None,
            "avg_fps": round((metrics.get(cid, (0, 0))[0] or 0), 2),
            "avg_inference_ms": round((metrics.get(cid, (0, 0))[1] or 0), 2),
        }
    conf_acc: dict[int, list[float]] = {}
    for cid, category, count, avg_conf, last in rows:
        bucket = out.setdefault(
            cid,
            {"camera_id": cid, "camera_name": f"Camera {cid}", "source_type": "", "total": 0,
             "sustained": 0, "possible": 0, "uncertain": 0, "mean_confidence": 0.0,
             "last_event_at": None, "avg_fps": 0.0, "avg_inference_ms": 0.0},
        )
        bucket["total"] += int(count)
        if category == EventCategory.SUSTAINED_PHONE_USE.value:
            bucket["sustained"] += int(count)
        elif category == EventCategory.POSSIBLE_PHONE_USE.value:
            bucket["possible"] += int(count)
        elif category == EventCategory.UNCERTAIN.value:
            bucket["uncertain"] += int(count)
        if avg_conf:
            conf_acc.setdefault(cid, []).append(float(avg_conf))
        if last and (bucket["last_event_at"] is None or str(last) > bucket["last_event_at"]):
            bucket["last_event_at"] = last.isoformat() if hasattr(last, "isoformat") else str(last)
    for cid, values in conf_acc.items():
        out[cid]["mean_confidence"] = round(sum(values) / len(values), 3)
    return sorted(out.values(), key=lambda r: r["total"], reverse=True)


def hourly_activity(days: int = 1) -> list[dict[str, Any]]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 30)))
    with SessionLocal() as db:
        rows = db.execute(
            select(DetectionEvent.started_at, DetectionEvent.category)
            .where(DetectionEvent.started_at >= since)
            .where(DetectionEvent.category == EventCategory.SUSTAINED_PHONE_USE.value)
        ).all()
    buckets = [0] * 24
    for started_at, _ in rows:
        if started_at:
            buckets[started_at.hour] += 1
    return [{"hour": h, "alerts": buckets[h]} for h in range(24)]


def health() -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": "ok",
        "detector": detector_manager.status.as_dict(),
        "voice": __import__("app.services.tts", fromlist=["voice_service"]).voice_service.status(),
        "workers": camera_manager.all_status(),
        "warnings": [],
    }
    try:
        import psutil

        vm = psutil.virtual_memory()
        data["system"] = {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": vm.percent,
            "memory_available_mb": round(vm.available / (1024 * 1024), 1),
            "process_memory_mb": round(psutil.Process().memory_info().rss / (1024 * 1024), 1),
            "cpu_count": psutil.cpu_count(),
        }
    except Exception:  # noqa: BLE001
        data["system"] = {}
    if not detector_manager.status.loaded:
        data["warnings"].append("No detector model loaded - detections are disabled")
    if detector_manager.status.degraded:
        data["warnings"].append(detector_manager.status.extra.get("degraded_reason", "Degraded detector"))
    if data["system"].get("memory_percent", 0) > 92:
        data["warnings"].append("Memory pressure is high; reduce cameras or inference resolution")
    return data


EXPORT_COLUMNS = [
    ("event_uuid", "Event ID"),
    ("camera_name", "Camera"),
    ("started_at", "Started at (UTC)"),
    ("ended_at", "Ended at (UTC)"),
    ("tracking_id", "Anonymous person ID"),
    ("category", "Event category"),
    ("confidence", "Confidence"),
    ("duration_s", "Duration (s)"),
    ("alert_status", "Alert status"),
    ("source", "Detection source"),
    ("acknowledged_by", "Reviewed by"),
    ("review_note", "Review note"),
]


def events_to_csv(events: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in EXPORT_COLUMNS])
    for ev in events:
        writer.writerow([ev.get(key, "") for key, _ in EXPORT_COLUMNS])
    return buf.getvalue()
