"""Events API, CSV export, settings validation and retention."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest


def _make_event(db, camera_id=1, category="sustained_phone_use", source="model", age_days=0):
    from app.db.models import DetectionEvent

    event = DetectionEvent(
        camera_id=camera_id,
        tracking_id="Person-07",
        category=category,
        confidence=0.82,
        started_at=datetime.now(timezone.utc) - timedelta(days=age_days),
        ended_at=datetime.now(timezone.utc) - timedelta(days=age_days) + timedelta(seconds=4),
        duration_s=4.0,
        alert_status="announced",
        source=source,
        evidence='{"use_score": 0.82}',
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def test_event_listing_and_filters(app_client, admin_headers, db):
    _make_event(db, category="sustained_phone_use")
    _make_event(db, category="uncertain", source="simulated")

    all_events = app_client.get("/api/events?limit=50", headers=admin_headers).json()
    assert len(all_events) >= 2
    sustained = app_client.get("/api/events?category=sustained_phone_use", headers=admin_headers).json()
    assert all(event["category"] == "sustained_phone_use" for event in sustained)
    simulated = app_client.get("/api/events?source=simulated", headers=admin_headers).json()
    assert simulated and all(event["source"] == "simulated" for event in simulated)
    assert app_client.get("/api/events?category=bogus", headers=admin_headers).status_code == 400
    assert app_client.get("/api/events?since=not-a-date", headers=admin_headers).status_code == 400


def test_event_detail_and_acknowledge(app_client, admin_headers, supervisor_headers, db):
    event = _make_event(db)
    detail = app_client.get(f"/api/events/{event.id}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["event_uuid"] == event.event_uuid

    ack = app_client.post(
        f"/api/events/{event.id}/acknowledge", headers=supervisor_headers, json={"note": "Reviewed with class teacher"}
    )
    assert ack.status_code == 200
    refreshed = app_client.get(f"/api/events/{event.id}", headers=admin_headers).json()
    assert refreshed["alert_status"] == "acknowledged"
    assert refreshed["acknowledged_by"] == "supervisor"
    assert refreshed["review_note"] == "Reviewed with class teacher"
    assert app_client.get("/api/events/999999", headers=admin_headers).status_code == 404


def test_csv_export_headers_and_rows(app_client, admin_headers, db):
    _make_event(db)
    response = app_client.get("/api/events/export/csv?days=30", headers=admin_headers)
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][0] == "Event ID"
    assert "Anonymous person ID" in rows[0]
    assert len(rows) > 1
    # No credentials, no video/frame data in exports.
    assert "enc:v1" not in response.text


def test_settings_validation_and_persistence(app_client, admin_headers):
    invalid = app_client.put("/api/settings", headers=admin_headers, json={"values": {"person_conf": 5}})
    assert invalid.status_code == 400
    unknown = app_client.put("/api/settings", headers=admin_headers, json={"values": {"nope": 1}})
    assert unknown.status_code == 400

    ok = app_client.put(
        "/api/settings",
        headers=admin_headers,
        json={"values": {"persistence_threshold": 0.72, "voice_enabled": False, "voice_message_sustained": "No phones in class."}},
    )
    assert ok.status_code == 200
    settings = {item["key"]: item["value"] for item in app_client.get("/api/settings", headers=admin_headers).json()}
    assert settings["persistence_threshold"] == 0.72
    assert settings["voice_enabled"] is False
    assert settings["voice_message_sustained"] == "No phones in class."

    # Restore for other tests.
    app_client.put(
        "/api/settings",
        headers=admin_headers,
        json={"values": {"persistence_threshold": 0.65, "voice_enabled": True}},
    )


def test_voice_endpoints(app_client, admin_headers):
    status = app_client.get("/api/settings/voice/status", headers=admin_headers)
    assert status.status_code == 200
    body = status.json()
    assert body["output"] in ("dashboard", "server", "both")
    assert "engine" in body

    test = app_client.post("/api/settings/voice/test", headers=admin_headers)
    assert test.status_code == 200
    assert test.json()["ok"] is True


def test_retention_purge_removes_old_events(app_client, admin_headers, db):
    old = _make_event(db, age_days=400)
    recent = _make_event(db, age_days=1)
    response = app_client.delete("/api/events/purge?older_than_days=90", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["detail"]["events_removed"] >= 1
    assert app_client.get(f"/api/events/{old.id}", headers=admin_headers).status_code == 404
    assert app_client.get(f"/api/events/{recent.id}", headers=admin_headers).status_code == 200


def test_stats_overview_and_summaries(app_client, admin_headers, db):
    _make_event(db)
    overview = app_client.get("/api/stats/overview", headers=admin_headers).json()
    for key in ("cameras_total", "occupancy_now", "active_alerts_now", "sustained_24h", "detector", "retention_days"):
        assert key in overview
    assert overview["detector"]["phone_capable"] in (True, False)

    daily = app_client.get("/api/stats/daily?days=7", headers=admin_headers).json()
    assert isinstance(daily, list)
    cameras = app_client.get("/api/stats/cameras?days=7", headers=admin_headers).json()
    assert isinstance(cameras, list) and cameras
    assert "camera_name" in cameras[0]


def test_audit_log_records_actions(app_client, admin_headers):
    app_client.put("/api/settings", headers=admin_headers, json={"values": {"person_conf": 0.4}})
    app_client.put("/api/settings", headers=admin_headers, json={"values": {"person_conf": 0.35}})
    logs = app_client.get("/api/audit?limit=200", headers=admin_headers).json()
    actions = {entry["action"] for entry in logs}
    assert "settings_updated" in actions
    assert all(entry["actor"] for entry in logs)


def test_audit_requires_admin(app_client, supervisor_headers):
    assert app_client.get("/api/audit", headers=supervisor_headers).status_code == 403


def test_system_info_privacy_flags(app_client, admin_headers):
    info = app_client.get("/api/system/info", headers=admin_headers).json()
    assert info["privacy"]["facial_recognition"] is False
    assert info["privacy"]["stores_video"] is False
    assert info["privacy"]["anonymous_ids"] is True


@pytest.mark.parametrize("path", ["/api/stats/overview", "/api/events"])
def test_no_credentials_in_listing(app_client, admin_headers, path):
    body = app_client.get(path, headers=admin_headers).text
    assert "password" not in body.lower()
    assert "enc:v1" not in body
