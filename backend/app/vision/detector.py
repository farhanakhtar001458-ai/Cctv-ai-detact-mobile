"""Object detection backends.

The pipeline is deliberately backend-agnostic. Four implementations ship:

``ultralytics``  YOLO11/YOLOv8 via Ultralytics (``.pt``, ``.onnx``, ``.engine``).
                 Default; auto-downloads official weights on first run.
``onnx``         ONNX Runtime with an exported YOLO model (no torch needed).
``hog``          OpenCV Histogram-of-Oriented-Gradients people detector.
                 Fully offline *degraded* fallback: people only, no phones.
``null``         No model (used by tests and when loading fails).

The manager exposes an explicit ``status`` object that the API and dashboard
surface, so the UI can state plainly whether detections are produced by a real
model, a degraded classical detector, or not at all.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

import cv2
import numpy as np

from app.core.config import settings
from app.vision.geometry import BoxArray

log = logging.getLogger(__name__)

PERSON_LABEL = "person"
PHONE_LABEL = "cell phone"
# COCO classes worth keeping for classroom context.
CONTEXT_CLASSES = {
    "book",
    "laptop",
    "bottle",
    "cup",
    "backpack",
    "chair",
    "tv",
    "remote",
    "keyboard",
}


@dataclass
class Detection:
    label: str
    conf: float
    box: BoxArray
    class_id: int = -1

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "conf": round(float(self.conf), 3),
            "box": [round(float(v), 1) for v in self.box],
        }


@dataclass
class DetectorStatus:
    backend: str = "none"
    model: str = ""
    loaded: bool = False
    degraded: bool = False
    phone_capable: bool = False
    device: str = "cpu"
    error: str = ""
    warmup_ms: float = 0.0
    loaded_at: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "model": self.model,
            "loaded": self.loaded,
            "degraded": self.degraded,
            "phone_capable": self.phone_capable,
            "device": self.device,
            "error": self.error,
            "warmup_ms": round(self.warmup_ms, 2),
            "loaded_at": self.loaded_at,
            **self.extra,
        }


class Detector(Protocol):
    name: str

    def detect(self, frame: np.ndarray) -> list[Detection]: ...

    def close(self) -> None: ...


def _letterbox(image: np.ndarray, new_shape: tuple[int, int] = (640, 640)) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Resize with aspect-ratio preservation; returns image, ratio, (pad_w, pad_h)."""
    h, w = image.shape[:2]
    new_h, new_w = new_shape
    ratio = min(new_h / h, new_w / w)
    resized = cv2.resize(image, (int(round(w * ratio)), int(round(h * ratio))), interpolation=cv2.INTER_LINEAR)
    dw = new_w - resized.shape[1]
    dh = new_h - resized.shape[0]
    top, bottom = dh // 2, dh - dh // 2
    left, right = dw // 2, dw - dw // 2
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
    return padded, ratio, (left, top)


class UltralyticsDetector:
    name = "ultralytics"

    def __init__(
        self,
        model_path: str,
        device: str = "auto",
        imgsz: int = 640,
        half: bool = False,
        conf: float = 0.25,
    ) -> None:
        from ultralytics import YOLO  # imported lazily; torch is optional at boot

        self.model_path = model_path
        self.imgsz = imgsz
        self.half = half
        self._conf = conf
        self.model = YOLO(model_path)
        self.device = self._resolve_device(device)
        self.names: dict[int, str] = dict(self.model.names)
        try:  # move model once so per-frame inference does not pay load cost
            self.model.to(self.device)
        except Exception as exc:  # noqa: BLE001 - non fatal
            log.warning("Could not move model to %s (%s); using default device", self.device, exc)
        self.phone_capable = any(str(n).lower() == PHONE_LABEL for n in self.names.values())
        if not self.phone_capable:
            log.warning(
                "Model %s has no '%s' class (classes: %s) - phone-use detection will be disabled",
                model_path, PHONE_LABEL, sorted({str(n) for n in self.names.values()})[:12],
            )

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device and device != "auto":
            return device
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda:0"
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                return "mps"
        except Exception:  # noqa: BLE001
            pass
        return "cpu"

    def detect(self, frame: np.ndarray) -> list[Detection]:
        kwargs: dict[str, Any] = {
            "imgsz": self.imgsz,
            "conf": self._conf,
            "device": self.device,
            "verbose": False,
        }
        if self.half and self.device.startswith("cuda"):
            kwargs["half"] = True  # only meaningful on CUDA; avoids deprecation noise
        results = self.model.predict(frame, **kwargs)
        out: list[Detection] = []
        if not results:
            return out
        r = results[0]
        if r.boxes is None:
            return out
        boxes = r.boxes.xyxy.cpu().numpy()
        confs = r.boxes.conf.cpu().numpy()
        classes = r.boxes.cls.cpu().numpy().astype(int)
        for box, conf, cls in zip(boxes, confs, classes):
            label = str(self.names.get(int(cls), str(cls)))
            out.append(Detection(label=label, conf=float(conf), box=box.astype(np.float32), class_id=int(cls)))
        return out

    def close(self) -> None:  # pragma: no cover - gc handles it
        self.model = None


