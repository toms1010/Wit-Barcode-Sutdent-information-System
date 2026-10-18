#!/usr/bin/env python3
"""Benchmark barcode detection and decoding at simulated scanning distances.

Why this exists
---------------
The project's augmentation set simulates distance with ``resize(0.70)`` and
``resize(1.30)``, which shrink or enlarge the *whole image*. The image and its
bounding box scale together, so the barcode keeps the same share of the frame,
and YOLO letterboxes every image to the same 640x640 input anyway. Measured on
the existing set: ``scale_070`` puts the barcode at 9.874% of the frame and
``orig_copy`` at 9.926%. The "scale" augmentation therefore teaches nothing
about distance, which is what this harness measures the consequence of.

What it does
------------
A card held *farther* from a fixed-resolution webcam occupies a smaller share
of the frame. That is simulated honestly: the real card image is scaled by a
factor and pasted onto a full 1280x720 webcam-sized canvas at a chosen position,
leaving the rest of the frame as scene background. The barcode therefore really
does shrink relative to the sensor, exactly as it does in the hand.

Distances are reported as the fraction of the frame's width the card covers,
which is the quantity that actually governs detection. No centimetres are
claimed: none were measured.

Everything reported here runs the project's real pipeline
(:class:`ScanPipeline` with the active YOLO model, the real ROI gating, the real
decoder and the real database verifier), so the numbers describe the shipping
path and not a mock.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402

logger = setup_logging()

CARD_A = settings.ASSETS_DIR / "images" / "Id_1.png"   # trains
CARD_B = settings.ASSETS_DIR / "images" / "Id_2.png"   # validates
CARD_C = settings.ASSETS_DIR / "images" / "Id_3.png"   # third card, new layout
CROP_A = settings.ASSETS_DIR / "barcodes" / "barcode_2.png"
CROP_B = settings.ASSETS_DIR / "barcodes" / "barcode_1.png"
CROP_C = settings.ASSETS_DIR / "barcodes" / "barcode_3.png"

#: Ground-truth payloads, re-derived by decoding rather than assumed.
PAYLOADS = {
    "Id_1.png": "000065412",
    "Id_2.png": "000065902",
    "Id_3.png": "000065736",
    "barcode_2.png": "000065412",
    "barcode_1.png": "000065902",
    "barcode_3.png": "000065736",
}

FRAME_W, FRAME_H = 1280, 720

#: Apparent size, expressed as the fraction of frame HEIGHT the card occupies.
#: ID cards are tall, so height is the dimension that actually runs out first;
#: parameterising by width would silently clamp every close case.
#: These are apparent sizes, not physical distances: none were measured in cm.
DISTANCES: List[Tuple[str, float]] = [
    ("close", 0.95),
    ("medium", 0.68),
    ("far", 0.42),
    ("very_far", 0.26),
]

POSITIONS: List[Tuple[str, float, float]] = [
    ("center", 0.50, 0.50),
    ("left", 0.22, 0.50),
    ("right", 0.78, 0.50),
    ("upper", 0.50, 0.26),
    ("lower", 0.50, 0.76),
]


def scene_background(rng: np.random.Generator) -> np.ndarray:
    """A plain indoor-ish background so the frame is not pure black.

    A black surround would make a detection unrealistically easy: the model was
    trained on cards composited over varied backgrounds, so the evaluation must
    not hand it an easier problem than the real camera does.
    """
    base = rng.integers(70, 140, size=(FRAME_H, FRAME_W, 3), dtype=np.uint8)
    base = cv2.GaussianBlur(base, (0, 0), 9)
    return base


def compose(card: np.ndarray, size_fraction: float, pos_x: float, pos_y: float,
            angle: float, rng: np.random.Generator,
            name: str) -> Tuple[np.ndarray, Tuple[float, float, float, float]]:
    """Paste a scaled, rotated copy of ``card`` onto a webcam-sized frame.

    ``size_fraction`` is the fraction of frame height the card should occupy.

    Returns the frame and the ground-truth barcode box in *frame* coordinates.
    The box is derived from the final, post-clamp scale, not the requested one:
    an earlier version computed it from the requested scale, so any frame where
    the card had to be shrunk to fit reported a ground truth that did not match
    the pixels, and the measured IoU was meaningless.
    """
    frame = scene_background(rng)

    card_h, card_w = card.shape[:2]
    scale = (FRAME_H * size_fraction) / card_h
    scale = min(scale, (FRAME_W * 0.98) / card_w)

    target_w = max(40, int(round(card_w * scale)))
    target_h = max(20, int(round(card_h * scale)))
    resized = cv2.resize(card, (target_w, target_h), interpolation=cv2.INTER_AREA)

    # Full affine from source card pixels to pasted-canvas pixels, as a 3x3
    # homogeneous matrix. The ground truth is obtained by pushing the box
    # corners through it, which is what the dataset builder does; deriving the
    # box by hand-rolled centre arithmetic produced IoU 0.0 at every non-zero
    # angle.
    M = np.array([[scale, 0.0, 0.0],
                  [0.0, scale, 0.0],
                  [0.0, 0.0, 1.0]], dtype=np.float64)

    if abs(angle) > 1e-3:
        diag = int(np.hypot(target_w, target_h)) + 4
        R = cv2.getRotationMatrix2D((target_w / 2, target_h / 2), angle, 1.0)
        # Compose the rotation with the translate-and-centre step in 2x3 affine
        # form: R @ T would need matching inner dimensions, so the translation
        # column is applied by hand.
        warp = R.copy()
        warp[:, 2] = R[:, 2] + R[:, :2] @ np.array([diag / 2 - target_w / 2,
                                                    diag / 2 - target_h / 2])
        T3 = np.array([[1.0, 0.0, diag / 2 - target_w / 2],
                       [0.0, 1.0, diag / 2 - target_h / 2],
                       [0.0, 0.0, 1.0]], dtype=np.float64)
        R3 = np.vstack([R, [0.0, 0.0, 1.0]])
        M = T3 @ R3 @ M
        resized = cv2.warpAffine(resized, warp, (diag, diag),
                                 flags=cv2.INTER_AREA, borderMode=cv2.BORDER_CONSTANT)

    ch, cw = resized.shape[:2]
    # A rotated card is bigger than the axis-aligned target, so shrink to fit
    # before placing, or the paste truncates and the card leaves the frame.
    if ch > FRAME_H or cw > FRAME_W:
        fit = min(FRAME_H / ch, FRAME_W / cw)
        nw, nh = max(1, int(cw * fit)), max(1, int(ch * fit))
        # The fit shrink composes into the same matrix, so the ground truth
        # follows the pixels that were actually pasted.
        S = np.array([[nw / cw, 0.0, 0.0],
                      [0.0, nh / ch, 0.0],
                      [0.0, 0.0, 1.0]], dtype=np.float64)
        M = S @ M
        resized = cv2.resize(resized, (nw, nh), interpolation=cv2.INTER_AREA)
        ch, cw = nh, nw

    x0 = int(round(np.clip(pos_x * FRAME_W - cw / 2, 0, FRAME_W - cw)))
    y0 = int(round(np.clip(pos_y * FRAME_H - ch / 2, 0, FRAME_H - ch)))
    frame[y0:y0 + ch, x0:x0 + cw] = resized

    T_place = np.array([[1.0, 0.0, x0], [0.0, 1.0, y0], [0.0, 0.0, 1.0]], dtype=np.float64)
    full = T_place @ M

    bx, by, bw, bh = BOXES[name]
    corners = np.array([[bx, by], [bx + bw, by], [bx + bw, by + bh], [bx, by + bh]],
                       dtype=np.float64)
    pts = np.hstack([corners, np.ones((4, 1))]) @ full.T
    gt = (pts[:, 0].min(), pts[:, 1].min(),
          pts[:, 0].max() - pts[:, 0].min(), pts[:, 1].max() - pts[:, 1].min())
    return frame, gt


#: Barcode boxes in each source image, in pixels. Taken from the project's own
#: dataset manifest so training and evaluation agree on geometry.
#:
#: Id_3.png and barcode_3.png are not in that manifest -- they arrived with the
#: third student -- so their boxes were measured here by locating the symbol with
#: the decoder at 4x and dividing the rect back to native resolution. That is a
#: measurement of where the bars are, not a guess from a preview.
BOXES: Dict[str, Tuple[int, int, int, int]] = {
    "Id_1.png": (33, 792, 525, 112),
    "Id_2.png": (60, 793, 479, 121),
    "Id_3.png": (38, 362, 204, 57),
    "barcode_2.png": (12, 6, 579, 124),
    "barcode_1.png": (18, 19, 528, 135),
    "barcode_3.png": (26, 23, 691, 196),
}

_NAME_BY_ID: Dict[int, str] = {}


def card_name_for(card: np.ndarray) -> str:
    """Identify a source image by content hash, never by call order."""
    key = hash(card.tobytes())
    return _NAME_BY_ID[key]


def iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ax1, ay1 = ax0 + aw, ay0 + ah
    bx1, by1 = bx0 + bw, by0 + bh
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def build_pipeline(model_path: Path, device: str, cooldown: float = 0.0,
                   roi_width: float = None, roi_height: float = None,
                   roi_overlap: float = None, confidence: float = None):
    from services.barcode_decoder import BarcodeDecoderService
    from services.scan_pipeline import ScanPipeline, StudentVerifier
    from services.student_service import StudentService
    from services.yolo_detector import YOLODetectorService

    detector = YOLODetectorService()
    if not detector.load_model(model_path):
        raise SystemExit(f"model not loadable: {model_path}")
    if confidence is not None:
        detector.confidence_threshold = confidence
    try:
        detector.device = device
    except Exception:
        logger.warning("could not force device %s; using detector default", device)

    kwargs = {}
    if roi_width is not None or roi_height is not None or roi_overlap is not None:
        # The pipeline reads the ratios from settings at call time, so an
        # override has to be applied to the module constants for the duration of
        # the run. Recorded in the report so a number is never quoted without
        # the gate configuration that produced it.
        import services.scan_pipeline as sp

        if roi_width is not None:
            sp.settings.SCAN_REGION_WIDTH_RATIO = roi_width
        if roi_height is not None:
            sp.settings.SCAN_REGION_HEIGHT_RATIO = roi_height
        if roi_overlap is not None:
            kwargs["overlap_threshold"] = roi_overlap
        _ROI_OVERRIDE.update(width=roi_width, height=roi_height, overlap=roi_overlap)

    return ScanPipeline(
        detector=detector,
        decoder=BarcodeDecoderService(),
        verifier=StudentVerifier(lookup=StudentService().get_student_by_barcode),
        cooldown_seconds=cooldown,
        **kwargs,
    )


#: Records the ROI configuration a run used, for the report.
_ROI_OVERRIDE: Dict = {}


def negative_frames(rng: np.random.Generator) -> List[Tuple[str, np.ndarray]]:
    """Frames that must NOT produce a verified student.

    Without these, a lower threshold or a wider gate would look free: a run that
    detects nothing everywhere also reports zero false positives. These are the
    project's own barcode-free documents plus plain scene background.
    """
    frames: List[Tuple[str, np.ndarray]] = []
    for name in ("student_1_info.png", "student_2_info.png"):
        path = settings.info_image(name)
        image = cv2.imread(str(path))
        if image is None:
            continue
        h, w = image.shape[:2]
        scale = (FRAME_H * 0.68) / h
        resized = cv2.resize(image, (int(w * scale), int(h * scale)))
        ch, cw = resized.shape[:2]
        frame = scene_background(rng)
        x0 = (FRAME_W - cw) // 2
        y0 = (FRAME_H - ch) // 2
        frame[y0:y0 + ch, x0:x0 + cw] = resized
        frames.append((name, frame))
    frames.append(("empty_background", scene_background(rng)))
    return frames


def _load(source: str) -> Optional[np.ndarray]:
    folder = "images" if source.startswith("Id_") else "barcodes"
    return cv2.imread(str(settings.ASSETS_DIR / folder / source))


def run(model_path: Path, device: str, sources: List[str], angles: List[float],
        seed: int, roi_width=None, roi_height=None, roi_overlap=None,
        confidence=None, negatives: bool = True) -> Dict:
    """Two sweeps, deliberately separated.

    *distance sweep* holds the card centred so apparent size is the only
    variable. Mixing positions in would conflate distance with the ROI gate:
    the pipeline only considers barcodes inside the yellow scan frame, so an
    off-centre card is refused on position even when its size is perfect, and
    the number would say nothing about distance.

    *position sweep* holds size at medium and varies placement.

    *negative sweep* runs frames with no barcode at all. Any verified student
    here is a false positive and is counted as such.
    """
    from services.scan_pipeline import ScanState

    pipeline = build_pipeline(model_path, device, roi_width=roi_width,
                              roi_height=roi_height, roi_overlap=roi_overlap,
                              confidence=confidence)
    rng = np.random.default_rng(seed)

    def evaluate(image, name, expected, size_fraction, pos_name, px, py, angle, sweep):
        frac = size_fraction[1]
        frame, gt = compose(image, frac, px, py, angle, rng, name)

        t0 = time.perf_counter()
        outcome = pipeline.process_frame(frame)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        best_iou = 0.0
        if outcome.primary_candidate is not None:
            x, y, w, h = outcome.primary_candidate.bbox
            best_iou = iou((x, y, w, h), gt)

        decoded = outcome.decoded_value
        verified = outcome.state is ScanState.VERIFIED and outcome.student is not None
        return {
            "sweep": sweep,
            "source": name,
            "distance": size_fraction[0] if sweep == "distance" else "medium",
            "size_fraction": frac,
            "position": pos_name,
            "angle": angle,
            "gt_box": [round(v, 1) for v in gt],
            "gt_barcode_w_px": round(gt[2], 1),
            "gt_barcode_area_frac": (gt[2] * gt[3]) / (FRAME_W * FRAME_H),
            "detected": bool(outcome.candidates),
            "decoded": decoded,
            "decoded_correct": decoded == expected,
            "verified": verified,
            "verified_student": getattr(outcome.student, "full_name", None),
            "wrong_student": bool(verified and decoded and decoded != expected),
            "iou": round(best_iou, 4),
            "latency_ms": round(latency_ms, 2),
            "state": outcome.state.value,
        }

    results: List[Dict] = []
    for name in sources:
        image = _load(name)
        if image is None:
            logger.warning("missing source %s", name)
            continue
        expected = PAYLOADS[name]

        for label, frac in DISTANCES:
            results.append(evaluate(image, name, expected, (label, frac),
                                    "center", 0.50, 0.50, 0.0, "distance"))
        for angle in angles:
            if angle == 0.0:
                continue
            results.append(evaluate(image, name, expected, ("medium", 0.68),
                                    "center", 0.50, 0.50, angle, "angle"))
        for pos_name, px, py in POSITIONS:
            if pos_name == "center":
                continue
            results.append(evaluate(image, name, expected, ("medium", 0.68),
                                    pos_name, px, py, 0.0, "position"))

    if negatives:
        for name, frame in negative_frames(rng):
            t0 = time.perf_counter()
            outcome = pipeline.process_frame(frame)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            results.append({
                "sweep": "negative",
                "source": name,
                "distance": "n/a",
                "size_fraction": None,
                "position": "center",
                "angle": 0.0,
                "gt_box": None,
                "gt_barcode_w_px": None,
                "gt_barcode_area_frac": 0.0,
                "detected": bool(outcome.candidates),
                "decoded": outcome.decoded_value,
                "decoded_correct": False,
                "verified": outcome.state is ScanState.VERIFIED and outcome.student is not None,
                "verified_student": getattr(outcome.student, "full_name", None),
                "wrong_student": outcome.state is ScanState.VERIFIED and outcome.student is not None,
                "iou": 0.0,
                "latency_ms": round(latency_ms, 2),
                "state": outcome.state.value,
            })

    return {"results": results}


def summarise(rows: List[Dict]) -> Dict:
    out: Dict = {}

    def stats(subset):
        if not subset:
            return None
        det = sum(r["detected"] for r in subset)
        dec = sum(r["decoded_correct"] for r in subset)
        ver = sum(r["verified"] for r in subset)
        wrong = sum(1 for r in subset if r["wrong_student"])
        lat = [r["latency_ms"] for r in subset]
        ious = [r["iou"] for r in subset if r["detected"]]
        return {
            "samples": len(subset),
            "card_height_pct": round(100 * statistics.mean(r["size_fraction"] for r in subset), 1),
            "barcode_area_frac_pct": round(
                100 * statistics.mean(r["gt_barcode_area_frac"] for r in subset), 3),
            "barcode_width_px": round(statistics.mean(r["gt_barcode_w_px"] for r in subset), 1),
            "detected": det,
            "detection_rate_pct": round(100 * det / len(subset), 1),
            "decoded_correct": dec,
            "decode_rate_pct": round(100 * dec / len(subset), 1),
            "verified": ver,
            "verified_rate_pct": round(100 * ver / len(subset), 1),
            "wrong_student": wrong,
            "decoded_but_unverified": dec - ver,
            "mean_iou_when_detected": round(statistics.mean(ious), 3) if ious else None,
            "latency_median_ms": round(statistics.median(lat), 2),
            "latency_mean_ms": round(statistics.mean(lat), 2),
            "latency_p95_ms": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 2),
        }

    for label, _ in DISTANCES:
        s = stats([r for r in rows if r["sweep"] == "distance" and r["distance"] == label])
        if s:
            out[label] = s
    for angle in sorted({r["angle"] for r in rows if r["sweep"] == "angle"}):
        key = f"angle_{angle:+.0f}"
        s = stats([r for r in rows if r["sweep"] == "angle" and r["angle"] == angle])
        if s:
            out[key] = s
    for pos in sorted({r["position"] for r in rows if r["sweep"] == "position"}):
        s = stats([r for r in rows if r["sweep"] == "position" and r["position"] == pos])
        if s:
            out[f"pos_{pos}"] = s
    s = stats([r for r in rows if r["sweep"] == "position" and r["position"] == "center"])
    if s:
        out["pos_center"] = s

    out["totals"] = {
        "samples": len(rows),
        "wrong_student": sum(1 for r in rows if r["wrong_student"]),
        "decoded_but_unverified": sum(1 for r in rows if r["decoded_correct"] and not r["verified"]),
        "negative_frames": sum(1 for r in rows if r["sweep"] == "negative"),
        "false_positive_detections": sum(1 for r in rows
                                         if r["sweep"] == "negative" and r["detected"]),
        "false_positive_decodes": sum(1 for r in rows
                                      if r["sweep"] == "negative" and r["decoded"]),
        "false_positive_verifications": sum(1 for r in rows
                                            if r["sweep"] == "negative" and r["verified"]),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=str(settings.YOLO_MODEL_PATH))
    ap.add_argument("--device", default=settings.YOLO_DEVICE)
    ap.add_argument("--sources", nargs="+", default=["Id_2.png", "barcode_1.png"],
                    help="held-out sources; use the validation card by default")
    ap.add_argument("--angles", nargs="+", type=float, default=[0.0, -8.0, 8.0])
    ap.add_argument("--seed", type=int, default=20261015)
    ap.add_argument("--out", default="reports/distance_benchmark.json")
    ap.add_argument("--tag", default="")
    ap.add_argument("--roi-width", type=float, default=None,
                    help="override SCAN_REGION_WIDTH_RATIO")
    ap.add_argument("--roi-height", type=float, default=None,
                    help="override SCAN_REGION_HEIGHT_RATIO")
    ap.add_argument("--roi-overlap", type=float, default=None,
                    help="override SCAN_REGION_OVERLAP_THRESHOLD")
    ap.add_argument("--confidence", type=float, default=None,
                    help="override the detector confidence threshold")
    ap.add_argument("--no-negatives", action="store_true",
                    help="skip the false-positive frames (not recommended)")
    args = ap.parse_args()

    payload = run(Path(args.model), args.device, args.sources, args.angles, args.seed,
                  roi_width=args.roi_width, roi_height=args.roi_height,
                  roi_overlap=args.roi_overlap, confidence=args.confidence,
                  negatives=not args.no_negatives)
    summary = summarise(payload["results"])

    print("=" * 78)
    print(f"DISTANCE BENCHMARK  {args.tag or 'run'}")
    print(f"  model  {args.model}")
    print(f"  device {args.device}")
    print(f"  frame  {FRAME_W}x{FRAME_H}; apparent size = fraction of frame height")
    roi_w = _ROI_OVERRIDE.get("width") or settings.SCAN_GATE_WIDTH_RATIO
    roi_h = _ROI_OVERRIDE.get("height") or settings.SCAN_GATE_HEIGHT_RATIO
    roi_o = _ROI_OVERRIDE.get("overlap")
    print(f"  guide  {settings.SCAN_REGION_WIDTH_RATIO:.2f} x "
          f"{settings.SCAN_REGION_HEIGHT_RATIO:.2f} (drawn, aiming aid)")
    print(f"  gate   {roi_w:.2f} x {roi_h:.2f}"
          + (f" overlap {roi_o:.2f}" if roi_o is not None
             else f" overlap {settings.SCAN_REGION_OVERLAP_THRESHOLD}")
          + "  (decode gate)")
    print("=" * 78)
    hdr = (f"{'case':16s} {'n':>3s} {'card%':>6s} {'bcw px':>7s} {'detect%':>8s} "
           f"{'decode%':>8s} {'verify%':>8s} {'wrongID':>8s} {'IoU':>6s} {'p50 ms':>7s} {'p95 ms':>7s}")
    print("-- distance (card centred, upright) --")
    print(hdr)
    for label, _ in DISTANCES:
        if label not in summary:
            continue
        s = summary[label]
        print(f"{label:16s} {s['samples']:3d} {s['card_height_pct']:6.0f} "
              f"{s['barcode_width_px']:7.0f} {s['detection_rate_pct']:8.1f} "
              f"{s['decode_rate_pct']:8.1f} {s['verified_rate_pct']:8.1f} "
              f"{s['wrong_student']:8d} {str(s['mean_iou_when_detected'] or 0):>6s} "
              f"{s['latency_median_ms']:7.2f} {s['latency_p95_ms']:7.2f}")

    extra = [k for k in summary if k.startswith("angle_") or k.startswith("pos_")]
    if extra:
        print("-- angle / position (medium size) --")
        print(hdr)
        for key in sorted(extra):
            s = summary[key]
            print(f"{key:16s} {s['samples']:3d} {s['card_height_pct']:6.0f} "
                  f"{s['barcode_width_px']:7.0f} {s['detection_rate_pct']:8.1f} "
                  f"{s['decode_rate_pct']:8.1f} {s['verified_rate_pct']:8.1f} "
                  f"{s['wrong_student']:8d} {str(s['mean_iou_when_detected'] or 0):>6s} "
                  f"{s['latency_median_ms']:7.2f} {s['latency_p95_ms']:7.2f}")

    t = summary["totals"]
    print(f"\n  wrong-student lookups     : {t['wrong_student']}")
    print(f"  decoded but unverified   : {t['decoded_but_unverified']}")
    print(f"  negative frames          : {t['negative_frames']}")
    print(f"  false-positive detections: {t['false_positive_detections']} "
          f"(decodes {t['false_positive_decodes']}, "
          f"verifications {t['false_positive_verifications']})")
    print("=" * 78)

    out_path = BASE_DIR / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "tag": args.tag,
        "model": args.model,
        "device": args.device,
        "sources": args.sources,
        "angles": args.angles,
        "seed": args.seed,
        "frame": [FRAME_W, FRAME_H],
        "roi": {"width": roi_w, "height": roi_h,
                "overlap": roi_o if roi_o is not None else settings.SCAN_REGION_OVERLAP_THRESHOLD,
                "confidence_override": args.confidence},
        "summary": summary,
        "results": payload["results"],
    }, indent=2))
    print(f"written: {out_path.relative_to(BASE_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())