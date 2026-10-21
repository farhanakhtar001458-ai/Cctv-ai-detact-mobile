"""Geometry helpers and the IoU tracker (multi-person, occlusion, 50 targets)."""

from __future__ import annotations

import numpy as np
import pytest

from app.vision.geometry import area, containment_ratio, iou, normalized_center_distance
from app.vision.tracker import IoUTracker, Track


def box(x1, y1, x2, y2):
    return np.array([x1, y1, x2, y2], dtype=np.float32)


def test_iou_basics():
    a = box(0, 0, 10, 10)
    assert iou(a, a) == pytest.approx(1.0)
    assert iou(a, box(20, 20, 30, 30)) == 0.0
    assert iou(a, box(5, 0, 15, 10)) == pytest.approx(50 / 150)
    assert area(a) == pytest.approx(100)


def test_containment_and_distance():
    person = box(100, 100, 160, 240)
    phone = box(120, 150, 138, 180)
    assert containment_ratio(phone, person) == pytest.approx(1.0)
    assert containment_ratio(person, phone) < 0.1
    assert normalized_center_distance(person, person) == 0.0


def test_tracker_requires_min_hits():
    tracker = IoUTracker(max_age=5, min_hits=3, iou_threshold=0.3)
    visible = []
    for i in range(2):
        visible = tracker.update([box(100 + i, 100, 160 + i, 240)], [0.9], now=100.0 + i)
    assert visible == []  # only 2 hits, min_hits=3
    visible = tracker.update([box(103, 100, 163, 240)], [0.9], now=103.0)
    assert len(visible) == 1
    assert visible[0].label == "Person-01"


def test_tracker_id_stability_across_movement():
    tracker = IoUTracker(max_age=10, min_hits=2)
    ids = set()
    for i in range(30):
        x = 100 + i * 3
        tracks = tracker.update([box(x, 100, x + 60, 240)], [0.9], now=float(i))
        if tracks:
            ids.add(tracks[0].track_id)
    assert ids == {1}, "tracking id must stay stable for a continuously visible person"


def test_tracker_recovers_after_occlusion():
    tracker = IoUTracker(max_age=10, min_hits=2, iou_threshold=0.3)
    for i in range(5):
        tracker.update([box(100, 100, 160, 240)], [0.9], now=float(i))
    # Person disappears for 6 frames (within max_age).
    for i in range(5, 11):
        tracker.update([], [], now=float(i))
    tracks = tracker.update([box(104, 100, 164, 240)], [0.9], now=11.0)
    assert tracks and tracks[0].track_id == 1, "same anonymous id should be recovered"


def test_tracker_creates_new_ids_after_max_age():
    tracker = IoUTracker(max_age=2, min_hits=1)
    tracker.update([box(100, 100, 160, 240)], [0.9], now=0.0)
    for i in range(1, 6):
        tracker.update([], [], now=float(i))
    tracks = tracker.update([box(100, 100, 160, 240)], [0.9], now=6.0)
    assert tracks[0].track_id == 2


def test_tracker_keeps_two_overlapping_people_separate():
    tracker = IoUTracker(max_age=5, min_hits=1, iou_threshold=0.2)
    a = box(100, 100, 170, 260)
    b = box(140, 110, 210, 270)  # 30px overlap
    tracks = tracker.update([a, b], [0.9, 0.8], now=1.0)
    assert len(tracks) == 2
    ids = {t.track_id for t in tracks}
    assert len(ids) == 2
    # Move both slightly; ids must persist and not swap.
    tracks2 = tracker.update([a + 5, b + 5], [0.9, 0.8], now=2.0)
    assert {t.track_id for t in tracks2} == ids


def test_tracker_handles_50_people():
    tracker = IoUTracker(max_age=10, min_hits=2, max_tracks=64)
    boxes = [box(20 + (i % 10) * 90, 20 + (i // 10) * 100, 20 + (i % 10) * 90 + 60, 20 + (i // 10) * 100 + 140) for i in range(50)]
    scores = [0.9] * 50
    tracks = []
    for frame in range(6):
        jittered = [b + (frame % 2) for b in boxes]
        tracks = tracker.update(jittered, scores, now=float(frame))
    assert len(tracks) == 50
    assert len({t.track_id for t in tracks}) == 50
    # Labels stay within the 50-person naming scheme.
    assert all(t.label.startswith("Person-") for t in tracks)


def test_track_velocity_prediction_used_for_matching():
    tracker = IoUTracker(max_age=5, min_hits=1, iou_threshold=0.9, max_distance_ratio=1.2)
    tracker.update([box(100, 100, 160, 240)], [0.9], now=0.0)
    for i in range(1, 4):
        tracker.update([box(100 + i * 8, 100, 160 + i * 8, 240)], [0.9], now=float(i))
    assert tracker.tracks[0].velocity[0] > 0
    predicted = tracker.tracks[0].predicted_box()
    assert predicted[0] > tracker.tracks[0].box[0]


def test_track_conf_alias():
    track = Track(track_id=1, box=box(0, 0, 10, 10), score=0.77)
    assert track.conf == 0.77
    assert track.label == "Person-01"
