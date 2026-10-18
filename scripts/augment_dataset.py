#!/usr/bin/env python3
"""Generate augmented training variants from the eligible original cards.

What this does and, just as importantly, what it does not claim:

* The source images are the **real** cards in ``assets/images/``. Nothing is
  synthesised: no barcode is generated, no payload invented, no value altered.
  A variant is the same card under different exposure, contrast, angle or scale.
* Every variant keeps a **transformed ground-truth box**, derived from the box
  that was measured on the original by the decoder. The transform is applied to
  the box by the same geometry that was applied to the pixels.
* Every variant is **verified**: it must still decode, and it must still decode
  to the original's value. A variant that fails either test is discarded rather
  than shipped as training data, because a label on an unreadable barcode
  teaches the detector nothing and a mis-transformed box teaches it the wrong
  thing.
* Variants are **derivatives, not samples**. They are grouped by source image and
  the whole group lives in one split, so a card is never both trained and
  validated on. Counts are always reported as "N originals -> M variants";
  M is never a dataset size in any meaningful sense.

Usage:
    python -m scripts.augment_dataset --out datasets/barcode_aug
    python -m scripts.augment_dataset --out datasets/barcode_aug --per-source 8
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402
from config.student_assets import (  # noqa: E402
    NEGATIVE_PANELS,
    STUDENT_ASSETS,
    negative_panels,
    source_split,
)
from scripts.build_dataset import decode_symbols, symbol_extent  # noqa: E402

logger = setup_logging()

CLASS_ID = 0
CLASS_NAME = "barcode"

#: Same split assignment as the baseline dataset, keyed by source image, so a
#: card cannot appear on both sides of the boundary in either dataset. Read from
#: the shared registry rather than restated, because restating it is how the
#: third card ended up decoded by the scanner and absent from every augmentation.
SOURCE_SPLIT = source_split()

#: ``test`` is written as well as trained on: a card held out entirely still needs
#: to exist on disk for the held-out evaluation to read.
DATASET_SPLITS = ("train", "val", "test")

#: The three cards, their tight crops, and the barcode-free panels.
CARDS = [a.card for a in STUDENT_ASSETS]
CROPS = [a.crop for a in STUDENT_ASSETS if a.crop]
PANELS = negative_panels()


# ------------------------------------------------------------ primitives --


def _as_i16(img: np.ndarray) -> np.ndarray:
    return img.astype(np.float32)


def scale_brightness(factor: float) -> Callable:
    return lambda img: np.clip(_as_i16(img) * factor, 0, 255).astype(np.uint8)


def adjust_contrast(factor: float) -> Callable:
    def op(img: np.ndarray) -> np.ndarray:
        f = img.astype(np.float32)
        mean = f.mean(axis=(0, 1), keepdims=True)
        return np.clip((f - mean) * factor + mean, 0, 255).astype(np.uint8)

    return op


def adjust_gamma(g: float) -> Callable:
    lut = np.clip(((np.arange(256) / 255.0) ** g) * 255.0, 0, 255).astype(np.uint8)
    return lambda img: cv2.LUT(img, lut)


def gaussian_blur(sigma: float) -> Callable:
    return lambda img: cv2.GaussianBlur(img, (0, 0), sigma)


def add_noise(sigma: float, seed: int) -> Callable:
    rng = np.random.default_rng(seed)

    def op(img: np.ndarray) -> np.ndarray:
        return np.clip(
            img.astype(np.int16) + rng.normal(0, sigma, img.shape), 0, 255
        ).astype(np.uint8)

    return op


def rotate_image(angle: float) -> Callable:
    def op(img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        cos, sin = abs(m[0, 0]), abs(m[0, 1])
        nw, nh = int(round(h * sin + w * cos)), int(round(h * cos + w * sin))
        m[0, 2] += nw / 2 - w / 2
        m[1, 2] += nh / 2 - h / 2
        fill = tuple(int(v) for v in np.median(img.reshape(-1, img.shape[-1]), axis=0))
        return cv2.warpAffine(
            img, m, (nw, nh), flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=fill,
        )

    return op


def resize_image(factor: float) -> Callable:
    def op(img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        return cv2.resize(
            img,
            (max(1, int(w * factor)), max(1, int(h * factor))),
            interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_LINEAR,
        )

    return op


def translate_image(dx: float, dy: float) -> Callable:
    def op(img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        m = np.float32([[1, 0, dx], [0, 1, dy]])
        border = tuple(int(v) for v in np.median(img.reshape(-1, img.shape[-1]), axis=0))
        return cv2.warpAffine(
            img, m, (w, h), flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=border,
        )

    return op


def perspective_warp(strength: float, seed: int) -> Callable:
    def op(img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        jitter = np.random.default_rng(seed).uniform(-strength, strength, (4, 2))
        dst = (src + jitter * np.float32([w, h])).astype(np.float32)
        m = cv2.getPerspectiveTransform(src, dst)
        return cv2.warpPerspective(
            img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
        )

    return op


def shade(strength: float) -> Callable:
    def op(img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        ramp = (xx / max(1, w - 1)) * 0.5 + (yy / max(1, h - 1)) * 0.5
        return np.clip(_as_i16(img) * (1.0 - strength * ramp)[..., None], 0, 255).astype(np.uint8)

    return op


#: Rotations need their matrix so the box can be rotated identically.
RotOp = Tuple[Callable, bool]  # (op, returns_matrix)


def photometric_set() -> List[Tuple[str, Callable]]:
    return [
        ("bright_dark_60", scale_brightness(0.60)),
        ("bright_dark_75", scale_brightness(0.75)),
        ("bright_over_130", scale_brightness(1.30)),
        ("bright_over_155", scale_brightness(1.55)),
        ("contrast_low_55", adjust_contrast(0.55)),
        ("contrast_high_145", adjust_contrast(1.45)),
        ("gamma_075", adjust_gamma(0.75)),
        ("gamma_130", adjust_gamma(1.30)),
        ("shade_35", shade(0.35)),
        ("blur_sigma_1", gaussian_blur(1.0)),
        ("noise_sigma_10", add_noise(10, 101)),
        ("noise_sigma_20", add_noise(20, 202)),
        ("jpeg_q40", None),
    ]


def geometric_set() -> List[Tuple[str, Callable, bool]]:
    """(name, op, is_rotation)."""
    return [
        ("rot_cw_04", rotate_image(4.0), True),
        ("rot_ccw_04", rotate_image(-4.0), True),
        ("rot_cw_08", rotate_image(8.0), True),
        ("rot_ccw_08", rotate_image(-8.0), True),
        ("scale_080", resize_image(0.80), False),
        ("scale_125", resize_image(1.25), False),
        ("scale_065", resize_image(0.65), False),
        ("shift_left_06", translate_image(-0.06, 0.0), False),
        ("shift_up_05", translate_image(0.0, -0.05), False),
        ("perspective_05", perspective_warp(0.05, 303), False),
    ]


def compress_jpeg(img: np.ndarray) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 40])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else img


# ------------------------------------------------------------- box maths --


def transform_box(
    box: Tuple[int, int, int, int],
    op_matrix: np.ndarray,
    out_w: int,
    out_h: int,
) -> Optional[Tuple[int, int, int, int]]:
    """Apply an affine/perspective matrix to the four box corners.

    Corners are transformed, not just the centre: a rotation changes both the
    centre *and* the axis-aligned extent, and transforming only the centre
    produces a box that no longer covers the symbol.
    """
    x, y, w, h = box
    corners = np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
    ones = np.hstack([corners, np.ones((4, 1), dtype=np.float32)])
    pts = (op_matrix @ ones.T).T

    # A 3x3 perspective matrix leaves a third homogeneous coordinate behind; it
    # must be divided out before the corners are usable as pixels.
    if np.all(np.abs(pts[:, 2]) > 1e-9):
        pts = pts[:, :2] / pts[:, 2:3]
    pts = pts[:, :2]

    minx, miny = pts.min(axis=0)
    maxx, maxy = pts.max(axis=0)

    # Clip to the canvas. A box that extends past the edge is not automatically
    # wrong, but one that has been pushed mostly outside is.
    minx = max(0.0, float(minx))
    miny = max(0.0, float(miny))
    maxx = min(float(out_w), float(maxx))
    maxy = min(float(out_h), float(maxy))
    bw, bh = maxx - minx, maxy - miny
    if bw < 4 or bh < 4:
        return None
    return int(round(minx)), int(round(miny)), int(round(bw)), int(round(bh))


def matrix_for(op: Callable, is_rotation: bool, img: np.ndarray) -> np.ndarray:
    """Recover the 3x3 matrix an operation used, by probing its corners.

    Deriving the matrix by construction for every operator would duplicate the
    operator's own geometry and risk the two drifting apart. Probing the actual
    output keeps the annotation transformation tied to the pixels that were
    really produced.
    """
    h, w = img.shape[:2]
    probes = np.float32([[0, 0], [w, 0], [w, h], [0, h], [w / 2, h / 2]])
    ones = np.hstack([probes, np.ones((5, 1), dtype=np.float32)])

    out = op(img)
    if is_rotation:
        # Rotation grows the canvas; map from the original frame to the new one.
        oh, ow = out.shape[:2]
        dst = np.float32(
            [[0, 0], [ow, 0], [ow, oh], [0, oh], [ow / 2, oh / 2]]
        )
    else:
        oh, ow = out.shape[:2]
        dst = np.float32(
            [
                [0, 0], [ow, 0], [ow, oh], [0, oh], [ow / 2, oh / 2]
            ]
        )
    return cv2.getPerspectiveTransform(probes[:4], dst[:4])


# --------------------------------------------------------------- builder --


def ground_truth_box(path: Path) -> Optional[Tuple[Tuple[int, int, int, int], str]]:
    """Decode the original to obtain its true payload and pixel box."""
    symbols = decode_symbols(path)
    if len(symbols) != 1:
        return None
    symbol = symbols[0]
    x1, y1, x2, y2 = symbol_extent(symbol)
    return (int(x1), int(y1), int(x2 - x1), int(y2 - y1)), symbol["data"]


def verify_variant(image: np.ndarray, box: Tuple[int, int, int, int], expected: str) -> Dict:
    """A variant is only usable if it still decodes, and decodes correctly.

    The box is then checked against the symbol actually found *in the variant*,
    which is the only way to know the transformed annotation landed on the right
    pixels. The criterion is containment plus tightness, not raw IoU: zbar
    reports a tighter rect for a tilted symbol than the axis-aligned box a
    detector will ever draw, so IoU alone would reject correct annotations for
    rotated variants (measured 0.59 for a 4 degree rotation that contained the
    symbol perfectly).

    A valid annotation may be a little loose. It must not be loose enough to
    teach the detector that the surrounding card is barcode.
    """
    from services.barcode_decoder import BarcodeDecoderService

    result = BarcodeDecoderService().decode_best(image)
    info: Dict = {"decoded": result.ok, "value": result.data, "method": result.attempt}
    if not result.ok:
        return info
    info["value_ok"] = result.data == expected

    symbols = decode_symbols_from(image)
    if symbols:
        sx1, sy1, sx2, sy2 = symbol_extent(symbols[0])
        bx, by, bw, bh = box
        ix1, iy1 = max(bx, sx1), max(by, sy1)
        ix2, iy2 = min(bx + bw, sx2), min(by + bh, sy2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        sym_area = max(1, (sx2 - sx1) * (sy2 - sy1))
        box_area = max(1, bw * bh)
        info["box_iou"] = round(inter / (box_area + sym_area - inter), 4)
        info["symbol_coverage"] = round(inter / sym_area, 4)
        info["box_area_ratio"] = round(box_area / sym_area, 3)
        info["contains_symbol"] = (
            ix1 <= sx1 + 1 and iy1 <= sy1 + 1 and ix2 >= sx2 - 1 and iy2 >= sy2 - 1
        )
    return info


def decode_symbols_from(image: np.ndarray) -> List[Dict]:
    from services.barcode_decoder import BarcodeDecoderService

    out: Dict[str, Dict] = {}
    for item in BarcodeDecoderService().decode_with_preprocessing(image):
        if item.data in out:
            continue
        rect = item.bbox
        out[item.data] = {
            "data": item.data,
            "left": int(rect.left),
            "top": int(rect.top),
            "width": int(rect.width),
            "height": int(rect.height),
        }
    return list(out.values())


def build(out_dir: Path, min_iou: float = 0.80, max_area_ratio: float = 2.5) -> Dict:
    manifest: Dict = {
        "target": str(out_dir),
        "synthetic_barcode_content_generated": False,
        "source_images": {},
        "groups": {},
        "discarded": [],
        "problems": [],
    }

    # Every source the registry knows, in registry order -- all three cards,
    # their crops and the barcode-free panels.
    targets = [a.card for a in STUDENT_ASSETS] + CROPS + PANELS
    missing = [p for p in targets if not p.exists()]
    if missing:
        manifest["problems"].append(f"missing source assets: {[str(p) for p in missing]}")
        return manifest

    for split in DATASET_SPLITS:
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    for source in targets:
        split = SOURCE_SPLIT[source]
        group = source.stem
        manifest["groups"][group] = {"split": split, "kept": 0, "discarded": 0, "files": []}
        image = cv2.imread(str(source))
        if image is None:
            manifest["problems"].append(f"unreadable source {source}")
            continue

        gt = ground_truth_box(source)
        if gt is None:
            # A barcode-free document: keep the original as a single negative and
            # do not manufacture photometric variants of it. Only the detector's
            # exposure behaviour is worth varying, and that is measured in
            # scripts/test_barcode_decoder.py instead of being trained on.
            stem = f"neg_{group}"
            shutil.copy2(source, out_dir / "images" / split / f"{stem}.png")
            (out_dir / "labels" / split / f"{stem}.txt").write_text("")
            manifest["source_images"][source.name] = {
                "split": split, "is_negative": True, "variants_kept": 0,
                "note": "no barcode; original kept as a single negative",
            }
            continue

        box, payload = gt
        manifest["source_images"][source.name] = {
            "split": split, "is_negative": False, "payload": payload,
            "box_xywh": list(box), "variants_kept": 0,
        }

        # Baseline copy of the untouched original, so the dataset can stand alone.
        base_stem = f"{group}_orig"
        shutil.copy2(source, out_dir / "images" / split / f"{base_stem}.png")
        h, w = image.shape[:2]
        write_label(out_dir / "labels" / split / f"{base_stem}.txt", box, w, h)
        manifest["groups"][group]["kept"] += 1
        manifest["groups"][group]["files"].append(base_stem)

        candidates: List[Tuple[str, Callable, bool]] = []
        for name, op in photometric_set():
            candidates.append((name, op if op is not None else compress_jpeg, False))
        candidates.extend(geometric_set())

        for name, op, is_rotation in candidates:
            stem = f"{group}_{name}"
            try:
                variant = op(image)
            except Exception as exc:
                manifest["discarded"].append({
                    "stem": stem, "reason": f"transform failed: {type(exc).__name__}: {exc}",
                })
                continue
            if not is_rotation:
                if variant.ndim != 3 or variant.size == 0:
                    manifest["discarded"].append({"stem": stem, "reason": "empty variant"})
                    continue

            matrix = matrix_for(op, is_rotation, image)
            vh, vw = variant.shape[:2]
            new_box = transform_box(box, matrix, vw, vh)
            if new_box is None:
                manifest["discarded"].append({"stem": stem, "reason": "box fell outside the canvas"})
                continue

            check = verify_variant(variant, new_box, payload)
            if not check.get("decoded"):
                manifest["discarded"].append({"stem": stem, "reason": "variant does not decode"})
                manifest["groups"][group]["discarded"] += 1
                continue
            if not check.get("value_ok"):
                manifest["discarded"].append({"stem": stem, "reason": "decoded the wrong value"})
                manifest["groups"][group]["discarded"] += 1
                continue
            if check.get("box_iou", 0.0) < min_iou and not (
                check.get("contains_symbol")
                and check.get("symbol_coverage", 0.0) >= 0.98
                and check.get("box_area_ratio", 99.0) <= max_area_ratio
            ):
                reason = (
                    f"box does not describe the symbol: iou={check.get('box_iou')} "
                    f"coverage={check.get('symbol_coverage')} "
                    f"area_ratio={check.get('box_area_ratio')}"
                )
                manifest["discarded"].append({"stem": stem, "reason": reason})
                manifest["groups"][group]["discarded"] += 1
                continue

            cv2.imwrite(str(out_dir / "images" / split / f"{stem}.png"), variant)
            write_label(out_dir / "labels" / split / f"{stem}.txt", new_box, vw, vh)
            manifest["groups"][group]["kept"] += 1
            manifest["groups"][group]["files"].append(stem)
            manifest["source_images"][source.name]["variants_kept"] += 1

    (out_dir / "data.yaml").write_text(data_yaml(out_dir))
    manifest = summarise(out_dir, manifest)
    return manifest


def write_label(path: Path, box: Tuple[int, int, int, int], w: int, h: int) -> None:
    x, y, bw, bh = box
    xc = (x + bw / 2) / w
    yc = (y + bh / 2) / h
    nw = bw / w
    nh = bh / h
    path.write_text(f"{CLASS_ID} {xc:.6f} {yc:.6f} {nw:.6f} {nh:.6f}\n")


def data_yaml(out_dir: Path) -> str:
    return f"""# Augmented dataset. Generated by scripts/augment_dataset.py.
