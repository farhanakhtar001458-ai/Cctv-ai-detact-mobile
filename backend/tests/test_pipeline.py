"""End-to-end frame pipeline with a stub detector (no weights required)."""

from __future__ import annotations

import numpy as np

from app.vision.pipeline import AnalyzerConfig, FrameAnalyzer, annotate, encode_jpeg
from app.vision.temporal import PhoneUseState
from tests.conftest import person, phone


def frame(width: int = 640, height: int = 360) -> np.ndarray:
    # Mid-grey frame so the low-light heuristic is not triggered.
    return np.full((height, width, 3), 120, dtype=np.uint8)


def make_analyzer(stub, persistence: float = 0.55, min_hits: int = 2) -> FrameAnalyzer:
    config = AnalyzerConfig(
        person_conf=0.3,
        phone_conf=0.25,
        persistence_threshold=persistence,
        tracker_min_hits=min_hits,
        min_sustained_s=1.0,
        window_s=4.0,
    )
    analyzer = FrameAnalyzer(config, simulated=False, detector=stub)
    analyzer.temporal.config.min_sustained_s = 1.0
    return analyzer


def test_person_only_produces_no_alert(fake_detector):
    fake_detector.default = [person(100, 100)]
    analyzer = make_analyzer(fake_detector)
    now = 1000.0
    for i in range(30):
        analysis = analyzer.analyze(frame(), now=now + i * 0.1)
    assert analysis.occupancy == 1
    assert analysis.tracks[0].state == PhoneUseState.NONE
    assert analysis.alert_tracks == []


def test_phone_in_hand_produces_sustained_alert(fake_detector):
    fake_detector.default = [person(100, 100, 70, 150), phone(150, 140)]
    analyzer = make_analyzer(fake_detector)
    now = 2000.0
    states = []
    analysis = None
    for i in range(50):
        analysis = analyzer.analyze(frame(), now=now + i * 0.1)
        if analysis.tracks:
            states.append(analysis.tracks[0].state)
    assert PhoneUseState.SUSTAINED in states
    final = analysis.tracks[0]
    assert final.phone_box is not None
    assert final.use_score > 0.5
    assert len(analysis.alert_tracks) == 1
    assert "maintained" in final.reason or "threshold" in final.reason


def test_phone_next_to_other_student_is_not_attributed(fake_detector):
    # Two well separated people; the phone sits inside person 2's box.
    fake_detector.default = [person(60, 100, 70, 150), person(420, 100, 70, 150), phone(470, 140)]
    analyzer = make_analyzer(fake_detector)
    now = 3000.0
    for i in range(40):
        analysis = analyzer.analyze(frame(720, 360), now=now + i * 0.1)
    by_id = {t.track_id: t for t in analysis.tracks}
    states = {tid: t.state for tid, t in by_id.items()}
    # Exactly one person may be associated with the phone.
    associated = [t for t in analysis.tracks if t.phone_box is not None]
    assert len(associated) == 1
    assert sum(1 for s in states.values() if s != PhoneUseState.NONE) == 1


def test_occluded_person_alerts_are_downgraded(fake_detector):
    # Person A almost fully inside person B -> occlusion flag -> uncertain.
    fake_detector.default = [person(100, 100, 70, 150), person(105, 105, 400, 200), phone(130, 160)]
    analyzer = make_analyzer(fake_detector)
    now = 4000.0
    for i in range(40):
        analysis = analyzer.analyze(frame(640, 360), now=now + i * 0.1)
    assert any(t.occluded for t in analysis.tracks)
    assert analysis.alert_tracks == [] or all(not t.occluded for t in analysis.alert_tracks)


def test_low_light_flag_is_reported(fake_detector):
    fake_detector.default = [person(100, 100)]
    analyzer = make_analyzer(fake_detector)
    dark = np.full((360, 640, 3), 12, dtype=np.uint8)
    analysis = analyzer.analyze(dark, now=5000.0)
    assert analysis.low_light is True
    assert any("Low light" in note for note in analysis.notes)


def test_phone_detection_can_be_disabled(fake_detector):
    fake_detector.default = [person(100, 100, 70, 150), phone(150, 140)]
    analyzer = make_analyzer(fake_detector)
    analyzer.config.phone_detection_enabled = False
    for i in range(20):
        analysis = analyzer.analyze(frame(), now=6000.0 + i * 0.1)
    assert all(t.state == PhoneUseState.NONE for t in analysis.tracks)


def test_50_people_are_tracked_without_exceeding_cap(fake_detector):
    people = []
    for row in range(5):
        for col in range(10):
            people.append(person(20 + col * 60, 10 + row * 68, 40, 60, conf=0.8))
    fake_detector.default = people
    config = AnalyzerConfig(max_visible_people=50, tracker_min_hits=1)
    analyzer = FrameAnalyzer(config, detector=fake_detector)
    analysis = None
    for i in range(6):
        analysis = analyzer.analyze(frame(640, 400), now=7000.0 + i * 0.1)
    assert analysis is not None
    assert analysis.occupancy == 50
    assert len({t.track_id for t in analysis.tracks}) == 50


def test_annotation_and_jpeg_encoding(fake_detector):
    fake_detector.default = [person(100, 100, 70, 150), phone(150, 140)]
    analyzer = make_analyzer(fake_detector)
    analysis = None
    for i in range(10):
        analysis = analyzer.analyze(frame(), now=8000.0 + i * 0.1)
    annotated = annotate(frame(), analysis)
    assert annotated.shape == (360, 640, 3)
    jpeg = encode_jpeg(annotated, 70)
    assert jpeg[:2] == b"\xff\xd8"
    assert len(jpeg) > 1000


def test_analysis_serialises_normalised_boxes(fake_detector):
    fake_detector.default = [person(100, 100, 70, 150)]
    analyzer = make_analyzer(fake_detector)
    analysis = None
    for i in range(5):
        analysis = analyzer.analyze(frame(), now=9000.0 + i * 0.1)
    payload = analysis.to_dict({"camera_id": 1})
    assert payload["camera_id"] == 1
    box = payload["tracks"][0]["box"]
    assert all(0.0 <= v <= 1.0 for v in box)
    assert payload["occupancy"] == 1
