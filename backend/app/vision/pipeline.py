"""The per-frame analysis pipeline (stages 1-5).

    frame -> detections -> person tracking -> phone/person association
          -> temporal evidence -> per-person state -> (alerts handled upstream)

The pipeline is pure and synchronous; the camera worker owns the thread, the
event database and the alert side effects. This keeps it directly unit
testable with synthetic frames or a stub detector.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

import cv2
import numpy as np

from app.vision.association import AssociationConfig, PersonRef, PhoneAssociation, PhonePersonAssociator
from app.vision.detector import (
    PHONE_LABEL,
    PERSON_LABEL,
    Detection,
    detector_manager,
    filter_detections,
)
from app.vision.geometry import BoxArray, area, containment_ratio
from app.vision.sources import SimScene
from app.vision.temporal import (
    PhoneUseState,
    TemporalAnalyzer,
    TemporalConfig,
    TrackEvidence,
)
from app.vision.tracker import Track, build_tracker


@dataclass
class AnalyzerConfig:
    person_conf: float = 0.35
    phone_conf: float = 0.30
    persistence_threshold: float = 0.65
    phone_detection_enabled: bool = True
    max_visible_people: int = 50
    window_s: float = 4.0
    min_sustained_s: float = 2.0
    tracker_type: str = "iou"
    tracker_max_age: int = 45
    tracker_min_hits: int = 3
    tracker_iou_threshold: float = 0.3
    low_light_threshold: float = 0.22
    count_context_objects: bool = False


@dataclass
class TrackSummary:
    track_id: int
    label: str
    box: BoxArray
    conf: float
    state: PhoneUseState
    use_score: float
    sustained_s: float
    phone_box: BoxArray | None = None
    phone_conf: float = 0.0
    association_score: float = 0.0
    ambiguous: bool = False
    occluded: bool = False
    reason: str = ""

    def to_dict(self, frame_w: int, frame_h: int) -> dict[str, Any]:
        def norm(box: BoxArray) -> list[float]:
            return [
                round(float(box[0]) / max(frame_w, 1), 4),
                round(float(box[1]) / max(frame_h, 1), 4),
                round(float(box[2]) / max(frame_w, 1), 4),
                round(float(box[3]) / max(frame_h, 1), 4),
            ]

        return {
            "track_id": self.track_id,
            "label": self.label,
            "box": norm(self.box),
            "conf": round(self.conf, 3),
            "state": self.state.value,
            "use_score": round(self.use_score, 3),
            "sustained_s": round(self.sustained_s, 2),
            "phone_box": norm(self.phone_box) if self.phone_box is not None else None,
            "phone_conf": round(self.phone_conf, 3),
            "association_score": round(self.association_score, 3),
            "ambiguous": self.ambiguous,
            "occluded": self.occluded,
            "reason": self.reason,
        }


@dataclass
class FrameAnalysis:
    timestamp: float
    frame_index: int
    frame_w: int
    frame_h: int
    people: list[Detection] = field(default_factory=list)
    phones: list[Detection] = field(default_factory=list)
    tracks: list[TrackSummary] = field(default_factory=list)
    occupancy: int = 0
    low_light: bool = False
    brightness: float = 0.0
    inference_ms: float = 0.0
    total_ms: float = 0.0
    simulated: bool = False
    model_backend: str = ""
    phone_capable: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def alert_tracks(self) -> list[TrackSummary]:
        return [t for t in self.tracks if t.state == PhoneUseState.SUSTAINED]

    @property
    def possible_tracks(self) -> list[TrackSummary]:
        return [t for t in self.tracks if t.state == PhoneUseState.POSSIBLE_USE]

    @property
    def uncertain_tracks(self) -> list[TrackSummary]:
        return [t for t in self.tracks if t.state in (PhoneUseState.UNCERTAIN,)]

    @property
    def phone_counts(self) -> int:
        return len(self.phones)

    def to_dict(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "timestamp": self.timestamp,
            "frame_index": self.frame_index,
            "frame_size": [self.frame_w, self.frame_h],
            "occupancy": self.occupancy,
            "simulated": self.simulated,
            "low_light": self.low_light,
            "brightness": round(self.brightness, 3),
            "phones_visible": len(self.phones),
            "active_alerts": len(self.alert_tracks),
            "possible_alerts": len(self.possible_tracks),
            "uncertain": len(self.uncertain_tracks),
            "inference_ms": round(self.inference_ms, 2),
            "total_ms": round(self.total_ms, 2),
            "model": {"backend": self.model_backend, "phone_capable": self.phone_capable},
            "tracks": [t.to_dict(self.frame_w, self.frame_h) for t in self.tracks],
            "notes": self.notes,
        }
        if extra:
            payload.update(extra)
        return payload


class SimulatedDetector:
    """Produces detections from the simulated scene ground truth (demo only)."""

    name = "simulated"
    phone_capable = True

    def __init__(self, scene: SimScene, width: int, height: int) -> None:
        self.scene = scene
        self.width = width
        self.height = height

    def detect(self, frame: np.ndarray) -> list[Detection]:
        h, w = frame.shape[:2]
        sx, sy = w / max(self.scene.width, 1), h / max(self.scene.height, 1)
        people, phones = self.scene.ground_truth(show_phones=True)
        dets: list[Detection] = []
        for box, conf in people:
            arr = np.array(box, dtype=np.float32) * np.array([sx, sy, sx, sy], dtype=np.float32)
            dets.append(Detection(PERSON_LABEL, conf, arr, 0))
        for box, conf in phones:
            arr = np.array(box, dtype=np.float32) * np.array([sx, sy, sx, sy], dtype=np.float32)
            dets.append(Detection(PHONE_LABEL, conf, arr, 67))
        return dets

    def close(self) -> None:
        pass


class FrameAnalyzer:
    def __init__(
        self,
        config: AnalyzerConfig,
        simulated: bool = False,
        detector: Any | None = None,
    ) -> None:
        self.config = config
        self.simulated = simulated
        # Optional injected detector (null backend in tests, custom models, mocks).
        self.detector = detector
        self.tracker = build_tracker(
            config.tracker_type,
            max_age=config.tracker_max_age,
            min_hits=config.tracker_min_hits,
            iou_threshold=config.tracker_iou_threshold,
            max_tracks=min(64, max(8, config.max_visible_people + 8)),
        )
        self.associator = PhonePersonAssociator(AssociationConfig())
        self.temporal = TemporalAnalyzer(
            TemporalConfig(
                window_s=config.window_s,
                min_sustained_s=config.min_sustained_s,
                persistence_threshold=config.persistence_threshold,
            )
        )
        self._frame_index = 0
        self._sim_detector: SimulatedDetector | None = None

    # -- helpers ----------------------------------------------------------
    def reset(self) -> None:
        self.tracker.reset()
        self.associator.reset()
        self.temporal.reset()
        self._frame_index = 0

    def _detect(self, frame: np.ndarray, scene: SimScene | None) -> list[Detection]:
        if self.detector is not None and not self.simulated:
            return self.detector.detect(frame)
        if self.simulated:
            if scene is None:
                return []
            if self._sim_detector is None or self._sim_detector.scene is not scene:
                self._sim_detector = SimulatedDetector(scene, frame.shape[1], frame.shape[0])
            return self._sim_detector.detect(frame)
        return detector_manager.detect(frame)

    @staticmethod
    def _occlusion_flags(boxes: Sequence[BoxArray]) -> list[bool]:
        flags = [False] * len(boxes)
        for i, a in enumerate(boxes):
            covered = 0.0
            for j, b in enumerate(boxes):
                if i == j:
                    continue
                covered += containment_ratio(a, b)
            flags[i] = covered > 0.55
        return flags

    # -- main -------------------------------------------------------------
    def analyze(
        self,
        frame: np.ndarray,
        now: float | None = None,
        scene: SimScene | None = None,
    ) -> FrameAnalysis:
        started = time.perf_counter()
        now = now if now is not None else time.time()
        self._frame_index += 1
        h, w = frame.shape[:2]
        cfg = self.config
        result = FrameAnalysis(
            timestamp=now,
            frame_index=self._frame_index,
            frame_w=w,
            frame_h=h,
            simulated=self.simulated,
        )

        t_detect = time.perf_counter()
        raw = self._detect(frame, scene)
        result.inference_ms = (time.perf_counter() - t_detect) * 1000

        people, phones = filter_detections(raw, cfg.person_conf, cfg.phone_conf)
        result.people = people
        result.phones = phones
        result.brightness = float(np.mean(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))) / 255.0 if frame.ndim == 3 else float(np.mean(frame)) / 255.0
        result.low_light = result.brightness < cfg.low_light_threshold
        if result.low_light:
            result.notes.append("Low light: detection confidence may be reduced")
        if self.detector is not None and not self.simulated:
            result.model_backend = getattr(self.detector, "name", "injected")
            result.phone_capable = bool(getattr(self.detector, "phone_capable", True))
        elif not self.simulated:
            result.model_backend = detector_manager.status.backend
            result.phone_capable = detector_manager.phone_capable
        else:
            result.model_backend = "simulated"
            result.phone_capable = True

        # Stage 2 - tracking. Sort by area so larger (closer) people win matches.
        order = sorted(range(len(people)), key=lambda i: area(people[i].box), reverse=True)
        boxes = [people[i].box for i in order]
        scores = [people[i].conf for i in order]
        tracks: list[Track] = self.tracker.update(boxes, scores, now)
        tracks = sorted(tracks, key=lambda t: t.track_id)[: cfg.max_visible_people]
        result.occupancy = len(tracks)
        if len(people) > cfg.max_visible_people:
            result.notes.append(
                f"{len(people)} people detected; configured maximum is {cfg.max_visible_people}"
            )

        # Stage 3 - phone/person association.
        person_refs = [PersonRef(t.track_id, t.label, t.box) for t in tracks]
        assoc_result = (
            self.associator.associate(person_refs, [(p.box, p.conf) for p in phones])
            if (cfg.phone_detection_enabled and result.phone_capable)
            else None
        )
        by_track: dict[int, PhoneAssociation] = {}
        if assoc_result:
            for assoc in assoc_result.associations:
                by_track[assoc.track_id] = assoc

        # Stage 4 - temporal evidence.
        states = self.temporal.update(now, [(t.track_id, t.label) for t in tracks], by_track)
        occlusion = self._occlusion_flags([t.box for t in tracks])

        for track, occluded in zip(tracks, occlusion):
            evidence: TrackEvidence | None = states.get(track.track_id)
            assoc = by_track.get(track.track_id)
            summary = TrackSummary(
                track_id=track.track_id,
                label=track.label,
                box=track.box,
                conf=track.conf,
                state=evidence.state if evidence else PhoneUseState.NONE,
                use_score=evidence.use_score if evidence else 0.0,
                sustained_s=evidence.sustained_s if evidence else 0.0,
                phone_box=assoc.phone_box if assoc else None,
                phone_conf=assoc.phone_conf if assoc else 0.0,
                association_score=assoc.score if assoc else 0.0,
                ambiguous=bool(assoc.ambiguous) if assoc else False,
                occluded=occluded,
                reason=evidence.reason if evidence else "",
            )
            if summary.occluded and summary.state == PhoneUseState.SUSTAINED:
                summary.state = PhoneUseState.UNCERTAIN
                summary.reason = "Person heavily occluded; alert downgraded to uncertain"
            result.tracks.append(summary)

        result.total_ms = (time.perf_counter() - started) * 1000
        return result


# ---------------------------------------------------------------------------
# Rendering (annotated snapshot for the dashboard MJPEG preview)
# ---------------------------------------------------------------------------

STATE_COLORS = {
    # BGR. Paired with text labels in the UI - colour is never the only signal.
    # green = person, blue = phone, amber = possible use, red = sustained
    # alert, grey = uncertain / unavailable.
    PhoneUseState.NONE: (90, 200, 90),
    PhoneUseState.PHONE_DETECTED: (235, 165, 40),
    PhoneUseState.POSSIBLE_USE: (30, 190, 245),
    PhoneUseState.SUSTAINED: (55, 55, 235),
    PhoneUseState.UNCERTAIN: (155, 155, 155),
}


def _label_clamp(x: int, text_width: int, frame_width: int) -> int:
    """Keep a label box fully inside the frame (labels used to be cut off at the edge)."""
    return max(0, min(x, frame_width - text_width - 8))


def _free_label_row(
    x: int, width: int, y_preferred: int, taken: list[tuple[int, int, int, int]], frame_height: int
) -> int:
    """Nudge a label upward until it no longer overlaps an already drawn label.

    Dense classroom frames put several people side by side, and their labels used
    to overprint each other, hiding the state of the person behind.
    """
    y = y_preferred
    for _ in range(6):
        clash = next(
            (box for box in taken if not (x + width < box[0] or x > box[2] or y + 16 < box[1] or y > box[3])),
            None,
        )
        if clash is None:
            return max(0, y)
        y = clash[1] - 18
    return max(0, min(y_preferred, frame_height - 18))


def annotate(frame: np.ndarray, analysis: FrameAnalysis, show_context: bool = False) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]
    thickness = max(1, int(round(w / 640)))
    drawn_labels: list[tuple[int, int, int, int]] = []
    for track in analysis.tracks:
        color = STATE_COLORS.get(track.state, (140, 140, 140))
        x1, y1, x2, y2 = (int(v) for v in track.box)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, thickness)
        label_map = {
            PhoneUseState.NONE: "PERSON",
            PhoneUseState.PHONE_DETECTED: "PHONE NEARBY",
            PhoneUseState.POSSIBLE_USE: "POSSIBLE USE",
            PhoneUseState.SUSTAINED: "PHONE USE ALERT",
            PhoneUseState.UNCERTAIN: "UNCERTAIN",
        }
        text = f"{track.label} | {label_map.get(track.state, 'PERSON')}"
        if track.state in (PhoneUseState.POSSIBLE_USE, PhoneUseState.SUSTAINED):
            text += f" {track.use_score:.0%}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
        lx = _label_clamp(x1, tw, w)
        ty = _free_label_row(lx, tw + 8, max(0, y1 - th - 6), drawn_labels, h)
        cv2.rectangle(out, (lx, ty), (lx + tw + 8, ty + th + 8), color, -1)
        cv2.putText(out, text, (lx + 4, ty + th + 1), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (10, 10, 10), 1, cv2.LINE_AA)
        drawn_labels.append((lx, ty, lx + tw + 8, ty + th + 8))
        if track.phone_box is not None:
            px1, py1, px2, py2 = (int(v) for v in track.phone_box)
            cv2.rectangle(out, (px1, py1), (px2, py2), (255, 200, 0), thickness)
            cv2.putText(out, f"PHONE {track.phone_conf:.2f}", (max(0, min(px1, w - 90)), min(h - 2, py2 + 14)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 220, 60), 1, cv2.LINE_AA)
    # HUD
    hud = f"People {analysis.occupancy} | Alerts {len(analysis.alert_tracks)} | {analysis.inference_ms:.0f}ms"
    if analysis.simulated:
        hud = "SIMULATED | " + hud
    if analysis.low_light:
        hud += " | LOW LIGHT"
    cv2.rectangle(out, (0, 0), (min(w, 8 * len(hud) + 16), 26), (20, 20, 24), -1)
    cv2.putText(out, hud, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1, cv2.LINE_AA)
    return out


def encode_jpeg(frame: np.ndarray, quality: int = 75) -> bytes:
    quality = int(max(30, min(95, quality)))
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return b""
    return buf.tobytes()
