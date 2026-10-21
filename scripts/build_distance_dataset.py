#!/usr/bin/env python3
"""Build a distance-augmented barcode detection dataset.

Why this dataset exists
-----------------------
The project's existing set simulates distance with ``resize(0.70)`` /
``resize(1.30)``, which scale the *whole image*. The image and its box scale
together, so the barcode keeps the same share of the frame, and YOLO letterboxes
every image to 640x640 regardless. Measured on that set:

    card_A__Id_1__orig_copy.png   barcode = 9.926% of the frame
    card_A__Id_1__scale_070.png   barcode = 9.874% of the frame

A 0.05 percentage-point difference. The augmentation taught the model nothing
about apparent size, which is why a card held at a normal distance was missed:
the real card at 42% of frame height puts its barcode at 144 px wide in a
1280 px frame, and nothing in training was ever that small.

What this builder does instead
------------------------------
Apparent size is simulated the way it actually occurs: the card is scaled and
pasted onto a full 1280x720 webcam-sized canvas, leaving the rest as scene. The
barcode therefore really does shrink relative to the sensor. The bounding box is
obtained by pushing the original box corners through the same affine used to
paste the pixels, so the label is exact by construction rather than estimated.

Honesty constraints
-------------------
* The originals are opened read-only and never written back.
* Split is by PHYSICAL CARD: card A trains, card B validates. A model therefore
  has to generalise to a card it has never seen, which is the only honest way to
  claim distance robustness from three cards.
* Photometric and geometric variants of one card are *not* independent
  real-world samples. The manifest records the parent of every file so the
  report cannot mistake 400 augmented copies of three cards for 400 photographs.
* No barcode content is synthesised: every barcode pixel comes from the two
  real cards, so no fake student identity or payload can be produced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402
from config.student_assets import (  # noqa: E402
    NEGATIVE_SPLITS,
    STUDENT_ASSETS,
    barcode_boxes,
)

logger = setup_logging()

CANVAS_W, CANVAS_H = 1280, 720

#: Ground-truth barcode boxes, read from the shared registry so training,
#: benchmarking and the box-propagation checks cannot disagree about where a
#: symbol is. A hand-copied value for barcode_1.png was once wider (590) than the
#: image (562 px), which produced labels with a normalised width above 1.0;
#: :func:`place` now refuses to write an out-of-canvas box at all, and the
#: registry's own verification re-checks every rectangle against a fresh decode.
BOXES: Dict[str, Tuple[int, int, int, int]] = {
    path.name: box for path, box in barcode_boxes().items()
}

#: Every source image with its group, split and payload. Derived, so a new card
#: is composited into this dataset the day it is registered rather than needing
#: four more lines edited here.
SOURCES: Dict[str, Dict[str, object]] = {
    path.name: {"group": entry.group, "split": entry.split, "payload": entry.payload}
    for entry in STUDENT_ASSETS
    for path in entry.sources
}

NEGATIVES = ["student_1_info.png", "student_2_info.png"]

#: The held-out card is written to `test` as well as the trained-on splits.
DATASET_SPLITS = ("train", "val", "test")

#: Apparent card height as a fraction of the 720 px canvas. These match the
#: benchmark's close/medium/far/very_far so training and evaluation describe the
#: same operating points.
#:
#: The two smallest entries (0.22 and 0.19) put card A's symbol at 78 px and
#: 67 px of frame width. Those are *below* the resolution at which a Code39
#: symbol can be decoded -- a symbol of 175 modules needs roughly one pixel per
#: module, so 67 px cannot be read by any reader -- and they are here so the
#: detector still *locates* a barcode that is too small to read. A located but
#: undecodable barcode is worth more than a missed one: the UI can then tell
#: the student to move closer instead of showing nothing at all.
SIZES = [0.95, 0.80, 0.68, 0.55, 0.42, 0.33, 0.26, 0.22, 0.19]

POSITIONS = [(0.50, 0.50), (0.30, 0.42), (0.70, 0.58), (0.50, 0.30), (0.50, 0.70)]

ANGLES = [0.0, 0.0, -6.0, 6.0, -12.0, 12.0]


def background(rng: np.random.Generator) -> np.ndarray:
    """A plausible indoor scene: soft gradient plus sensor-ish noise.

    Pure black would make every detection trivially easy and would flatter the
    model in exactly the regime we are trying to measure. The gradient also
    gives the detector edges to reject.
    """
    top = rng.integers(90, 170)
    bottom = rng.integers(40, 110)
    column = np.linspace(top, bottom, CANVAS_H, dtype=np.float32)[:, None]
    frame = np.repeat(column[:, :, None], CANVAS_W, axis=1)
    frame = np.repeat(frame, 3, axis=2).astype(np.uint8)
    frame = cv2.GaussianBlur(frame, (0, 0), 7)
    noise = rng.normal(0, 3.0, frame.shape)
    return np.clip(frame.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def photometric(image: np.ndarray, variant: int, rng: np.random.Generator) -> np.ndarray:
    """Lighting / contrast / blur / noise, matching the documented variants."""
    out = image.astype(np.float32)
    if variant == 1:      # dim indoor
        out *= 0.55
    elif variant == 2:    # bright
        out *= 1.45
    elif variant == 3:    # low contrast
        mean = out.mean()
        out = mean + (out - mean) * 0.55
    elif variant == 4:    # mild motion blur
        k = np.zeros((3, 3), np.float32)
        k[0, 0] = k[0, 2] = 0.15
        k[1, :] = 0.2
        out = cv2.filter2D(out, -1, k)
    elif variant == 5:    # sensor noise
        out += rng.normal(0, 6.0, out.shape)
    elif variant == 6:    # warm/dim, low contrast combined
        out = out * 0.75
        mean = out.mean()
        out = mean + (out - mean) * 0.8
    return np.clip(out, 0, 255).astype(np.uint8)


def affine_for(scale: float, angle: float, diag: int, w: int, h: int) -> np.ndarray:
    """3x3 map from source pixels to the rotation canvas.

    The resize scale is part of this matrix. An earlier revision built only the
    rotation/translation here and relied on a separate variable for the scale,
    so the written labels stayed in *source* pixel units: a 95%-height sample
    was labelled with cy=1.20, which is outside the canvas entirely. Labels are
    now always pushed through this matrix, so they cannot drift from the pixels.
    """
    S = np.array([[scale, 0.0, 0.0],
                  [0.0, scale, 0.0],
                  [0.0, 0.0, 1.0]], dtype=np.float64)
    if abs(angle) <= 1e-3:
        return S
    R = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    R3 = np.vstack([R, [0.0, 0.0, 1.0]])
    T = np.array([[1.0, 0.0, diag / 2 - w / 2],
                  [0.0, 1.0, diag / 2 - h / 2],
                  [0.0, 0.0, 1.0]], dtype=np.float64)
    return T @ R3 @ S


def place(source: np.ndarray, name: str, size_fraction: float, pos, angle: float,
          variant: int, rng: np.random.Generator
          ) -> Tuple[np.ndarray, Optional[Tuple[float, float, float, float]]]:
    """Composite one training frame and return it with its ground-truth box."""
    frame = background(rng)

    h, w = source.shape[:2]
    scale = (CANVAS_H * size_fraction) / h
    scale = min(scale, (CANVAS_W * 0.97) / w)

    tw, th = max(8, int(round(w * scale))), max(8, int(round(h * scale)))
    resized = cv2.resize(source, (tw, th), interpolation=cv2.INTER_AREA)

    if abs(angle) > 1e-3:
        diag = int(np.hypot(tw, th)) + 4
        R = cv2.getRotationMatrix2D((tw / 2, th / 2), angle, 1.0)
        warp = R.copy()
        warp[:, 2] = R[:, 2] + R[:, :2] @ np.array([diag / 2 - tw / 2,
                                                    diag / 2 - th / 2])
        M = affine_for(scale, angle, diag, tw, th)
        resized = cv2.warpAffine(resized, warp, (diag, diag),
                                 flags=cv2.INTER_AREA, borderMode=cv2.BORDER_CONSTANT)
    else:
        diag = th
        M = affine_for(scale, 0.0, th, tw, th)

    ch, cw = resized.shape[:2]
    if ch > CANVAS_H or cw > CANVAS_W:
        fit = min(CANVAS_H / ch, CANVAS_W / cw)
        nw, nh = max(1, int(cw * fit)), max(1, int(ch * fit))
        S = np.array([[nw / cw, 0.0, 0.0], [0.0, nh / ch, 0.0], [0.0, 0.0, 1.0]],
                     dtype=np.float64)
        M = S @ M
        resized = cv2.resize(resized, (nw, nh), interpolation=cv2.INTER_AREA)
        ch, cw = nh, nw

    x0 = int(round(np.clip(pos[0] * CANVAS_W - cw / 2, 0, CANVAS_W - cw)))
    y0 = int(round(np.clip(pos[1] * CANVAS_H - ch / 2, 0, CANVAS_H - ch)))
    resized = photometric(resized, variant, rng)
    frame[y0:y0 + ch, x0:x0 + cw] = resized

    T = np.array([[1.0, 0.0, x0], [0.0, 1.0, y0], [0.0, 0.0, 1.0]], dtype=np.float64)
    full = T @ M

    if name not in BOXES:
        # A document with no barcode on it: there is nothing to label, which is
        # exactly why these frames belong in the set.
        return frame, None

    bx, by, bw, bh = BOXES[name]
    corners = np.array([[bx, by], [bx + bw, by], [bx + bw, by + bh], [bx, by + bh]],
                       dtype=np.float64)
    pts = np.hstack([corners, np.ones((4, 1))]) @ full.T
    x0b, y0b = pts[:, 0].min(), pts[:, 1].min()
    x1b, y1b = pts[:, 0].max(), pts[:, 1].max()

    # Reject a box that no longer meaningfully contains a barcode: pasting very
    # small makes the symbol approach a few pixels and the label would be noise.
    if x1b - x0b < 12 or y1b - y0b < 5:
        return frame, None
    # Reject a box that leaves the canvas. A YOLO label is normalised, so a box
    # that overhangs the frame yields a centre or size outside [0,1] and trains
    # the model on an impossible annotation. Better to write no sample.
    if x0b < 0 or y0b < 0 or x1b > CANVAS_W or y1b > CANVAS_H:
        return frame, None
    return frame, (x0b, y0b, x1b - x0b, y1b - y0b)


def write_sample(out: Path, split: str, stem: str, frame: np.ndarray,
                 box: Optional[Tuple[float, float, float, float]]) -> None:
    img_dir = out / "images" / split
    lbl_dir = out / "labels" / split
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(img_dir / f"{stem}.png"), frame)
    if box is None:
        # A negative frame: an empty label file is the YOLO convention.
        (lbl_dir / f"{stem}.txt").write_text("")
        return
    x, y, w, h = box
    cx = (x + w / 2) / CANVAS_W
    cy = (y + h / 2) / CANVAS_H
    (lbl_dir / f"{stem}.txt").write_text(
        f"0 {cx:.6f} {cy:.6f} {w / CANVAS_W:.6f} {h / CANVAS_H:.6f}\n"
    )


def build(out: Path, seed: int, limit: Optional[int] = None) -> Dict:
    rng = np.random.default_rng(seed)
    manifest: Dict = {
        "seed": seed,
        "canvas": [CANVAS_W, CANVAS_H],
        "sizes": SIZES,
        "positions": POSITIONS,
        "angles": ANGLES,
        "sources": SOURCES,
        "important": (
            "Split is by physical card: card_A trains, card_B validates, card_C "
            "is held out entirely. Files are augmented copies of three real "
            "cards, NOT independent photographs. Barcode content is never "
            "synthesised."
        ),
        "files": [],
    }

    counts = {split: 0 for split in DATASET_SPLITS}
    for name, meta in SOURCES.items():
        folder = "images" if name.startswith("Id_") else "barcodes"
        path = settings.ASSETS_DIR / folder / name
        source = cv2.imread(str(path))
        if source is None:
            logger.warning("missing source %s", path)
            continue
        digest = hashlib.md5(path.read_bytes()).hexdigest()
        split = str(meta["split"])
        group = str(meta["group"])

        index = 0
        for size_fraction in SIZES:
            for pos in POSITIONS:
                for angle in ANGLES:
                    variant = index % 7
                    frame, box = place(source, name, size_fraction, pos, angle,
                                       variant, rng)
                    stem = f"{group}__{name[:-4]}__h{int(size_fraction*100):03d}" \
                           f"_p{int(pos[0]*100):03d}_a{angle:+.0f}_v{variant}"
                    if box is None:
                        continue          # too small to label honestly
                    write_sample(out, split, stem, frame, box)
                    counts[split] += 1
                    manifest["files"].append({
                        "file": stem, "split": split, "group": group,
                        "parent": name, "parent_md5": digest,
                        "payload": meta["payload"],
                        "size_fraction": size_fraction,
                        "pos": list(pos), "angle": angle, "variant": variant,
                        "box_xywh": [round(v, 1) for v in box],
                    })
                    index += 1
                    if limit and counts[split] >= limit:
                        break

    # Negatives: documents with no barcode at all, at several sizes. Without
    # these a lower threshold or a wider gate would appear to cost nothing.
    #
    # The count matters. Four copies of a page teach the detector almost
    # nothing about that page's text, and a model trained that way still
    # fires on it -- which is exactly what the promotion gate caught on the
    # 1024-pixel model. Each panel is therefore placed many times, at the
    # scales, positions and angles a person actually holds it at, so the
    # detector learns the *kind* of thing that is not a barcode.
    for name in NEGATIVES:
        path = settings.info_image(name)
        image = cv2.imread(str(path))
        if image is None:
            continue
        stem_base = f"negative__{name[:-4]}"
        digest = hashlib.md5(path.read_bytes()).hexdigest()
        # One split per document, from the registry. This loop used to write each
        # panel into every split, which put the same document in train and in
        # val: leakage that inflates validation and lets a model memorise a
        # specific page instead of learning to reject barcode-free paper.
        split = NEGATIVE_SPLITS.get(name, "train")
        for i, size_fraction in enumerate([0.95, 0.68, 0.42, 0.26]):
            for j, pos in enumerate(POSITIONS):
                for k, angle in enumerate((-9.0, 0.0, 9.0)):
                    frame, _ = place(image, name, size_fraction, pos, angle, i % 7, rng)
                    stem = f"{stem_base}__h{int(size_fraction*100):03d}_p{j}_a{k}"
                    write_sample(out, split, stem, frame, None)
                    counts[split] += 1
                    manifest["files"].append({
                        "file": stem, "split": split, "group": "negative",
                        "parent": name, "parent_md5": digest,
                        "payload": None, "size_fraction": size_fraction,
                        "pos": list(pos), "angle": angle, "variant": i % 7,
                        "box_xywh": None,
                    })

    manifest["counts"] = counts
    (out / "build_manifest.json").write_text(json.dumps(manifest, indent=2))
    (out / "data.yaml").write_text(
        "# Distance-augmented barcode detection dataset.\n"
        "# Generated by scripts/build_distance_dataset.py from the three real cards.\n"
        "# Split by physical card: card_A trains, card_B validates, card_C is\n"
        "# held out entirely and is written to images/test.\n"
        f"path: {out}\n"
        "train: images/train\n"
        "val: images/val\n"
        "test: images/test\n\n"
        "names:\n  0: barcode\nnc: 1\n"
    )
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="datasets/barcode_distance")
    ap.add_argument("--seed", type=int, default=20261015)
    ap.add_argument("--limit", type=int, default=None,
                    help="cap images per split (debugging only)")
    args = ap.parse_args()

    out = BASE_DIR / args.out
    manifest = build(out, args.seed, args.limit)
    print("=" * 78)
    print("DISTANCE DATASET")
    print("=" * 78)
    print(f"  out    {args.out}")
    print(f"  canvas {CANVAS_W}x{CANVAS_H}")
    print(f"  counts {manifest['counts']}")
    by_parent: Dict[str, int] = {}
    for f in manifest["files"]:
        by_parent[f["parent"]] = by_parent.get(f["parent"], 0) + 1
    print("  distinct real source images:", len(by_parent))
    for k, v in sorted(by_parent.items()):
        print(f"     {k:24s} {v} augmented copies")
    print()
    print("  These are augmented copies of three cards, not independent")
    print("  real-world samples. Generalisation claims beyond that are not")
    print("  supported by this collection.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())