"""Camera API: credential hygiene, validation, lifecycle and preview."""

from __future__ import annotations

import time


def test_create_camera_strips_credentials_from_response(app_client, admin_headers):
    payload = {
        "name": "Corridor CCTV",
        "location": "Block B",
        "source_type": "rtsp",
        "url": "rtsp://192.168.1.50:554/stream1",
        "username": "camera-user",
        "password": "SuperSecret#42",
        "inference_width": 1280,
        "inference_height": 720,
    }
    response = app_client.post("/api/cameras", headers=admin_headers, json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert "SuperSecret#42" not in response.text
    assert "camera-user" not in response.text
    assert body["url_display"] == "rtsp://192.168.1.50:554/stream1"
    assert body["has_credentials"] is True

    # Listing must not leak the credentials either.
    listing = app_client.get("/api/cameras", headers=admin_headers)
    assert "SuperSecret" not in listing.text
    assert "camera-user@" not in listing.text


def test_credentials_are_encrypted_at_rest(app_client, admin_headers, db):
    from app.db.models import Camera

    app_client.post(
        "/api/cameras",
        headers=admin_headers,
        json={
            "name": "At-rest check",
            "source_type": "rtsp",
            "url": "rtsp://10.0.0.9/live",
            "username": "u1",
            "password": "p@ssword",
        },
    )
    camera = db.query(Camera).filter(Camera.name == "At-rest check").one()
    assert camera.url_encrypted.startswith("enc:v1:")
    assert "p@ssword" not in camera.url_encrypted
    from app.core.crypto import decrypt_secret

    assert decrypt_secret(camera.url_encrypted) == "rtsp://u1:p%40ssword@10.0.0.9/live"


def test_inline_credentials_in_url_are_scrubbed(app_client, admin_headers):
    response = app_client.post(
        "/api/cameras",
        headers=admin_headers,
        json={"name": "Inline creds", "source_type": "rtsp", "url": "rtsp://admin:pass123@192.168.0.10/stream"},
    )
    assert response.status_code == 201
    assert "pass123" not in response.text
    assert response.json()["url_display"] == "rtsp://192.168.0.10/stream"


def test_invalid_url_scheme_rejected(app_client, admin_headers):
    response = app_client.post(
        "/api/cameras",
        headers=admin_headers,
        json={"name": "Bad", "source_type": "rtsp", "url": "ftp://nope"},
    )
    assert response.status_code == 422


def test_rtsp_camera_requires_url(app_client, admin_headers):
    response = app_client.post(
        "/api/cameras", headers=admin_headers, json={"name": "No URL", "source_type": "rtsp"}
    )
    assert response.status_code == 400


def test_camera_update_keeps_stored_password(app_client, admin_headers, db):
    from app.core.crypto import decrypt_secret
    from app.db.models import Camera

    created = app_client.post(
        "/api/cameras",
        headers=admin_headers,
        json={"name": "Keep creds", "source_type": "rtsp", "url": "rtsp://1.2.3.4/s", "username": "a", "password": "b"},
    ).json()
    updated = app_client.patch(
        f"/api/cameras/{created['id']}", headers=admin_headers, json={"name": "Kept creds renamed"}
    )
    assert updated.status_code == 200
    camera = db.query(Camera).filter(Camera.id == created["id"]).one()
    assert decrypt_secret(camera.url_encrypted) == "rtsp://a:b@1.2.3.4/s"


def test_camera_test_endpoint_reports_failure_for_unreachable_stream(app_client, admin_headers):
    response = app_client.post(
        "/api/cameras/test",
        headers=admin_headers,
        json={"name": "probe", "source_type": "rtsp", "url": "rtsp://127.0.0.1:9/none", "username": "", "password": ""},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "error" in body["detail"]


def test_simulated_camera_lifecycle_and_events(app_client, admin_headers):
    created = app_client.post(
        "/api/cameras",
        headers=admin_headers,
        json={
            "name": "Test Sim",
            "source_type": "simulated",
            "url": "sim://classroom",
            "frame_sample": 1,
            "alert_cooldown_s": 5,
            "persistence_threshold": 0.5,
        },
    )
    assert created.status_code == 201
    camera_id = created.json()["id"]
    try:
        started = app_client.post(f"/api/cameras/{camera_id}/start", headers=admin_headers)
        assert started.status_code == 200, started.text
        # Wait for the worker to produce detections.
        deadline = time.time() + 20
        analysis = None
        while time.time() < deadline:
            response = app_client.get(f"/api/cameras/{camera_id}/analysis", headers=admin_headers)
            if response.status_code == 200 and response.json()["frame_index"] > 15:
                analysis = response.json()
                break
            time.sleep(0.4)
        assert analysis is not None, "worker did not produce analysis"
        assert analysis["simulated"] is True
        assert analysis["occupancy"] > 0
        assert len(analysis["tracks"]) == analysis["occupancy"]

        status = app_client.get(f"/api/cameras/{camera_id}/status", headers=admin_headers).json()
        assert status["status"] == "online"

        # Simulated events must be tagged as such.
        events = app_client.get(
            f"/api/events?camera_id={camera_id}&limit=20", headers=admin_headers
        ).json()
        if events:
            assert all(event["source"] == "simulated" and event["simulated"] for event in events)

        snapshot = app_client.get(f"/api/cameras/{camera_id}/snapshot.jpg", headers=admin_headers)
        assert snapshot.status_code == 200
        assert snapshot.content[:2] == b"\xff\xd8"
    finally:
        stopped = app_client.post(f"/api/cameras/{camera_id}/stop", headers=admin_headers)
        assert stopped.status_code == 200
        assert app_client.get(f"/api/cameras/{camera_id}/analysis", headers=admin_headers).status_code == 404
        app_client.delete(f"/api/cameras/{camera_id}", headers=admin_headers)


def test_disabled_camera_cannot_start(app_client, admin_headers):
    created = app_client.post(
        "/api/cameras",
        headers=admin_headers,
        json={"name": "Disabled one", "source_type": "simulated", "url": "sim://x", "enabled": False},
    ).json()
    response = app_client.post(f"/api/cameras/{created['id']}/start", headers=admin_headers)
    assert response.status_code == 409
    app_client.delete(f"/api/cameras/{created['id']}", headers=admin_headers)


def test_unknown_camera_returns_404(app_client, admin_headers):
    assert app_client.get("/api/cameras/9999", headers=admin_headers).status_code == 404
    assert app_client.post("/api/cameras/9999/start", headers=admin_headers).status_code == 404
