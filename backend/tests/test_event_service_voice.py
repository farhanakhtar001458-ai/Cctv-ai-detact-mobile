"""Event lifecycle bookkeeping, redaction and voice alert gating."""

from __future__ import annotations

import logging
import time

import numpy as np
import pytest

from app.db.models import Camera, DetectionEvent, EventCategory
from app.vision.pipeline import FrameAnalysis, TrackSummary
from app.vision.temporal import PhoneUseState


def track(track_id: int = 1, state: PhoneUseState = PhoneUseState.SUSTAINED, score: float = 0.8) -> TrackSummary:
    return TrackSummary(
        track_id=track_id,
        label=f"Person-{track_id:02d}",
        box=np.array([100, 100, 170, 260], dtype=np.float32),
        conf=0.9,
        state=state,
        use_score=score,
        sustained_s=3.0,
        phone_box=np.array([130, 150, 150, 180], dtype=np.float32),
        phone_conf=0.8,
        association_score=0.7,
        reason="test",
    )


def ensure_camera(db, camera_id: int) -> int:
    """Events reference cameras by FK, so make sure the row exists."""
    if db.get(Camera, camera_id) is None:
        db.add(Camera(id=camera_id, name=f"Test camera {camera_id}", source_type="simulated", url_encrypted=""))
        db.commit()
    return camera_id


def analysis(tracks, simulated: bool = False) -> FrameAnalysis:
    return FrameAnalysis(
        timestamp=time.time(),
        frame_index=1,
        frame_w=640,
        frame_h=360,
        tracks=list(tracks),
        occupancy=len(tracks),
        simulated=simulated,
        model_backend="simulated" if simulated else "stub",
        phone_capable=True,
    )


def test_event_service_opens_and_closes_events(db):
    from app.services.events import EventService

    service = EventService(camera_id=ensure_camera(db, 1), source="model")
    created = service.sync(analysis([track()]))
    assert len(created) == 1
    assert created[0]["category"] == EventCategory.SUSTAINED_PHONE_USE.value
    assert created[0]["alert_status"] == "pending"

    # Same state again -> no duplicate event.
    again = service.sync(analysis([track()]))
    assert again == []

    # State changes -> old event closed, new one opened.
    changed = service.sync(analysis([track(state=PhoneUseState.POSSIBLE_USE)]))
    assert len(changed) == 1
    assert changed[0]["category"] == EventCategory.POSSIBLE_PHONE_USE.value
    db.expire_all()
    closed = db.query(DetectionEvent).filter(DetectionEvent.category == "sustained_phone_use").all()
    assert closed and closed[-1].ended_at is not None

    # Person leaves -> remaining event closed.
    service.sync(analysis([]))
    db.expire_all()
    open_events = db.query(DetectionEvent).filter(DetectionEvent.ended_at.is_(None)).all()
    assert open_events == []


def test_event_service_marks_announced(db):
    from app.services.events import EventService

    service = EventService(camera_id=ensure_camera(db, 2), source="model")
    created = service.sync(analysis([track()]))
    event_id = created[0]["event_id"]
    service.mark_announced(event_id, __import__("app.db.models", fromlist=["AlertStatus"]).AlertStatus.ANNOUNCED)
    db.expire_all()
    row = db.get(DetectionEvent, event_id)
    assert row.alert_status == "announced"


