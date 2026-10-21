#!/usr/bin/env python
"""Fine-tune a YOLO detector for classroom phone-use detection.

The pretrained COCO model already knows ``person`` and ``cell phone``; fine
tuning on your authorised classroom footage is what makes it useful for small,
oblique, seated, low-light phones.

Recommended workflow (see docs/TRAINING.md for the full protocol):

    1. python scripts/prepare_dataset.py --src datasets/raw --out datasets/classroom
    2. python scripts/train.py --data datasets/classroom/data.yaml --model yolo11n.pt --epochs 80
    3. python scripts/evaluate.py --data datasets/classroom --split test
    4. Copy best.pt to the server and set SCAI_MODEL_PATH=/path/best.pt

Hardware notes
--------------
* CPU-only training is impractical for classroom-scale data; a single 8-12 GB
  GPU handles 640px training with batch 8-16.
* Use ``--imgsz 960`` if phones are small in frame and you have the VRAM.
* Ultralytics YOLO is AGPL-3.0 (or commercial). Check licensing before
  deploying in a commercial product.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Fine-tune YOLO for classroom phone detection")
    parser.add_argument("--data", required=True, help="data.yaml from prepare_dataset.py")
    parser.add_argument("--model", default="yolo11n.pt", help="Base weights (yolo11n/s/m, yolov8n...)")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default=None, help="cpu | 0 | 0,1 (default: auto)")
    parser.add_argument("--patience", type=int, default=20, help="Early-stopping patience")
    parser.add_argument("--project", default="runs/classroom")
    parser.add_argument("--name", default="yolo_classroom")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    data_path = Path(args.data).expanduser().resolve()
    if not data_path.exists():
        print(f"ERROR: dataset config not found: {data_path}")
        return 2

    try:
        from ultralytics import YOLO
    except ImportError:
        print("ERROR: ultralytics not installed. pip install -r requirements-vision.txt")
        return 3

    model = YOLO(args.model)
    print(f"Training {args.model} on {data_path} (epochs={args.epochs}, imgsz={args.imgsz})")
    results = model.train(
        data=str(data_path),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        patience=args.patience,
        project=args.project,
        name=args.name,
        workers=args.workers,
        resume=args.resume,
        exist_ok=True,
        # Classroom-specific augmentation: small phones benefit from mosaic and
        # mild scale jitter; keep flips modest since seat layout is meaningful.
        mosaic=1.0,
        mixup=0.05,
        scale=0.5,
        degrees=3.0,
        fliplr=0.5,
        hsv_h=0.015,
        hsv_s=0.5,
        hsv_v=0.4,
        # Class imbalance: phones are rare relative to people.
        cls=0.7,
        plots=True,
        val=True,
    )
    best = Path(args.project) / args.name / "weights" / "best.pt"
    print("\nTraining complete.")
    print(f"Best weights: {best if best.exists() else '(see run directory)'}")
    print("Validation metrics (mAP50, mAP50-95, precision, recall):")
    try:
        metrics = results.results_dict  # type: ignore[attr-defined]
        for key in ("metrics/mAP50(B)", "metrics/mAP50-95(B)", "metrics/precision(B)", "metrics/recall(B)"):
            if key in metrics:
                print(f"  {key}: {metrics[key]:.4f}")
    except Exception:  # noqa: BLE001
        pass
    print(
        "\nNext steps:\n"
        "  1. python scripts/evaluate.py --data <dataset> --split test --model <best.pt>\n"
        "  2. Review false positives (books, calculators, remotes) and add them as negatives\n"
        "  3. python scripts/export_onnx.py --model <best.pt>  (optional, faster deployment)\n"
        "  4. Set SCAI_MODEL_PATH to best.pt on the monitoring server and reload the model"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
