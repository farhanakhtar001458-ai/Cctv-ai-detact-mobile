"""Stage 4/5/6 - temporal phone-use analysis, state machine and alert gating.

A single frame can only ever tell us "a phone is near a person". This module
accumulates evidence over a sliding time window per tracked person and derives
one of five states:

``none``                 nothing relevant visible for this person
``phone_detected``       a phone is visible/associated but evidence is weak
``possible_phone_use``   evidence is accumulating but below the alert threshold
``sustained_phone_use``  the configured persistence/confidence threshold held
``uncertain``            contradictory or low-quality evidence (e.g. ambiguous
                         assignment between two students, heavy occlusion)

Only ``sustained_phone_use`` may trigger an audible alert, and only through
:class:`AlertSuppressor` (per-person cooldown + duplicate suppression).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque


class PhoneUseState(str, Enum):
    NONE = "none"
    PHONE_DETECTED = "phone_detected"
    POSSIBLE_USE = "possible_phone_use"
    SUSTAINED = "sustained_phone_use"
    UNCERTAIN = "uncertain"


@dataclass
class TemporalConfig:
    window_s: float = 4.0
    min_sustained_s: float = 2.0
    gap_tolerance_s: float = 1.0
    persistence_threshold: float = 0.65
    possible_threshold: float = 0.35
    uncertain_score: float = 0.30
    ambiguity_ratio_limit: float = 0.45
    hysteresis: float = 0.08
    max_window_samples: int = 240


@dataclass
class _Sample:
    t: float
    associated: bool
    score: float
    phone_conf: float
    ambiguous: bool
    in_zone: bool
    in_head: bool


@dataclass
class TrackEvidence:
    track_id: int
    label: str
    state: PhoneUseState = PhoneUseState.NONE
    use_score: float = 0.0
    sustained_s: float = 0.0
    presence_ratio: float = 0.0
    mean_score: float = 0.0
    head_zone_ratio: float = 0.0
    ambiguity_ratio: float = 0.0
    samples: int = 0
    phone_assoc: object | None = None  # last PhoneAssociation (kept for evidence)
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "track_id": self.track_id,
            "label": self.label,
            "state": self.state.value,
            "use_score": round(self.use_score, 3),
            "sustained_s": round(self.sustained_s, 2),
            "presence_ratio": round(self.presence_ratio, 3),
            "mean_score": round(self.mean_score, 3),
            "head_zone_ratio": round(self.head_zone_ratio, 3),
            "ambiguity_ratio": round(self.ambiguity_ratio, 3),
            "samples": self.samples,
            "reason": self.reason,
        }


class TemporalAnalyzer:
    """Sliding-window evidence accumulator + hysteresis state machine."""

    def __init__(self, config: TemporalConfig | None = None) -> None:
        self.config = config or TemporalConfig()
        self._windows: dict[int, Deque[_Sample]] = {}
        self._states: dict[int, TrackEvidence] = {}
        self._run_start: dict[int, float] = {}
        self._last_assoc: dict[int, float] = {}

    def reset(self) -> None:
        self._windows.clear()
        self._states.clear()
        self._run_start.clear()
        self._last_assoc.clear()

    def prune(self, active_track_ids: set[int]) -> None:
        for tid in list(self._windows):
            if tid not in active_track_ids:
                del self._windows[tid]
                self._states.pop(tid, None)
                self._run_start.pop(tid, None)
                self._last_assoc.pop(tid, None)

    def update(
        self,
        now: float,
        tracks: list[tuple[int, str]],
        associations: dict[int, object],
    ) -> dict[int, TrackEvidence]:
        """Add one frame of evidence for every visible person; return states."""
        cfg = self.config
        active: set[int] = set()
        for track_id, label in tracks:
            active.add(track_id)
            assoc = associations.get(track_id)
            if assoc is not None:
                sample = _Sample(
                    t=now,
                    associated=True,
                    score=float(getattr(assoc, "score", 0.0)),
                    phone_conf=float(getattr(assoc, "phone_conf", 0.0)),
                    ambiguous=bool(getattr(assoc, "ambiguous", False)),
                    in_zone=bool(getattr(assoc, "evidence", {}).get("in_use_zone", False)),
                    in_head=bool(getattr(assoc, "evidence", {}).get("in_head_zone", False)),
                )
                last = self._last_assoc.get(track_id)
                if last is None or (now - last) > cfg.gap_tolerance_s:
                    self._run_start[track_id] = now
                self._last_assoc[track_id] = now
            else:
                sample = _Sample(now, False, 0.0, 0.0, False, False, False)

            window = self._windows.setdefault(track_id, deque(maxlen=cfg.max_window_samples))
            window.append(sample)
            while window and now - window[0].t > cfg.window_s:
                window.popleft()

            evidence = self._evaluate(track_id, label, window, now)
            if assoc is not None:
                evidence.phone_assoc = assoc
            self._states[track_id] = evidence

        self.prune(active)
        return {tid: self._states[tid] for tid in active if tid in self._states}

    def _evaluate(self, track_id: int, label: str, window: Deque[_Sample], now: float) -> TrackEvidence:
        cfg = self.config
        n = len(window)
        ev = TrackEvidence(track_id=track_id, label=label, samples=n)
        if n == 0:
            return ev
        assoc_samples = [s for s in window if s.associated]
        ev.presence_ratio = len(assoc_samples) / n
        if not assoc_samples:
            ev.state = PhoneUseState.NONE
            ev.reason = "No phone associated with this person in the analysis window"
            self._run_start.pop(track_id, None)
            return ev

        ev.mean_score = sum(s.score for s in assoc_samples) / len(assoc_samples)
        ev.head_zone_ratio = sum(1 for s in assoc_samples if s.in_head) / len(assoc_samples)
        ev.ambiguity_ratio = sum(1 for s in assoc_samples if s.ambiguous) / len(assoc_samples)
        mean_conf = sum(s.phone_conf for s in assoc_samples) / len(assoc_samples)
        zone_ratio = sum(1 for s in assoc_samples if s.in_zone) / len(assoc_samples)

        run_start = self._run_start.get(track_id)
        ev.sustained_s = max(0.0, now - run_start) if run_start is not None else 0.0

        # Evidence of *use* is only meaningful when the phone is credibly linked
        # to this person, so the association quality scales the whole score
        # instead of contributing a small additive term.
        use_quality = (
            0.45 * ev.presence_ratio
            + 0.20 * zone_ratio
            + 0.20 * ev.head_zone_ratio
            + 0.15 * mean_conf
        )
        association_factor = 0.45 + 0.55 * ev.mean_score
        use_score = use_quality * association_factor * (1.0 - 0.5 * ev.ambiguity_ratio)
        ev.use_score = float(max(0.0, min(1.0, use_score)))

        previous = self._states.get(track_id)
        prev_state = previous.state if previous else PhoneUseState.NONE
        sustained_hold = (
            prev_state == PhoneUseState.SUSTAINED
            and ev.use_score >= cfg.persistence_threshold - cfg.hysteresis
            and ev.sustained_s >= cfg.min_sustained_s * 0.6
        )

        if ev.ambiguity_ratio >= cfg.ambiguity_ratio_limit:
            ev.state = PhoneUseState.UNCERTAIN
            ev.reason = (
                f"Phone association ambiguous in {ev.ambiguity_ratio:.0%} of frames "
                "(more than one plausible student)"
            )
        elif ev.use_score < cfg.possible_threshold or ev.sustained_s < 0.6:
            if ev.use_score < cfg.uncertain_score and ev.presence_ratio < 0.5:
                ev.state = PhoneUseState.PHONE_DETECTED
                ev.reason = "Mobile phone visible near this person; evidence below use threshold"
            else:
                ev.state = PhoneUseState.POSSIBLE_USE
                ev.reason = "Phone appears to be held, awaiting persistence threshold"
        elif sustained_hold or (
            ev.use_score >= cfg.persistence_threshold and ev.sustained_s >= cfg.min_sustained_s
        ):
            ev.state = PhoneUseState.SUSTAINED
            ev.reason = (
                f"Use confidence {ev.use_score:.2f} maintained for {ev.sustained_s:.1f}s "
                f"(>= {cfg.persistence_threshold:.2f} / {cfg.min_sustained_s:.1f}s)"
            )
        elif ev.use_score >= cfg.possible_threshold:
            ev.state = PhoneUseState.POSSIBLE_USE
            ev.reason = (
                f"Use confidence {ev.use_score:.2f} sustained {ev.sustained_s:.1f}s "
                f"- below alert threshold"
            )
        else:
            ev.state = PhoneUseState.UNCERTAIN
            ev.reason = "Conflicting evidence in analysis window"
        return ev


@dataclass
class _Gate:
    last_announced: float
    count: int = 1


class AlertSuppressor:
    """Cooldown + duplicate suppression for alerts and announcements."""

    def __init__(self, cooldown_s: float = 60.0, per_track: bool = True) -> None:
        self.cooldown_s = cooldown_s
        self.per_track = per_track
        self._gates: dict[tuple, _Gate] = {}
        self._active: dict[tuple, float] = {}

    def configure(self, cooldown_s: float) -> None:
        self.cooldown_s = max(0.0, float(cooldown_s))

    def _key(self, camera_id: int, track_id: int, category: str) -> tuple:
        return (camera_id, track_id if self.per_track else 0, category)

    def should_alert(self, camera_id: int, track_id: int, category: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        key = self._key(camera_id, track_id, category)
        gate = self._gates.get(key)
        if gate is None:
            self._gates[key] = _Gate(last_announced=now)
            return True
        if now - gate.last_announced >= self.cooldown_s:
            gate.last_announced = now
            gate.count += 1
            return True
        return False

    def active_since(self, camera_id: int, track_id: int, category: str) -> float | None:
        return self._active.get(self._key(camera_id, track_id, category))

    def set_active(self, camera_id: int, track_id: int, category: str, active: bool, now: float | None = None) -> None:
        key = self._key(camera_id, track_id, category)
        if active and key not in self._active:
            self._active[key] = now if now is not None else time.time()
        elif not active:
            self._active.pop(key, None)

    def reset(self) -> None:
        self._gates.clear()
        self._active.clear()

    def prune(self, now: float, max_idle_s: float = 3600.0) -> None:
        for key in [k for k, g in self._gates.items() if now - g.last_announced > max_idle_s]:
            self._gates.pop(key, None)
