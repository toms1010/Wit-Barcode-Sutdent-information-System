#!/usr/bin/env python3
"""Robustness matrix: how the detector and the decoder behave under change.

Runs the six approved assets through controlled, *in-memory* perturbations -
exposure, contrast, tilt, scale, blur, noise, perspective, shadow - and records
detection, decoding and student-lookup outcomes separately for each.

Two rules this script exists to enforce:

1. **Detection is not decoding.** A box around a barcode says nothing about
   whether its value could be read. Each is measured and reported in its own
   column, because collapsing them hides exactly the failure that matters.
2. **Variants are not data.** Nothing here is written into a training set, and
   no count from this file may be quoted as dataset size or as evidence of
   real-world accuracy. The perturbations exist to find the breaking point of a
   decoder, and that is all they are.

Transformations are applied to arrays in memory. The six asset files are never
modified, and nothing is saved unless ``--dump-dir`` is given.

Usage:
    python -m scripts.test_barcode_decoder
    python -m scripts.test_barcode_decoder --dump-dir reports/robustness_samples
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.settings import settings  # noqa: E402
from config.logging_config import setup_logging  # noqa: E402
from config.student_assets import NEGATIVE_PANELS, STUDENT_ASSETS  # noqa: E402

logger = setup_logging()

#: Every card and crop the project has, plus the barcode-free panels. Read from
#: the shared registry so this robustness matrix covers all three students: it
#: used to be built from two hard-coded file names, which is why the third card
#: -- the one that needed preprocessing most -- had no robustness rows at all.
CARDS = [a.card for a in STUDENT_ASSETS]
CROPS = [a.crop for a in STUDENT_ASSETS if a.crop]
PANELS = [settings.info_image(name) for name in NEGATIVE_PANELS]

PASS, FAIL, NOT_TESTED = "PASS", "FAIL", "NOT TESTED"

#: The payload each asset genuinely carries, established by decoding the
#: originals. A variant is only "correct" if it still reads this value; a wrong
#: value is a false read and is reported as such, never as a pass.
TRUTH = {path: entry.payload for entry in STUDENT_ASSETS for path in entry.sources}


# ------------------------------------------------------------ transforms --


def _f(scale: float) -> Callable:
    return lambda i: np.clip(i.astype(np.float32) * scale, 0, 255).astype(np.uint8)


def gamma(g: float) -> Callable:
    lut = np.clip(((np.arange(256) / 255.0) ** g) * 255.0, 0, 255).astype(np.uint8)
    return lambda i: cv2.LUT(i, lut)


def contrast(factor: float) -> Callable:
    def op(i: np.ndarray) -> np.ndarray:
        f = factor
        mean = i.mean(axis=(0, 1), keepdims=True)
        return np.clip((i.astype(np.float32) - mean) * f + mean, 0, 255).astype(np.uint8)

    return op


def rotate(angle: float) -> Callable:
    def op(i: np.ndarray) -> np.ndarray:
        h, w = i.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        cos, sin = abs(m[0, 0]), abs(m[0, 1])
        nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
        m[0, 2] += nw / 2 - w / 2
        m[1, 2] += nh / 2 - h / 2
        fill = tuple(int(v) for v in np.median(i.reshape(-1, i.shape[-1]), axis=0))
        return cv2.warpAffine(i, m, (nw, nh), borderMode=cv2.BORDER_CONSTANT, borderValue=fill)

    return op


def blur(sigma: float) -> Callable:
    return lambda i: cv2.GaussianBlur(i, (0, 0), sigma)


def noise(sigma: float, seed: int = 7) -> Callable:
    rng = np.random.default_rng(seed)

    def op(i: np.ndarray) -> np.ndarray:
        return np.clip(
            i.astype(np.int16) + rng.normal(0, sigma, i.shape), 0, 255
        ).astype(np.uint8)

    return op


def resize(factor: float) -> Callable:
    def op(i: np.ndarray) -> np.ndarray:
        h, w = i.shape[:2]
        nw, nh = max(1, int(w * factor)), max(1, int(h * factor))
        interp = cv2.INTER_AREA if factor < 1 else cv2.INTER_LINEAR
        return cv2.resize(i, (nw, nh), interpolation=interp)

    return op


def shadow(strength: float = 0.45) -> Callable:
    """A soft diagonal darkening, as if a hand or the phone body shaded the card."""

    def op(i: np.ndarray) -> np.ndarray:
        h, w = i.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        ramp = (xx / max(1, w - 1)) * 0.5 + (yy / max(1, h - 1)) * 0.5
        mask = 1.0 - strength * ramp
        return np.clip(i.astype(np.float32) * mask[..., None], 0, 255).astype(np.uint8)

    return op


def perspective(strength: float = 0.08, seed: int = 11) -> Callable:
    """Mild viewing-angle change; the barcode quadrilateral stays estimable."""

    def op(i: np.ndarray) -> np.ndarray:
        h, w = i.shape[:2]
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        jitter = np.random.default_rng(seed).uniform(-strength, strength, (4, 2))
        dst = (src + jitter * np.float32([w, h])).astype(np.float32)
        m = cv2.getPerspectiveTransform(src, dst)
        return cv2.warpPerspective(i, m, (w, h), borderMode=cv2.BORDER_REPLICATE)

    return op


def jpeg(quality: int = 30) -> Callable:
    def op(i: np.ndarray) -> np.ndarray:
        ok, buf = cv2.imencode(".jpg", i, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else i

    return op


#: (label, transform). Photometric first because they are the realistic ones.
CONDITIONS: List[Tuple[str, Callable[[np.ndarray], np.ndarray]]] = [
    ("original", lambda i: i),
    ("exposure x0.25 (dark)", _f(0.25)),
    ("exposure x0.50 (underexposed)", _f(0.50)),
    ("exposure x1.60 (bright)", _f(1.60)),
    ("exposure x2.60 (overexposed, clipped)", _f(2.60)),
    ("gamma 0.55 (lift shadows)", gamma(0.55)),
    ("gamma 1.80 (crush highlights)", gamma(1.80)),
    ("contrast x0.35 (washed out)", contrast(0.35)),
    ("contrast x1.80 (punched)", contrast(1.80)),
    ("shadow 45% diagonal", shadow()),
    ("blur sigma 1.0", blur(1.0)),
    ("blur sigma 2.0", blur(2.0)),
    ("blur sigma 3.0", blur(3.0)),
    ("noise sigma 18", noise(18)),
    ("noise sigma 35", noise(35)),
    ("rotate -5 deg", rotate(-5)),
    ("rotate +5 deg", rotate(5)),
    ("rotate -9 deg", rotate(-9)),
    ("rotate +9 deg", rotate(9)),
    ("rotate -13 deg", rotate(-13)),
    ("rotate +13 deg", rotate(13)),
    ("rotate 90 deg (sideways)", rotate(90)),
    ("resize x0.55 (further away)", resize(0.55)),
    ("resize x0.35 (far away)", resize(0.35)),
    ("resize x1.8 (closer)", resize(1.8)),
    ("perspective 8%", perspective()),
    ("jpeg q30", jpeg()),
]


# -------------------------------------------------------------- measuring --


def iou(a, b) -> float:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax1 + aw, bx1 + bw), min(ay1 + ah, by1 + bh)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def decode_truth(image: np.ndarray, expected: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """Decode an image directly and say whether it produced the true value."""
    from services.barcode_decoder import BarcodeDecoderService

    result = BarcodeDecoderService().decode_best(image)
    if not result.ok:
        return False, None, result.attempt
    return result.data == expected, result.data, result.attempt


def lookup(value: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Resolve a decoded value against the database, without leaking PII."""
    if not value:
        return False, None
    try:
        from services.student_service import StudentService

        student = StudentService().get_student_by_barcode(value)
    except Exception as exc:
        logger.warning("lookup failed for %s: %s", value, type(exc).__name__)
        return False, None
    if student is None:
        return False, None
    return True, student.reference_number


