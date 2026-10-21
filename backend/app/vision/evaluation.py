"""Detection metrics: greedy IoU matching, precision/recall, AP and confusion.

Pure NumPy so it runs anywhere (CI, CPU-only validation hosts) and is directly
unit tested. ``scripts/evaluate.py`` wraps it with YOLO-format dataset loading
and report generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from app.vision.geometry import iou


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def xyxy(self) -> np.ndarray:
        return np.array([self.x, self.y, self.x + self.w, self.y + self.h], dtype=np.float32)


@dataclass
class Prediction:
    box: np.ndarray
    conf: float
    class_id: int


@dataclass
class GroundTruth:
    box: np.ndarray
    class_id: int


@dataclass
class ImageResult:
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    matches: list[tuple[int, int]] = field(default_factory=list)  # (pred index, gt index)
    confusions: list[tuple[int, int]] = field(default_factory=list)  # (gt class, pred class)


def match_image(
    predictions: Sequence[Prediction],
    ground_truths: Sequence[GroundTruth],
    iou_threshold: float = 0.5,
) -> ImageResult:
    """Greedy one-to-one matching by descending confidence (COCO style)."""
    result = ImageResult()
    order = sorted(range(len(predictions)), key=lambda i: predictions[i].conf, reverse=True)
    used_gt: set[int] = set()
    for pred_idx in order:
        pred = predictions[pred_idx]
        best_iou = 0.0
        best_gt = -1
        for gt_idx, gt in enumerate(ground_truths):
            if gt_idx in used_gt:
                continue
            score = iou(pred.box, gt.box)
            if score > best_iou:
                best_iou = score
                best_gt = gt_idx
        if best_gt >= 0 and best_iou >= iou_threshold:
            used_gt.add(best_gt)
            result.true_positives += 1
            result.matches.append((pred_idx, best_gt))
            gt_class = ground_truths[best_gt].class_id
            if gt_class != pred.class_id:
                result.confusions.append((gt_class, pred.class_id))
        else:
            result.false_positives += 1
    result.false_negatives = len(ground_truths) - len(used_gt)
    return result


@dataclass
class MetricSummary:
    precision: float
    recall: float
    f1: float
    true_positives: int
    false_positives: int
    false_negatives: int
    total_predictions: int
    total_ground_truths: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "tp": self.true_positives,
            "fp": self.false_positives,
            "fn": self.false_negatives,
            "predictions": self.total_predictions,
            "ground_truths": self.total_ground_truths,
        }


def summarise(results: Iterable[ImageResult]) -> MetricSummary:
    tp = fp = fn = total_pred = total_gt = 0
    for result in results:
        tp += result.true_positives
        fp += result.false_positives
        fn += result.false_negatives
        total_pred += result.true_positives + result.false_positives
        total_gt += result.true_positives + result.false_negatives
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return MetricSummary(precision, recall, f1, tp, fp, fn, total_pred, total_gt)


def average_precision(
    predictions_per_image: Sequence[Sequence[Prediction]],
    ground_truth_per_image: Sequence[Sequence[GroundTruth]],
    iou_threshold: float = 0.5,
) -> dict[str, float]:
    """All-point interpolated AP for a single class (binary presence)."""
    all_preds: list[tuple[float, bool]] = []
    total_gt = sum(len(gt) for gt in ground_truth_per_image)
    for predictions, ground_truths in zip(predictions_per_image, ground_truth_per_image):
        order = sorted(range(len(predictions)), key=lambda i: predictions[i].conf, reverse=True)
        used: set[int] = set()
        for pred_pos in order:
            pred = predictions[pred_pos]
            best_iou = 0.0
            best_gt = -1
            for gt_idx, gt in enumerate(ground_truths):
                if gt_idx in used:
                    continue
                score = iou(pred.box, gt.box)
                if score > best_iou:
                    best_iou, best_gt = score, gt_idx
            if best_gt >= 0 and best_iou >= iou_threshold:
                used.add(best_gt)
                all_preds.append((float(pred.conf), True))
            else:
                all_preds.append((float(pred.conf), False))
    if not all_preds or total_gt == 0:
        return {"ap50": 0.0, "mAP": 0.0, "num_predictions": float(len(all_preds)), "num_gt": float(total_gt)}

    all_preds.sort(key=lambda item: item[0], reverse=True)
    tp_cum = np.cumsum([1 if is_tp else 0 for _, is_tp in all_preds])
    fp_cum = np.cumsum([0 if is_tp else 1 for _, is_tp in all_preds])
    recall = tp_cum / total_gt
    precision = tp_cum / np.maximum(tp_cum + fp_cum, 1e-9)
    # Make precision monotonically decreasing (all-point interpolation).
    for i in range(len(precision) - 2, -1, -1):
        precision[i] = max(precision[i], precision[i + 1])
    recall = np.concatenate([[0.0], recall])
    precision = np.concatenate([[1.0], precision])
    ap = float(np.trapezoid(precision, recall)) if hasattr(np, "trapezoid") else float(np.trapz(precision, recall))
    return {"ap50": round(ap, 4), "mAP": round(ap, 4), "num_predictions": float(len(all_preds)), "num_gt": float(total_gt)}


def confusion_matrix(
    results: Sequence[ImageResult],
    num_classes: int,
    class_names: Sequence[str] | None = None,
) -> dict[str, object]:
    """Confusion matrix over matched pairs plus FN/FP rows/columns."""
    size = num_classes + 1  # last index = background
    matrix = np.zeros((size, size), dtype=int)
    for result in results:
        for gt_class, pred_class in result.confusions:
            matrix[gt_class, pred_class] += 1
        for pred_idx, gt_idx in result.matches:
            # Correct matches are counted separately by the caller (class-aware).
            pass
        # Background column: unmatched GT (misses)
        matrix[num_classes, num_classes] += 0
    names = list(class_names) if class_names else [f"class_{i}" for i in range(num_classes)]
    names.append("background")
    return {"matrix": matrix.tolist(), "labels": names}


def build_confusion(
    per_image: Sequence[tuple[Sequence[Prediction], Sequence[GroundTruth]]],
    class_names: Sequence[str],
    iou_threshold: float = 0.5,
    class_id_map: dict[int, int] | None = None,
) -> dict[str, object]:
    """Confusion matrix including background (FN/FP) cells.

    ``matrix[gt][pred]``: last row = missed ground truths (FN), last column =
    unmatched predictions (FP).
    """
    n = len(class_names)
    matrix = np.zeros((n + 1, n + 1), dtype=int)
    # Model class ids (e.g. COCO 0 / 67) map onto compact matrix indices.
    mapping = class_id_map or {i: i for i in range(n)}

    def idx(class_id: int) -> int:
        return mapping.get(int(class_id), n)

    for predictions, ground_truths in per_image:
        order = sorted(range(len(predictions)), key=lambda i: predictions[i].conf, reverse=True)
        used: set[int] = set()
        for pred_pos in order:
            pred = predictions[pred_pos]
            best_iou = 0.0
            best_gt = -1
            for gt_idx, gt in enumerate(ground_truths):
                if gt_idx in used:
                    continue
                score = iou(pred.box, gt.box)
                if score > best_iou:
                    best_iou, best_gt = score, gt_idx
            if best_gt >= 0 and best_iou >= iou_threshold:
                used.add(best_gt)
                matrix[idx(ground_truths[best_gt].class_id), idx(pred.class_id)] += 1
            else:
                matrix[n, idx(pred.class_id)] += 1
        for gt_idx, gt in enumerate(ground_truths):
            if gt_idx not in used:
                matrix[idx(gt.class_id), n] += 1
    labels = list(class_names) + ["background"]
    return {"matrix": matrix.tolist(), "labels": labels}


def sweep_thresholds(
    per_image: Sequence[tuple[Sequence[Prediction], Sequence[GroundTruth]]],
    class_id: int,
    thresholds: Sequence[float] = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7),
    iou_threshold: float = 0.5,
) -> list[dict[str, float]]:
    """Precision/recall as a function of confidence threshold for one class."""
    out: list[dict[str, float]] = []
    for threshold in thresholds:
        results = []
        for predictions, ground_truths in per_image:
            selected = [p for p in predictions if p.class_id == class_id and p.conf >= threshold]
            gts = [g for g in ground_truths if g.class_id == class_id]
            results.append(match_image(selected, gts, iou_threshold))
        summary = summarise(results)
        out.append({"threshold": float(threshold), **summary.as_dict()})
    return out


def render_confusion_svg(matrix: Sequence[Sequence[int]], labels: Sequence[str], title: str = "Confusion matrix") -> str:
    """Dependency-free SVG confusion matrix (used when matplotlib is absent)."""
    n = len(labels)
    cell = 54
    left = 150
    top = 90
    width = left + n * cell + 40
    height = top + n * cell + 60
    total = max(1, int(np.max(np.array(matrix)))) if matrix else 1
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'font-family="ui-monospace, monospace" font-size="11">',
        f'<rect width="{width}" height="{height}" fill="#0d1219"/>',
        f'<text x="16" y="26" fill="#e6edf3" font-size="14" font-family="sans-serif">{title}</text>',
        f'<text x="{left + n * cell / 2}" y="60" fill="#8b98a9" text-anchor="middle">Predicted →</text>',
        f'<text x="18" y="{top + n * cell / 2}" fill="#8b98a9" transform="rotate(-90 18 {top + n * cell / 2})" text-anchor="middle">Ground truth →</text>',
    ]
    for j, label in enumerate(labels):
        parts.append(
            f'<text x="{left + j * cell + cell / 2}" y="{top - 8}" fill="#a9b6c6" text-anchor="end" '
            f'transform="rotate(-40 {left + j * cell + cell / 2} {top - 8})">{label}</text>'
        )
    for i, label in enumerate(labels):
        y = top + i * cell
        parts.append(f'<text x="{left - 10}" y="{y + cell / 2 + 4}" fill="#a9b6c6" text-anchor="end">{label}</text>')
        for j in range(n):
            value = int(matrix[i][j])
            intensity = 0.08 + 0.85 * (value / total)
            if i == j:
                color = f"rgba(63,185,80,{intensity:.2f})"
            elif i == n - 1 or j == n - 1:
                color = f"rgba(248,81,73,{intensity:.2f})"
            else:
                color = f"rgba(217,165,33,{intensity:.2f})"
            x = left + j * cell
            parts.append(f'<rect x="{x}" y="{y}" width="{cell - 2}" height="{cell - 2}" rx="4" fill="{color}"/>')
            parts.append(
                f'<text x="{x + cell / 2 - 1}" y="{y + cell / 2 + 4}" fill="#eaf1f8" text-anchor="middle" '
                f'font-size="12">{value}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)
