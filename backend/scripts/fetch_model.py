#!/usr/bin/env python
"""Provision detector weights for offline or air-gapped deployments.

The default configuration uses Ultralytics YOLO11n (COCO), which includes the
``person`` and ``cell phone`` classes needed by this project.

Usage
-----
    # Let Ultralytics download the official weights (needs internet):
    python scripts/fetch_model.py

    # Copy a pre-approved model you already have (verified with SHA-256):
    python scripts/fetch_model.py --source /media/usb/yolo11n.pt --sha256 <digest>

    # Verify what is installed:
    python scripts/fetch_model.py --check

Notes
-----
* Only use weights whose licence permits your deployment (Ultralytics YOLO is
  AGPL-3.0 by default; Ultralytics offers a commercial licence, and Apache-2.0
  alternatives such as YOLOX or RT-DETR can be exported to ONNX).
* The file is written to ``$SCAI_MODEL_PATH`` (default ``yolo11n.pt`` in the
  backend working directory) or to ``--dest``.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

KNOWN_MODELS = {
    "yolo11n.pt": "YOLO11 nano - fastest, good for 50 people on a mid GPU",
    "yolo11s.pt": "YOLO11 small - better small-object (phone) recall",
    "yolo11m.pt": "YOLO11 medium - best accuracy, needs a GPU",
    "yolov8n.pt": "YOLOv8 nano - widely validated fallback",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check(path: Path) -> int:
    if not path.exists():
        print(f"[MISSING] {path}")
        return 1
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb < 0.1:
        print(f"[INVALID] {path} is only {size_mb:.3f} MB - likely a Git LFS pointer or an error page")
        print("          Delete it and re-run without --check, or copy real weights with --source.")
        return 1
    print(f"[OK] {path} ({size_mb:.1f} MB, sha256={sha256(path)})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision YOLO weights for the classroom monitor")
    parser.add_argument("--dest", default=None, help="Destination path (default: SCAI_MODEL_PATH or yolo11n.pt)")
    parser.add_argument("--source", default=None, help="Local file to copy instead of downloading")
    parser.add_argument("--sha256", default=None, help="Expected SHA-256 of the source file")
    parser.add_argument("--check", action="store_true", help="Only verify the installed weights")
    parser.add_argument("--model", default=None, help="Official model name to download (default: from env)")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.core.config import settings  # noqa: E402

    dest = Path(args.dest or settings.model_path)
    if not dest.is_absolute():
        dest = Path(__file__).resolve().parents[1] / dest

    if args.check:
        status = check(dest)
        print()
        print("Known models:")
        for name, note in KNOWN_MODELS.items():
            print(f"  {name:14s} {note}")
        return status

    if args.source:
        source = Path(args.source).expanduser()
        if not source.exists():
            print(f"ERROR: source not found: {source}")
            return 2
        digest = sha256(source)
        if args.sha256 and digest.lower() != args.sha256.lower():
            print(f"ERROR: SHA-256 mismatch\n  expected {args.sha256}\n  actual   {digest}")
            return 3
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        print(f"Copied {source} -> {dest}\nsha256={digest}")
        return 0

    model_name = args.model or dest.name
    try:
        from ultralytics import YOLO
    except ImportError:
        print("ERROR: ultralytics is not installed. pip install -r requirements-vision.txt")
        return 4

    print(f"Downloading/loading {model_name} via Ultralytics (this needs internet on first run)...")
    try:
        model = YOLO(model_name)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: could not fetch weights: {exc}")
        print(
            "\nAir-gapped option:\n"
            "  1. On a connected machine: pip install ultralytics && yolo export model=yolo11n.pt format=onnx\n"
            "  2. Copy yolo11n.pt (or the ONNX file) to this host\n"
            "  3. python scripts/fetch_model.py --source /path/yolo11n.pt\n"
            "  4. For ONNX set SCAI_DETECTOR_BACKEND=onnx and SCAI_ONNX_MODEL_PATH=/path/model.onnx"
        )
        return 5

    names = dict(getattr(model, "names", {}))
    required = {0: "person", 67: "cell phone"}
    for class_id, label in required.items():
        if str(names.get(class_id, "")).lower() != label:
            print(f"WARNING: class {class_id} is {names.get(class_id)!r}, expected {label!r}")
    source_path = Path(getattr(model, "ckpt_path", "") or "")
    if source_path.exists() and source_path.resolve() != dest.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest)
    return check(dest)


if __name__ == "__main__":
    raise SystemExit(main())
