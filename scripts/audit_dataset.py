"""Dataset integrity audit for the barcode detector.

Read-only. Reports image/label counts, unreadable files, invalid annotation
coordinates, wrong class ids, exact and near-duplicate images, train/val/test
leakage and the proportion of negative (barcode-free) samples.

Usage:
    python -m scripts.audit_dataset
    python -m scripts.audit_dataset --json reports/dataset_audit.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import cv2
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
DATASET_DIR = BASE_DIR / "dataset"
SPLITS = ("train", "val", "test")


def _image_paths(split: str) -> List[Path]:
    d = DATASET_DIR / "images" / split
    if not d.exists():
        return []
    return sorted(p for p in d.iterdir() if p.is_file())


def _label_paths(split: str) -> List[Path]:
    d = DATASET_DIR / "labels" / split
    if not d.exists():
        return []
    return sorted(d.glob("*.txt"))


def _hash(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _phash_distance(a: np.ndarray, b: np.ndarray) -> int:
    """Hamming distance between 64-bit perceptual hashes (max 64)."""
    return int(bin(int(a, 16) ^ int(b, 16)).count("1"))


def audit() -> Dict:
    import numpy as np

    report: Dict = {
        "dataset_dir": str(DATASET_DIR),
        "problems": [],
        "splits": {},
        "class_counts": {},
        "exact_duplicates": [],
        "near_duplicates": [],
        "negative_samples": [],
        "unreadable_images": [],
        "box_aspect_ratios": {},
    }

    yaml_path = DATASET_DIR / "data.yaml"
    if yaml_path.exists():
        cfg = yaml.safe_load(yaml_path.read_text()) or {}
        report["data_yaml"] = {k: str(v) for k, v in cfg.items()}
        # ultralytics resolves 'path' relative to data.yaml
        root = (DATASET_DIR / str(cfg.get("path", "."))).resolve()
        report["resolved_root"] = str(root)
        for key in ("train", "val", "test"):
            if key not in cfg:
                report["problems"].append(f"data.yaml missing '{key}' entry")
        for key in ("train", "val", "test"):
            rel = cfg.get(key)
            if rel and not (root / str(rel)).exists():
                report["problems"].append(
                    f"data.yaml '{key}' points at a missing directory: {rel}"
                )

    exact: Dict[str, List[str]] = defaultdict(list)
    phashes: Dict[str, Dict[str, int]] = {}
    class_counts: Dict[str, int] = defaultdict(int)

    for split in SPLITS:
        images, labels = _image_paths(split), _label_paths(split)
        report["splits"][split] = {"images": len(images), "labels": len(labels)}
        label_dir = DATASET_DIR / "labels" / split

        for img_path in images:
            img = cv2.imread(str(img_path))
            if img is None:
                report["unreadable_images"].append(str(img_path.relative_to(BASE_DIR)))
                continue
            exact[_hash(img_path)].append(str(img_path.relative_to(BASE_DIR)))
            if min(img.shape[:2]) < 32:
                report["problems"].append(
                    f"{split}/{img_path.name} is tiny: {img.shape[:2]}"
                )
            try:
                import imagehash

                phashes.setdefault(split, {})[img_path.name] = str(
                    imagehash.phash(img)
                )
            except Exception:
                pass

            if not (label_dir / f"{img_path.stem}.txt").exists():
                report["negative_samples"].append(
                    str(img_path.relative_to(BASE_DIR))
                )

        for label_path in labels:
            text = label_path.read_text().strip()
            if not text:
                # An empty .txt is the correct way to mark a negative sample.
                report["negative_samples"].append(
                    str(label_path.relative_to(BASE_DIR))
                )
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                parts = line.split()
                if len(parts) != 5:
                    report["problems"].append(
                        f"{label_path.name}:{line_no} expected 5 fields, got {len(parts)}"
                    )
                    continue
                cls, *vals = parts
                class_counts[cls] += 1
                try:
                    x, y, w, h = (float(v) for v in vals)
                except ValueError:
                    report["problems"].append(
                        f"{label_path.name}:{line_no} non-numeric values: {vals}"
                    )
                    continue
                if not cls.isdigit():
                    report["problems"].append(
                        f"{label_path.name}:{line_no} non-integer class id '{cls}'"
                    )
                if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                    report["problems"].append(
                        f"{label_path.name}:{line_no} centre outside [0,1]: {x},{y}"
                    )
                if not (0.0 < w <= 1.0 and 0.0 < h <= 1.0):
                    report["problems"].append(
                        f"{label_path.name}:{line_no} invalid size: {w},{h}"
                    )
                if x - w / 2 < -1e-3 or x + w / 2 > 1 + 1e-3:
                    report["problems"].append(
                        f"{label_path.name}:{line_no} box crosses left/right edge"
                    )
                if y - h / 2 < -1e-3 or y + h / 2 > 1 + 1e-3:
                    report["problems"].append(
                        f"{label_path.name}:{line_no} box crosses top/bottom edge"
                    )

                # Barcodes are wide and short; a square-ish box usually means
                # the whole card was annotated instead of the symbol.
                ars = report["box_aspect_ratios"].setdefault(split, [])
                ip = next(
                    (
                        DATASET_DIR / "images" / split / f"{label_path.stem}{ext}"
                        for ext in (".png", ".jpg", ".jpeg")
                        if (DATASET_DIR / "images" / split / f"{label_path.stem}{ext}").exists()
                    ),
                    None,
                )
                if ip is not None:
                    img = cv2.imread(str(ip))
                    if img is not None and h > 0:
                        ars.append(
                            round((w * img.shape[1]) / (h * img.shape[0]), 2)
                        )

    report["class_counts"] = dict(class_counts)

    report["exact_duplicates"] = [v for v in exact.values() if len(v) > 1]

    # Near-duplicate and cross-split leakage detection.
    splits_with_hash = {s: dict(phashes.get(s, {})) for s in SPLITS if phashes.get(s)}
    for split_a in splits_with_hash:
        names_a = splits_with_hash[split_a]
        keys_a = list(names_a)
        for split_b in splits_with_hash:
            names_b = splits_with_hash[split_b]
            for name_a in keys_a:
                for name_b in names_b:
                    if split_a == split_b and name_a >= name_b:
                        continue
                    dist = _phash_distance(names_a[name_a], names_b[name_b])
                    if dist <= 6:
                        report["near_duplicates"].append(
                            {
                                "a": f"{split_a}/{name_a}",
                                "b": f"{split_b}/{name_b}",
                                "phash_distance": dist,
                                "cross_split": split_a != split_b,
                            }
                        )

    total = sum(s["images"] for s in report["splits"].values())
    report["total_images"] = total
    report["split_percentages"] = {
        s: round(s_i["images"] / total * 100, 1) if total else 0.0
        for s, s_i in report["splits"].items()
    }
    report["negative_sample_percent"] = (
        round(len(report["negative_samples"]) / total * 100, 1) if total else 0.0
    )
    return report


def print_report(report: Dict) -> None:
    print(f"Dataset: {report['dataset_dir']}")
    if "data_yaml" in report:
        print(f"data.yaml: {report['data_yaml']}")
    print(f"Total images: {report['total_images']}")
    for split, info in report["splits"].items():
        print(
            f"  {split:5s}: {info['images']:4d} images / {info['labels']:4d} labels "
            f"({report['split_percentages'].get(split, 0)}%)"
        )
    print(f"Class ids: {report['class_counts']}")
    print(f"Unreadable images: {report['unreadable_images'] or 'none'}")
    print(f"Exact duplicates: {len(report['exact_duplicates'])}")
    for grp in report["exact_duplicates"]:
        print(f"    {grp}")
    print(f"Near-duplicate pairs (pHash<=6): {len(report['near_duplicates'])}")
    for nd in report["near_duplicates"][:20]:
        flag = "CROSS-SPLIT" if nd["cross_split"] else "same-split"
        print(f"    {nd['a']} <-> {nd['b']}  d={nd['phash_distance']}  {flag}")
    print(
        f"Negative (no-barcode) samples: {len(report['negative_samples'])} "
        f"({report['negative_sample_percent']}%)"
    )
    for split, ars in report["box_aspect_ratios"].items():
        if ars:
            print(
                f"  {split} box aspect w/h: min={min(ars)} max={max(ars)} "
                f"mean={sum(ars)/len(ars):.2f}"
            )
    print(f"Problems ({len(report['problems'])}):")
    for p in report["problems"][:40]:
        print(f"    - {p}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=Path, default=None, help="write JSON report here")
    args = ap.parse_args()

    report = audit()
    print_report(report)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2))
        print(f"\nWrote {args.json}")
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())