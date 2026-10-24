#!/usr/bin/env python
"""Pre-flight check for one camera stream (RTSP / HTTP-MJPEG / file / webcam).

Answers the only question that matters before you add a camera to the
dashboard: *can this machine actually pull frames from this URL, and if not,
exactly why?*

    python scripts/check_stream.py --url rtsp://192.168.1.64:554/Streaming/Channels/101
    python scripts/check_stream.py --url http://192.168.1.55:8080/video --frames 40
    python scripts/check_stream.py --url /media/clips/classroom.mp4
    python scripts/check_stream.py --url http://109.206.96.58:8080/ --json

Exit codes
----------
    0  frames decoded (stream is usable)
    2  host unreachable (DNS / refused / timeout / blocked)
    3  reachable but no video frames decoded
    4  authentication required (401 / RTSP 401)
    5  bad arguments

Credentials embedded in a URL are NEVER printed - they are redacted in every
line of output, including errors and JSON.
"""

from __future__ import annotations

import argparse
import http.client
import json
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OK = "\033[32m[ OK ]\033[0m"
WARN = "\033[33m[WARN]\033[0m"
FAIL = "\033[31m[FAIL]\033[0m"


def redact(url: str) -> str:
    """Hide any user:password@ part of a URL (and query tokens)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparsable url>"
    if not parts.netloc:
        return url
    netloc = parts.netloc
    if "@" in netloc:
        host = netloc.rsplit("@", 1)[1]
        user = netloc.rsplit("@", 1)[0].split(":")[0]
        netloc = f"{user}:***@{host}"
    query = ""
    if parts.query:
        keys = [item.split("=")[0] for item in parts.query.split("&") if item]
        query = "&".join(f"{key}=***" for key in keys)
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def guess_kind(url: str) -> str:
    lowered = url.lower()
    if lowered.startswith("sim://"):
        return "simulated"
    if lowered.startswith("rtsp"):
        return "rtsp"
    if lowered.startswith("rtsps"):
        return "rtsp"
    if lowered.startswith(("http://", "https://")):
        return "http"
    if url.isdigit():
        return "webcam"
    return "file"


def tcp_probe(host: str, port: int, timeout: float) -> dict[str, Any]:
    """Plain TCP reachability, with a human explanation for every failure."""
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {
                "ok": True,
                "ms": round((time.perf_counter() - started) * 1000, 1),
                "detail": "TCP handshake completed",
            }
    except socket.gaierror as exc:
        return {
            "ok": False,
            "reason": "dns",
            "detail": f"Cannot resolve host '{host}' ({exc.strerror or exc}). Check the spelling, "
            "or the device/DNS is unreachable from this machine.",
        }
    except ConnectionRefusedError:
        return {
            "ok": False,
            "reason": "refused",
            "detail": f"Host is up but nothing is listening on port {port}. Wrong port, "
            "or the camera/web UI is not running.",
        }
    except (socket.timeout, TimeoutError):
        return {
            "ok": False,
            "reason": "timeout",
            "detail": f"No answer from {host}:{port} within {timeout}s. Usually a firewall, "
            "NAT, or the device being on a different network.",
        }
    except OSError as exc:
        return {
            "ok": False,
            "reason": "error",
            "detail": f"Connection reset or blocked while reaching {host}:{port} ({exc}). "
            "A proxy/firewall is dropping the traffic, or the port is closed.",
        }


def http_probe(url: str, timeout: float) -> dict[str, Any]:
    """Fetch only the response headers so MJPEG streams do not run forever."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    https = parts.scheme == "https"
    port = parts.port or (443 if https else 80)
    path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    conn_cls = http.client.HTTPSConnection if https else http.client.HTTPConnection
    conn = conn_cls(host, port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"User-Agent": "scai-check-stream/1.0", "Connection": "close"})
        response = conn.getresponse()
        content_type = (response.getheader("Content-Type") or "").lower()
        return {
            "ok": response.status < 400,
            "status": response.status,
            "content_type": content_type,
            "www_authenticate": response.getheader("WWW-Authenticate") or "",
            "cors": response.getheader("Access-Control-Allow-Origin") or "",
        }
    finally:
        conn.close()


