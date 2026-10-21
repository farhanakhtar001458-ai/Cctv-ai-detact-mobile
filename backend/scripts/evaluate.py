#!/usr/bin/env python
"""Evaluate detector accuracy on a labelled YOLO-format dataset.

Computes precision / recall / F1 / AP50 and a confusion matrix for the classes
that matter in a classroom (person, cell phone), plus a confidence-threshold
sweep you can use to choose operating points for each camera type.

Usage
-----
    python scripts/evaluate.py --data datasets/classroom/data.yaml --split val
    python scripts/evaluate.py --data datasets/classroom --split test --conf 0.35 \
        --classes person "cell phone" --out reports/eval
    python scripts/evaluate.py --data datasets/classroom --limit 200 --iou 0.5

The dataset is expected in standard YOLO layout:

    datasets/classroom/
      images/{train,val,test}/*.jpg
      labels/{train,val,test}/*.txt      # class cx cy w h (normalised)
      data.yaml                          # names: {0: person, 67: cell phone}

Notes
-----
* Only use footage you are authorised to record. Do not publish evaluation
  reports that contain identifiable students.
* This script measures *detection* quality. Phone-use alert quality (precision
  of the multi-frame state machine) needs labelled alert windows - see
  docs/EVALUATION.md for the protocol.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vision.evaluation import (  # noqa: E402
    GroundTruth,
    Prediction,
    average_precision,
    build_confusion,
    match_image,
    render_confusion_svg,
    summarise,
    sweep_thresholds,
)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def load_yaml_names(data: Path) -> dict[int, str]:
    yaml_path = data if data.suffix in (".yaml", ".yml") else data / "data.yaml"
    names: dict[int, str] = {}
    if yaml_path.exists():
        text = yaml_path.read_text(encoding="utf-8")
        # Tolerate yaml without the PyYAML dependency for simple name maps.
        import re

        block = re.search(r"names\s*:\s*(?:\n((?:\s+[-\w\"'.: ]+\n?)+)|\[(.*?)\])", text)
        if block:
            if block.group(1):
                for line in block.group(1).strip().splitlines():
                    match = re.match(r"\s*(\d+)\s*:\s*[\"']?([^\"'\n#]+)", line.strip())
                    if match:
                        names[int(match.group(1))] = match.group(2).strip()
            elif block.group(2):
                for index, item in enumerate(block.group(2).split(",")):
                    names[index] = item.strip().strip("\"'")
    if not names:
        names = {0: "person", 67: "cell phone"}
    return names


def resolve_split_root(data: Path, split: str) -> Path:
    root = data if data.is_dir() else data.parent
    for candidate in (root / "images" / split, root / split / "images", root / split):
        if candidate.is_dir():
            return candidate
    return root / "images" / split


def labels_dir_for(images_dir: Path) -> Path:
    parts = list(images_dir.parts)
    if "images" in parts:
        index = len(parts) - 1 - parts[::-1].index("images")
        parts[index] = "labels"
        return Path(*parts)
    return images_dir.parent / "labels"


def read_labels(path: Path, width: int, height: int) -> list[GroundTruth]:
    out: list[GroundTruth] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        class_id = int(float(fields[0]))
        cx, cy, w, h = (float(v) for v in fields[1:5])
        x1 = (cx - w / 2) * width
        y1 = (cy - h / 2) * height
        x2 = (cx + w / 2) * width
        y2 = (cy + h / 2) * height
        out.append(GroundTruth(np.array([x1, y1, x2, y2], dtype=np.float32), class_id))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate detector precision/recall on labelled footage")
    parser.add_argument("--data", required=True, help="Dataset root or data.yaml")
    parser.add_argument("--split", default="val")
    parser.add_argument("--model", default=None, help="Weights path (default: SCAI_MODEL_PATH)")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold for reported metrics")
    parser.add_argument("--iou", type=float, default=0.5, help="IoU threshold for a match")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--device", default=None)
    parser.add_argument("--classes", nargs="*", default=["person", "cell phone"])
    parser.add_argument("--out", default="reports/evaluation")
    args = parser.parse_args()

    import cv2

    from app.core.config import settings

    data = Path(args.data).expanduser().resolve()
    names_map = load_yaml_names(data)
    name_to_id = {v.lower(): k for k, v in names_map.items()}
    target_ids: dict[int, str] = {}
    for label in args.classes:
        class_id = name_to_id.get(label.lower())
        if class_id is None:
            print(f"WARNING: class '{label}' is not in data.yaml names; skipping")
            continue
        target_ids[class_id] = label
    if not target_ids:
        print("ERROR: none of the requested classes exist in the dataset")
        return 2

    images_dir = resolve_split_root(data, args.split)
    images = sorted(p for p in images_dir.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
    if args.limit:
        images = images[: args.limit]
    if not images:
        print(f"ERROR: no images found in {images_dir}")
        return 2
    labels_dir = labels_dir_for(images_dir)
    print(f"Evaluating {len(images)} images from {images_dir}")

    model_path = args.model or settings.model_path
    model_arg = Path(model_path).expanduser()
    if not model_arg.is_absolute() and model_arg.suffix and not model_arg.exists():
        # Relative weight names are resolved by ultralytics as downloadable
        # assets; fail fast with guidance instead of a long TLS retry loop.
        from app.vision.detector import model_exists

        if not model_exists(str(model_arg)) and not model_exists(settings.model_path):
            print(
                f"ERROR: weights not found: {model_path}\n"
                "       Provision them first:  python scripts/fetch_model.py --source /path/to/best.pt\n"
                "       Or point at an existing file:  --model /opt/models/classroom.pt\n"
                "       (set SCAI_ALLOW_MODEL_AUTODOWNLOAD=true to let ultralytics download)"
            )
            return 3
    try:
        from ultralytics import YOLO

        model = YOLO(model_path)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot load model '{model_path}': {exc}")
        return 3

    per_image: list[tuple[list[Prediction], list[GroundTruth]]] = []
    for index, image_path in enumerate(images, start=1):
        frame = cv2.imread(str(image_path))
        if frame is None:
            print(f"  skipping unreadable image {image_path.name}")
            continue
        height, width = frame.shape[:2]
        gt_path = labels_dir / f"{image_path.stem}.txt"
        ground_truths = [gt for gt in read_labels(gt_path, width, height) if gt.class_id in target_ids]

        results = model.predict(
            frame, imgsz=args.imgsz, conf=min(0.05, args.conf), verbose=False,
            device=args.device or None,
        )
        predictions: list[Prediction] = []
        if results:
            boxes = results[0].boxes
            if boxes is not None:
                for box, conf, cls in zip(
                    boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy(), boxes.cls.cpu().numpy().astype(int)
                ):
                    if int(cls) in target_ids:
                        predictions.append(Prediction(np.asarray(box, dtype=np.float32), float(conf), int(cls)))
        per_image.append((predictions, ground_truths))
        if index % 25 == 0:
            print(f"  {index}/{len(images)} images")

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": str(data),
        "split": args.split,
        "images": len(per_image),
        "model": str(model_path),
        "iou_threshold": args.iou,
        "confidence_threshold": args.conf,
        "classes": {},
        "threshold_sweep": {},
    }

    class_id_map = {class_id: position for position, class_id in enumerate(target_ids)}
    for class_id, label in target_ids.items():
        results = []
        for predictions, ground_truths in per_image:
            selected = [p for p in predictions if p.class_id == class_id and p.conf >= args.conf]
            gts = [g for g in ground_truths if g.class_id == class_id]
            results.append(match_image(selected, gts, args.iou))
        summary = summarise(results)
        ap = average_precision(
            [[p for p in preds if p.class_id == class_id] for preds, _ in per_image],
            [[g for g in gts if g.class_id == class_id] for _, gts in per_image],
            args.iou,
        )
        report["classes"][label] = {**summary.as_dict(), **ap}
        report["threshold_sweep"][label] = sweep_thresholds(per_image, class_id, iou_threshold=args.iou)

    confusion = build_confusion(
        per_image, list(target_ids.values()), args.iou, class_id_map=class_id_map
    )
    report["confusion"] = confusion

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    (out_dir / f"evaluation_{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    svg = render_confusion_svg(confusion["matrix"], confusion["labels"], f"Confusion matrix ({args.split})")
    (out_dir / f"confusion_{stamp}.svg").write_text(svg, encoding="utf-8")

    lines = [
        f"# Detection evaluation - {args.split} split",
        "",
        f"* Model: `{model_path}`",
        f"* Dataset: `{data}` ({len(per_image)} images)",
        f"* Match IoU: {args.iou} · confidence threshold: {args.conf}",
        "",
        "| Class | Precision | Recall | F1 | AP50 | TP | FP | FN |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for label, values in report["classes"].items():
        lines.append(
            f"| {label} | {values['precision']:.3f} | {values['recall']:.3f} | {values['f1']:.3f} | "
            f"{values['ap50']:.3f} | {values['tp']} | {values['fp']} | {values['fn']} |"
        )
    lines += ["", "## Confidence sweep", "", "| Class | Threshold | Precision | Recall | F1 |", "| --- | --- | --- | --- | --- |"]
    for label, rows in report["threshold_sweep"].items():
        for row in rows:
            lines.append(
                f"| {label} | {row['threshold']:.2f} | {row['precision']:.3f} | {row['recall']:.3f} | {row['f1']:.3f} |"
            )
    lines += [
        "",
        "## Confusion matrix",
        "",
        f"Rows = ground truth, columns = prediction, last row/column = background (misses / false positives). "
        f"See `confusion_{stamp}.svg`.",
        "",
        "```",
        "        " + "  ".join(f"{name[:10]:>10s}" for name in confusion["labels"]),
    ]
    for label, row in zip(confusion["labels"], confusion["matrix"]):
        lines.append(f"{label[:10]:>10s}" + "".join(f"{value:>12d}" for value in row))
    lines += ["```", ""]
    markdown = "\n".join(lines)
    (out_dir / f"evaluation_{stamp}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"Report written to {out_dir}/evaluation_{stamp}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
