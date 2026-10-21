"""Authentication, authorization, rate limiting and account management."""

from __future__ import annotations

import pytest


def test_health_is_public(app_client):
    response = app_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    # No sensitive data leaks through the public probe.
    body = response.text.lower()
    assert "password" not in body and "secret" not in body


def test_protected_routes_require_token(app_client):
    for path in ("/api/cameras", "/api/events", "/api/stats/overview", "/api/settings"):
        response = app_client.get(path)
        assert response.status_code == 401, path


def test_login_and_permissions(app_client):
    response = app_client.post("/api/auth/login", json={"username": "admin", "password": "AdminPass123"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["user"]["role"] == "admin"
    headers = {"Authorization": f"Bearer {payload['access_token']}"}
    perms = app_client.get("/api/auth/permissions", headers=headers).json()
    assert perms["can_manage_users"] and perms["can_change_settings"]


def test_bad_password_is_rejected(app_client):
    response = app_client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert response.status_code == 401


def test_viewer_cannot_change_settings(app_client, supervisor_headers):
    created = app_client.post(
        "/api/auth/users",
        headers=supervisor_headers,
        json={"username": "viewer1", "password": "ViewerPass123", "role": "viewer"},
    )
    # Supervisors cannot manage users.
    assert created.status_code == 403


def test_admin_can_create_viewer_and_viewer_is_read_only(app_client, admin_headers):
    created = app_client.post(
        "/api/auth/users",
        headers=admin_headers,
        json={"username": "viewer2", "password": "ViewerPass123", "role": "viewer", "full_name": "Viewer Two"},
    )
    assert created.status_code == 201, created.text
    login = app_client.post("/api/auth/login", json={"username": "viewer2", "password": "ViewerPass123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert app_client.get("/api/cameras", headers=headers).status_code == 200
    # Reads allowed, writes denied.
    assert app_client.put("/api/settings", headers=headers, json={"values": {"person_conf": 0.4}}).status_code == 403
    camera = app_client.post(
        "/api/cameras",
        headers=headers,
        json={"name": "Nope", "source_type": "simulated", "url": "sim://x"},
    )
    assert camera.status_code == 403
    assert app_client.get("/api/auth/users", headers=headers).status_code == 403


def test_weak_password_rejected_by_schema(app_client, admin_headers):
    response = app_client.post(
        "/api/auth/users",
        headers=admin_headers,
        json={"username": "shortpw", "password": "123", "role": "viewer"},
    )
    assert response.status_code == 422


def test_duplicate_username_conflict(app_client, admin_headers):
    response = app_client.post(
        "/api/auth/users",
        headers=admin_headers,
        json={"username": "admin", "password": "AnotherPass123", "role": "viewer"},
    )
    assert response.status_code == 409


def test_stream_token_has_scope_and_works_for_snapshot(app_client, admin_headers):
    started = app_client.post("/api/cameras/1/start", headers=admin_headers)
    assert started.status_code == 200
    try:
        token_response = app_client.post("/api/auth/stream-token", headers=admin_headers)
        assert token_response.status_code == 200
        stream_token = token_response.json()["token"]
        # Query-parameter auth works for <img> tags. Wait for the first frame.
        import time

        snapshot = None
        deadline = time.time() + 15
        while time.time() < deadline:
            snapshot = app_client.get(f"/api/cameras/1/snapshot.jpg?token={stream_token}")
            if snapshot.status_code == 200:
                break
            time.sleep(0.25)
        assert snapshot is not None and snapshot.status_code == 200
        assert snapshot.headers["content-type"] == "image/jpeg"
        # ...and no token means no access.
        assert app_client.get("/api/cameras/1/snapshot.jpg").status_code == 401
    finally:
        app_client.post("/api/cameras/1/stop", headers=admin_headers)


def test_password_change_flow(app_client, admin_headers):
    app_client.post(
        "/api/auth/users",
        headers=admin_headers,
        json={"username": "pwuser", "password": "OldPass1234", "role": "viewer"},
    )
    login = app_client.post("/api/auth/login", json={"username": "pwuser", "password": "OldPass1234"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    changed = app_client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "OldPass1234", "new_password": "NewPass1234"},
    )
    assert changed.status_code == 200
    assert app_client.post("/api/auth/login", json={"username": "pwuser", "password": "OldPass1234"}).status_code == 401
    assert app_client.post("/api/auth/login", json={"username": "pwuser", "password": "NewPass1234"}).status_code == 200


@pytest.mark.parametrize("path", ["/api/docs", "/api/health"])
def test_security_headers(app_client, path):
    response = app_client.get(path)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_login_rate_limit_returns_429(app_client, reset_rate_limiter):
    from app.core.config import settings

    original = settings.rate_limit_enabled
    settings.rate_limit_enabled = True
    try:
        codes = []
        for _ in range(14):
            codes.append(
                app_client.post(
                    "/api/auth/login", json={"username": "admin", "password": "definitely-wrong"}
                ).status_code
            )
        assert 429 in codes, codes
    finally:
        settings.rate_limit_enabled = original
