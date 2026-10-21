"""Phone <-> person association (stage 3)."""

from __future__ import annotations

import numpy as np

from app.vision.association import PersonRef, PhonePersonAssociator, dedupe_phone_boxes


def box(x1, y1, x2, y2):
    return np.array([x1, y1, x2, y2], dtype=np.float32)


PERSON_A = PersonRef(1, "Person-01", box(100, 100, 170, 260))
PERSON_B = PersonRef(2, "Person-02", box(400, 100, 470, 260))


def test_phone_in_use_zone_is_assigned_to_that_person():
    assoc = PhonePersonAssociator()
    phone = box(130, 150, 150, 180)  # inside person A's upper body
    result = assoc.associate([PERSON_A, PERSON_B], [(phone, 0.9)])
    assert len(result.associations) == 1
    match = result.associations[0]
    assert match.track_id == 1
    assert match.evidence["in_use_zone"] is True
    assert match.score > 0.5
    assert not match.ambiguous


def test_same_phone_is_never_given_to_two_people():
    assoc = PhonePersonAssociator()
    phone = box(150, 150, 168, 180)
    result = assoc.associate([PERSON_A, PERSON_B], [(phone, 0.9)])
    assert len(result.associations) == 1
    assert len({a.track_id for a in result.associations}) == 1


def test_far_away_phone_is_unassigned():
    assoc = PhonePersonAssociator()
    phone = box(700, 400, 718, 430)
    result = assoc.associate([PERSON_A, PERSON_B], [(phone, 0.9)])
    assert result.associations == []
    assert result.unassigned_phones == [0]


def test_ambiguous_assignment_between_neighbours_is_flagged():
    assoc = PhonePersonAssociator()
    left = PersonRef(1, "Person-01", box(100, 100, 170, 260))
    right = PersonRef(2, "Person-02", box(165, 100, 235, 260))  # overlapping
    phone = box(160, 170, 176, 190)  # on the boundary between the two
    result = assoc.associate([left, right], [(phone, 0.85)])
    assert len(result.associations) == 1
    assert result.associations[0].ambiguous is True
    assert result.ambiguous_pairs == 1


def test_implausible_phone_size_is_rejected():
    assoc = PhonePersonAssociator()
    tiny = box(130, 150, 131, 151)  # less than min area ratio of the person box
    result = assoc.associate([PERSON_A], [(tiny, 0.9)])
    assert result.associations == []


def test_continuity_bonus_prefers_previous_owner():
    assoc = PhonePersonAssociator()
    phone = box(168, 150, 186, 180)  # between A and B? use near A
    first = assoc.associate([PERSON_A, PERSON_B], [(phone, 0.9)])
    owner = first.associations[0].track_id
    second = assoc.associate([PERSON_A, PERSON_B], [(phone, 0.9)])
    assert second.associations[0].track_id == owner
    assert second.associations[0].evidence["continuity"] is True


def test_no_people_means_all_phones_unassigned():
    assoc = PhonePersonAssociator()
    result = assoc.associate([], [(box(0, 0, 10, 10), 0.9), (box(50, 50, 60, 60), 0.8)])
    assert result.unassigned_phones == [0, 1]


def test_dedupe_phone_boxes_keeps_highest_confidence():
    a = (box(10, 10, 30, 40), 0.6)
    b = (box(11, 11, 31, 41), 0.9)
    kept = dedupe_phone_boxes([a, b])
    assert len(kept) == 1
    assert kept[0][1] == 0.9
