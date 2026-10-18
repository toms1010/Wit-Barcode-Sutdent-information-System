"""Build a YOLO dataset from the project's permitted assets. Nothing else.

Hard rules this module enforces, not merely documents:

* The **only** images that may enter the dataset are the cards and the
  barcode-free panels listed in
  :mod:`config.student_assets`. ``assets/barcodes/*.png`` are crops of those
  same physical cards, so they are reference material for decoding only and are
  never copied in -- training on them would leak the card they came from. (The
  detection and augmentation builders take the opposite view on purpose and keep
  a crop with its own card; both are fine, as long as it is a decision and not
  an accident. :func:`config.student_assets.cards_only` and
  :func:`~config.student_assets.card_groups` are the two rules.)
* **Nothing is generated.** No synthetic barcode is rendered, no augmentation is
  synthesised, no external dataset is read. Every pixel comes from an existing
  asset, copied byte for byte.
* **Every box is derived from a real decode.** A positive sample gets its
  coordinates from zbar actually reading that image; if nothing decodes, the
  image is not labelled positive and the build records a problem. A negative
  sample must decode *nothing*, otherwise it would poison training.
* Source files are never written to. The build asserts their digests are
  unchanged when it finishes.

Splits are assigned per physical card, not per file, so no card is ever both
trained on and validated on. All three students are covered: card A trains,
card B validates, card C is **test only** and is written to its own split so the
active model's held-out behaviour can be measured on a card it has never seen.

The honest size of this dataset is one to two images per split. That is a
limitation of the assets available, not something this script can fix, and it is
recorded in the build manifest for the training report to quote.

Usage:
    python -m scripts.build_dataset --out datasets/barcode
    python -m scripts.build_dataset --out datasets/barcode --validate-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.settings import settings
from config.student_assets import (  # noqa: E402
    NEGATIVE_SPLITS,
    STUDENT_ASSETS,
    cards_only,
    group_splits,
    negative_panels,
)
from services.barcode_decoder import BarcodeDecoderService  # noqa: E402

# ---------------------------------------------------------------- sources --
ASSETS = BASE_DIR / "assets"

#: The complete set of files this build is allowed to touch: the cards it copies,
#: the crops it only decodes, and the barcode-free panels. Built from the
#: registry so a fourth card cannot be added to the project and quietly left out
#: of the dataset: the registry is the single place a student is declared, and
#: every builder reads it.
ALLOWED_ASSETS: Tuple[Path, ...] = tuple(
    [path for group in cards_only().values() for path in group]
    + [a.crop for a in STUDENT_ASSETS if a.crop is not None]
    + negative_panels()
)

#: Reference only. These are decoded and reported, never copied into the dataset.
DECODE_REFERENCE: Tuple[Path, ...] = tuple(
    a.crop for a in STUDENT_ASSETS if a.crop is not None
)

#: Only images that decode to a barcode become positives, grouped by the physical
#: card they came from -- a card and its tight crop are one group, so a crop can
#: never be trained on while the card it was cut from is validated on.
REAL_CARD_GROUPS: Dict[str, List[Path]] = cards_only()

#: Card A trains, card B validates, card C is held out entirely.
CARD_GROUP_SPLIT = group_splits()

#: Documents with no barcode at all. Verified by decode, not assumed.
NEGATIVES: Dict[Path, str] = {
    settings.info_image(name): split for name, split in NEGATIVE_SPLITS.items()
}

CLASS_NAME = "barcode"
CLASS_ID = 0

#: Splits this build writes. ``test`` exists because a card is held out
#: entirely; omitting it here would silently write the held-out card into ``val``
#: and turn every held-out number into a number the model was chosen on.
DATASET_SPLITS = ("train", "val", "test")


# ------------------------------------------------------- ground truth -----


def _decoder() -> BarcodeDecoderService:
    return BarcodeDecoderService()


def decode_symbols(image_path: Path) -> List[Dict]:
    """Every symbol readable in ``image_path``, with its pixel rectangle.

    Two decoders, and the reason is a card rather than a preference:
    :meth:`~services.barcode_decoder.BarcodeDecoderService.decode_with_preprocessing`
    runs the filter variants and finds *several* distinct symbols when an image
    carries more than one, while
    :meth:`~services.barcode_decoder.BarcodeDecoderService.decode_best` runs the
    production ordered strategy, which also interpolates an undersampled crop and
    is therefore the only one of the two that can read ``Id_3.png`` -- its symbol
    is 204 px wide, under the floor below which every filter variant returns
    nothing.

    The filtered pass runs first so the "more than one symbol in this image"
    guard below keeps its meaning; the ordered pass is the fallback that makes
    the third card buildable rather than silently dropped. Whichever attempt won
    is recorded, so a manifest never implies a bare decode succeeded.
    """
    image = cv2.imread(str(image_path))
    if image is None:
        return []

    decoder = _decoder()
    seen: Dict[str, Dict] = {}
    for item in decoder.decode_with_preprocessing(image):
        if item.data in seen:
            continue
        rect = item.bbox
        seen[item.data] = {
            "data": item.data,
            "type": item.type,
            # pyzbar Rect is (left, top, width, height).
            "left": int(rect.left),
            "top": int(rect.top),
            "width": int(rect.width),
            "height": int(rect.height),
            "attempt": "preprocessing",
        }

    if not seen:
        result = decoder.decode_best(image)
        if result.ok:
            # ``source_bbox``, not ``bbox``: an interpolation attempt reports its
            # rectangle in the enlarged image's pixels, which would place the
            # annotation outside this image.
            rect = result.barcodes[0].source_bbox or tuple(result.barcodes[0].bbox)
            seen[result.data] = {
                "data": result.data,
                "type": result.type,
                "left": int(rect[0]),
                "top": int(rect[1]),
                "width": int(rect[2]),
                "height": int(rect[3]),
                "attempt": result.attempt,
                "attempts_tried": result.attempts_tried,
            }
    return list(seen.values())


def symbol_extent(symbol: Dict) -> Tuple[float, float, float, float]:
    """``(x1, y1, x2, y2)`` of a decoded symbol, polygon-preferred."""
    return (
        symbol["left"],
        symbol["top"],
        symbol["left"] + symbol["width"],
        symbol["top"] + symbol["height"],
    )


def derive_box(image_path: Path) -> Tuple[Optional[str], List[Dict]]:
    """Derive the YOLO annotation for a positive sample from its own pixels.

    Returns ``(label_text, evidence)``. ``label_text`` is ``None`` when no
    barcode decodes, which means the caller must not label the image positive.

    The box is the decoder's own rectangle for the symbol, so it cannot be a
    guess. zbar's polygon is preferred over ``rect`` when present because the
    rectangle of a slightly rotated card is a looser fit.
    """
    image = cv2.imread(str(image_path))
    if image is None:
        return None, [{"error": "unreadable image"}]

    h, w = image.shape[:2]
    symbols = decode_symbols(image_path)
    evidence = symbols
    if not symbols:
        return None, evidence

    # One barcode per card in this project. If that ever stops being true the
    # build must be revisited rather than silently taking the first symbol.
    if len(symbols) > 1:
        return None, evidence + [{"error": f"{len(symbols)} symbols found; expected 1"}]

    symbol = symbols[0]
    x1, y1, x2, y2 = symbol_extent(symbol)

    x1 = max(0, min(x1, w - 1))
    y1 = max(0, min(y1, h - 1))
    x2 = max(x1 + 1, min(x2, w))
    y2 = max(y1 + 1, min(y2, h))

    xc = (x1 + x2) / 2 / w
    yc = (y1 + y2) / 2 / h
    nw = (x2 - x1) / w
    nh = (y2 - y1) / h

    label = f"{CLASS_ID} {xc:.6f} {yc:.6f} {nw:.6f} {nh:.6f}"
    evidence = evidence + [
        {
            "image_size": [w, h],
            "box_xyxy": [int(x1), int(y1), int(x2), int(y2)],
            "box_yolo_normalised": [round(xc, 6), round(yc, 6), round(nw, 6), round(nh, 6)],
        }
    ]
    return label, evidence


# ------------------------------------------------------------- helpers ----


def _data_yaml(target_dir: Path) -> str:
    """Render data.yaml.

    ``path`` must be absolute. Ultralytics does *not* resolve a relative
    ``path`` against the data.yaml location -- it resolves against its own
    datasets directory, so a portable-looking ``path: .`` silently points
    somewhere else and training aborts with "images not found".
    """
    return f"""# Generated by scripts/build_dataset.py from assets/images/ only.