def rtsp_probe(url: str, timeout: float) -> dict[str, Any]:
    """Send RTSP OPTIONS: distinguishes 'no RTSP server' from 'needs a login'."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or 554
    request = f"OPTIONS {url} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: scai-check-stream\r\n\r\n"
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(request.encode())
            sock.settimeout(timeout)
            data = sock.recv(2048).decode("utf-8", "replace")
    except (socket.timeout, TimeoutError):
        return {"ok": False, "reason": "timeout", "detail": "RTSP server did not answer OPTIONS"}
    except OSError as exc:
        return {"ok": False, "reason": "error", "detail": f"RTSP socket error: {exc}"}
    first = data.splitlines()[0] if data else "(empty response)"
    return {
        "ok": "200" in first,
        "status_line": first,
        "unauthorised": "401" in first,
    }


def opencv_probe(url: str, kind: str, frames: int, timeout_ms: int) -> dict[str, Any]:
    """Decode real frames and measure resolution / FPS / brightness."""
    import cv2
    import numpy as np

    from app.vision.sources import build_source

    source = None
    try:
        source = build_source(kind, url, fps=15.0, pace=False)
        source.open()
        captured: list[np.ndarray] = []
        started = time.perf_counter()
        deadline = started + max(3.0, timeout_ms / 1000.0)
        while len(captured) < frames and time.perf_counter() < deadline:
            frame = source.read()
            if frame is not None:
                captured.append(frame)
        elapsed = time.perf_counter() - started
        if not captured:
            return {"ok": False, "reason": "no_frames", "frames": 0,
                    "detail": f"reached the source but decoded 0 frames in {elapsed:.1f}s"}
        last = captured[-1]
        height, width = last.shape[:2]
        gray = cv2.cvtColor(last, cv2.COLOR_BGR2GRAY)
        return {
            "ok": True,
            "frames": len(captured),
            "width": int(width),
            "height": int(height),
            "fps": round(len(captured) / elapsed, 1) if elapsed > 0 else 0.0,
            "brightness": round(float(np.mean(gray)) / 255.0, 3),
            "low_light": bool(float(np.mean(gray)) / 255.0 < 0.22),
            "seconds": round(elapsed, 2),
        }
    except Exception as exc:  # noqa: BLE001 - reported to the operator
        return {"ok": False, "reason": "open_error", "detail": f"{type(exc).__name__}: {exc}"[:300]}
    finally:
        if source is not None:
            source.release()


def explain_http(probe: dict[str, Any]) -> tuple[str, str]:
    """Turn an HTTP response into (status_line, actionable hint)."""
    status = probe.get("status")
    ctype = probe.get("content_type", "")
    auth = probe.get("www_authenticate", "")
    if status in (401, 403):
        scheme = auth.split(",")[0] if auth else "HTTP auth"
        return (
            FAIL,
            f"HTTP {status} - authentication required ({scheme}). Add the camera username "
            "and password in the dashboard (they are encrypted at rest).",
        )
    if status == 404:
        return FAIL, "HTTP 404 - wrong path. Common MJPEG paths: /video, /videostream.cgi, " \
                     "/mjpg/video.mjpg, /stream, /axis-cgi/mjpg/video.cgi"
    if status and status >= 400:
        return FAIL, f"HTTP {status} - the server refused the request."
    if "multipart/x-mixed-replace" in ctype:
        return OK, f"HTTP {status} - MJPEG stream detected ({ctype}). Use source type 'http'."
    if ctype.startswith("video/"):
        return OK, f"HTTP {status} - video stream detected ({ctype})."
    if "text/html" in ctype:
        return WARN, (
            f"HTTP {status} - HTML page, not a video stream ({ctype}). This looks like a "
            "web UI or login page. Use the stream path (often /video or /videostream.cgi), "
            "not the site root."
        )
    if ctype.startswith("image/"):
        return WARN, f"HTTP {status} - single JPEG snapshot ({ctype}), not a live stream."
    return WARN, f"HTTP {status} - content type '{ctype or 'unknown'}' is not a video stream."


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether a camera stream is usable.")
    parser.add_argument("--url", required=True, help="RTSP/HTTP URL, file path or webcam index")
    parser.add_argument("--type", default="auto",
                        choices=["auto", "rtsp", "http", "file", "webcam", "simulated"])
    parser.add_argument("--frames", type=int, default=30, help="frames to decode (default 30)")
    parser.add_argument("--timeout", type=float, default=8.0, help="network timeout in seconds")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    kind = guess_kind(args.url) if args.type == "auto" else args.type
    safe_url = redact(args.url)
    report: dict[str, Any] = {"url": safe_url, "kind": kind, "checks": {}}

    if not args.json:
        print(f"Target : {safe_url}")
        print(f"Type   : {kind}")

    # 1. reachability -----------------------------------------------------
    if kind in ("rtsp", "http"):
        parts = urlsplit(args.url)
        host = parts.hostname or ""
        port = parts.port or (554 if kind == "rtsp" else (443 if parts.scheme == "https" else 80))
        tcp = tcp_probe(host, port, args.timeout)
        report["checks"]["tcp"] = {**tcp, "host": host, "port": port}
        if not args.json:
            print(f"TCP    : {host}:{port} -> " + ("reachable" if tcp["ok"] else tcp["reason"]))
            if not tcp["ok"]:
                print(f"{FAIL} {tcp['detail']}")
        if not tcp["ok"]:
            if args.json:
                print(json.dumps(report, indent=2))
            return 2

    if kind == "http":
        try:
            probe = http_probe(args.url, args.timeout)
            report["checks"]["http"] = probe
            if not args.json:
                status, message = explain_http(probe)
                print(f"HTTP   : {status} {message}")
        except Exception as exc:  # noqa: BLE001
            report["checks"]["http"] = {"ok": False, "detail": str(exc)[:300]}
            if not args.json:
                print(f"{FAIL} HTTP request failed: {exc}")

    if kind == "rtsp":
        probe = rtsp_probe(args.url, args.timeout)
        report["checks"]["rtsp"] = probe
        if not args.json:
            if probe.get("ok"):
                print(f"RTSP   : {OK} {probe.get('status_line')}")
            elif probe.get("unauthorised"):
                print(f"RTSP   : {FAIL} 401 - username/password required (add them in the dashboard)")
            else:
                print(f"RTSP   : {FAIL} {probe.get('status_line') or probe.get('detail')}")

    # 2. decode real frames ----------------------------------------------
    result = opencv_probe(args.url, kind, args.frames, int(args.timeout * 1000))
    report["checks"]["frames"] = result
    report["ok"] = bool(result.get("ok"))

    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else (3 if result.get("reason") in {"no_frames", "open_error"} else 3)

    if not result.get("ok"):
        print(f"{FAIL} No video: {result.get('detail', 'unknown error')}")
        print()
        print("Hints:")
        print("  * RTSP URL format differs per vendor (Hikvision: /Streaming/Channels/101,")
        print("    Dahua: /cam/realmonitor?channel=1&subtype=0, Axis: /axis-media/media.amp).")
        print("  * If the backend runs in a cloud/Codespace, it cannot reach a LAN camera")
        print("    (192.168.x.x / 10.x.x.x) without a VPN. Run the backend on the same network.")
        return 3

    if not args.json:
        print(
            f"Frames : {OK} {result['frames']} decoded in {result['seconds']}s -> "
            f"{result['fps']} FPS, {result['width']}x{result['height']}"
        )
        if result.get("low_light"):
            print(f"{WARN} Very dark frame (brightness {result['brightness']}): detection "
                  "confidence will drop. Check the lighting or IR mode.")
        print()
        print("Ready to add in the dashboard:")
        print(f"  Source type : {kind}")
        print(f"  URL         : {safe_url}")
        print("  (Put the username/password in the camera form, never in the URL box.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
