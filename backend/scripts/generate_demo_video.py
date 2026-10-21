#!/usr/bin/env python
"""Render a sample classroom video (MP4) for file-based testing.

The generated video is a *simulation*, useful for exercising the ingest ->
detect -> track -> alert path without any CCTV hardware or student footage.

    python scripts/generate_demo_video.py --out samples/sim_classroom.mp4 --seconds 60

Then register it as a camera (source type "file") or run:

    python scripts/benchmark.py --video samples/sim_classroom.mp4 --frames 120
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vision.sources import SimulatedSource  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a simulated classroom video")
    parser.add_argument("--out", default="../samples/sim_classroom.mp4")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--fps", type=float, default=12)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=540)
    args = parser.parse_args()

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    source = SimulatedSource("sim://classroom", width=args.width, height=args.height, fps=args.fps, pace=False)
    source.open()
    writer = cv2.VideoWriter(
        str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (args.width, args.height)
    )
    if not writer.isOpened():
        print("ERROR: OpenCV could not open the MP4 writer (codec missing?)")
        return 2
    total = int(args.seconds * args.fps)
    half = total // 2
    for index in range(total):
        frame = source.read()
        if frame is None:
            break
        writer.write(frame)
        if index == half:
            # Second half is dimmed to exercise the low-light path.
            dim = (frame * 0.35).astype("uint8")
            cv2.putText(dim, "LOW LIGHT SEGMENT", (40, args.height - 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2, cv2.LINE_AA)
        if index >= half:
            writer.write((frame * 0.35).astype("uint8"))
        if index % int(args.fps * 5) == 0:
            print(f"  rendered {index}/{total} frames")
    writer.release()
    source.release()
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"Wrote {out_path} ({size_mb:.1f} MB, {total} frames @ {args.fps} fps)")
    print(
        "Register it in the dashboard: Cameras -> Add camera -> Source type 'Local video file', "
        f"path {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
