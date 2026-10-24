"""Unit tests for scripts/check_stream.py (no network access required)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_stream as cs  # noqa: E402


@pytest.mark.parametrize(
    "url, expected",
    [
        ("rtsp://192.168.1.10:554/Streaming/Channels/101", "rtsp"),
        ("rtsps://cam.local/live", "rtsp"),
        ("http://192.168.1.55:8080/video", "http"),
        ("https://example.com/stream.mjpg", "http"),
        ("sim://classroom", "simulated"),
        ("/media/clips/lesson.mp4", "file"),
        ("0", "webcam"),
    ],
)
def test_guess_kind(url, expected):
    assert cs.guess_kind(url) == expected


def test_redact_hides_password():
    safe = cs.redact("rtsp://admin:SuperSecret123@192.168.1.64:554/live")
    assert "SuperSecret123" not in safe
    assert safe == "rtsp://admin:***@192.168.1.64:554/live"


def test_redact_hides_query_tokens():
    safe = cs.redact("http://cam.local/video?user=a&password=b")
    assert "password=b" not in safe
    assert safe.endswith("user=***&password=***")


def test_redact_leaves_plain_urls_alone():
    assert cs.redact("http://192.168.1.55:8080/video") == "http://192.168.1.55:8080/video"


def test_redact_survives_garbage():
    assert isinstance(cs.redact("not a url at all"), str)


def test_explain_http_auth_required():
    status, message = cs.explain_http({"status": 401, "content_type": "text/plain",
                                       "www_authenticate": "Basic realm=cam"})
    assert status == cs.FAIL
    assert "authentication required" in message
    assert "Basic" in message


def test_explain_http_mjpeg_is_ok():
    status, message = cs.explain_http(
        {"status": 200, "content_type": "multipart/x-mixed-replace; boundary=frame"}
    )
    assert status == cs.OK
    assert "MJPEG" in message


def test_explain_http_html_page_is_flagged():
    status, message = cs.explain_http({"status": 200, "content_type": "text/html; charset=utf-8"})
    assert status == cs.WARN
    assert "not a video stream" in message


def test_explain_http_404_gives_path_hints():
    status, message = cs.explain_http({"status": 404, "content_type": "text/html"})
    assert status == cs.FAIL
    assert "/video" in message


def test_explain_http_snapshot():
    status, message = cs.explain_http({"status": 200, "content_type": "image/jpeg"})
    assert status == cs.WARN
    assert "snapshot" in message


def test_tcp_probe_reports_dns_failure(monkeypatch):
    import socket

    def boom(*_args, **_kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(cs.socket, "create_connection", boom)
    result = cs.tcp_probe("no-such-host.invalid", 554, 1.0)
    assert result["ok"] is False
    assert result["reason"] == "dns"
    assert "resolve" in result["detail"].lower()


def test_tcp_probe_reports_refused(monkeypatch):
    def boom(*_args, **_kwargs):
        raise ConnectionRefusedError()

    monkeypatch.setattr(cs.socket, "create_connection", boom)
    result = cs.tcp_probe("127.0.0.1", 9, 1.0)
    assert result["reason"] == "refused"
    assert "9" in result["detail"]


def test_tcp_probe_reports_timeout(monkeypatch):
    import socket as real_socket

    def boom(*_args, **_kwargs):
        raise real_socket.timeout()

    monkeypatch.setattr(cs.socket, "create_connection", boom)
    result = cs.tcp_probe("10.0.0.1", 554, 0.1)
    assert result["reason"] == "timeout"


def test_tcp_probe_reports_reset(monkeypatch):
    def boom(*_args, **_kwargs):
        raise ConnectionResetError()

    monkeypatch.setattr(cs.socket, "create_connection", boom)
    result = cs.tcp_probe("203.0.113.9", 8080, 1.0)
    assert result["reason"] == "error"
    assert "blocked" in result["detail"].lower()
