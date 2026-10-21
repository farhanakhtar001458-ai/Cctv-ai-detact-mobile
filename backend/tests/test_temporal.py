"""Temporal evidence, the state machine and alert suppression (stages 4-6)."""

from __future__ import annotations

import numpy as np

from app.vision.association import PhoneAssociation
from app.vision.temporal import AlertSuppressor, PhoneUseState, TemporalAnalyzer, TemporalConfig


def assoc(track_id: int, score: float = 0.85, ambiguous: bool = False, in_head: bool = True) -> PhoneAssociation:
    return PhoneAssociation(
        phone_index=0,
        phone_box=np.array([120, 150, 140, 180], dtype=np.float32),
        phone_conf=0.85,
        track_id=track_id,
        track_label=f"Person-{track_id:02d}",
        score=score,
        ambiguous=ambiguous,
        evidence={"in_use_zone": True, "in_head_zone": in_head},
    )


def run(analyzer: TemporalAnalyzer, seconds: float, fps: float = 10.0, assoc_score: float = 0.85, ambiguous: bool = False):
    states = {}
    for i in range(int(seconds * fps)):
        t = 100.0 + i / fps
        states = analyzer.update(t, [(1, "Person-01")], {1: assoc(1, assoc_score, ambiguous)})
    return states[1]


def test_sustained_phone_use_reaches_alert_state():
    analyzer = TemporalAnalyzer(TemporalConfig(persistence_threshold=0.6, min_sustained_s=2.0))
    state = run(analyzer, seconds=4.0)
    assert state.state == PhoneUseState.SUSTAINED
    assert state.use_score >= 0.6
    assert state.sustained_s >= 2.0
    assert "threshold" in state.reason.lower() or "maintained" in state.reason.lower()


def test_short_exposure_stays_possible_not_alert():
    analyzer = TemporalAnalyzer(TemporalConfig(persistence_threshold=0.65, min_sustained_s=2.0))
    state = run(analyzer, seconds=1.0)
    assert state.state in (PhoneUseState.POSSIBLE_USE, PhoneUseState.PHONE_DETECTED)
    assert state.state != PhoneUseState.SUSTAINED


def test_low_score_never_alerts():
    analyzer = TemporalAnalyzer(TemporalConfig(persistence_threshold=0.7, min_sustained_s=1.0))
    state = run(analyzer, seconds=6.0, assoc_score=0.35)
    assert state.state != PhoneUseState.SUSTAINED


def test_ambiguous_association_downgrades_to_uncertain():
    analyzer = TemporalAnalyzer(TemporalConfig(persistence_threshold=0.6, min_sustained_s=1.0))
    state = run(analyzer, seconds=5.0, ambiguous=True)
    assert state.state == PhoneUseState.UNCERTAIN
    assert "ambiguous" in state.reason.lower()


def test_no_phone_means_no_state():
    analyzer = TemporalAnalyzer()
    for i in range(20):
        states = analyzer.update(100 + i * 0.1, [(1, "Person-01")], {})
    assert states[1].state == PhoneUseState.NONE


def test_gap_resets_persistence_timer():
    analyzer = TemporalAnalyzer(TemporalConfig(persistence_threshold=0.6, min_sustained_s=2.0, gap_tolerance_s=0.5))
    run(analyzer, seconds=1.0)
    # Phone disappears for 2 seconds.
    for i in range(20):
        analyzer.update(102.0 + i * 0.1, [(1, "Person-01")], {})
    state = run(analyzer, seconds=1.0)
    assert state.state != PhoneUseState.SUSTAINED, "persistence must restart after a gap"
    assert state.sustained_s < 2.0


def test_states_are_pruned_for_departed_people():
    analyzer = TemporalAnalyzer()
    analyzer.update(100.0, [(1, "Person-01")], {1: assoc(1)})
    assert 1 in analyzer._states
    analyzer.update(101.0, [], {})
    assert 1 not in analyzer._states


def test_alert_suppressor_cooldown():
    suppressor = AlertSuppressor(cooldown_s=60)
    assert suppressor.should_alert(1, 1, "sustained", now=100.0) is True
    assert suppressor.should_alert(1, 1, "sustained", now=110.0) is False
    assert suppressor.should_alert(1, 1, "sustained", now=161.0) is True
    # A different person is independent.
    assert suppressor.should_alert(1, 2, "sustained", now=161.0) is True
    # A different camera is independent.
    assert suppressor.should_alert(2, 1, "sustained", now=161.0) is True


def test_alert_suppressor_per_camera_mode():
    suppressor = AlertSuppressor(cooldown_s=30, per_track=False)
    assert suppressor.should_alert(1, 1, "sustained", now=0.0) is True
    assert suppressor.should_alert(1, 2, "sustained", now=5.0) is False
    assert suppressor.should_alert(1, 3, "sustained", now=31.0) is True


def test_active_since_tracking():
    suppressor = AlertSuppressor(cooldown_s=10)
    suppressor.set_active(1, 1, "sustained", True, now=5.0)
    assert suppressor.active_since(1, 1, "sustained") == 5.0
    suppressor.set_active(1, 1, "sustained", False)
    assert suppressor.active_since(1, 1, "sustained") is None
