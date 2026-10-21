"""Multi-object tracking for people.

Two backends are provided:

* :class:`IoUTracker` - dependency-free IoU + constant-velocity tracker used by
  default. Handles occlusion gaps (``max_age``), requires ``min_hits`` before a
  track is published and caps the number of simultaneous tracks.
* :class:`ByteTrackAdapter` - optional adapter around Ultralytics' BYTETracker
  (``SCAI_TRACKER_TYPE=bytetrack``). Falls back to :class:`IoUTracker` when the
  package is unavailable.

Tracking IDs are anonymous and session scoped (``Person-01`` ...). They are
never derived from appearance or biometric features and are recycled only when
a track is definitively lost.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np

from app.vision.geometry import BoxArray, area, iou, normalized_center_distance

log = logging.getLogger(__name__)

MAX_TRACKS = 64  # hard cap; the product requirement is 50 visible people


@dataclass
class Track:
    track_id: int
    box: BoxArray
    score: float
    hits: int = 1
    age: int = 1
    misses: int = 0
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(2, dtype=np.float32))
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    confidence: float = 1.0

    @property
    def label(self) -> str:
        return f"Person-{self.track_id:02d}"

    @property
    def conf(self) -> float:
        """Alias kept for readability at call sites (== detection score)."""
        return self.score

    @property
    def center(self) -> tuple[float, float]:
        return ((float(self.box[0]) + float(self.box[2])) / 2.0, (float(self.box[1]) + float(self.box[3])) / 2.0)

    def predicted_box(self) -> BoxArray:
        vx, vy = float(self.velocity[0]), float(self.velocity[1])
        return np.array(
            [
                self.box[0] + vx,
                self.box[1] + vy,
                self.box[2] + vx,
                self.box[3] + vy,
            ],
            dtype=np.float32,
        )

    def update(self, box: BoxArray, score: float, now: float) -> None:
        old_cx, old_cy = self.center
        new_cx = (float(box[0]) + float(box[2])) / 2.0
        new_cy = (float(box[1]) + float(box[3])) / 2.0
        raw = np.array([new_cx - old_cx, new_cy - old_cy], dtype=np.float32)
        # Exponential smoothing keeps the velocity estimate stable under jitter.
        self.velocity = 0.6 * self.velocity + 0.4 * raw
        self.box = box
        self.score = score
        self.hits += 1
        self.misses = 0
        self.last_seen = now
        self.confidence = min(1.0, 0.5 + 0.1 * self.hits)

    def mark_missed(self) -> None:
        self.misses += 1
        self.age += 1
        # Decay velocity when the target is not observed.
        self.velocity = self.velocity * 0.5


class IoUTracker:
    """Greedy IoU + distance tracker with motion prediction and gap tolerance."""

    name = "iou"

    def __init__(
        self,
        max_age: int = 45,
        min_hits: int = 3,
        iou_threshold: float = 0.3,
        max_distance_ratio: float = 0.9,
        max_tracks: int = MAX_TRACKS,
    ) -> None:
        self.max_age = max(1, max_age)
        self.min_hits = max(1, min_hits)
        self.iou_threshold = iou_threshold
        self.max_distance_ratio = max_distance_ratio
        self.max_tracks = max_tracks
        self.tracks: list[Track] = []
        self._next_id = 1
        self._retired: list[int] = []

    # -- id management ----------------------------------------------------
    def _new_id(self) -> int:
        if len(self._retired) > 200:
            self._retired = self._retired[-100:]
        track_id = self._next_id
        self._next_id += 1
        if track_id > 99:  # keep labels short, wrap after 99 simultaneous sessions
            self._next_id = 1
            while any(t.track_id == self._next_id for t in self.tracks):
                self._next_id += 1
            track_id = self._next_id
        return track_id

    # -- main API ---------------------------------------------------------
    def update(self, boxes: list[BoxArray], scores: list[float], now: float | None = None) -> list[Track]:
        """Associate detections to tracks; returns confirmed, currently visible tracks."""
        now = now if now is not None else time.time()
        if len(self.tracks) > self.max_tracks:
            # Keep the most recently seen tracks only.
            self.tracks.sort(key=lambda t: t.last_seen, reverse=True)
            self.tracks = self.tracks[: self.max_tracks]

        predictions = [t.predicted_box() for t in self.tracks]
        assigned_tracks: set[int] = set()
        assigned_dets: set[int] = set()
        pairs: list[tuple[float, int, int]] = []

        for ti, tbox in enumerate(predictions):
            for di, dbox in enumerate(boxes):
                score_iou = iou(tbox, dbox)
                dist_ratio = normalized_center_distance(tbox, dbox)
                if score_iou >= self.iou_threshold or dist_ratio <= self.max_distance_ratio:
                    # Prioritise IoU, break ties with proximity.
                    pair_score = score_iou + (1.0 - min(dist_ratio, 1.0)) * 0.3
                    pairs.append((pair_score, ti, di))

        pairs.sort(key=lambda p: p[0], reverse=True)
        for _, ti, di in pairs:
            if ti in assigned_tracks or di in assigned_dets:
                continue
            track = self.tracks[ti]
            track.update(boxes[di], scores[di], now)
            assigned_tracks.add(ti)
            assigned_dets.add(di)

        for ti, track in enumerate(self.tracks):
            if ti not in assigned_tracks:
                track.mark_missed()

        for di, box in enumerate(boxes):
            if di in assigned_dets:
                continue
            if len(self.tracks) >= self.max_tracks:
                break
            new_track = Track(
                track_id=self._new_id(),
                box=box,
                score=scores[di],
                hits=1,
                first_seen=now,
                last_seen=now,
            )
            self.tracks.append(new_track)

        survivors: list[Track] = []
        for track in self.tracks:
            if track.misses > self.max_age:
                self._retired.append(track.track_id)
                continue
            survivors.append(track)
        self.tracks = survivors

        return [t for t in self.tracks if t.hits >= self.min_hits and t.misses == 0]

    def reset(self) -> None:
        self.tracks.clear()
        self._next_id = 1
        self._retired.clear()

    def visible_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.misses == 0 and t.hits >= self.min_hits]

    def get(self, track_id: int) -> Track | None:
        for track in self.tracks:
            if track.track_id == track_id:
                return track
        return None

    def drop(self, track_id: int) -> None:
        self.tracks = [t for t in self.tracks if t.track_id != track_id]
        self._retired.append(track_id)


class ByteTrackAdapter:
    """Thin adapter over Ultralytics' BYTETracker (optional)."""

    name = "bytetrack"

    def __init__(self, **kwargs) -> None:
        self._fallback = IoUTracker(
            max_age=kwargs.get("max_age", 45),
            min_hits=kwargs.get("min_hits", 3),
            iou_threshold=kwargs.get("iou_threshold", 0.3),
        )
        self._tracker = None
        try:  # pragma: no cover - depends on optional package
            from ultralytics.trackers.basetrack import BaseTrack
            from ultralytics.trackers.byte_tracker import BYTETracker
            from types import SimpleNamespace

            args = SimpleNamespace(
                track_high_thresh=0.4,
                track_low_thresh=0.1,
                new_track_thresh=0.5,
                track_buffer=self._fallback.max_age,
                match_thresh=0.8,
                fuse_score=True,
            )
            self._tracker = BYTETracker(args, frame_rate=15)
            self._BaseTrack = BaseTrack
            log.info("Using Ultralytics BYTETracker")
        except Exception as exc:  # noqa: BLE001 - optional dependency
            log.warning("BYTETracker unavailable (%s); using IoU tracker", exc)

    def update(self, boxes: list[BoxArray], scores: list[float], now: float | None = None) -> list[Track]:
        now = now if now is not None else time.time()
        if self._tracker is None or not boxes:
            return self._fallback.update(boxes, scores, now)
        try:  # pragma: no cover - depends on optional package
            dets = np.hstack([np.asarray(boxes, dtype=np.float32), np.asarray(scores, dtype=np.float32).reshape(-1, 1)])
            online = self._tracker.update(dets, None, None)
            result: list[Track] = []
            for st in online:
                tlwh = st.tlwh
                box = np.array(
                    [tlwh[0], tlwh[1], tlwh[0] + tlwh[2], tlwh[1] + tlwh[3]], dtype=np.float32
                )
                track = Track(track_id=int(st.track_id), box=box, score=float(st.score))
                track.last_seen = now
                track.hits = max(self._fallback.min_hits, int(st.track_len))
                result.append(track)
            self._fallback.tracks = result
            return result
        except Exception as exc:  # noqa: BLE001
            log.warning("BYTETracker update failed (%s); falling back", exc)
            self._tracker = None
            return self._fallback.update(boxes, scores, now)

    def reset(self) -> None:
        self._fallback.reset()

    def visible_tracks(self) -> list[Track]:
        return self._fallback.visible_tracks()

    def get(self, track_id: int) -> Track | None:
        return self._fallback.get(track_id)

    def drop(self, track_id: int) -> None:
        self._fallback.drop(track_id)


def build_tracker(name: str = "iou", **kwargs):
    if name == "bytetrack":
        return ByteTrackAdapter(**kwargs)
    return IoUTracker(**kwargs)


def count_visible(tracks: list[Track], min_area_ratio: float = 0.0, frame_area: float = 0.0) -> int:
    if frame_area <= 0 or min_area_ratio <= 0:
        return len(tracks)
    return sum(1 for t in tracks if area(t.box) / frame_area >= min_area_ratio)
