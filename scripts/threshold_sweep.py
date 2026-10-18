#!/usr/bin/env python3
"""Confidence-threshold sweep: does raising the threshold actually fix the false positive?

The question this answers is narrow and important. A detection threshold can
"fix" a false positive in two very different ways:

* it removes the false detection because that detection was weak; or
* it removes it because it also threw away genuine, low-confidence detections.

Only the first is a fix. So every threshold is scored on three things at once:
how many of the four real barcodes are still found, how many still *decode*, and
how many false detections survive on the two documents that contain no barcode.

A configuration is only acceptable if it keeps all four real barcodes detectable
and decodable while removing the false positives. Anything else is reported as
"trading away detections", not as a fix.

Detection and decoding are counted separately throughout: a box is not a read.

Usage:
    python -m scripts.threshold_sweep
    python -m scripts.threshold_sweep --models active models_yolo/candidates/<run>/weights/best.pt
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402
from config.student_assets import NEGATIVE_PANELS, STUDENT_ASSETS  # noqa: E402

logger = setup_logging()


PANELS = [settings.info_image(name) for name in NEGATIVE_PANELS]

#: Images that genuinely contain a barcode, with the value it must read. Built
#: from the shared registry so the sweep covers all three cards; it used to be
#: two hard-coded file names, so the third card's confidence curve -- the one that
#: would justify or break the current 0.75 threshold for a card design the model
#: never trained on -- could not be measured at all.
POSITIVES: Dict[Path, str] = {
    path: entry.payload for entry in STUDENT_ASSETS for path in entry.sources
}
#: Images that contain no barcode at all. Any detection here is a false positive.
NEGATIVES = tuple(PANELS)


def crop_with_margin(image: np.ndarray, bbox, margin: float) -> np.ndarray:
    """The crop production actually uses, margin included.

    Measuring a raw detection box instead would score a code path that does not
    exist in the scanner, and would punish a model for producing a tight box.
    """
    x, y, w, h = bbox
    x0 = max(0, int(x - w * margin))
    y0 = max(0, int(y - h * margin))
    x1 = min(image.shape[1], int(x + w * (1 + margin)))
    y1 = min(image.shape[0], int(y + h * (1 + margin)))
    return image[y0:y1, x0:x1]


def score_model(weights: Path, thresholds: List[float]) -> Dict:
    from services.barcode_decoder import BarcodeDecoderService
    from services.yolo_detector import Detection, YOLODetectorService

    detector = YOLODetectorService()
    if not detector.load_model(weights):
        return {"model": str(weights), "loadable": False, "thresholds": {}}
    decoder = BarcodeDecoderService()

    images: Dict[Path, np.ndarray] = {}
    for path in list(POSITIVES) + list(NEGATIVES):
        if path.exists():
            images[path] = cv2.imread(str(path))

    out: Dict = {
        "model": str(weights),
        "loadable": True,
        "classes": detector.class_names,
        "thresholds": {},
    }

    for threshold in thresholds:
        detector.set_confidence_threshold(threshold)
        row: Dict = {
            "threshold": threshold,
            "positives": {},
            "negatives": {},
            "true_detections": 0,
            "true_decodes": 0,
            "false_detections": 0,
            "wrong_value_reads": 0,
        }

        for path, expected in POSITIVES.items():
            image = images.get(path)
            if image is None:
                row["positives"][path.name] = {"status": "NOT TESTED", "reason": "asset missing"}
                continue
            dets = detector.detect(image)
            values: List[str] = []
            for det in dets:
                values += [
                    d.data for d in decoder.decode_with_preprocessing(
                        crop_with_margin(image, det.bbox, settings.SCAN_CROP_MARGIN_RATIO)
                    )
                ]
            correct = expected in values
            row["positives"][path.name] = {
                "detected": bool(dets),
                "max_conf": round(max((d.confidence for d in dets), default=0.0), 3),
                "det_count": len(dets),
                "decoded_values": values,
                "read_correct_value": correct,
            }
            row["true_detections"] += 1 if dets else 0
            row["true_decodes"] += 1 if correct else 0
            row["wrong_value_reads"] += 1 if (values and not correct) else 0

        for path in NEGATIVES:
            image = images.get(path)
            if image is None:
                row["negatives"][path.name] = {"status": "NOT TESTED", "reason": "asset missing"}
                continue
            dets = detector.detect(image)
            values: List[str] = []
            for det in dets:
                values += [
                    d.data for d in decoder.decode_with_preprocessing(
                        crop_with_margin(image, det.bbox, settings.SCAN_CROP_MARGIN_RATIO)
                    )
                ]
            row["negatives"][path.name] = {
                "false_detections": len(dets),
                "max_conf": round(max((d.confidence for d in dets), default=0.0), 3),
                "decoded_values": values,
            }
            row["false_detections"] += len(dets)
            row["wrong_value_reads"] += 1 if values else 0

        # A threshold is acceptable only if it keeps every real barcode both
        # visible and readable while removing every false detection.
        row["acceptable"] = (
            row["true_detections"] == len(POSITIVES)
            and row["true_decodes"] == len(POSITIVES)
            and row["false_detections"] == 0
        )
        if row["false_detections"] == 0 and not row["acceptable"]:
            row["verdict"] = (
                f"clears the false positives but loses genuine detections "
                f"({row['true_detections']}/{len(POSITIVES)} detected, "
                f"{row['true_decodes']}/{len(POSITIVES)} decoded) - rejected"
            )
        elif row["acceptable"]:
            row["verdict"] = "keeps all real barcodes detectable and decodable, zero false detections"
        else:
            row["verdict"] = f"{row['false_detections']} false detection(s) remain"
        out["thresholds"][str(threshold)] = row

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", nargs="+", default=None,
                    help="weight files to compare (default: active model)")
    ap.add_argument("--thresholds", nargs="+", type=float,
                    default=[0.35, 0.50, 0.60, 0.70, 0.80, 0.90])
    ap.add_argument("--out", type=Path, default=BASE_DIR / "reports" / "threshold_sweep.json")
    args = ap.parse_args()

    weights = [Path(m) for m in args.models] if args.models else [settings.YOLO_MODEL_PATH]

    print("=" * 104)
    print("Confidence threshold sweep")
    print(f"crop margin: {settings.SCAN_CROP_MARGIN_RATIO} (production value)")
    print("=" * 104)

    results = []
    for w in weights:
        print(f"\n### {w}")
        started = time.perf_counter()
        result = score_model(w, args.thresholds)
        result["seconds"] = round(time.perf_counter() - started, 1)
        results.append(result)
        if not result["loadable"]:
            print("   NOT LOADABLE")
            continue
        print(f"{'thr':>5s} {'det':>5s} {'decoded':>8s} {'falseFP':>8s} {'wrongval':>9s}  verdict")
        for key in sorted(result["thresholds"], key=float):
            r = result["thresholds"][key]
            print(f"{r['threshold']:>5.2f} {r['true_detections']:>2d}/4 {r['true_decodes']:>3d}/4 "
                  f"{r['false_detections']:>8d} {r['wrong_value_reads']:>9d}  {r['verdict']}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "crop_margin": settings.SCAN_CROP_MARGIN_RATIO,
        "thresholds_tested": args.thresholds,
        "models": results,
    }, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
