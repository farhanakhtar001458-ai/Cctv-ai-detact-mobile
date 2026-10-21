#!/usr/bin/env python
"""Benchmark the detection pipeline on *your* hardware.

Measures, per configuration:

* end-to-end frame latency (resize + detect + track + associate + temporal)
* sustained inference FPS (single thread, no preview encoding)
* end-to-end alert latency contribution (frames until a sustained state)
* CPU / RAM usage, optional GPU utilisation (via ``nvidia-smi`` if present)
* behaviour as the number of simultaneously visible people grows to 50

This is how you validate the "50 people" requirement; do not assume it.

Examples
--------
    python scripts/benchmark.py --source sim://classroom --frames 300
    python scripts/benchmark.py --video samples/classroom.mp4 --resolutions 640x360,960x540,1280x720
    python scripts/benchmark.py --source rtsp://cam/stream --frames 200 --camera-name "Room 101"
    python scripts/benchmark.py --source sim://classroom --people 10,25,50 --out benchmarks/

Outputs a Markdown + JSON report under ``benchmarks/``.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vision.detector import detector_manager  # noqa: E402
from app.vision.pipeline import AnalyzerConfig, FrameAnalyzer  # noqa: E402


def parse_resolutions(value: str) -> list[tuple[int, int]]:
    out = []
    for item in value.split(","):
        width, _, height = item.lower().partition("x")
        out.append((int(width), int(height)))
    return out


def gpu_info() -> dict[str, Any]:
    try:
        import torch

        if torch.cuda.is_available():
            return {
                "available": True,
                "device": torch.cuda.get_device_name(0),
                "memory_gb": round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1),
            }
        return {"available": False, "device": "cpu"}
    except Exception:  # noqa: BLE001
        return {"available": False, "device": "unknown"}


def gpu_utilisation() -> float | None:
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if output.returncode == 0:
            return float(output.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        pass
    return None


def build_source(kind: str, url: str, width: int, height: int):
    from app.vision.sources import build_source as factory

    # Unpaced reads: we measure analysis throughput, not source pacing.
    return factory(kind, url, fps=30.0, width=width, height=height, pace=False)


def crowd_frame(analyzer: FrameAnalyzer, people: int, width: int, height: int) -> None:
    """Feed N synthetic person boxes into the tracker to measure scaling."""
    import numpy as np

    from app.vision.detector import Detection

    boxes = []
    cols = max(1, int(np.ceil(np.sqrt(people))))
    cell_w = width / cols
    cell_h = height / max(1, int(np.ceil(people / cols)))
    for i in range(people):
        row, col = divmod(i, cols)
        x = col * cell_w + 4
        y = row * cell_h + 4
        boxes.append(
            Detection(
                "person", 0.9,
                np.array([x, y, x + cell_w * 0.7, y + cell_h * 0.8], dtype=np.float32), 0,
            )
        )
    analyzer._detect = lambda frame, scene=None: boxes  # type: ignore[assignment]


def run_benchmark(
    video: str,
    source_kind: str,
    resolution: tuple[int, int],
    frames: int,
    warmup: int,
    people: int | None,
) -> dict[str, Any]:
    import cv2
    import psutil

    width, height = resolution
    config = AnalyzerConfig(tracker_min_hits=1)
    analyzer = FrameAnalyzer(config)

    if source_kind == "synthetic":
        frame = np.random.default_rng(1).integers(40, 180, (height, width, 3), dtype=np.uint8)
        source = None
        frames = max(frames, 30)
    else:
        source = build_source(source_kind, video, width, height)
        source.open()

    if people:
        crowd_frame(analyzer, people, width, height)
        config.phone_detection_enabled = False

    process = psutil.Process()
    cpu_samples: list[float] = []
    mem_samples: list[float] = []
    gpu_samples: list[float] = []
    latencies: list[float] = []
    infer_only: list[float] = []

    def next_frame(index: int) -> np.ndarray:
        if source is None:
            return np.random.default_rng(index).integers(40, 180, (height, width, 3), dtype=np.uint8)
        frame = source.read()
        if frame is None:
            raise RuntimeError("frame read failed")
        return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)

    started = time.perf_counter()
    for i in range(warmup + frames):
        frame = next_frame(i)
        t0 = time.perf_counter()
        analysis = analyzer.analyze(frame)
        elapsed = time.perf_counter() - t0
        if i >= warmup:
            latencies.append(elapsed * 1000)
            infer_only.append(analysis.inference_ms)
            if i % 20 == 0:
                cpu_samples.append(psutil.cpu_percent(interval=None))
                mem_samples.append(process.memory_info().rss / (1024 * 1024))
                util = gpu_utilisation()
                if util is not None:
                    gpu_samples.append(util)
    total = time.perf_counter() - started
    if source is not None:
        source.release()

    processed = max(1, len(latencies))
    return {
        "resolution": f"{width}x{height}",
        "people": people,
        "frames": processed,
        "wall_seconds": round(total, 2),
        "pipeline_fps": round(processed / total, 2),
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 2),
            "p50": round(statistics.median(latencies), 2),
            "p95": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 2),
            "max": round(max(latencies), 2),
        },
        "inference_ms_mean": round(statistics.mean(infer_only), 2),
        "cpu_percent_mean": round(statistics.mean(cpu_samples), 1) if cpu_samples else None,
        "ram_mb_mean": round(statistics.mean(mem_samples), 1) if mem_samples else None,
        "gpu_util_percent_mean": round(statistics.mean(gpu_samples), 1) if gpu_samples else None,
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Hardware benchmark - {report['generated_at']}",
        "",
        f"* Host: `{report['host']['platform']}` · Python {report['host']['python']} · "
        f"{report['host']['cpu_count']} CPU cores · {report['host']['ram_gb']} GB RAM",
        f"* GPU: {report['host']['gpu'].get('device')}",
        f"* Detector: `{report['detector']['backend']}` · model `{report['detector']['model']}` · "
        f"device `{report['detector']['device']}`",
        "",
    ]
    if report["detector"].get("degraded"):
        lines += ["> **Degraded detector.** " + str(report["detector"].get("degraded_reason")), ""]
    if not report["detector"].get("phone_capable"):
        lines += ["> **No phone detections**: this backend cannot detect mobile phones, so phone-use "
                  "alerting is disabled in this benchmark.", ""]
    lines += [
        "| Resolution | People | Frames | Pipeline FPS | Latency p50 | Latency p95 | Inference mean | CPU % | RAM MB | GPU % |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["runs"]:
        lines.append(
            f"| {row['resolution']} | {row['people'] or '-'} | {row['frames']} | {row['pipeline_fps']} | "
            f"{row['latency_ms']['p50']} ms | {row['latency_ms']['p95']} ms | {row['inference_ms_mean']} ms | "
            f"{row['cpu_percent_mean'] if row['cpu_percent_mean'] is not None else '-'} | "
            f"{row['ram_mb_mean'] if row['ram_mb_mean'] is not None else '-'} | "
            f"{row['gpu_util_percent_mean'] if row['gpu_util_percent_mean'] is not None else '-'} |"
        )
    lines += [
        "",
        "## How to read this",
        "",
        "* **Pipeline FPS** is analysis throughput with preview encoding disabled. The dashboard "
        "preview runs at `snapshot_fps` (default 8) independently.",
        "* For N cameras, required throughput is `N x configured_fps`. If pipeline FPS is below that, "
        "increase `frame_sample`, lower the inference resolution, or add a second worker host.",
        "* Phone-use alerts are gated by persistence (default 2 s of sustained evidence), so the "
        "**alert latency** is at least `min_sustained_seconds` plus the pipeline latency above.",
        "* Precision/recall are *not* measured here - see `docs/EVALUATION.md` and "
        "`scripts/evaluate.py` for accuracy on your own labelled footage.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark the classroom AI pipeline")
    parser.add_argument("--video", default="", help="Video file or stream URL (omit for synthetic frames)")
    parser.add_argument(
        "--source", default="auto",
        help="auto|file|rtsp|http|webcam|simulated|synthetic (auto picks by URL)",
    )
    parser.add_argument("--frames", type=int, default=150)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--resolutions", default="640x360,960x540,1280x720")
    parser.add_argument("--people", default="", help="Comma separated crowd sizes, e.g. 10,25,50")
    parser.add_argument("--out", default="../benchmarks")
    args = parser.parse_args()

    import psutil

    kind = args.source
    if kind == "auto":
        if not args.video:
            kind = "synthetic"
        elif args.video.startswith("sim://"):
            kind = "simulated"
        elif args.video.startswith(("rtsp://", "rtsps://")):
            kind = "rtsp"
        elif args.video.startswith(("http://", "https://")):
            kind = "http"
        elif args.video.isdigit():
            kind = "webcam"
        else:
            kind = "file"

    status = detector_manager.load()
    print(f"Detector: {status.backend} / {status.model} / {status.device} (phone_capable={status.phone_capable})")
    if status.degraded:
        print(f"WARNING: {status.extra.get('degraded_reason')}")

    resolutions = parse_resolutions(args.resolutions)
    people_counts = [int(p) for p in args.people.split(",") if p.strip()] if args.people else [None]

    runs = []
    for people in people_counts:
        for resolution in resolutions:
            print(f"  running {resolution[0]}x{resolution[1]} people={people or 'real'} ...", flush=True)
            try:
                runs.append(run_benchmark(args.video, kind, resolution, args.frames, args.warmup, people))
            except Exception as exc:  # noqa: BLE001
                print(f"    failed: {exc}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": psutil.cpu_count(logical=True),
            "ram_gb": round(psutil.virtual_memory().total / 1e9, 1),
            "gpu": gpu_info(),
        },
        "detector": status.as_dict(),
        "source": {"kind": kind, "video": args.video or "synthetic"},
        "runs": runs,
    }
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    (out_dir / f"benchmark_{stamp}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = to_markdown(report)
    (out_dir / f"benchmark_{stamp}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"Report written to {out_dir}/benchmark_{stamp}.md (+ .json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