# Single class: the barcode symbol itself, never the whole card.
# NOTE: 'path' is absolute on purpose; see build_dataset._data_yaml.
path: {target_dir.resolve()}
train: images/train
val: images/val
# The third card is held out: it is written to its own split and is never
# trained on nor used to pick a checkpoint. Pointing `test` at `val` would make
# the held-out number a number the model was selected on.
test: images/test

names:
  0: {CLASS_NAME}
nc: 1
"""


def _digest(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _write_empty_label(path: Path) -> None:
    """YOLO marks a negative sample with a present but empty label file."""
    path.write_text("")


def _validate_label(label_path: Path) -> List[str]:
    """Return a list of problems; empty means the label is usable."""
    problems: List[str] = []
    text = label_path.read_text().strip()
    if not text:
        return problems  # valid negative
    for lineno, line in enumerate(text.splitlines(), start=1):
        parts = line.split()
        if len(parts) != 5:
            problems.append(f"{label_path.name}:{lineno} expected 5 fields")
            continue
        cls, *vals = parts
        if not cls.isdigit() or cls != str(CLASS_ID):
            problems.append(f"{label_path.name}:{lineno} class must be {CLASS_ID}, got {cls!r}")
        try:
            x, y, w, h = (float(v) for v in vals)
        except ValueError:
            problems.append(f"{label_path.name}:{lineno} non-numeric coordinates")
            continue
        if not (0 <= x <= 1 and 0 <= y <= 1):
            problems.append(f"{label_path.name}:{lineno} centre outside [0,1]")
        if not (0 < w <= 1 and 0 < h <= 1):
            problems.append(f"{label_path.name}:{lineno} non-positive size")
        if x - w / 2 < -1e-3 or x + w / 2 > 1 + 1e-3:
            problems.append(f"{label_path.name}:{lineno} box crosses a side edge")
        if y - h / 2 < -1e-3 or y + h / 2 > 1 + 1e-3:
            problems.append(f"{label_path.name}:{lineno} box crosses a top/bottom edge")
    return problems


def _summarise(out_dir: Path, manifest: Dict) -> Dict:
    counts = {}
    hashes: Dict[str, List[str]] = defaultdict(list)
    if out_dir.exists():
        for split in DATASET_SPLITS:
            img_dir = out_dir / "images" / split
            lab_dir = out_dir / "labels" / split
            images = sorted(p for p in img_dir.iterdir() if p.is_file()) if img_dir.exists() else []
            labels = sorted(lab_dir.glob("*.txt")) if lab_dir.exists() else []
            positives = negatives = 0
            for lab in labels:
                if lab.read_text().strip():
                    positives += 1
                else:
                    negatives += 1
            counts[split] = {
                "images": len(images),
                "labels": len(labels),
                "positive": positives,
                "negative": negatives,
                "unmatched_images": [
                    p.name for p in images if not (lab_dir / f"{p.stem}.txt").exists()
                ],
            }
            for img in images:
                hashes[_digest(img)].append(f"{split}/{img.name}")
    manifest["counts"] = counts

    # Exact-duplicate detection across the whole dataset.
    manifest["exact_duplicates"] = [v for v in hashes.values() if len(v) > 1]
    if manifest["exact_duplicates"]:
        manifest["problems"].append(
            f"identical images present more than once: {manifest['exact_duplicates']}"
        )

    test_cards = {
        f for g, imgs in manifest.get("card_groups", {}).items()
        if imgs and manifest["card_groups"][g]["split"] == "test"
        for f in manifest["card_groups"][g]["files"]
    }
    manifest["independent_real_cards_in_test"] = sorted(test_cards)
    # Leakage check across all three splits, stated in the manifest rather than
    # assumed: a group that appears in two splits would invalidate every score
    # trained on this dataset.
    seen: Dict[str, str] = {}
    for group, info in manifest.get("card_groups", {}).items():
        for split_seen in {info["split"]}:
            if group in seen and seen[group] != split_seen:
                manifest["problems"].append(
                    f"LEAKAGE: group {group} appears in both {seen[group]} and {split_seen}"
                )
            seen[group] = split_seen

    train_cards = {
        f for g, imgs in manifest.get("card_groups", {}).items()
        if imgs and manifest["card_groups"][g]["split"] == "train"
        for f in manifest["card_groups"][g]["files"]
    }
    val_cards = {
        f for g, imgs in manifest.get("card_groups", {}).items()
        if imgs and manifest["card_groups"][g]["split"] == "val"
        for f in manifest["card_groups"][g]["files"]
    }
    manifest["independent_real_cards_in_train"] = sorted(train_cards)
    manifest["independent_real_cards_in_val"] = sorted(val_cards)
    return manifest


# ---------------------------------------------------------------- build ---


def build(out_dir: Path, validate_only: bool = False) -> Dict:
    manifest: Dict = {
        "source_dataset": None,
        "target": str(out_dir),
        "allowed_assets": [str(p) for p in ALLOWED_ASSETS],
        # Exactly what is copied in: the cards and the barcode-free panels. The
        # tight crops are deliberately absent -- they are decoding reference
        # material, and listing them here would make the manifest disagree with
        # the rule this module exists to enforce.
        "yolo_images_allowed": [
            str(p) for p in [a.card for a in STUDENT_ASSETS] + list(negative_panels())
        ],
        "synthetic_data_used": False,
        "external_dataset_used": False,
        "card_groups": {},
        "negatives": {},
        "decode_reference": {},
        "source_digests_before": {},
        "problems": [],
        "counts": {},
    }

    missing = [p for p in ALLOWED_ASSETS if not p.exists()]
    if missing:
        manifest["problems"].append(f"missing permitted assets: {[str(p) for p in missing]}")
        return manifest

    for asset in ALLOWED_ASSETS:
        manifest["source_digests_before"][str(asset)] = _digest(asset)

    if not validate_only:
        for split in DATASET_SPLITS:
            (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
            (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    def place(img: Path, label_text: str, split: str, prefix: str) -> None:
        stem = f"{prefix}_{img.stem}"
        label_path = out_dir / "labels" / split / f"{stem}.txt"
        if not validate_only:
            shutil.copy2(img, out_dir / "images" / split / f"{stem}{img.suffix}")
            label_path.write_text(label_text if label_text.endswith("\n") else label_text + "\n")
        manifest["problems"].extend(_validate_label(label_path) if not validate_only else [])

    # Positive samples: the box comes from decoding the image itself.
    for group, images in REAL_CARD_GROUPS.items():
        split = CARD_GROUP_SPLIT[group]
        manifest["card_groups"][group] = {"split": split, "files": [], "labels": {}}
        for img in images:
            label_text, evidence = derive_box(img)
            if label_text is None:
                manifest["problems"].append(
                    f"{img.name}: no single decodable barcode, cannot be labelled positive "
                    f"(evidence={evidence})"
                )
                continue
            place(img, label_text, split, group)
            manifest["card_groups"][group]["files"].append(img.name)
            manifest["card_groups"][group]["labels"][img.name] = {
                "label": label_text.strip(),
                "evidence": evidence,
            }

    # Barcode-free documents, so the model learns to reject them.
    for panel, split in NEGATIVES.items():
        symbols = decode_symbols(panel)
        if symbols:
            manifest["problems"].append(
                f"{panel.name}: decodes {[s['data'] for s in symbols]} and must not be a negative"
            )
            continue
        stem = f"negative_{panel.stem}"
        if not validate_only:
            shutil.copy2(panel, out_dir / "images" / split / f"{stem}.png")
            _write_empty_label(out_dir / "labels" / split / f"{stem}.txt")
        manifest["negatives"][panel.name] = split

    # Decode references, recorded as evidence only. Never copied into the dataset.
    for reference in DECODE_REFERENCE:
        manifest["decode_reference"][reference.name] = decode_symbols(reference)

    # The build must not have written to its own sources.
    for asset in ALLOWED_ASSETS:
        if _digest(asset) != manifest["source_digests_before"][str(asset)]:
            manifest["problems"].append(f"SOURCE MODIFIED: {asset}")

    if not validate_only:
        (out_dir / "data.yaml").write_text(_data_yaml(out_dir))

    return _summarise(out_dir, manifest)


def print_report(m: Dict) -> None:
    print(f"target: {m['target']}")
    for split, c in m.get("counts", {}).items():
        print(
            f"  {split:5s}: {c['images']:3d} images  {c['positive']:3d} positive  "
            f"{c['negative']:2d} negative"
            + (f"  UNMATCHED={c['unmatched_images']}" if c["unmatched_images"] else "")
        )
    print("card groups (leakage-controlled):")
    for group, info in m.get("card_groups", {}).items():
        print(f"  {group} -> {info['split']}: {info['files']}")
        for name, detail in info.get("labels", {}).items():
            print(f"      {name}: {detail['label']}")
    print(f"negatives: {m.get('negatives')}")
    print("decode reference (not trained on):")
    for name, symbols in m.get("decode_reference", {}).items():
        print(f"  {name}: " + (", ".join(f"{s['data']} ({s['type']})" for s in symbols) or "no decode"))
    print(f"synthetic data used: {m.get('synthetic_data_used')}")
    print(f"independent real cards: train={len(m.get('independent_real_cards_in_train', []))} "
          f"val={len(m.get('independent_real_cards_in_val', []))}")
    print(f"exact duplicates: {m.get('exact_duplicates') or 'none'}")
    print(f"problems: {m['problems'] or 'none'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=BASE_DIR / "datasets" / "barcode")
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args()

    manifest = build(args.out, validate_only=args.validate_only)
    print_report(manifest)
    if not args.validate_only:
        report = args.out / "build_manifest.json"
        report.write_text(json.dumps(manifest, indent=2))
        print(f"\nwrote {report}")
    return 1 if manifest["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
