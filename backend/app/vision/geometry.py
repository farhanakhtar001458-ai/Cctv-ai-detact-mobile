"""Small, dependency-light bounding box geometry helpers.

Boxes are ``numpy`` arrays in ``[x1, y1, x2, y2]`` (pixel) order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

BoxArray = np.ndarray


def as_box(values: Sequence[float]) -> BoxArray:
    box = np.asarray(values, dtype=np.float32).reshape(4)
    return box


def area(box: BoxArray) -> float:
    w = max(0.0, float(box[2] - box[0]))
    h = max(0.0, float(box[3] - box[1]))
    return w * h


def center(box: BoxArray) -> tuple[float, float]:
    return (float(box[0] + box[2]) / 2.0, float(box[1] + box[3]) / 2.0)


def width(box: BoxArray) -> float:
    return max(0.0, float(box[2] - box[0]))


def height(box: BoxArray) -> float:
    return max(0.0, float(box[3] - box[1]))


def diagonal(box: BoxArray) -> float:
    return float(np.hypot(width(box), height(box)))


def clip(box: BoxArray, w: int, h: int) -> BoxArray:
    return np.array(
        [
            np.clip(box[0], 0, w - 1),
            np.clip(box[1], 0, h - 1),
            np.clip(box[2], 0, w - 1),
            np.clip(box[3], 0, h - 1),
        ],
        dtype=np.float32,
    )


def expand(box: BoxArray, ratio: float) -> BoxArray:
    dx = width(box) * ratio / 2.0
    dy = height(box) * ratio / 2.0
    return np.array([box[0] - dx, box[1] - dy, box[2] + dx, box[3] + dy], dtype=np.float32)


def intersection(a: BoxArray, b: BoxArray) -> float:
    x1 = max(float(a[0]), float(b[0]))
    y1 = max(float(a[1]), float(b[1]))
    x2 = min(float(a[2]), float(b[2]))
    y2 = min(float(a[3]), float(b[3]))
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def iou(a: BoxArray, b: BoxArray) -> float:
    inter = intersection(a, b)
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def iou_matrix(boxes_a: Iterable[BoxArray], boxes_b: Iterable[BoxArray]) -> np.ndarray:
    a = [as_box(x) for x in boxes_a]
    b = [as_box(x) for x in boxes_b]
    out = np.zeros((len(a), len(b)), dtype=np.float32)
    for i, ba in enumerate(a):
        for j, bb in enumerate(b):
            out[i, j] = iou(ba, bb)
    return out


def contains_point(box: BoxArray, point: tuple[float, float]) -> bool:
    return bool(box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3])


def contains_center(outer: BoxArray, inner: BoxArray) -> bool:
    return contains_point(outer, center(inner))


def center_distance(a: BoxArray, b: BoxArray) -> float:
    ax, ay = center(a)
    bx, by = center(b)
    return float(np.hypot(ax - bx, ay - by))


def normalized_center_distance(a: BoxArray, b: BoxArray) -> float:
    """Distance between centres scaled by the reference box diagonal."""
    diag = diagonal(a)
    if diag <= 0:
        return float("inf")
    return center_distance(a, b) / diag


def containment_ratio(inner: BoxArray, outer: BoxArray) -> float:
    """Fraction of ``inner``'s area that lies inside ``outer``."""
    a = area(inner)
    return intersection(inner, outer) / a if a > 0 else 0.0


def overlap_ratio(box: BoxArray, other: BoxArray) -> float:
    """Fraction of ``box`` covered by ``other`` (used for occlusion estimates)."""
    return containment_ratio(box, other)


@dataclass(frozen=True)
class Box:
    """Lightweight immutable box wrapper for non-numpy call sites / tests."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def array(self) -> BoxArray:
        return np.array([self.x1, self.y1, self.x2, self.y2], dtype=np.float32)

    def scaled(self, fx: float, fy: float) -> "Box":
        return Box(self.x1 * fx, self.y1 * fy, self.x2 * fx, self.y2 * fy)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (int(self.x1), int(self.y1), int(self.x2), int(self.y2))
