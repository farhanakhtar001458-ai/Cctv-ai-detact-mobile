#!/usr/bin/env python
"""Export a YOLO checkpoint to ONNX (or TensorRT) for faster/leaner inference.

    python scripts/export_onnx.py --model runs/classroom/yolo_classroom/weights/best.pt
    python scripts/export_onnx.py --model yolo11n.pt --engine trt --half

Then point the backend at it (ONNX Runtime only, no torch needed at runtime):

    SCAI_DETECTOR_BACKEND=onnx
    SCAI_ONNX_MODEL_PATH=/opt/models/classroom.onnx
    SCAI_MODEL_IMGSZ=640
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Export YOLO weights to ONNX / TensorRT")
    parser.add_argument("--model", required=True)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--engine", default="onnx", choices=["onnx", "engine", "openvino", "torchscript"])
    parser.add_argument("--half", action="store_true", help="FP16 (GPU only)")
    parser.add_argument("--out", default=None, help="Copy the exported file to this path")
    parser.add_argument("--dynamic", action="store_true")
    parser.add_argument("--simplify", action="store_true", default=True)
    args = parser.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        print("ERROR: ultralytics not installed")
        return 2

    model_path = Path(args.model).expanduser()
    if not model_path.exists():
        print(f"ERROR: {model_path} not found")
        return 2

    model = YOLO(str(model_path))
    exported = model.export(
        format=args.engine,
        imgsz=args.imgsz,
        half=args.half,
        dynamic=args.dynamic,
        simplify=args.simplify,
    )
    exported_path = Path(str(exported))
    print(f"Exported: {exported_path}")

    if args.out:
        destination = Path(args.out).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exported_path, destination)
        print(f"Copied to {destination}")
        print(
            "\nConfigure the backend:\n"
            f"  SCAI_DETECTOR_BACKEND={'onnx' if args.engine == 'onnx' else 'auto'}\n"
            f"  SCAI_ONNX_MODEL_PATH={destination}\n"
            f"  SCAI_MODEL_IMGSZ={args.imgsz}\n"
            f"  SCAI_MODEL_HALF={'true' if args.half else 'false'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
