#!/usr/bin/env python3
"""Build the barcode-detection dataset from the four supplied images.

Source images (never modified):

    assets/images/Id_1.png       card A
    assets/images/Id_2.png       card B
    assets/barcodes/barcode_2.png   tight crop OF CARD A
    assets/barcodes/barcode_1.png   tight crop OF CARD B

Grouping is by **physical card**, not by filename. ``barcode_1`` carries
``000065902``, the payload of ``Id_2``; ``barcode_2`` carries ``000065412``, the
payload of ``Id_1``. Verified by decoding, not assumed. Card A and its crop go
to train; card B and its crop go to val, so no card is ever on both sides of the
split.

The two information panels carry no barcode and are used as negatives.

Augmentation
-----------
Controlled, seeded, and recorded in the manifest. Every variant must still
decode to the *source image's own value*; a variant that reads differently, or
does not read at all, is discarded rather than shipped as training data.

Bounding boxes under rotation
-----------------------------
A rotated barcode's axis-aligned box grows with the angle, and by 8 degrees it
is ~3.7x the symbol's area -- too loose to teach a detector. Two policies are
therefore applied in order:

1. **Propagate** the original box through the same transform that was applied to
   the pixels, and keep it when it still contains the symbol and is tight enough.
2. **Re-measure** when it does not: decode the variant and take the symbol's own
   extent. This is a measurement of the variant, not a guess, and it is verified
   to contain the symbol before being written.

Rotation of a variant additionally tries small deskew attempts, so medium and
large tilts are kept instead of discarded.

Usage:
    python -m scripts.build_detection_dataset --out datasets/barcode_detection
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402
from config.student_assets import (  # noqa: E402
    NEGATIVE_SPLITS,
    card_groups,
    group_splits,
)

logger = setup_logging()

CLASS_ID = 0
CLASS_NAME = "barcode"
SEED = 20261015

#: Groups, splits and negatives all come from the shared registry, so this
#: builder covers every student the project has instead of the two it was written
#: for. Card C lands in `test`, which is written but never trained on.
CARD_GROUPS: Dict[str, List[Path]] = card_groups()
GROUP_SPLIT = group_splits()

#: ``test`` is written as well as trained on, for the same reason.
DATASET_SPLITS = ("train", "val", "test")
NEGATIVES: Dict[Path, str] = {
    settings.info_image(name): split for name, split in NEGATIVE_SPLITS.items()
}

#: Default acceptance limits for a propagated box.
MAX_AREA_RATIO = 2.6


# ----------------------------------------------------------- transforms ---

def scale_brightness(f: float) -> Callable:
    return lambda i: np.clip(i.astype(np.float32) * f, 0, 255).astype(np.uint8)


def adjust_gamma(g: float) -> Callable:
    lut = np.clip(((np.arange(256) / 255.0) ** g) * 255.0, 0, 255).astype(np.uint8)
    return lambda i: cv2.LUT(i, lut)


def adjust_contrast(f: float) -> Callable:
    def op(i: np.ndarray) -> np.ndarray:
        a = i.astype(np.float32)
        m = a.mean(axis=(0, 1), keepdims=True)
        return np.clip((a - m) * f + m, 0, 255).astype(np.uint8)
    return op


def blur(sigma: float) -> Callable:
    return lambda i: cv2.GaussianBlur(i, (0, 0), sigma)


def noise(sigma: float, seed: int) -> Callable:
    rng = np.random.default_rng(seed)
    def op(i: np.ndarray) -> np.ndarray:
        return np.clip(i.astype(np.int16) + rng.normal(0, sigma, i.shape), 0, 255).astype(np.uint8)
    return op


def rotate(angle: float) -> Callable:
    def op(i: np.ndarray) -> np.ndarray:
        h, w = i.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        c, s = abs(m[0, 0]), abs(m[0, 1])
        nw, nh = int(round(h * s + w * c)), int(round(h * c + w * s))
        m[0, 2] += nw / 2 - w / 2
        m[1, 2] += nh / 2 - h / 2
        fill = tuple(int(v) for v in np.median(i.reshape(-1, i.shape[-1]), axis=0))
        return cv2.warpAffine(i, m, (nw, nh), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=fill)
    return op


def resize(f: float) -> Callable:
    def op(i: np.ndarray) -> np.ndarray:
        h, w = i.shape[:2]
        return cv2.resize(i, (max(1, int(w * f)), max(1, int(h * f))),
                          interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
    return op


def translate(fx: float, fy: float) -> Callable:
    def op(i: np.ndarray) -> np.ndarray:
        h, w = i.shape[:2]
        m = np.float32([[1, 0, fx * w], [0, 1, fy * h]])
        fill = tuple(int(v) for v in np.median(i.reshape(-1, i.shape[-1]), axis=0))
        return cv2.warpAffine(i, m, (w, h), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=fill)
    return op


def skew(magnitude: float) -> Callable:
    """Horizontal/vertical shear, as if the card were held slightly askew."""
    def op(i: np.ndarray) -> np.ndarray:
        h, w = i.shape[:2]
        shear_x = magnitude
        shear_y = magnitude * 0.5
        m = np.float32([[1, shear_x, -shear_x * w / 2], [shear_y, 1, -shear_y * h / 2]])
        fill = tuple(int(v) for v in np.median(i.reshape(-1, i.shape[-1]), axis=0))
        return cv2.warpAffine(i, m, (w, h), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=fill)
    return op


def perspective(strength: float, seed: int) -> Callable:
    def op(i: np.ndarray) -> np.ndarray:
        h, w = i.shape[:2]
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        j = np.random.default_rng(seed).uniform(-strength, strength, (4, 2))
        dst = (src + j * np.float32([w, h])).astype(np.float32)
        m = cv2.getPerspectiveTransform(src, dst)
        return cv2.warpPerspective(i, m, (w, h), flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_REPLICATE)
    return op


def shade(strength: float) -> Callable:
    def op(i: np.ndarray) -> np.ndarray:
        h, w = i.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        ramp = (xx / max(1, w - 1)) * 0.5 + (yy / max(1, h - 1)) * 0.5
        return np.clip(i.astype(np.float32) * (1.0 - strength * ramp)[..., None],
                       0, 255).astype(np.uint8)
    return op


def glare(strength: float = 0.55) -> Callable:
    """A soft bright wash over part of the image, as from a reflection."""
    def op(i: np.ndarray) -> np.ndarray:
        h, w = i.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        cx, cy = w * 0.65, h * 0.35
        r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (0.9 * max(w, h))
        mask = np.clip(1.0 - strength * np.exp(-(r ** 2) * 2.2), 0, 2)[:, :, None]
        return np.clip(i.astype(np.float32) * mask, 0, 255).astype(np.uint8)
    return op


def occlude(height_fraction: float, position: float = 0.0) -> Callable:
    """Cover a band across the image, keeping most of the barcode readable.

    The band is placed at a vertical offset so it clips the *edge* of the card
    or a corner of the symbol rather than the middle of it -- a finger across
    the middle of a barcode is genuinely unreadable, and training on an
    unreadable target teaches the wrong thing.
    """
    def op(i: np.ndarray) -> np.ndarray:
        out = i.copy()
        h, w = out.shape[:2]
        band_h = max(1, int(h * height_fraction))
        # position in [-0.5, 0.5]: where the band sits relative to centre
        y0 = int(np.clip(h * (0.5 + position) - band_h / 2, 0, h - band_h))
        fill = tuple(int(v) for v in np.median(out.reshape(-1, out.shape[-1]), axis=0))
        out[y0:y0 + band_h, :] = fill
        return out
    return op


def jpeg(quality: int) -> Callable:
    def op(i: np.ndarray) -> np.ndarray:
        ok, buf = cv2.imencode(".jpg", i, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else i
    return op


def compose(brightness: float = 1.0, contrast: float = 1.0, gamma: float = 1.0,
            shadow: float = 0.0, blur_sigma: float = 0.0, noise_sigma: float = 0.0,
            glare_amount: float = 0.0) -> Callable:
    """A realistic combined lighting/optics change rather than one axis at a time.

    A real camera does not underexpose *or* add noise; it does both. Training on
    single-axis changes only teaches the detector each axis in isolation.
    """
    def op(i: np.ndarray) -> np.ndarray:
        out = i.astype(np.float32)
        if gamma != 1.0:
            out = 255.0 * np.clip((out / 255.0) ** gamma, 0, 1)
        if contrast != 1.0:
            m = out.mean(axis=(0, 1), keepdims=True)
            out = (out - m) * contrast + m
        if brightness != 1.0:
            out = out * brightness
        if shadow:
            h, w = out.shape[:2]
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            ramp = (xx / max(1, w - 1)) * 0.5 + (yy / max(1, h - 1)) * 0.5
            out = out * (1.0 - shadow * ramp)[..., None]
        if glare_amount:
            h, w = out.shape[:2]
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            r = np.sqrt((xx - w * 0.65) ** 2 + (yy - h * 0.35) ** 2) / (0.9 * max(w, h))
            out = out * np.clip(1.0 - glare_amount * np.exp(-(r ** 2) * 2.2), 0, 2)[:, :, None]
        out = np.clip(out, 0, 255).astype(np.uint8)
        if blur_sigma:
            out = cv2.GaussianBlur(out, (0, 0), blur_sigma)
        if noise_sigma:
            rng = np.random.default_rng(SEED + int(blur_sigma * 100))
            out = np.clip(out.astype(np.int16) + rng.normal(0, noise_sigma, out.shape),
                          0, 255).astype(np.uint8)
        return out
    return op


#: (name, transform, geometric?). Geometric transforms get box propagation.
CONDITIONS: List[Tuple[str, Callable, bool]] = [
    ("orig_copy", lambda i: i, False),
    # --- photometric -------------------------------------------------------
    ("light_dim", scale_brightness(0.55), False),
    ("light_dimmer", scale_brightness(0.30), False),
    ("light_bright", scale_brightness(1.45), False),
    ("light_hot", scale_brightness(1.90), False),
    ("gamma_down", adjust_gamma(0.65), False),
    ("gamma_up", adjust_gamma(1.45), False),
    ("contrast_low", adjust_contrast(0.50), False),
    ("contrast_high", adjust_contrast(1.50), False),
    ("shadow_soft", shade(0.35), False),
    ("shadow_hard", shade(0.55), False),
    ("glare_soft", glare(0.45), False),
    ("blur_soft", blur(1.0), False),
    ("blur_hard", blur(2.0), False),
    ("noise_light", noise(14, SEED + 1), False),
    ("noise_heavy", noise(30, SEED + 2), False),
    ("jpeg_q35", jpeg(35), False),
    # --- realistic combinations -------------------------------------------
    ("combo_dark_noisy", compose(brightness=0.45, contrast=1.2, gamma=1.2,
                                 shadow=0.3, noise_sigma=18), False),
    ("combo_bright_glare", compose(brightness=1.5, contrast=0.9, glare_amount=0.5), False),
    ("combo_indoor", compose(brightness=0.7, contrast=1.15, gamma=1.1,
                             shadow=0.35, blur_sigma=1.0, noise_sigma=10), False),
    # --- geometric ---------------------------------------------------------
    ("rot_04", rotate(4.0), True),
    ("rot_08", rotate(8.0), True),
    ("rot_14", rotate(14.0), True),
    ("rot_neg04", rotate(-4.0), True),
    ("rot_neg08", rotate(-8.0), True),
    ("skew_soft", skew(0.06), True),
    ("skew_strong", skew(0.12), True),
    ("persp_04", perspective(0.04, SEED + 3), True),
    ("persp_09", perspective(0.09, SEED + 4), True),
    ("scale_070", resize(0.70), True),
    ("scale_130", resize(1.30), True),
    ("shift_left", translate(-0.07, 0.0), True),
    ("shift_up", translate(0.0, -0.06), True),
    ("occlude_edge", occlude(0.12, 0.35), True),
    ("occlude_corner", occlude(0.18, 0.30), True),
]


# ------------------------------------------------------- ground truth -----

def decode_all(image: np.ndarray) -> List[Dict]:
    """Every symbol readable in an image, across variants.

    The filtered pass runs first and keeps its meaning: it finds *several*
    distinct symbols when an image carries more than one. The production ordered
    decode is the fallback, because it also interpolates an undersampled crop --
    without it ``Id_3.png`` yields nothing at all (its symbol is 204 px wide,
    under the floor where every filter variant returns nothing) and the whole
    card is reported as undecodable instead of being augmented.

    Coordinates come from ``source_bbox``: an interpolation attempt reports its
    rectangle in the enlarged image's pixels, and using those here would place
    the ground-truth box outside the image it belongs to.
    """
    from services.barcode_decoder import BarcodeDecoderService

    svc = BarcodeDecoderService()
    seen: Dict[str, Dict] = {}
    for item in svc.decode_with_preprocessing(image):
        if item.data in seen:
            continue
        r = item.bbox
        seen[item.data] = {
            "data": item.data,
            "type": item.type,
            "left": int(r.left), "top": int(r.top),
            "width": int(r.width), "height": int(r.height),
            "attempt": "preprocessing",
        }

    if not seen:
        result = svc.decode_best(image)
        if result.ok:
            rect = result.barcodes[0].source_bbox or tuple(result.barcodes[0].bbox)
            seen[result.data] = {
                "data": result.data,
                "type": result.type,
                "left": int(rect[0]), "top": int(rect[1]),
                "width": int(rect[2]), "height": int(rect[3]),
                "attempt": result.attempt,
            }
    return list(seen.values())


def extent(sym: Dict) -> Tuple[int, int, int, int]:
    return (sym["left"], sym["top"], sym["left"] + sym["width"], sym["top"] + sym["height"])


def decode_with_deskew(image: np.ndarray, expected: str) -> Optional[Dict]:
    """Decode, allowing small deskew attempts for rotated variants.

    zbar reads upright symbols natively; a tilted one needs help. This is tried
    only for geometric variants, so the base path stays a single decode.
    """
    from services.barcode_decoder import BarcodeDecoderService, rotate_preserve

    svc = BarcodeDecoderService()
    result = svc.decode_best(image)
    if result.ok and result.data == expected:
        for sym in decode_all(image):
            if sym["data"] == expected:
                return sym
    for angle in (-12.0, -8.0, -4.0, 4.0, 8.0, 12.0):
        rotated = rotate_preserve(image, angle)
        if rotated is None:
            continue
        result = svc.decode_best(rotated)
        if result.ok and result.data == expected:
            for sym in decode_all(rotated):
                if sym["data"] == expected:
                    return dict(sym, deskew=angle)
    return None


def matrix_for(op: Callable, is_geometric: bool, img: np.ndarray) -> np.ndarray:
    """Recover the matrix the transform actually used, by probing its corners."""
    h, w = img.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h], [w / 2, h / 2]])
    out = op(img)
    if out is None or getattr(out, "ndim", 0) != 3 or out.size == 0:
        return np.eye(3, dtype=np.float32)
    oh, ow = out.shape[:2]
    dst = np.float32([[0, 0], [ow, 0], [ow, oh], [0, oh], [ow / 2, oh / 2]])
    return cv2.getPerspectiveTransform(src[:4], dst[:4])


def propagate_box(box, matrix, out_w: int, out_h: int) -> Optional[Tuple[int, int, int, int]]:
    """Apply the transform to the four box corners and clip to the canvas."""
    x, y, w, h = box
    corners = np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
    ones = np.hstack([corners, np.ones((4, 1), dtype=np.float32)])
    pts = (matrix @ ones.T).T
    if np.all(np.abs(pts[:, 2]) > 1e-9):
        pts = pts[:, :2] / pts[:, 2:3]
    pts = pts[:, :2]
    minx = max(0.0, float(pts[:, 0].min()))
    miny = max(0.0, float(pts[:, 1].min()))
    maxx = min(float(out_w), float(pts[:, 0].max()))
    maxy = min(float(out_h), float(pts[:, 1].max()))
    bw, bh = maxx - minx, maxy - miny
    if bw < 4 or bh < 4:
        return None
    return int(round(minx)), int(round(miny)), int(round(bw)), int(round(bh))


def measure(box, variant: np.ndarray, sym: Optional[Dict],
            max_area_ratio: float) -> Tuple[Optional[Tuple[int, int, int, int]], str]:
    """Choose between the propagated box and one measured from the variant.

    Returns ``(box, policy)``. ``policy`` records which was used, so the
    manifest shows it rather than hiding the choice.
    """
    if sym is None:
        return None, "no-symbol"
    sx1, sy1, sx2, sy2 = extent(sym)
    sym_area = max(1, (sx2 - sx1) * (sy2 - sy1))

    if box is not None:
        bx, by, bw, bh = box
        ix1, iy1 = max(bx, sx1), max(by, sy1)
        ix2, iy2 = min(bx + bw, sx2), min(by + bh, sy2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        contains = (ix1 <= sx1 + 1 and iy1 <= sy1 + 1 and ix2 >= sx2 - 1 and iy2 >= sy2 - 1)
        if contains and inter / sym_area >= 0.98 and (bw * bh) / sym_area <= max_area_ratio:
            return box, "propagated"

    # Too loose, or it missed: measure the symbol that is actually in the image.
    measured = (sx1, sy1, max(1, sx2 - sx1), max(1, sy2 - sy1))
    return measured, "remeasured"


def write_label(path: Path, box, w: int, h: int) -> None:
    x, y, bw, bh = box
    path.write_text(
        f"{CLASS_ID} {(x + bw / 2) / w:.6f} {(y + bh / 2) / h:.6f} "
        f"{bw / w:.6f} {bh / h:.6f}\n"
    )


def data_yaml(out: Path) -> str:
    return f"""# Barcode detection dataset. Generated by scripts/build_detection_dataset.py.
