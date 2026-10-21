"""Registry and lifecycle control for camera workers.

Kept in its own module so API routers can import the manager without pulling in
the worker's heavier vision imports (and to avoid import cycles).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from app.core.config import settings
from app.db.models import Camera, CameraStatus
from app.db.session import SessionLocal
from app.services.worker import CameraWorker

log = logging.getLogger(__name__)


class CameraManager:
    """Registry of camera workers with start/stop control and status reporting."""

    def __init__(self) -> None:
        self._workers: dict[int, CameraWorker] = {}
        self._lock = threading.RLock()

    # -- control -----------------------------------------------------------
    def start(self, camera_id: int) -> dict[str, Any]:
        with self._lock:
            worker = self._workers.get(camera_id)
            if worker and worker.alive:
                return worker.status()
            max_workers = int(settings.max_camera_workers)
            if len([w for w in self._workers.values() if w.alive]) >= max_workers:
                raise RuntimeError(f"Maximum concurrent camera workers reached ({max_workers})")
            with SessionLocal() as db:
                camera = db.get(Camera, camera_id)
                if camera is None:
                    raise LookupError("Camera not found")
                if not camera.enabled:
                    raise RuntimeError("Camera is disabled")
                db.expunge(camera)
            worker = CameraWorker(camera)
            self._workers[camera_id] = worker
            worker.start()
            return worker.status()

    def stop(self, camera_id: int) -> dict[str, Any]:
        with self._lock:
            worker = self._workers.get(camera_id)
        if worker:
            worker.stop()
            with self._lock:
                self._workers.pop(camera_id, None)
            return worker.status()
        return {"camera_id": camera_id, "status": CameraStatus.STOPPED.value}

    def stop_all(self) -> None:
        with self._lock:
            workers = list(self._workers.items())
            self._workers.clear()
        for _, worker in workers:
            try:
                worker.stop()
            except Exception:  # noqa: BLE001
                pass

    def restart(self, camera_id: int) -> dict[str, Any]:
        self.stop(camera_id)
        time.sleep(0.2)
        return self.start(camera_id)

    def is_running(self, camera_id: int) -> bool:
        with self._lock:
            worker = self._workers.get(camera_id)
            return bool(worker and worker.alive)

    def running_ids(self) -> list[int]:
        with self._lock:
            return [cid for cid, w in self._workers.items() if w.alive]

    # -- queries -----------------------------------------------------------
    def status(self, camera_id: int) -> dict[str, Any] | None:
        with self._lock:
            worker = self._workers.get(camera_id)
        return worker.status() if worker else None

    def all_status(self) -> dict[int, dict[str, Any]]:
        with self._lock:
            workers = dict(self._workers)
        return {cid: w.status() for cid, w in workers.items()}

    def snapshot(self, camera_id: int) -> bytes:
        with self._lock:
            worker = self._workers.get(camera_id)
        return worker.snapshot() if worker else b""

    def analysis(self, camera_id: int) -> dict[str, Any] | None:
        with self._lock:
            worker = self._workers.get(camera_id)
        return worker.analysis() if worker else None

    def totals(self) -> dict[str, int]:
        statuses = self.all_status().values()
        return {
            "cameras_running": sum(1 for s in statuses if s["status"] == CameraStatus.ONLINE.value),
            "occupancy": sum(int(s["occupancy"]) for s in statuses),
            "active_alerts": sum(int(s["active_alerts"]) for s in statuses),
            "possible_alerts": sum(int(s["possible_alerts"]) for s in statuses),
            "phones_visible": sum(int(s["phones_visible"]) for s in statuses),
        }


camera_manager = CameraManager()
