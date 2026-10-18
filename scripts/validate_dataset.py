#!/usr/bin/env python3
"""Validate a YOLO dataset's images and annotations.

Takes the dataset directory as an argument. It used to be hardcoded to
``dataset/``, so the only dataset it could check was the first one ever built:
every dataset produced since (``datasets/barcode``, ``datasets/barcode_aug``,
``datasets/barcode_detection``) had to be taken on trust, and so did the
held-out split the builders now write.

What is checked, per split named in ``data.yaml``:

* the image opens and its label file exists;
* a label line is five fields with a known class id and a normalised centre and
  size -- a box wider than the image is the failure mode that a hand-copied
  ground truth once produced here;
* an empty label is accepted **only** for a deliberate negative. The builders
  name these two ways -- ``negative_...`` in the baseline and detection datasets,
  ``neg_...`` in the augmented one -- so both prefixes count and anything else
  does not. YOLO uses an empty file to mean "no object", so
  an empty label on a real barcode image is a silently unlabelled positive, and a
  box on a barcode-free document is a false positive written into the dataset;
* no card appears in two splits. Leakage is checked here rather than trusted,
  because it is the one mistake that makes every score in the run meaningless.

Usage:
    python -m scripts.validate_dataset
    python -m scripts.validate_dataset datasets/barcode_detection_all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import yaml

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from config.logging_config import setup_logging  # noqa: E402

logger = setup_logging()

#: Split folders looked for under ``images/`` and ``labels/``. ``test`` is
#: included because a held-out card is written there and must be checked too.
SPLITS = ("train", "val", "test")

#: Prefixes the dataset builders give a deliberate barcode-free negative.
#: ``negative_`` (baseline, detection) and ``neg_`` (augmented).
NEGATIVE_PREFIXES = ("negative", "neg_")

IMAGE_SUFFIXES = ("*.jpg", "*.jpeg", "*.png", "*.bmp")


def _group_of(stem: str) -> str:
    """The card a generated file came from, from its filename.

    Builders prefix every file with the physical card's group (``card_C__...``),
    so the group can be read back without trusting a manifest. Negatives are
    grouped by their *document* rather than by rendered size: the distance
    builder emits ``negative__student_1_info__h042`` and friends, and treating
    each size as its own group would hide the fact that one panel was written
    into several splits.
    """
    head = stem.split("__", 1)[0]
    if head.startswith("card_"):
        return head
    if any(p in stem for p in NEGATIVE_PREFIXES):
        parts = stem.split("__")
        return f"negative:{parts[1] if len(parts) > 1 else stem}"
    return stem


def _splits_from(data_config: Dict, dataset_dir: Path) -> List[str]:
    """Which splits this dataset actually declares, preferring ``data.yaml``."""
    declared = []
    for split in SPLITS:
        value = data_config.get(split)
        if isinstance(value, str):
            # `path` in data.yaml is absolute (see build_dataset._data_yaml).
            candidate = Path(value)
            resolved = candidate if candidate.is_absolute() else dataset_dir / candidate
            if (resolved).exists():
                declared.append(split)
    if declared:
        return declared
    # No usable declaration: fall back to the folders that exist, and say so,
    # rather than reporting success on an empty scan.
    present = [s for s in SPLITS if (dataset_dir / "images" / s).exists()]
    if present:
        logger.warning(
            "data.yaml names no readable split directory; falling back to the "
            "folders present: %s", present
        )
    return present


def validate_dataset(dataset_dir: Optional[Path] = None) -> bool:
    dataset_dir = Path(dataset_dir) if dataset_dir else project_root / "dataset"
    images_dir = dataset_dir / "images"
    labels_dir = dataset_dir / "labels"
    data_yaml = dataset_dir / "data.yaml"

    if not data_yaml.exists():
        logger.error(f"data.yaml not found at {data_yaml}")
        return False

    data_config = yaml.safe_load(data_yaml.read_text()) or {}
    class_names = data_config.get("names", [])
    if isinstance(class_names, dict):
        class_names = [class_names[k] for k in sorted(class_names)]
    logger.info(f"dataset : {dataset_dir}")
    logger.info(f"classes : {class_names}")

    splits = _splits_from(data_config, dataset_dir)
    if not splits:
        logger.error("no split directories to validate")
        return False

    total_images = total_valid = total_invalid = 0
    #: split -> {group: count}, for the leakage check below.
    seen_groups: Dict[str, Dict[str, int]] = {}

    for split in splits:
        img_dir = images_dir / split
        lbl_dir = labels_dir / split

        if not img_dir.exists():
            logger.warning(f"Images directory not found: {img_dir}")
            continue

        images: List[Path] = []
        for pattern in IMAGE_SUFFIXES:
            images.extend(sorted(img_dir.glob(pattern)))

        logger.info(f"{split}: {len(images)} images")
        seen_groups[split] = {}

        for img_path in images:
            total_images += 1
            group = _group_of(img_path.stem)
            seen_groups[split][group] = seen_groups[split].get(group, 0) + 1

            img = cv2.imread(str(img_path))
            if img is None:
                logger.warning(f"Cannot read image: {img_path}")
                total_invalid += 1
                continue

            lbl_path = lbl_dir / f"{img_path.stem}.txt"
            if not lbl_path.exists():
                logger.warning(f"Missing label: {lbl_path}")
                total_invalid += 1
                continue

            lines = [ln for ln in lbl_path.read_text().splitlines() if ln.strip()]
            # Builders name their negatives, so intent is readable from the file
            # name rather than guessed. An empty label on anything else is an
            # unlabelled positive; a box on a negative is a false positive baked
            # into the dataset.
            is_negative = any(p in img_path.stem for p in NEGATIVE_PREFIXES)
            if not lines:
                if is_negative:
                    total_valid += 1
                else:
                    logger.warning(
                        f"Empty label on a positive image: {lbl_path} -- the sample "
                        f"will train the model that nothing is there"
                    )
                    total_invalid += 1
                continue
            if is_negative:
                logger.warning(
                    f"Negative carries {len(lines)} box(es): {lbl_path} -- a "
                    f"barcode-free document must have an empty label"
                )
                total_invalid += 1
                continue

            valid = True
            for line in lines:
                parts = line.split()
                if len(parts) != 5:
                    logger.warning(f"Invalid annotation format: {lbl_path} - {line}")
                    valid = False
                    break
                try:
                    class_id = int(parts[0])
                    x_center, y_center, width, height = map(float, parts[1:])
                except ValueError:
                    logger.warning(f"Non-numeric values in {lbl_path}: {line}")
                    valid = False
                    break
                if class_names and class_id >= len(class_names):
                    logger.warning(f"Invalid class ID {class_id} in {lbl_path}")
                    valid = False
                    break
                if not (0 <= x_center <= 1 and 0 <= y_center <= 1
                        and 0 <= width <= 1 and 0 <= height <= 1):
                    logger.warning(f"Invalid bbox coordinates in {lbl_path}: {parts[1:]}")
                    valid = False
                    break
                if width == 0 or height == 0:
                    logger.warning(f"Zero-area box in {lbl_path}: {parts[1:]}")
                    valid = False
                    break

            if valid:
                total_valid += 1
            else:
                total_invalid += 1

    # Leakage: a group present in two splits invalidates every score trained here.
    owner: Dict[str, str] = {}
    for split, groups in seen_groups.items():
        for group in groups:
            if group in owner and owner[group] != split:
                logger.error(
                    f"LEAKAGE: group {group} appears in both {owner[group]} and {split}"
                )
                total_invalid += 1
            owner.setdefault(group, split)

    logger.info(f"Total images: {total_images}")
    logger.info(f"Valid: {total_valid}")
    logger.info(f"Invalid: {total_invalid}")
    logger.info(
        "Groups per split: "
        + ", ".join(f"{s}={len(g)}" for s, g in seen_groups.items())
    )
    return total_invalid == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", nargs="?", type=Path, default=None,
                    help="dataset directory (default: dataset/)")
    args = ap.parse_args()

    logger.info("Validating YOLO dataset...")
    if validate_dataset(args.dataset):
        logger.info("Dataset validation passed!")
        return 0
    logger.error("Dataset validation failed!")
    return 1


if __name__ == "__main__":
    sys.exit(main())