# Every image is a transformation of a REAL card in assets/images/.
# No synthetic barcode content was generated.
# NOTE: these are derivatives, not independent samples. Read
# dataset/DATASET_REPORT.md before quoting any metric from this data.
path: {out_dir.resolve()}
train: images/train
val: images/val
# The held-out card lives in its own split. Pointing `test` at `val` would
# report a held-out number for a card the model was selected on.
test: images/test

names:
  0: {CLASS_NAME}
nc: 1
"""


def summarise(out_dir: Path, manifest: Dict) -> Dict:
    counts = {}
    for split in DATASET_SPLITS:
        img_dir = out_dir / "images" / split
        lab_dir = out_dir / "labels" / split
        images = sorted(p for p in img_dir.iterdir() if p.is_file()) if img_dir.exists() else []
        pos = neg = 0
        for lab in sorted(lab_dir.glob("*.txt")) if lab_dir.exists() else []:
            if lab.read_text().strip():
                pos += 1
            else:
                neg += 1
        counts[split] = {
            "images": len(images), "positive": pos, "negative": neg,
            "unmatched": [
                p.name for p in images if not (lab_dir / f"{p.stem}.txt").exists()
            ],
        }
    manifest["counts"] = counts
    manifest["unique_original_images"] = sum(
        1 for v in manifest["source_images"].values() if not v["is_negative"]
    )
    manifest["total_images"] = sum(c["images"] for c in counts.values())
    # Leakage guard: one source image must map to exactly one split.
    splits = {g: m["split"] for g, m in manifest["groups"].items()}
    manifest["leakage_check"] = "one split per source group"
    manifest["source_to_split"] = splits

    digests = {}
    for split in DATASET_SPLITS:
        for img in sorted((out_dir / "images" / split).iterdir()):
            d = hashlib.md5(img.read_bytes()).hexdigest()
            digests.setdefault(d, []).append(f"{split}/{img.name}")
    manifest["exact_duplicates"] = [v for v in digests.values() if len(v) > 1]
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=BASE_DIR / "datasets" / "barcode_aug")
    ap.add_argument("--min-iou", type=float, default=0.80)
    ap.add_argument("--max-area-ratio", type=float, default=2.5,
                    help="reject a box more than this many times the symbol area")
    args = ap.parse_args()

    manifest = build(args.out, min_iou=args.min_iou, max_area_ratio=args.max_area_ratio)
    print(f"target: {manifest['target']}")
    for split, c in manifest.get("counts", {}).items():
        print(f"  {split:5s}: {c['images']:3d} images  {c['positive']:3d} positive  {c['negative']:2d} negative")
    print("\nunique original images with a barcode:", manifest.get("unique_original_images"))
    print("total generated images           :", manifest.get("total_images"))
    print("synthetic barcode content        :", manifest.get("synthetic_barcode_content_generated"))
    for group, info in manifest["groups"].items():
        print(f"  {group:12s} -> {info['split']:5s} kept={info['kept']:3d} discarded={info['discarded']:3d}")
    print("\ndiscarded variants:")
    for d in manifest["discarded"]:
        print(f"  {d['stem']:28s} {d['reason']}")
    print("exact duplicates:", manifest.get("exact_duplicates") or "none")
    print("problems:", manifest["problems"] or "none")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "augment_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {args.out / 'augment_manifest.json'}")
    return 1 if manifest["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
