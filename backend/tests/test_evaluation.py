"""Detection metric implementation (used by scripts/evaluate.py)."""

from __future__ import annotations

import numpy as np

from app.vision.evaluation import (
    GroundTruth,
    Prediction,
    average_precision,
    build_confusion,
    match_image,
    render_confusion_svg,
    summarise,
    sweep_thresholds,
)


def b(x1, y1, x2, y2):
    return np.array([x1, y1, x2, y2], dtype=np.float32)


def test_match_image_counts_tp_fp_fn():
    preds = [Prediction(b(10, 10, 60, 60), 0.9, 0), Prediction(b(200, 200, 260, 260), 0.8, 67)]
    gts = [GroundTruth(b(10, 10, 60, 60), 0)]
    result = match_image(preds, gts)
    assert result.true_positives == 1
    assert result.false_positives == 1
    assert result.false_negatives == 0


def test_missed_detection_is_false_negative():
    result = match_image([], [GroundTruth(b(10, 10, 60, 60), 0)])
    assert result.false_negatives == 1
    summary = summarise([result])
    assert summary.recall == 0.0


def test_duplicate_predictions_are_false_positives():
    preds = [Prediction(b(10, 10, 60, 60), 0.9, 0), Prediction(b(11, 11, 61, 61), 0.7, 0)]
    gts = [GroundTruth(b(10, 10, 60, 60), 0)]
    result = match_image(preds, gts)
    assert result.true_positives == 1
    assert result.false_positives == 1


def test_summary_metrics():
    results = [
        match_image([Prediction(b(0, 0, 10, 10), 0.9, 0)], [GroundTruth(b(0, 0, 10, 10), 0)]),
        match_image([Prediction(b(0, 0, 10, 10), 0.9, 0)], []),
        match_image([], [GroundTruth(b(0, 0, 10, 10), 0)]),
    ]
    summary = summarise(results)
    assert summary.true_positives == 1
    assert summary.false_positives == 1
    assert summary.false_negatives == 1
    assert summary.precision == 0.5
    assert summary.recall == 0.5
    assert summary.f1 == 0.5


def test_average_precision_perfect_detector():
    preds = [[Prediction(b(0, 0, 10, 10), 0.9, 0), Prediction(b(20, 20, 30, 30), 0.8, 0)]]
    gts = [[GroundTruth(b(0, 0, 10, 10), 0), GroundTruth(b(20, 20, 30, 30), 0)]]
    ap = average_precision(preds, gts)
    assert ap["ap50"] == 1.0


def test_average_precision_penalises_false_positives():
    preds = [[Prediction(b(0, 0, 10, 10), 0.9, 0), Prediction(b(300, 300, 310, 310), 0.95, 0)]]
    gts = [[GroundTruth(b(0, 0, 10, 10), 0)]]
    ap = average_precision(preds, gts)
    assert 0.4 <= ap["ap50"] <= 0.7


def test_confusion_matrix_includes_background():
    per_image = [
        (
            [Prediction(b(0, 0, 10, 10), 0.9, 0), Prediction(b(50, 50, 60, 60), 0.8, 67)],
            [GroundTruth(b(0, 0, 10, 10), 0), GroundTruth(b(50, 50, 60, 60), 0)],
        )
    ]
    confusion = build_confusion(per_image, ["person", "cell phone"], class_id_map={0: 0, 67: 1})
    matrix = confusion["matrix"]
    assert confusion["labels"] == ["person", "cell phone", "background"]
    assert matrix[0][0] == 1  # correct person
    assert matrix[0][1] == 1  # person labeled as cell phone
    assert matrix[2][1] == 0


def test_threshold_sweep_monotonic_recall():
    per_image = [
        (
            [Prediction(b(0, 0, 10, 10), 0.9, 0), Prediction(b(100, 100, 110, 110), 0.45, 0)],
            [GroundTruth(b(0, 0, 10, 10), 0), GroundTruth(b(100, 100, 110, 110), 0)],
        )
    ]
    sweep = sweep_thresholds(per_image, class_id=0, thresholds=(0.3, 0.5, 0.8))
    recalls = [row["recall"] for row in sweep]
    assert recalls[0] >= recalls[1] >= recalls[2]


def test_svg_render_is_valid_markup():
    svg = render_confusion_svg([[3, 0, 1], [0, 5, 2], [1, 1, 0]], ["person", "cell phone", "background"])
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "Confusion matrix" in svg
    assert "cell phone" in svg
