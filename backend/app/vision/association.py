"""Stage 3 - associate mobile phones with the person most likely holding them.

Evidence used (all geometric / temporal, never biometric):

* **containment** - how much of the phone box lies inside the person's box.
* **proximity** - centre distance normalised by the person-box diagonal.
* **use zone** - the phone's centre relative to the upper body, which is where
  a held phone appears for a seated or standing student.
* **head zone** - top of the person box; a phone here is more likely being
  looked at (a coarse proxy, as the detector has no pose/keypoint head).
* **size plausibility** - phone box must be a believable fraction of the body.
* **continuity** - a small bonus for the person this phone was associated with
  on the previous frame, which keeps assignments stable frame to frame.

The module never claims certainty: ambiguous assignments are reported with
``ambiguous=True`` and the temporal stage downgrades them to "uncertain".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from app.vision.geometry import (
    BoxArray,
    area,
    center,
    containment_ratio,
    diagonal,
    expand,
    height,
    width,
)


@dataclass
class PersonRef:
    """Minimal view of a tracked person needed for association."""

    track_id: int
    label: str
    box: BoxArray


@dataclass
class AssociationConfig:
    expand_ratio: float = 0.10
    max_distance_ratio: float = 0.85
    use_zone_top: float = 0.02  # relative to person box height
    use_zone_bottom: float = 0.62
    use_zone_width: float = 0.95
    head_zone_bottom: float = 0.26
    min_phone_area_ratio: float = 0.0004  # of the person box area
    max_phone_area_ratio: float = 0.20
    ambiguity_margin: float = 0.12
    continuity_bonus: float = 0.08


@dataclass
class PhoneAssociation:
    phone_index: int
    phone_box: BoxArray
    phone_conf: float
    track_id: int
    track_label: str
    score: float
    ambiguous: bool = False
    second_score: float = 0.0
    evidence: dict[str, float | bool | str] = field(default_factory=dict)

    @property
    def evidence_tag(self) -> str:
        flags = [k for k, v in self.evidence.items() if isinstance(v, bool) and v]
        return ",".join(flags) if flags else "geometry_only"


@dataclass
class AssociationResult:
    associations: list[PhoneAssociation]
    unassigned_phones: list[int]
    ambiguous_pairs: int

    @property
    def phone_count(self) -> int:
        return len(self.associations) + len(self.unassigned_phones)


def _zone_score(person_box: BoxArray, phone_center: tuple[float, float], cfg: AssociationConfig) -> tuple[float, bool]:
    """1.0 inside the upper-body 'use zone', fading to 0 outside."""
    h = height(person_box)
    w = width(person_box)
    if h <= 0 or w <= 0:
        return 0.0, False
    top = person_box[1] + cfg.use_zone_top * h
    bottom = person_box[1] + cfg.use_zone_bottom * h
    left = person_box[0] + (1 - cfg.use_zone_width) / 2 * w
    right = person_box[2] - (1 - cfg.use_zone_width) / 2 * w
    cx, cy = phone_center
    if left <= cx <= right and top <= cy <= bottom:
        return 1.0, True
    # Soft falloff measured in body heights.
    dy = max(0.0, top - cy, cy - bottom) / h
    dx = max(0.0, left - cx, cx - right) / max(w, 1e-6)
    falloff = max(0.0, 1.0 - 2.5 * max(dy, dx))
    return falloff, False


def _head_zone(person_box: BoxArray, phone_center: tuple[float, float], cfg: AssociationConfig) -> bool:
    h = height(person_box)
    if h <= 0:
        return False
    bottom = person_box[1] + cfg.head_zone_bottom * h
    return bool(person_box[0] <= phone_center[0] <= person_box[2] and person_box[1] - 0.05 * h <= phone_center[1] <= bottom)


class PhonePersonAssociator:
    def __init__(self, config: AssociationConfig | None = None) -> None:
        self.config = config or AssociationConfig()
        self._last_owner: dict[int, int] = {}  # phone slot -> track id (continuity)

    def reset(self) -> None:
        self._last_owner.clear()

    def associate(
        self,
        people: Sequence[PersonRef],
        phones: Sequence[tuple[BoxArray, float]],
    ) -> AssociationResult:
        cfg = self.config
        if not phones:
            return AssociationResult([], [], 0)
        if not people:
            return AssociationResult([], list(range(len(phones))), 0)

        # Score every (phone, person) pair.
        scored: list[tuple[float, int, int, dict]] = []
        for pi, (pbox, pconf) in enumerate(phones):
            pc = center(pbox)
            phone_area = area(pbox)
            for person in people:
                pbox_person = person.box
                p_area = area(pbox_person)
                if p_area <= 0:
                    continue
                expanded = expand(pbox_person, cfg.expand_ratio)
                contain = containment_ratio(pbox, expanded)
                dist = np.hypot(pc[0] - (pbox_person[0] + pbox_person[2]) / 2,
                                pc[1] - (pbox_person[1] + pbox_person[3]) / 2)
                norm_dist = dist / max(diagonal(pbox_person), 1e-6)
                if norm_dist > cfg.max_distance_ratio:
                    continue
                proximity = max(0.0, 1.0 - norm_dist / cfg.max_distance_ratio)
                zone, in_zone = _zone_score(pbox_person, pc, cfg)
                in_head = _head_zone(pbox_person, pc, cfg)
                size_ratio = phone_area / p_area
                size_ok = cfg.min_phone_area_ratio <= size_ratio <= cfg.max_phone_area_ratio
                if not size_ok:
                    continue
                continuity = 1.0 if self._last_owner.get(pi) == person.track_id else 0.0
                raw = (
                    0.44 * contain
                    + 0.24 * proximity
                    + 0.22 * zone
                    + 0.10 * (1.0 if in_head else 0.0)
                )
                raw = raw * (0.7 + 0.3 * float(pconf))
                raw += cfg.continuity_bonus * continuity
                raw = float(min(1.0, raw))
                scored.append(
                    (
                        raw,
                        pi,
                        person.track_id,
                        {
                            "containment": round(contain, 3),
                            "proximity": round(float(proximity), 3),
                            "in_use_zone": bool(in_zone),
                            "in_head_zone": bool(in_head),
                            "size_ratio": round(float(size_ratio), 4),
                            "continuity": bool(continuity),
                        },
                    )
                )

        scored.sort(key=lambda s: s[0], reverse=True)
        used_people: set[int] = set()
        used_phones: set[int] = set()
        people_by_id = {p.track_id: p for p in people}
        associations: list[PhoneAssociation] = []
        ambiguous = 0

        # Greedy one-phone-to-one-person assignment, best evidence first.
        for score, pi, tid, evidence in scored:
            if pi in used_phones or tid in used_people:
                continue
            pbox, pconf = phones[pi]
            # Second-best alternative owner for the same phone (ambiguity test).
            runner_up = max(
                (s for s in scored if s[1] == pi and s[2] != tid and s[2] not in used_people),
                default=(0.0, -1, -1, {}),
                key=lambda s: s[0],
            )[0]
            is_ambiguous = (score - runner_up) < cfg.ambiguity_margin or score < 0.30
            used_phones.add(pi)
            used_people.add(tid)
            associations.append(
                PhoneAssociation(
                    phone_index=pi,
                    phone_box=pbox,
                    phone_conf=float(pconf),
                    track_id=tid,
                    track_label=people_by_id[tid].label,
                    score=score,
                    ambiguous=is_ambiguous,
                    second_score=float(runner_up),
                    evidence=evidence,
                )
            )
            if is_ambiguous:
                ambiguous += 1
            self._last_owner[pi] = tid

        # Drop continuity entries for phones that vanished.
        self._last_owner = {k: v for k, v in self._last_owner.items() if k in used_phones}
        unassigned = [i for i in range(len(phones)) if i not in used_phones]
        return AssociationResult(associations, unassigned, ambiguous)


def dedupe_phone_boxes(phones: Iterable[tuple[BoxArray, float]], iou_threshold: float = 0.6) -> list[tuple[BoxArray, float]]:
    """Remove near-duplicate phone detections (NMS already runs, this is a guard)."""
    from app.vision.geometry import iou

    kept: list[tuple[BoxArray, float]] = []
    for box, conf in sorted(phones, key=lambda p: p[1], reverse=True):
        if all(iou(box, k[0]) < iou_threshold for k in kept):
            kept.append((box, conf))
    return kept
