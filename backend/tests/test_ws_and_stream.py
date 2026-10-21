"""WebSocket authentication/realtime protocol and MJPEG preview auth."""

from __future__ import annotations

import json
import time

import pytest


def _token(app_client, username="admin", password="AdminPass123") -> str:
    response = app_client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_websocket_requires_token(app_client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with app_client.websocket_connect("/api/ws") as ws:
            ws.receive_text()
    assert excinfo.value.code == 4401


def test_websocket_rejects_invalid_token(app_client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with app_client.websocket_connect("/api/ws?token=not-a-jwt") as ws:
            ws.receive_text()
    assert excinfo.value.code == 4401


def test_websocket_hello_and_ping(app_client):
    token = _token(app_client)
    with app_client.websocket_connect(f"/api/ws?token={token}") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["topic"] == "hello"
        assert hello["type"] == "connected"
        assert hello["user"]["username"] == "admin"
        assert "cameras" in hello and "recent_events" in hello

        ws.send_text(json.dumps({"type": "ping", "t": 123}))
        pong = json.loads(ws.receive_text())
        assert pong["topic"] == "pong"

        ws.send_text(json.dumps({"type": "subscribe", "topics": ["frame", "alert"]}))
        ack = json.loads(ws.receive_text())
        assert ack["type"] == "subscribed"
        assert set(ack["topics"]) == {"frame", "alert"}


def test_websocket_receives_live_frames(app_client, admin_headers):
    token = _token(app_client)
    started = app_client.post("/api/cameras/1/start", headers=admin_headers)
    assert started.status_code == 200
    try:
        with app_client.websocket_connect(f"/api/ws?token={token}&topics=frame") as ws:
            json.loads(ws.receive_text())  # hello
            deadline = time.time() + 20
            frame = None
            while time.time() < deadline:
                message = json.loads(ws.receive_text())
                if message.get("topic") == "frame":
                    frame = message
                    break
            assert frame is not None, "no frame updates over websocket"
            assert frame["camera_id"] == 1
            assert frame["simulated"] is True
            assert "tracks" in frame
            assert frame["model"]["backend"] == "simulated"
    finally:
        app_client.post("/api/cameras/1/stop", headers=admin_headers)


def test_mjpeg_requires_stream_token(app_client, admin_headers):
    started = app_client.post("/api/cameras/1/start", headers=admin_headers)
    assert started.status_code == 200
    try:
        assert app_client.get("/api/cameras/1/stream.mjpg").status_code == 401
        token = app_client.post("/api/auth/stream-token", headers=admin_headers).json()["token"]
        # Immediate check of the response headers only (the stream is infinite).
        with app_client.stream("GET", f"/api/cameras/1/stream.mjpg?token={token}") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("multipart/x-mixed-replace")
            assert response.headers["cache-control"] == "no-store"
    finally:
        app_client.post("/api/cameras/1/stop", headers=admin_headers)


def test_snapshot_available_for_running_camera(app_client, admin_headers):
    started = app_client.post("/api/cameras/1/start", headers=admin_headers)
    assert started.status_code == 200
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            response = app_client.get("/api/cameras/1/snapshot.jpg", headers=admin_headers)
            if response.status_code == 200:
                assert response.content[:2] == b"\xff\xd8"
                assert response.headers["x-content-type-options"] == "nosniff"
                return
            time.sleep(0.25)
        raise AssertionError("no snapshot produced")
    finally:
        app_client.post("/api/cameras/1/stop", headers=admin_headers)


def test_stopping_camera_clears_analysis(app_client, admin_headers):
    app_client.post("/api/cameras/1/start", headers=admin_headers)
    time.sleep(2)
    app_client.post("/api/cameras/1/stop", headers=admin_headers)
    assert app_client.get("/api/cameras/1/analysis", headers=admin_headers).status_code == 404
    assert app_client.get("/api/cameras/1/snapshot.jpg", headers=admin_headers).status_code == 404
