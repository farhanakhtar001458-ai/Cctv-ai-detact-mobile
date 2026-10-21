#!/usr/bin/env python
"""Validate and split a classroom phone-detection dataset (YOLO format).

What it does
------------
1. Validates images/labels pairing, coordinate ranges and class ids.
2. Reports class balance (person vs cell phone) - phone boxes are usually <5%
   of instances, which is the main cause of poor recall.
3. Suggests dataset improvements based on the statistics.
4. Creates deterministic train/val/test splits (grouped by session prefix to
   avoid near-duplicate frames leaking between splits).
5. Writes ``data.yaml`` ready for ``scripts/train.py``.

Usage
-----
    python scripts/prepare_dataset.py --src datasets/raw --out datasets/classroom \
        --val 0.15 --test 0.15
    python scripts/prepare_dataset.py --src datasets/classroom --check-only
"""

from __future__ import annotations

import argparse
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
DEFAULT_NAMES = {0: "person", 67: "cell phone"}


def find_pairs(root: Path) -> tuple[list[Path], dict[Path, Path], list[str]]:
    images: list[Path] = []
    label_for: dict[Path, Path] = {}
    problems: list[str] = []
    label_dirs = sorted({p.parent for p in root.rglob("*.txt") if "labels" in p.parts})
    for image in sorted(p for p in root.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES):
        images.append(image)
        label = None
        for directory in label_dirs:
            candidate = directory / f"{image.stem}.txt"
            if candidate.exists():
                label = candidate
                break
        if label is None:
            problems.append(f"missing label file for {image.relative_to(root)}")
            continue
        label_for[image] = label
    return images, label_for, problems


def validate(root: Path, label_for: dict[Path, Path]) -> tuple[Counter, list[str]]:
    counts: Counter = Counter()
    problems: list[str] = []
    for image, label in label_for.items():
        for lineno, line in enumerate(label.read_text(encoding="utf-8").splitlines(), start=1):
            fields = line.split()
            if not fields:
                continue
            if len(fields) != 5:
                problems.append(f"{label.name}:{lineno} expected 5 fields, got {len(fields)}")
                continue
            try:
                class_id = int(float(fields[0]))
                cx, cy, w, h = (float(v) for v in fields[1:])
            except ValueError:
                problems.append(f"{label.name}:{lineno} non-numeric values")
                continue
            if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < w <= 1 and 0 < h <= 1):
                problems.append(f"{label.name}:{lineno} coordinates out of range")
            if class_id not in DEFAULT_NAMES:
                problems.append(
                    f"{label.name}:{lineno} class id {class_id} not in {list(DEFAULT_NAMES)} "
                    "(remap the dataset or pass --keep-other-classes)"
                )
            counts[DEFAULT_NAMES.get(class_id, str(class_id))] += 1
    return counts, problems


def session_key(name: str | Path) -> str:
    """Group frames that came from the same recording session/video."""
    stem = Path(name).stem
    for separator in ("_frame", "-frame", "_f", "-"):
        if separator in stem:
            return stem.split(separator)[0]
    return stem.rstrip("0123456789") or stem


def split(root: Path, images: list[Path], label_for: dict[Path, Path], out: Path, val: float, test: float, seed: int) -> dict[str, int]:
    groups: dict[str, list[Path]] = defaultdict(list)
    for image in images:
        groups[session_key(image.name)].append(image)
    keys = sorted(groups)
    random.Random(seed).shuffle(keys)

    total = len(images)
    target_val = int(total * val)
    target_test = int(total * test)
    assignment: dict[str, str] = {}
    counts = {"val": 0, "test": 0}
    for key in keys:
        size = len(groups[key])
        if counts["test"] + size <= target_test and target_test > 0:
            assignment[key] = "test"
            counts["test"] += size
        elif counts["val"] + size <= target_val and target_val > 0:
            assignment[key] = "val"
            counts["val"] += size
        else:
            assignment[key] = "train"

    for split_name in ("train", "val", "test"):
        (out / "images" / split_name).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split_name).mkdir(parents=True, exist_ok=True)

    summary = Counter()
    for key, members in groups.items():
        split_name = assignment[key]
        for image in members:
            shutil.copy2(image, out / "images" / split_name / image.name)
            shutil.copy2(label_for[image], out / "labels" / split_name / f"{image.stem}.txt")
            summary[split_name] += 1
    return dict(summary)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and split a YOLO classroom dataset")
    parser.add_argument("--src", required=True, help="Raw dataset directory (images + labels)")
    parser.add_argument("--out", default="datasets/classroom", help="Output dataset directory")
    parser.add_argument("--val", type=float, default=0.15)
    parser.add_argument("--test", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--max-problems", type=int, default=25)
    args = parser.parse_args()

    src = Path(args.src).expanduser().resolve()
    if not src.exists():
        print(f"ERROR: {src} does not exist")
        return 2

    images, label_for, missing = find_pairs(src)
    counts, problems = validate(src, label_for)
    problems = missing + problems

    print(f"Images: {len(images)} · labelled: {len(label_for)}")
    print("Instances per class:")
    for name, count in counts.most_common():
        print(f"  {name:14s} {count}")
    total = sum(counts.values()) or 1
    phone_share = counts.get("cell phone", 0) / total
    print(f"Phone share of instances: {phone_share:.2%}")

    if problems:
        print(f"\n{len(problems)} problem(s) found (showing {min(len(problems), args.max_problems)}):")
        for problem in problems[: args.max_problems]:
            print(f"  - {problem}")
    else:
        print("\nNo structural problems found.")

    print("\nSuggestions:")
    if phone_share < 0.02:
        print("  * Very few phone instances: add hard examples (small, oblique, low-light phones),")
        print("    and consider oversampling images that contain phones during training.")
    if counts.get("person", 0) < 500:
        print("  * Fewer than ~500 person instances: recall for seated/occluded students will be weak.")
    print("  * Include negative frames: empty hands, books, calculators, water bottles and")
    print("    projectors/remotes near students, so the phone class does not absorb them.")
    print("  * Capture varying lighting (morning/evening, blinds open/closed), camera heights and")
    print("    both near (close-up) and far (wide) classroom views.")
    print("  * Do not train on footage of identifiable students without documented authorisation.")

    if args.check_only or problems:
        if problems and not args.check_only:
            print("\nFix the problems above before splitting.")
        return 0 if not problems else 1

    out = Path(args.out).expanduser().resolve()
    summary = split(src, images, label_for, out, args.val, args.test, args.seed)
    yaml_path = out / "data.yaml"
    names_block = "\n".join(f"  {key}: {value}" for key, value in DEFAULT_NAMES.items())
    yaml_path.write_text(
        "# Generated by scripts/prepare_dataset.py\n"
        f"path: {out.as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "test: images/test\n"
        "nc: 2\n"
        f"names:\n{names_block}\n"
        "# NOTE: class ids are kept as their COCO ids (0=person, 67=cell phone) so the\n"
        "# pretrained YOLO head transfers without remapping.\n",
        encoding="utf-8",
    )
    print(f"\nSplit: {summary}")
    print(f"Dataset written to {out} (data.yaml created)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
