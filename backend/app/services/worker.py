"""Camera worker: one thread per camera running the full real-time pipeline.

The worker owns video ingestion, frame sampling, inference, tracking, temporal
analysis, event persistence, JPEG snapshot generation and telemetry. API
requests never block on it: they read the shared :class:`CameraRuntime` state.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.db.models import Camera, CameraMetric, CameraStatus, EventSource
from app.db.session import SessionLocal
from app.services.alerts import AlertCoordinator
from app.services.broadcast import broadcaster
from app.services.events import EventService
from app.services.settings_store import settings_store
from app.vision.detector import detector_manager
from app.vision.pipeline import AnalyzerConfig, FrameAnalyzer, FrameAnalysis, annotate, encode_jpeg
from app.vision.sources import FrameSource, SourceError, build_source, clamp_resolution

log = logging.getLogger(__name__)


@dataclass
class CameraRuntime:
    camera_id: int
    name: str
    source_type: str
    simulated: bool = False
    status: str = CameraStatus.STOPPED.value
    message: str = ""
    fps: float = 0.0
    inference_ms: float = 0.0
    total_ms: float = 0.0
    occupancy: int = 0
    phones_visible: int = 0
    active_alerts: int = 0
    possible_alerts: int = 0
    uncertain: int = 0
    low_light: bool = False
    frames_processed: int = 0
    frames_decoded: int = 0
    reconnects: int = 0
    last_frame_at: float = 0.0
    last_error: str = ""
    started_at: float = 0.0
    model_backend: str = ""
    phone_capable: bool = True
    tracks: list[dict[str, Any]] = field(default_factory=list)
    frame_size: tuple[int, int] = (0, 0)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "name": self.name,
            "source_type": self.source_type,
            "simulated": self.simulated,
            "status": self.status,
            "message": self.message,
            "fps": round(self.fps, 2),
            "inference_ms": round(self.inference_ms, 2),
            "total_ms": round(self.total_ms, 2),
            "occupancy": self.occupancy,
            "phones_visible": self.phones_visible,
            "active_alerts": self.active_alerts,
            "possible_alerts": self.possible_alerts,
            "uncertain": self.uncertain,
            "low_light": self.low_light,
            "frames_processed": self.frames_processed,
            "frames_decoded": self.frames_decoded,
            "reconnects": self.reconnects,
            "last_frame_at": self.last_frame_at,
            "last_error": self.last_error,
            "started_at": self.started_at,
            "model_backend": self.model_backend,
            "phone_capable": self.phone_capable,
            "frame_size": list(self.frame_size),
            "notes": self.notes,
            "tracks": self.tracks,
            "stale": (time.time() - self.last_frame_at) > 15 if self.last_frame_at else False,
        }


class CameraWorker:
    def __init__(self, camera: Camera) -> None:
        self.camera_id = camera.id
        self.camera_name = camera.name
        self.source_type = camera.source_type
        self.simulated = camera.source_type == "simulated"
        self.config = camera
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._jpeg: bytes = b""
        self._jpeg_at = 0.0
        self._analysis: FrameAnalysis | None = None
        self.runtime = CameraRuntime(
            camera_id=camera.id,
            name=camera.name,
            source_type=camera.source_type,
            simulated=self.simulated,
        )
        self._fps_ema = 0.0
        self._infer_ema = 0.0
        self._last_processed = 0.0
        self._last_metric_write = 0.0
        self._last_broadcast = 0.0
        self._event_service = EventService(
            camera_id=camera.id,
            source=EventSource.SIMULATED.value if self.simulated else EventSource.MODEL.value,
        )
        self._alerts = AlertCoordinator(camera.id)

    # -- public ------------------------------------------------------------
    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def start(self) -> None:
        if self.alive:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"camera-{self.camera_id}", daemon=True)
        self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=join_timeout)
        self._event_service.close_all()
        self._set_status(CameraStatus.STOPPED.value, "Stopped by operator")

    def snapshot(self) -> bytes:
        with self._lock:
            return self._jpeg

    def analysis(self) -> dict[str, Any] | None:
        with self._lock:
            if self._analysis is None:
                return None
            return self._analysis.to_dict({"camera_id": self.camera_id})

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self.runtime.as_dict()

    def _set_status(self, status: str, message: str = "") -> None:
        with self._lock:
            changed = self.runtime.status != status
            self.runtime.status = status
            if message:
                self.runtime.message = message
        if changed or message:
            broadcaster.publish_threadsafe(
                {
                    "topic": "camera",
                    "type": "camera_status",
                    "camera_id": self.camera_id,
                    "camera_name": self.camera_name,
                    "status": status,
                    "message": message,
                    "simulated": self.simulated,
                    "timestamp": time.time(),
                }
            )

    # -- main loop ---------------------------------------------------------
    def _build_source(self) -> FrameSource:
        cam = self.config
        try:
            url = decrypt_secret(cam.url_encrypted)
        except ValueError as exc:
            raise SourceError(f"Stored stream credential cannot be decrypted: {exc}") from exc
        if not url and cam.source_type != "simulated":
            raise SourceError("Camera has no stream URL configured")
        return build_source(
            cam.source_type,
            url,
            fps=15.0,
            loop=True,
            width=cam.inference_width,
            height=cam.inference_height,
        )

    def _analyzer(self) -> FrameAnalyzer:
        cam = self.config
        cfg = AnalyzerConfig(
            person_conf=float(settings_store.get("person_conf", cam.detect_conf)),
            phone_conf=float(settings_store.get("phone_conf", cam.phone_conf)),
            persistence_threshold=float(cam.persistence_threshold),
            phone_detection_enabled=bool(settings_store.get("phone_detection_enabled", True))
            and bool(cam.phone_detection_enabled),
            max_visible_people=int(settings_store.get("max_visible_people", settings.max_visible_people)),
            window_s=float(settings_store.get("analysis_window_seconds", 4.0)),
            min_sustained_s=float(settings_store.get("min_sustained_seconds", 2.0)),
            tracker_type=settings.tracker_type,
            tracker_max_age=settings.tracker_max_age,
            tracker_min_hits=settings.tracker_min_hits,
            tracker_iou_threshold=settings.tracker_iou_threshold,
        )
        return FrameAnalyzer(cfg, simulated=self.simulated)

    def _run(self) -> None:
        self._set_status(CameraStatus.CONNECTING.value, "Opening stream")
        source: FrameSource | None = None
        try:
            source = self._build_source()
            source.open()
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)[:300]
            self.runtime.last_error = msg
            self._set_status(CameraStatus.ERROR.value, msg)
            log.error("Camera %s failed to open: %s", self.camera_name, msg)
            if source:
                source.release()
            return

        analyzer = self._analyzer()
        detector_status = detector_manager.load()
        if not self.simulated:
            with self._lock:
                self.runtime.model_backend = detector_status.backend
                self.runtime.phone_capable = detector_status.phone_capable
            if detector_status.degraded:
                self._set_status(
                    CameraStatus.ONLINE.value,
                    "Running in degraded mode: " + (detector_status.extra.get("degraded_reason") or "no phone detection"),
                )
            else:
                self._set_status(CameraStatus.ONLINE.value, "Live")
        else:
            with self._lock:
                self.runtime.model_backend = "simulated"
            self._set_status(CameraStatus.ONLINE.value, "Simulated feed (no real camera)")

        started = time.time()
        with self._lock:
            self.runtime.started_at = started
        sample = max(1, int(self.config.frame_sample))
        frame_index = 0
        consecutive_errors = 0
        try:
            while not self._stop.is_set():
                try:
                    frame = source.read()
                except SourceError as exc:
                    self._set_status(CameraStatus.ERROR.value, str(exc)[:300])
                    break
                except Exception as exc:  # noqa: BLE001
                    consecutive_errors += 1
                    self.runtime.last_error = str(exc)[:300]
                    if consecutive_errors > 50:
                        self._set_status(CameraStatus.ERROR.value, "Repeated stream errors")
                        break
                    time.sleep(0.2)
                    continue
                if frame is None:
                    self._set_status(CameraStatus.RECONNECTING.value, "Stream interrupted - reconnecting")
                    with self._lock:
                        self.runtime.reconnects = source.reconnects
                    time.sleep(0.05)
                    continue
                consecutive_errors = 0
                frame_index += 1
                with self._lock:
                    self.runtime.frames_decoded += 1
                    self.runtime.reconnects = source.reconnects
                if frame_index % sample != 0:
                    continue
                if not settings_store.get("detection_enabled", True):
                    # Monitoring paused by an operator: keep the preview alive.
                    self._publish_idle_frame(frame)
                    continue
                try:
                    self._process(frame, analyzer, source)
                except Exception as exc:  # noqa: BLE001 - keep the worker alive
                    consecutive_errors += 1
                    self.runtime.last_error = f"{type(exc).__name__}: {exc}"[:300]
                    log.exception("Frame processing error on %s", self.camera_name)
                    if consecutive_errors > 20:
                        self._set_status(CameraStatus.ERROR.value, "Repeated processing errors - worker stopped")
                        break
                    time.sleep(0.1)
        finally:
            try:
                self._event_service.close_all()
            finally:
                source.release()
                self._set_status(CameraStatus.STOPPED.value, "Worker stopped")
                log.info("Camera worker stopped: %s", self.camera_name)

    def _publish_idle_frame(self, frame: np.ndarray) -> None:
        annotated = annotate(frame, FrameAnalysis(timestamp=time.time(), frame_index=0,
                                                  frame_w=frame.shape[1], frame_h=frame.shape[0],
                                                  simulated=self.simulated,
                                                  notes=["Detection paused by operator"]))
        jpeg = encode_jpeg(annotated, settings.snapshot_quality)
        with self._lock:
            self._jpeg = jpeg
            self._jpeg_at = time.time()

    def _process(self, frame: np.ndarray, analyzer: FrameAnalyzer, source: FrameSource) -> None:
        cam = self.config
        h, w = frame.shape[:2]
        target_w, target_h = clamp_resolution(cam.inference_width, cam.inference_height)
        work = frame
        if (w, h) != (target_w, target_h):
            work = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)

        scene = getattr(source, "scene", None) if self.simulated else None
        analysis = analyzer.analyze(work, scene=scene)

        # Low light: lift shadows for the detector on the *next* frame is not
        # possible retroactively, so we simply flag it and record the note.
        with self._lock:
            self._analysis = analysis
            self.runtime.occupancy = analysis.occupancy
            self.runtime.phones_visible = analysis.phone_counts
            self.runtime.active_alerts = len(analysis.alert_tracks)
            self.runtime.possible_alerts = len(analysis.possible_tracks)
            self.runtime.uncertain = len(analysis.uncertain_tracks)
            self.runtime.low_light = analysis.low_light
            self.runtime.inference_ms = analysis.inference_ms
            self.runtime.total_ms = analysis.total_ms
            self.runtime.frames_processed += 1
            self.runtime.last_frame_at = analysis.timestamp
            self.runtime.frame_size = (analysis.frame_w, analysis.frame_h)
            self.runtime.notes = analysis.notes
            self.runtime.tracks = [t.to_dict(analysis.frame_w, analysis.frame_h) for t in analysis.tracks]
            self.runtime.model_backend = analysis.model_backend
            self.runtime.phone_capable = analysis.phone_capable

        # FPS / latency smoothing.
        now = time.monotonic()
        if self._last_processed:
            delta = now - self._last_processed
            if delta > 0:
                inst = 1.0 / delta
                self._fps_ema = inst if self._fps_ema == 0 else 0.85 * self._fps_ema + 0.15 * inst
        self._last_processed = now
        self._infer_ema = analysis.inference_ms if self._infer_ema == 0 else 0.85 * self._infer_ema + 0.15 * analysis.inference_ms
        with self._lock:
            self.runtime.fps = self._fps_ema
            self.runtime.inference_ms = self._infer_ema

        # Render + store the annotated snapshot for the MJPEG endpoint.
        annotated = annotate(work, analysis)
        jpeg = encode_jpeg(annotated, settings.snapshot_quality)
        with self._lock:
            self._jpeg = jpeg
            self._jpeg_at = analysis.timestamp

        # Event persistence + alerts.
        created = self._event_service.sync(analysis)
        self._alerts.configure()
        self._alerts.handle(self.camera_name, created, self._event_service, simulated=self.simulated)

        # Telemetry + realtime broadcast (throttled to snapshot FPS).
        if analysis.timestamp - self._last_broadcast >= 1.0 / max(1.0, float(settings_store.get("snapshot_fps", 8.0))):
            self._last_broadcast = analysis.timestamp
            payload = analysis.to_dict({"camera_id": self.camera_id, "camera_name": self.camera_name})
            payload["topic"] = "frame"
            payload["type"] = "frame_update"
            broadcaster.publish_threadsafe(payload)
        self._maybe_write_metrics(analysis)

    def _maybe_write_metrics(self, analysis: FrameAnalysis) -> None:
        now = time.time()
        if now - self._last_metric_write < 10:
            return
        self._last_metric_write = now
        try:
            import psutil

            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.Process().memory_info().rss / (1024 * 1024)
        except Exception:  # noqa: BLE001
            cpu, mem = 0.0, 0.0
        try:
            with SessionLocal() as db:
                db.add(
                    CameraMetric(
                        camera_id=self.camera_id,
                        fps=round(self._fps_ema, 2),
                        inference_ms=round(self._infer_ema, 2),
                        occupancy=analysis.occupancy,
                        active_alerts=len(analysis.alert_tracks),
                        cpu_percent=round(cpu, 2),
                        memory_mb=round(mem, 1),
                        status=CameraStatus.ONLINE.value,
                    )
                )
                db.commit()
        except Exception as exc:  # noqa: BLE001
            log.debug("Metric write failed: %s", exc)