def test_voice_alert_is_suppressed_within_cooldown(db, monkeypatch):
    from app.services import alerts as alerts_module
    from app.services.events import EventService
    from app.services.settings_store import settings_store

    settings_store.set_many({"voice_enabled": True, "voice_output": "dashboard", "alert_cooldown_seconds": 600})
    sent = []
    monkeypatch.setattr(alerts_module.voice_service, "announce", lambda announcement: sent.append(announcement))
    monkeypatch.setattr(alerts_module.voice_service, "rate_limited", lambda limit: False)
    monkeypatch.setattr(alerts_module, "voice_slot_free", lambda: True)

    coordinator = alerts_module.AlertCoordinator(camera_id=ensure_camera(db, 999))
    service = EventService(camera_id=ensure_camera(db, 999), source="model")
    events = service.sync(analysis([track(track_id=5)]))
    coordinator.handle("Test Camera", events, service)

    # Second identical alert inside the cooldown must be suppressed.
    coordinator2 = alerts_module.AlertCoordinator(camera_id=ensure_camera(db, 999))
    coordinator2.suppressor = coordinator.suppressor
    events2 = service.sync(analysis([track(track_id=5, state=PhoneUseState.POSSIBLE_USE)]))
    events2[0]["category"] = EventCategory.SUSTAINED_PHONE_USE.value
    coordinator2.handle("Test Camera", events2, service)

    assert len(sent) == 1, "only the first alert should be announced"
    assert "Mobile phone" in sent[0].text or sent[0].text


def test_voice_disabled_prevents_announcement(db, monkeypatch):
    from app.services import alerts as alerts_module
    from app.services.events import EventService
    from app.services.settings_store import settings_store

    settings_store.set_many({"voice_enabled": False})
    sent = []
    monkeypatch.setattr(alerts_module.voice_service, "announce", lambda announcement: sent.append(announcement))
    coordinator = alerts_module.AlertCoordinator(camera_id=ensure_camera(db, 777))
    service = EventService(camera_id=ensure_camera(db, 777), source="model")
    coordinator.handle("C", service.sync(analysis([track(track_id=8)])), service)
    assert sent == []
    settings_store.set_many({"voice_enabled": True})


def test_simulated_alerts_are_labelled(db, monkeypatch):
    from app.services import alerts as alerts_module
    from app.services.events import EventService
    from app.services.settings_store import settings_store

    settings_store.set_many({"voice_enabled": True, "voice_output": "dashboard", "alert_cooldown_seconds": 5})
    sent = []
    monkeypatch.setattr(alerts_module.voice_service, "announce", lambda announcement: sent.append(announcement))
    monkeypatch.setattr(alerts_module.voice_service, "rate_limited", lambda limit: False)
    monkeypatch.setattr(alerts_module, "voice_slot_free", lambda: True)

    coordinator = alerts_module.AlertCoordinator(camera_id=ensure_camera(db, 555))
    service = EventService(camera_id=ensure_camera(db, 555), source="simulated")
    coordinator.handle("Sim", service.sync(analysis([track(track_id=9)], simulated=True)), service, simulated=True)
    assert sent and sent[0].text.startswith("[SIMULATED]")


def test_log_redaction_never_leaks_credentials():
    from app.core.logging import redact

    message = "connecting rtsp://admin:secret123@10.0.0.5:554/live password=hunter2"
    cleaned = redact(message)
    assert "secret123" not in cleaned
    assert "hunter2" not in cleaned
    assert "rtsp://admin:***@10.0.0.5:554/live" in cleaned


def test_redacting_filter_applies_to_records(caplog):
    from app.core.logging import RedactingFilter

    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "url rtsp://user:pass@host/x", None, None
    )
    RedactingFilter().filter(record)
    assert "pass" not in record.getMessage()


def test_encrypt_decrypt_roundtrip():
    from app.core.crypto import decrypt_secret, encrypt_secret

    secret = "rtsp://user:p@ss:word@cam.local:554/stream"
    cipher = encrypt_secret(secret)
    assert cipher.startswith("enc:v1:")
    assert secret not in cipher
    assert decrypt_secret(cipher) == secret
    assert encrypt_secret(secret) != cipher  # random nonce


def test_password_hashing():
    from app.core.security import hash_password, verify_password

    hashed = hash_password("CorrectHorse1")
    assert hashed.startswith("pbkdf2_sha256$")
    assert "CorrectHorse1" not in hashed
    assert verify_password("CorrectHorse1", hashed)
    assert not verify_password("wrong", hashed)
    with pytest.raises(ValueError):
        hash_password("short")