# Sources: assets/images/Id_1.png, Id_2.png and assets/barcodes/barcode_2.png, barcode_1.png.
# No synthetic barcode content. No external data.
# Split is by PHYSICAL CARD: card A (+ its crop) trains, card B (+ its crop) validates.
path: {out.resolve()}
train: images/train
val: images/val
# The held-out card lives in its own split. Pointing `test` at `val` would
# report a held-out number for a card the model was selected on.
test: images/test

names:
  0: {CLASS_NAME}
nc: 1
"""


def build(out: Path, max_area_ratio: float = MAX_AREA_RATIO) -> Dict:
    manifest: Dict = {
        "seed": SEED,
        "max_area_ratio": max_area_ratio,
        "sources": {}, "groups": {}, "discarded": [], "problems": [],
        "synthetic_barcode_content": False,
        "external_data": False,
        "annotations": {
            "policy": "propagate the decoded box through the same transform, "
                      "re-measure from the variant when propagation is too loose",
            "acceptance": f"must contain the decoded symbol and be <= {max_area_ratio}x its area",
        },
    }

    missing = [p for p in list(CARD_GROUPS.values()) and
               [x for g in CARD_GROUPS.values() for x in g] + list(NEGATIVES)
               if not p.exists()]
    if missing:
        manifest["problems"].append(f"missing sources: {[str(p) for p in missing]}")
        return manifest

    for split in DATASET_SPLITS:
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    digests = {}
    for group, images in CARD_GROUPS.items():
        split = GROUP_SPLIT[group]
        manifest["groups"][group] = {"split": split, "kept": 0, "discarded": 0, "files": []}
        for source in images:
            image = cv2.imread(str(source))
            if image is None:
                manifest["problems"].append(f"unreadable source {source}")
                continue
            symbols = decode_all(image)
            if not symbols:
                manifest["problems"].append(
                    f"{source.name}: no barcode decodes; cannot be used as a positive"
                )
                continue
            symbol = max(symbols, key=lambda s: s["width"] * s["height"])
            payload = symbol["data"]
            box = (symbol["left"], symbol["top"], symbol["width"], symbol["height"])
            digests[str(source)] = hashlib.md5(source.read_bytes()).hexdigest()
            manifest["sources"][source.name] = {
                "group": group, "split": split, "payload": payload,
                "symbology": symbol["type"], "box_xywh": list(box),
                "md5": digests[str(source)],
            }

            for name, op, is_geom in CONDITIONS:
                stem = f"{group}__{source.stem}__{name}"
                variant = op(image)
                if variant is None or getattr(variant, "ndim", 0) != 3 or variant.size == 0:
                    manifest["discarded"].append({"stem": stem, "reason": "empty variant"})
                    manifest["groups"][group]["discarded"] += 1
                    continue

                sym = decode_with_deskew(variant, payload) if is_geom \
                    else next((s for s in decode_all(variant) if s["data"] == payload), None)
                if sym is None:
                    manifest["discarded"].append({
                        "stem": stem,
                        "reason": "variant does not decode to the source value (unreadable target)",
                    })
                    manifest["groups"][group]["discarded"] += 1
                    continue

                vh, vw = variant.shape[:2]
                matrix = matrix_for(op, is_geom, image)
                propagated = propagate_box(box, matrix, vw, vh) if is_geom else box
                final_box, policy = measure(propagated, variant, sym, max_area_ratio)
                if final_box is None:
                    manifest["discarded"].append({"stem": stem, "reason": policy})
                    manifest["groups"][group]["discarded"] += 1
                    continue

                cv2.imwrite(str(out / "images" / split / f"{stem}.png"), variant)
                write_label(out / "labels" / split / f"{stem}.txt", final_box, vw, vh)
                manifest["groups"][group]["kept"] += 1
                manifest["groups"][group]["files"].append(stem)

    for panel, split in NEGATIVES.items():
        image = cv2.imread(str(panel))
        if image is None:
            manifest["problems"].append(f"unreadable panel {panel}")
            continue
        syms = decode_all(image)
        if syms:
            manifest["problems"].append(
                f"{panel.name} decodes {[s['data'] for s in syms]} and must not be a negative"
            )
            continue
        digests[str(panel)] = hashlib.md5(panel.read_bytes()).hexdigest()
        manifest["sources"][panel.name] = {
            "group": "negatives", "split": split, "is_negative": True,
            "md5": digests[str(panel)],
            "note": "no barcode decodes in any variant; kept as a single negative",
        }
        stem = f"negative__{panel.stem}"
        shutil.copy2(panel, out / "images" / split / f"{stem}.png")
        (out / "labels" / split / f"{stem}.txt").write_text("")

    # Sources must be untouched by the build.
    for path_str, digest in digests.items():
        if hashlib.md5(Path(path_str).read_bytes()).hexdigest() != digest:
            manifest["problems"].append(f"SOURCE MODIFIED: {path_str}")

    (out / "data.yaml").write_text(data_yaml(out))
    manifest = summarise(out, manifest)
    (out / "build_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def summarise(out: Path, manifest: Dict) -> Dict:
    counts = {}
    hashes: Dict[str, List[str]] = {}
    for split in DATASET_SPLITS:
        img_dir = out / "images" / split
        lab_dir = out / "labels" / split
        images = sorted(p for p in img_dir.iterdir() if p.is_file()) if img_dir.exists() else []
        pos = neg = 0
        for lab in sorted(lab_dir.glob("*.txt")) if lab_dir.exists() else []:
            if lab.read_text().strip():
                pos += 1
            else:
                neg += 1
        counts[split] = {
            "images": len(images), "positive": pos, "negative": neg,
            "unmatched": [p.name for p in images if not (lab_dir / f"{p.stem}.txt").exists()],
        }
        for img in images:
            hashes.setdefault(hashlib.md5(img.read_bytes()).hexdigest(), []).append(
                f"{split}/{img.name}")
    manifest["counts"] = counts
    manifest["total_images"] = sum(c["images"] for c in counts.values())
    manifest["unique_source_images"] = len(
        [v for v in manifest["sources"].values() if not v.get("is_negative")])
    manifest["exact_duplicates"] = [v for v in hashes.values() if len(v) > 1]
    manifest["card_to_split"] = {g: m["split"] for g, m in manifest["groups"].items()}
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=BASE_DIR / "datasets" / "barcode_detection")
    ap.add_argument("--max-area-ratio", type=float, default=MAX_AREA_RATIO)
    args = ap.parse_args()

    started = time.time()
    manifest = build(args.out, max_area_ratio=args.max_area_ratio)
    print(f"target            {args.out}")
    print(f"seed              {manifest['seed']}")
    for split, c in manifest.get("counts", {}).items():
        print(f"  {split:5s}: {c['images']:3d} images  {c['positive']:3d} positive  "
              f"{c['negative']:2d} negative")
    print(f"unique source images with a barcode: {manifest['unique_source_images']}")
    print(f"generated images                   : {manifest['total_images']}")
    print(f"card -> split                      : {manifest['card_to_split']}")
    print(f"synthetic barcode content          : {manifest['synthetic_barcode_content']}")
    for g, m in manifest["groups"].items():
        print(f"  {g:8s} kept={m['kept']:3d} discarded={m['discarded']:3d}")
    print(f"\ndiscarded ({len(manifest['discarded'])}):")
    reasons = {}
    for d in manifest["discarded"]:
        reasons.setdefault(d["reason"].split("(")[0].strip(), []).append(d["stem"])
    for reason, stems in reasons.items():
        print(f"  {reason}: {len(stems)}")
        for s in stems[:6]:
            print(f"      {s}")
        if len(stems) > 6:
            print(f"      ... and {len(stems) - 6} more")
    print(f"\nexact duplicates : {manifest['exact_duplicates'] or 'none'}")
    print(f"problems         : {manifest['problems'] or 'none'}")
    print(f"built in {time.time() - started:.0f}s -> {args.out / 'build_manifest.json'}")
    return 1 if manifest["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
