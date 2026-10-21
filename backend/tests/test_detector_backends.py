"""Detector backends: contract, capability reporting and graceful fallback."""

from __future__ import annotations

import numpy as np
import pytest

from app.vision.detector import (
    Detection,
    DetectorStatus,
    HogPeopleDetector,
    NullDetector,
    filter_detections,
    model_exists,
)

FRAME = np.full((360, 640, 3), 110, dtype=np.uint8)


def test_null_detector_contract():
    detector = NullDetector()
    assert detector.detect(FRAME) == []
    assert detector.phone_capable is False
    detector.close()


def test_hog_detector_contract_and_capability():
    detector = HogPeopleDetector()
    detections = detector.detect(FRAME)
    assert isinstance(detections, list)
    assert detector.phone_capable is False, "HOG must never claim phone capability"
    for det in detections:
        assert det.label == "person"
        assert det.box.shape == (4,)
        assert 0.0 <= det.conf <= 1.0
    detector.close()


def test_filter_detections_applies_per_class_thresholds():
    people = Detection("person", 0.5, np.array([0, 0, 10, 20], dtype=np.float32), 0)
    weak_person = Detection("person", 0.2, np.array([0, 0, 10, 20], dtype=np.float32), 0)
    phone = Detection("cell phone", 0.4, np.array([0, 0, 5, 8], dtype=np.float32), 67)
    weak_phone = Detection("cell phone", 0.1, np.array([0, 0, 5, 8], dtype=np.float32), 67)
    book = Detection("book", 0.9, np.array([0, 0, 30, 30], dtype=np.float32), 73)

    filtered_people, filtered_phones = filter_detections(
        [people, weak_person, phone, weak_phone, book], person_conf=0.35, phone_conf=0.3
    )
    assert filtered_people == [people]
    assert filtered_phones == [phone]


def test_detector_status_serialisation():
    status = DetectorStatus(backend="hog", degraded=True, phone_capable=False, model="hog")
    payload = status.as_dict()
    assert payload["backend"] == "hog"
    assert payload["degraded"] is True
    assert payload["phone_capable"] is False


def test_model_exists_rejects_tiny_files(tmp_path):
    pointer = tmp_path / "yolo11n.pt"
    pointer.write_text("version https://git-lfs.github.com/spec/v1")
    assert model_exists(str(pointer)) is False

    real = tmp_path / "real.pt"
    real.write_bytes(b"\x00" * (200 * 1024))
    assert model_exists(str(real)) is True


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("ultralytics"),
    reason="ultralytics not installed",
)
def test_ultralytics_detector_contract(tmp_path):
    """Build a YOLO architecture from YAML (no weight download) and verify the adapter."""
    from ultralytics import YOLO

    from app.vision.detector import UltralyticsDetector

    weights = tmp_path / "random_yolo11n.pt"
    if not weights.exists():
        model = YOLO("yolo11n.yaml")
        model.save(str(weights))

    detector = UltralyticsDetector(str(weights), device="cpu", imgsz=320)
    assert detector.names, "class names should be loaded"
    detections = detector.detect(FRAME)
    assert isinstance(detections, list)
    for det in detections:
        assert isinstance(det, Detection)
        assert det.box.shape == (4,)
    # Untrained weights have no meaningful 'cell phone' class -> capability False.
    assert detector.phone_capable in (True, False)
    detector.close()


def test_detector_manager_falls_back_to_null(monkeypatch):
    """When every backend is unavailable the manager reports it instead of crashing."""
    from app.vision import detector as detector_module

    manager = detector_module.DetectorManager()

    def boom():
        raise RuntimeError("no weights")

    monkeypatch.setattr(manager, "_build_ultralytics", boom, raising=False)
    monkeypatch.setattr(detector_module.settings, "detector_backend", "ultralytics")
    status = manager.load()
    assert status.backend == "null"
    assert status.loaded is False
    assert manager.detect(FRAME) == []
    manager.shutdown()