def run_asset(path: Path, detector, truth: Optional[str]) -> List[Dict]:
    base = cv2.imread(str(path))
    if base is None:
        return [{"condition": "-", "status": NOT_TESTED, "note": "asset unreadable"}]

    rows: List[Dict] = []
    for label, op in CONDITIONS:
        variant = op(base)
        row: Dict = {"condition": label, "size": [variant.shape[1], variant.shape[0]]}

        # --- detection -----------------------------------------------------
        dets = detector.detect(variant)
        best = max(dets, key=lambda d: d.confidence) if dets else None
        row["detected"] = bool(dets)
        row["det_count"] = len(dets)
        row["max_conf"] = round(best.confidence, 3) if best else 0.0

        # --- decoding (independent of detection) ---------------------------
        decoded, value, attempt = (False, None, None)
        if truth:
            decoded, value, attempt = decode_truth(variant, truth)
        row["decoded"] = decoded
        row["decoded_value"] = value
        row["decode_method"] = attempt

        # --- student lookup -------------------------------------------------
        resolved, ref = lookup(value)
        row["student_matched"] = resolved

        # --- verdict ---------------------------------------------------------
        if truth:
            # A barcode must still be readable. Detection is reported alongside
            # but does not by itself decide the row: a decoder-only read of the
            # full image is a valid outcome for these assets.
            row["status"] = PASS if decoded else FAIL
            note = ""
            if decoded and not row["detected"]:
                note = "decoded without a YOLO detection"
            if not row["detected"] and decoded:
                row["status"] = PASS
            row["note"] = note
        else:
            # A barcode-free document must produce no detection and no value.
            row["status"] = PASS if (not dets and not value) else FAIL
            row["note"] = "false detection" if dets else ""
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=BASE_DIR / "reports" / "robustness_matrix.json")
    ap.add_argument("--dump-dir", type=Path, default=None,
                    help="optionally save the generated variants here (not for training)")
    args = ap.parse_args()

    from services.yolo_detector import YOLODetectorService

    detector = YOLODetectorService()
    if not detector.load_model(settings.YOLO_MODEL_PATH):
        logger.error("No model at %s; detection columns will be empty.", settings.YOLO_MODEL_PATH)

    print("=" * 100)
    print("Barcode robustness matrix (in-memory variants of the six assets only)")
    print(f"model: {settings.YOLO_MODEL_PATH}  conf={settings.YOLO_CONFIDENCE_THRESHOLD}")
    print("=" * 100)
    print(f"{'asset':22s} {'condition':34s} {'det':>4s} {'conf':>6s} {'decoded':>8s} {'method':>14s} {'student':>8s} {'':>6s}")

    results: Dict[str, List[Dict]] = {}
    totals = {PASS: 0, FAIL: 0, NOT_TESTED: 0}

    for path in CARDS + CROPS + PANELS:
        truth = TRUTH.get(path)
        if not path.exists():
            logger.warning("missing asset %s", path)
            continue
        rows = run_asset(path, detector, truth)
        results[path.name] = rows
        for row in rows:
            totals[row["status"]] += 1
            if args.dump_dir and row["condition"] != "original":
                base = cv2.imread(str(path))
                for label, op in CONDITIONS:
                    if label == row["condition"]:
                        args.dump_dir.mkdir(parents=True, exist_ok=True)
                        out = args.dump_dir / f"{path.stem}__{label.replace(' ', '_').replace('/', '')}.png"
                        cv2.imwrite(str(out), op(base))
                        break
            if path.name.startswith("student"):
                det_s = f"{row['det_count']}"
            else:
                det_s = "yes" if row["detected"] else "no"
            print(
                f"{path.stem:22s} {row['condition']:34s} {det_s:>4s} {row['max_conf']:>6.3f} "
                f"{('yes' if row['decoded'] else 'NO'):>8s} {str(row['decode_method'] or '-'):>14s} "
                f"{('yes' if row['student_matched'] else 'no'):>8s} {row['status']:>6s}"
            )
        print()

    print("=" * 100)
    print(f"PASS={totals[PASS]}  FAIL={totals[FAIL]}  NOT_TESTED={totals[NOT_TESTED]}")
    print("Note: these variants are robustness probes, not dataset samples.")
    print("=" * 100)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "model": str(settings.YOLO_MODEL_PATH),
        "confidence_threshold": settings.YOLO_CONFIDENCE_THRESHOLD,
        "totals": totals,
        "results": results,
    }, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
