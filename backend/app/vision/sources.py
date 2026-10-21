"""Video ingestion: RTSP/HTTP CCTV streams, local files, webcams, simulated demo.

The reader is intentionally defensive: CCTV cameras drop connections, sleep,
or return corrupt frames. ``OpenCVSource`` reconnects with exponential backoff
and reports state so the worker/API/UI can show an honest connection status.
"""

from __future__ import annotations

import logging
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

log = logging.getLogger(__name__)

# Prefer TCP for RTSP: fewer artefacts than UDP, and timeouts surface properly.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;8000000|fflags;nobuffer|flags;low_delay|max_delay;500000",
)


class SourceError(RuntimeError):
    pass


class FrameSource:
    """Base class for frame producers."""

    kind = "base"

    def __init__(self, url: str, width: int = 1280, height: int = 720) -> None:
        self.url = url
        self.width = width
        self.height = height
        self.reconnects = 0
        self.frames_read = 0
        self.last_error = ""
        self.started_at = 0.0

    def open(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def read(self) -> np.ndarray | None:  # pragma: no cover - abstract
        raise NotImplementedError

    def release(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def info(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "width": self.width,
            "height": self.height,
            "frames_read": self.frames_read,
            "reconnects": self.reconnects,
            "last_error": self.last_error,
        }


@dataclass
class RetryPolicy:
    max_attempts: int = 0  # 0 = unlimited
    base_delay_s: float = 1.0
    max_delay_s: float = 20.0
    read_timeout_s: float = 8.0


class OpenCVSource(FrameSource):
    """cv2.VideoCapture wrapper with reconnection, pacing and timeouts."""

    def __init__(
        self,
        url: str,
        kind: str = "rtsp",
        width: int = 1280,
        height: int = 720,
        fps: float = 15.0,
        loop: bool = True,
        policy: RetryPolicy | None = None,
        pace: bool = True,
    ) -> None:
        super().__init__(url, width, height)
        self.kind = kind
        # ``pace=False`` reads as fast as possible (benchmarking / offline analysis).
        self.pace = pace
        self.target_fps = max(0.5, fps)
        self.loop = loop
        self.policy = policy or RetryPolicy()
        self.cap: cv2.VideoCapture | None = None
        self._stop = threading.Event()
        self._next_frame_at = 0.0
        self._failing = False
        self._attempts = 0

    # -- capture helpers ---------------------------------------------------
    def _capture_target(self) -> tuple[str | int, int]:
        if self.kind == "webcam":
            try:
                return int(self.url), cv2.CAP_ANY
            except ValueError:
                return self.url, cv2.CAP_ANY
        if self.kind in ("rtsp", "http"):
            return self.url, cv2.CAP_FFMPEG
        return self.url, cv2.CAP_ANY

    def open(self) -> None:
        target, api = self._capture_target()
        self._stop.clear()
        cap = cv2.VideoCapture(target, api)
        if self.kind in ("rtsp", "http"):
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:  # noqa: BLE001
                pass
        if not cap.isOpened() or not cap.grab():
            cap.release()
            raise SourceError(f"Unable to open stream ({self.kind})")
        native_fps = cap.get(cv2.CAP_PROP_FPS) or 0
        if 1 <= native_fps <= 120:
            self.target_fps = native_fps if self.kind in ("file",) else min(self.target_fps, native_fps)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if w and h:
            self.width, self.height = w, h
        self.cap = cap
        self.started_at = time.time()
        self._next_frame_at = time.monotonic()
        log.info("Source opened kind=%s resolution=%dx%d fps=%.1f", self.kind, self.width, self.height, self.target_fps)

    def read(self) -> np.ndarray | None:
        if self.cap is None:
            self._reconnect()
            return None
        if self._stop.is_set():
            return None
        # Pace local files to their native FPS so demos behave like live video.
        if self.kind == "file" and self.pace:
            now = time.monotonic()
            wait = self._next_frame_at - now
            if wait > 0:
                time.sleep(min(wait, 0.5))
            self._next_frame_at = max(now, self._next_frame_at) + 1.0 / self.target_fps
        ok, frame = self.cap.read()
        if not ok or frame is None:
            self.last_error = "Frame grab failed (stream ended or interrupted)"
            if self.kind == "file" and self.loop:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self.cap.read()
                if ok and frame is not None:
                    self.frames_read += 1
                    return frame
            self._reconnect()
            return None
        self.frames_read += 1
        if self._failing:
            self._failing = False
        return frame

    def _reconnect(self) -> None:
        self._failing = True
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        if self._stop.is_set():
            return
        policy = self.policy
        self._attempts += 1
        if policy.max_attempts and self._attempts > policy.max_attempts:
            raise SourceError(f"Stream unavailable after {policy.max_attempts} reconnect attempts")
        delay = min(policy.max_delay_s, policy.base_delay_s * (2 ** (self._attempts - 1)))
        log.warning("Source read failed, reconnecting in %.1fs (attempt %d)", delay, self._attempts)
        if self._stop.wait(delay):
            return
        try:
            self.open()
            self.reconnects += 1
            self._attempts = 0
            self.last_error = ""
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self._reconnect()

    def release(self) -> None:
        self._stop.set()
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:  # noqa: BLE001
                pass
            self.cap = None

    def info(self) -> dict[str, Any]:
        base = super().info()
        base.update({"fps_target": round(self.target_fps, 2)})
        return base


# ---------------------------------------------------------------------------
# Simulated classroom (explicitly a demo, never presented as a real detection)
# ---------------------------------------------------------------------------


@dataclass
class SimStudent:
    x: float
    y: float
    w: float = 46.0
    h: float = 92.0
    has_phone: bool = False
    phone_progress: float = 0.0  # 0 = pocket, 1 = raised in front of face
    tint: tuple[int, int, int] = (90, 120, 200)
    vx: float = 0.0
    present: bool = True


@dataclass
class SimScene:
    students: list[SimStudent] = field(default_factory=list)
    width: int = 960
    height: int = 540
    frame_index: int = 0

    def ground_truth(self, show_phones: bool) -> tuple[list[tuple[list[float], float]], list[tuple[list[float], float]]]:
        people: list[tuple[list[float], float]] = []
        phones: list[tuple[list[float], float]] = []
        for s in self.students:
            if not s.present:
                continue
            people.append([[s.x, s.y, s.x + s.w, s.y + s.h], 0.93])
            if show_phones and s.has_phone and s.phone_progress > 0.15:
                px, py = self.phone_box(s)
                phones.append([[px, py, px + 16, py + 26], 0.88])
        return people, phones

    @staticmethod
    def phone_box(s: SimStudent) -> tuple[float, float]:
        # Raise the phone from desk level to face level as progress increases.
        face_x = s.x + s.w * 0.62
        face_y = s.y + s.h * 0.18
        desk_y = s.y + s.h * 0.68
        py = desk_y + (face_y - desk_y) * s.phone_progress
        return face_x, py


class SimulatedSource(FrameSource):
    """Renders a synthetic classroom and publishes ground-truth detections.

    This exists so the dashboard, alerting and TTS can be demonstrated without
    CCTV hardware. Every event it produces is tagged ``source=simulated`` and
    the UI watermarks the feed as SIMULATED.
    """

    kind = "simulated"

    def __init__(
        self,
        url: str = "sim://classroom",
        width: int = 960,
        height: int = 540,
        fps: float = 12.0,
        pace: bool = True,
    ) -> None:
        super().__init__(url, width, height)
        self.target_fps = max(1.0, fps)
        self.pace = pace
        self.scene = SimScene(width=width, height=height)
        self._stop = threading.Event()
        self._rng = random.Random(7)
        self._next_frame_at = 0.0
        self._build()

    def _build(self) -> None:
        rng = random.Random(11)
        cols, rows = 5, 4
        idx = 0
        for r in range(rows):
            for c in range(cols):
                if idx >= 16:
                    break
                x = 70 + c * 172 + rng.randint(-8, 8)
                y = 120 + r * 100 + rng.randint(-4, 4)
                tint = (int(70 + 40 * rng.random()), int(90 + 60 * rng.random()), int(120 + 80 * rng.random()))
                self.scene.students.append(
                    SimStudent(x=x, y=y, has_phone=rng.random() < 0.16, tint=tint, vx=rng.uniform(-0.15, 0.15))
                )
                idx += 1

    def open(self) -> None:
        self._stop.clear()
        self.started_at = time.time()
        self._next_frame_at = time.monotonic()

    def read(self) -> np.ndarray | None:
        if self._stop.is_set():
            return None
        now = time.monotonic()
        if self.pace:
            wait = self._next_frame_at - now
            if wait > 0:
                time.sleep(min(wait, 0.5))
            self._next_frame_at = max(now, self._next_frame_at) + 1.0 / self.target_fps
        else:
            # Unpaced: advance the simulation by one tick per frame read.
            self._next_frame_at = now
        self._advance()
        self.frames_read += 1
        self.scene.frame_index += 1
        return self._render()

    def _advance(self) -> None:
        rng = self._rng
        for s in self.scene.students:
            s.x += s.vx
            if s.x < 40 or s.x > self.width - 120:
                s.vx *= -1
            if s.has_phone:
                # Phones rise/fall so alerts build up and clear realistically.
                s.phone_progress = min(1.0, max(0.0, s.phone_progress + rng.choice([-0.05, -0.02, 0.0, 0.05, 0.09])))
                if rng.random() < 0.004:
                    s.has_phone = False
                    s.phone_progress = 0.0
            elif rng.random() < 0.003:
                s.has_phone = True
        # Occasionally a student leaves / another arrives (small churn).
        if rng.random() < 0.003:
            present = [s for s in self.scene.students if s.present]
            if len(present) > 4:
                rng.choice(present).present = False
        if rng.random() < 0.004:
            absent = [s for s in self.scene.students if not s.present]
            if absent:
                rng.choice(absent).present = True

    def _render(self) -> np.ndarray:
        w, h = self.width, self.height
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        # Floor + wall gradient (dark classroom look).
        for y in range(h):
            blend = y / max(1, h - 1)
            frame[y, :, :] = (int(38 + 18 * blend), int(42 + 20 * blend), int(50 + 22 * blend))
        cv2.rectangle(frame, (30, 30), (w - 30, 96), (58, 62, 72), -1)
        cv2.putText(frame, "SIMULATED CLASSROOM FEED - NOT REAL CCTV", (46, 74),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.62, (170, 210, 255), 1, cv2.LINE_AA)
        for s in self.scene.students:
            if not s.present:
                continue
            # desk
            cv2.rectangle(frame, (int(s.x - 18), int(s.y + s.h * 0.6)),
                          (int(s.x + s.w + 18), int(s.y + s.h * 0.88)), (72, 58, 44), -1)
            # torso
            cv2.rectangle(frame, (int(s.x), int(s.y + s.h * 0.28)),
                          (int(s.x + s.w), int(s.y + s.h)), s.tint, -1)
            # head
            cv2.circle(frame, (int(s.x + s.w / 2), int(s.y + s.h * 0.16)),
                       int(s.w * 0.30), (150, 168, 190), -1)
            # arms
            cv2.line(frame, (int(s.x + 4), int(s.y + s.h * 0.38)),
                     (int(s.x + s.w * 0.38), int(s.y + s.h * 0.66)), (120, 138, 160), 6)
            cv2.line(frame, (int(s.x + s.w - 4), int(s.y + s.h * 0.38)),
                     (int(s.x + s.w * 0.62), int(s.y + s.h * 0.66)), (120, 138, 160), 6)
            if s.has_phone and s.phone_progress > 0.15:
                px, py = self.scene.phone_box(s)
                cv2.rectangle(frame, (int(px), int(py)), (int(px + 16), int(py + 26)), (30, 200, 240), -1)
        noise = np.random.default_rng(self.scene.frame_index).normal(0, 4, frame.shape)
        frame = np.clip(frame.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        # Vignette-ish lighting variation to exercise low-light handling.
        if self.scene.frame_index % 900 > 800:
            frame = (frame * 0.45).astype(np.uint8)
        return frame

    def release(self) -> None:
        self._stop.set()

    def info(self) -> dict[str, Any]:
        base = super().info()
        base.update({"note": "Simulated feed - detections are synthetic ground truth"})
        return base


def build_source(
    source_type: str,
    url: str,
    fps: float = 15.0,
    loop: bool = True,
    width: int = 1280,
    height: int = 720,
    pace: bool = True,
) -> FrameSource:
    if source_type == "simulated":
        return SimulatedSource(url or "sim://classroom", width=width, height=height, fps=min(fps, 12.0), pace=pace)
    if source_type == "webcam":
        return OpenCVSource(url or "0", kind="webcam", width=width, height=height, fps=fps, pace=pace)
    if source_type == "file":
        path = Path(url)
        if not path.exists():
            raise SourceError(f"Video file not found: {url}")
        return OpenCVSource(str(path), kind="file", width=width, height=height, fps=fps, loop=loop, pace=pace)
    if source_type in ("rtsp", "http"):
        # Live streams are never artificially paced.
        return OpenCVSource(url, kind=source_type, width=width, height=height, fps=fps, pace=pace)
    raise SourceError(f"Unsupported source type: {source_type}")


def probe_stream(url: str, kind: str = "rtsp", timeout_s: float = 6.0) -> dict[str, Any]:
    """One-shot connectivity probe used by the 'test connection' API."""
    started = time.perf_counter()
    source: FrameSource | None = None
    try:
        source = build_source(kind, url, fps=15.0)
        source.open()
        deadline = time.time() + timeout_s
        frame = None
        while time.time() < deadline:
            frame = source.read()
            if frame is not None:
                break
        if frame is None:
            return {"ok": False, "error": f"No frame received within {timeout_s}s"}
        h, w = frame.shape[:2]
        return {
            "ok": True,
            "width": w,
            "height": h,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}
    finally:
        if source is not None:
            source.release()


def estimate_brightness(frame: np.ndarray) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return float(np.mean(gray)) / 255.0


def estimate_sharpness(frame: np.ndarray) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def is_low_light(frame: np.ndarray, threshold: float = 0.22) -> bool:
    return estimate_brightness(frame) < threshold


def clamp_resolution(width: int, height: int) -> tuple[int, int]:
    width = int(max(160, min(3840, width)))
    height = int(max(120, min(2160, height)))
    # keep multiples of 2 for encoder friendliness
    return width - width % 2, height - height % 2


def safe_fps(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        return 1.0
    return min(60.0, value)