class OnnxYoloDetector:
    """YOLOv8/v11 ONNX inference (single output tensor, no NMS nodes assumed)."""

    name = "onnx"

    def __init__(self, model_path: str, imgsz: int = 640, conf: float = 0.25, iou: float = 0.45) -> None:
        import onnxruntime as ort

        self.model_path = model_path
        self.imgsz = imgsz
        self.conf = conf
        self.iou_thr = iou
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        available = ort.get_available_providers()
        self.providers = [p for p in providers if p in available] or ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(model_path, providers=self.providers)
        self.input_name = self.session.get_inputs()[0].name
        self.names = _default_coco_names()
        self.phone_capable = PHONE_LABEL in self.names.values()

    def detect(self, frame: np.ndarray) -> list[Detection]:
        img, ratio, (dw, dh) = _letterbox(frame, (self.imgsz, self.imgsz))
        blob = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))[None]
        preds = self.session.run(None, {self.input_name: blob})[0]
        preds = np.squeeze(preds)
        if preds.ndim != 2:
            return []
        if preds.shape[0] < preds.shape[1]:  # (84, N) -> (N, 84)
            preds = preds.T
        boxes_xywh = preds[:, :4]
        scores_all = preds[:, 4:]
        class_ids = scores_all.argmax(axis=1)
        scores = scores_all[np.arange(len(scores_all)), class_ids]
        keep = scores >= self.conf
        if not keep.any():
            return []
        xywh = boxes_xywh[keep]
        scores = scores[keep]
        class_ids = class_ids[keep]
        boxes = np.zeros_like(xywh)
        boxes[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
        boxes[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
        boxes[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
        boxes[:, 3] = xywh[:, 1] + xywh[:, 3] / 2
        boxes[:, [0, 2]] = (boxes[:, [0, 2]] - dw) / ratio
        boxes[:, [1, 3]] = (boxes[:, [1, 3]] - dh) / ratio
        xywh_nms = np.hstack([boxes[:, :2], boxes[:, 2:] - boxes[:, :2]])
        idxs = cv2.dnn.NMSBoxes(
            xywh_nms.tolist(), scores.tolist(), self.conf, self.iou_thr
        )
        out: list[Detection] = []
        if len(idxs) == 0:
            return out
        for i in np.array(idxs).flatten():
            cid = int(class_ids[i])
            out.append(
                Detection(
                    label=self.names.get(cid, str(cid)),
                    conf=float(scores[i]),
                    box=boxes[i].astype(np.float32),
                    class_id=cid,
                )
            )
        return out

    def close(self) -> None:  # pragma: no cover
        self.session = None


def _default_coco_names() -> dict[int, str]:
    return {
        0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 4: "airplane", 5: "bus", 6: "train",
        7: "truck", 8: "boat", 9: "traffic light", 10: "fire hydrant", 11: "stop sign",
        12: "parking meter", 13: "bench", 14: "bird", 15: "cat", 16: "dog", 17: "horse",
        18: "sheep", 19: "cow", 20: "elephant", 21: "bear", 22: "zebra", 23: "giraffe",
        24: "backpack", 25: "umbrella", 26: "handbag", 27: "tie", 28: "suitcase",
        29: "frisbee", 30: "skis", 31: "snowboard", 32: "sports ball", 33: "kite",
        34: "baseball bat", 35: "baseball glove", 36: "skateboard", 37: "surfboard",
        38: "tennis racket", 39: "bottle", 40: "wine glass", 41: "cup", 42: "fork",
        43: "knife", 44: "spoon", 45: "bowl", 46: "banana", 47: "apple", 48: "sandwich",
        49: "orange", 50: "broccoli", 51: "carrot", 52: "hot dog", 53: "pizza", 54: "donut",
        55: "cake", 56: "chair", 57: "couch", 58: "potted plant", 59: "bed",
        60: "dining table", 61: "toilet", 62: "tv", 63: "laptop", 64: "mouse",
        65: "remote", 66: "keyboard", 67: "cell phone", 68: "microwave", 69: "oven",
        70: "toaster", 71: "sink", 72: "refrigerator", 73: "book", 74: "clock",
        75: "vase", 76: "scissors", 77: "teddy bear", 78: "hair drier", 79: "toothbrush",
    }


class HogPeopleDetector:
    """Offline, classical people detector used only when no YOLO model is present.

    Limitations are surfaced through :class:`DetectorStatus` (``degraded=True``,
    ``phone_capable=False``): HOG detects standing/walking pedestrians, misses
    most seated students and many occluded people, and cannot detect phones.
    """

    name = "hog"

    def __init__(self, confidence: float = 0.5) -> None:
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self.confidence = confidence
        self.phone_capable = False

    def detect(self, frame: np.ndarray) -> list[Detection]:
        # HOG is slow on large frames; cap the working width.
        h, w = frame.shape[:2]
        scale = 1.0
        work = frame
        if w > 960:
            scale = 960.0 / w
            work = cv2.resize(frame, (960, int(h * scale)))
        rects, weights = self.hog.detectMultiScale(
            work, winStride=(8, 8), padding=(8, 8), scale=1.05
        )
        out: list[Detection] = []
        for (x, y, bw, bh), weight in zip(rects, np.ravel(weights)):
            # Map the raw SVM decision value into a 0..1-ish confidence.
            conf = float(1.0 / (1.0 + np.exp(-float(weight) * 3.0)))
            if conf < self.confidence:
                continue
            box = np.array([x, y, x + bw, y + bh], dtype=np.float32) / scale
            out.append(Detection(PERSON_LABEL, conf, box, 0))
        return out

    def close(self) -> None:  # pragma: no cover
        pass


class NullDetector:
    name = "null"
    phone_capable = False

    def detect(self, frame: np.ndarray) -> list[Detection]:
        return []

    def close(self) -> None:
        pass


class DetectorManager:
    """Owns the process-wide detector instance and exposes a health status."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._infer_lock = threading.Lock()
        self._detector: Detector | None = None
        self.status = DetectorStatus()
        self._init_error = ""

    # -- lifecycle --------------------------------------------------------
    def load(self, force: bool = False) -> DetectorStatus:
        with self._lock:
            if self._detector is not None and not force:
                return self.status
            if self._detector is not None and force:
                try:
                    self._detector.close()
                except Exception:  # noqa: BLE001
                    pass
                self._detector = None
            status = DetectorStatus()
            backend = settings.detector_backend
            candidates: list[tuple[str, Any]] = []

            if backend in ("auto", "ultralytics"):
                candidates.append(("ultralytics", lambda: self._build_ultralytics()))
            if backend in ("auto", "onnx") and (settings.onnx_model_path or settings.model_path.endswith(".onnx")):
                candidates.append(("onnx", lambda: self._build_onnx()))
            if backend == "auto":
                candidates.append(("hog", lambda: self._build_hog()))

            started = time.perf_counter()
            for name, factory in candidates:
                try:
                    detector = factory()
                    detector.detect(np.zeros((480, 640, 3), dtype=np.uint8))  # warm-up
                    warm = (time.perf_counter() - started) * 1000
                    self._detector = detector
                    status.backend = detector.name
                    status.model = str(getattr(detector, "model_path", "opencv-hog"))
                    status.loaded = True
                    status.degraded = detector.name == "hog"
                    status.phone_capable = bool(getattr(detector, "phone_capable", False))
                    status.device = str(getattr(detector, "device", "cpu"))
                    status.warmup_ms = warm
                    status.loaded_at = time.time()
                    status.extra = {
                        "degraded_reason": (
                            "YOLO weights unavailable - using OpenCV HOG fallback: people only, "
                            "no mobile-phone detection"
                        )
                        if status.degraded
                        else "",
                        "classes": len(getattr(detector, "names", {})),
                    }
                    self.status = status
                    log.info(
                        "Detector ready backend=%s model=%s device=%s phone_capable=%s warmup=%.0fms",
                        status.backend, status.model, status.device, status.phone_capable, warm,
                    )
                    return status
                except Exception as exc:  # noqa: BLE001 - try the next backend
                    self._init_error = f"{name}: {exc}"
                    log.warning("Detector backend '%s' unavailable: %s", name, exc)

            self._detector = NullDetector()
            self.status = DetectorStatus(
                backend="null",
                loaded=False,
                degraded=True,
                error=self._init_error or "No detector backend available",
                extra={"degraded_reason": "No model loaded - detections disabled"},
            )
            log.error("No detector available: %s", self.status.error)
            return self.status

    def _build_ultralytics(self) -> Detector:
        if not settings.allow_model_autodownload and not model_exists(settings.model_path):
            raise FileNotFoundError(
                f"{settings.model_path} is not present and model auto-download is disabled"
            )
        return UltralyticsDetector(
            model_path=settings.model_path,
            device=settings.model_device,
            imgsz=settings.model_imgsz,
            half=settings.model_half,
            conf=min(settings.detect_conf_default, settings.phone_conf_default),
        )

    def _build_onnx(self) -> Detector:
        path = settings.onnx_model_path or settings.model_path
        return OnnxYoloDetector(path, imgsz=settings.model_imgsz, conf=settings.phone_conf_default)

    def _build_hog(self) -> Detector:
        return HogPeopleDetector()

    # -- inference --------------------------------------------------------
    @property
    def detector(self) -> Detector:
        if self._detector is None:
            self.load()
        assert self._detector is not None
        return self._detector

    @property
    def available(self) -> bool:
        return self._detector is not None and not isinstance(self._detector, NullDetector)

    @property
    def phone_capable(self) -> bool:
        return bool(getattr(self._detector, "phone_capable", False)) if self._detector else False

    def detect(self, frame: np.ndarray) -> list[Detection]:
        det = self.detector
        with self._infer_lock:  # Ultralytics models are not re-entrant
            return det.detect(frame)

    def shutdown(self) -> None:
        with self._lock:
            if self._detector is not None:
                try:
                    self._detector.close()
                except Exception:  # noqa: BLE001
                    pass
            self._detector = None


detector_manager = DetectorManager()


def filter_detections(
    detections: Sequence[Detection],
    person_conf: float,
    phone_conf: float,
    keep_context: bool = False,
) -> tuple[list[Detection], list[Detection]]:
    """Split raw detections into (people, phones) using per-class thresholds."""
    people: list[Detection] = []
    phones: list[Detection] = []
    for det in detections:
        label = det.label.lower()
        if label == PERSON_LABEL and det.conf >= person_conf:
            people.append(det)
        elif label == PHONE_LABEL and det.conf >= phone_conf:
            phones.append(det)
    return people, phones


def describe_unavailable(path: str) -> str:
    return (
        f"Model file not found: {path}. Provision weights with "
        "`python scripts/fetch_model.py` (see docs/TRAINING.md)."
    )


def model_exists(path: str) -> bool:
    p = Path(path)
    return p.exists() and p.stat().st_size > 1024 * 100
