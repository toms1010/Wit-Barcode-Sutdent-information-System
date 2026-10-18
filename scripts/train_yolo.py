#!/usr/bin/env python3
"""Train a barcode detector and promote it only on evidence.

The previous version of this script copied its result straight into
``models_yolo/active/`` whenever the active file happened to be missing, and
never evaluated the candidate. Both behaviours are corrected here:

* the active model is backed up before anything touches it, and it is never
  replaced unless a candidate has been evaluated and actually measures better;
* the candidate is scored on real held-out assets, not only on the synthetic
  validation set, because that is where the previous model was weakest.

Nothing runs automatically. Call it explicitly.

Examples
--------
    python -m scripts.train_yolo --build-dataset --epochs 60
    python -m scripts.train_yolo --epochs 60 --promote
    python -m scripts.train_yolo --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import cv2

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from config.logging_config import setup_logging
from config.settings import settings
from config.student_assets import CARDS, NEGATIVE_PANELS, source_split

logger = setup_logging()

#: Candidate weights live under candidates/; the live scanner only ever reads
#: settings.YOLO_MODEL_PATH (models_yolo/active/best.pt).
CANDIDATE_ROOT = settings.MODELS_YOLO_DIR / "candidates"
BACKUP_ROOT = settings.MODELS_YOLO_DIR / "backups"
ACTIVE_MODEL = settings.YOLO_MODEL_PATH

#: Real, held-out assets used to judge a candidate -- every source image whose
#: split is not ``train``, taken from the shared registry. Card B (validation)
#: and card C (test) both belong here, so a candidate is measured on two unseen
#: cards instead of one and can never be scored on a card it trained on.
REAL_EVAL_ASSETS = [
    path
    for path, split in source_split().items()
    if split != "train" and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
    and "info" not in path.parent.name
]
#: Real assets in the training split. Reported for context, never as the gate.
REAL_TRAIN_ASSETS = [
    path
    for path, split in source_split().items()
    if split == "train" and "info" not in path.parent.name
]
#: Barcode-free documents: a good model must produce nothing on these.
NEGATIVE_EVAL_ASSETS = [settings.info_image(name) for name in NEGATIVE_PANELS]

#: Ultralytics' default augmentation is tuned for hundreds of images. On a
#: dataset of two it is destructive: mosaic cuts the one barcode into quarters,
#: and scale/perspective jitter moves it off the pixels the model must learn.
#: "tame" keeps only the transforms that cannot destroy a barcode's geometry.
AUGMENTATION_PROFILES: Dict[str, Dict] = {
    "default": {},
    "tame": {
        "mosaic": 0.0,
        "mixup": 0.0,
        "copy_paste": 0.0,
        "degrees": 0.0,
        "translate": 0.0,
        "scale": 0.0,
        "shear": 0.0,
        "perspective": 0.0,
        "flipud": 0.0,
        "fliplr": 0.0,
        "erasing": 0.0,
        # Keep the colour jitter that helps dark-on-orange bars survive, but
        # drop it to a fraction of the default.
        "hsv_h": 0.005,
        "hsv_s": 0.3,
        "hsv_v": 0.3,
    },
}


# ------------------------------------------------------------- preflight --


def resolve_pretrained(name: str = "yolov8n.pt") -> Path:
    """Find a compatible pretrained checkpoint.

    Checkpoint names differ between Ultralytics releases, so the requested file
    is looked for in the project root and next to the installed package, and a
    short list of known-good alternatives is tried before giving up.
    """
    candidates: List[Path] = [project_root / name]
    alt = name.replace("yolov8", "yolo11").replace("yolov8", "yolov8")
    candidates += [project_root / alt]
    for alt_name in ("yolo11n.pt", "yolov8n.pt", "yolov8s.pt"):
        candidates.append(project_root / alt_name)

    for path in candidates:
        if path.exists():
            logger.info("Using pretrained weights: %s", path)
            return path

    raise FileNotFoundError(
        "No pretrained checkpoint found. Looked for: "
        + ", ".join(str(c) for c in candidates)
        + ". Download one, e.g. place yolov8n.pt in the project root."
    )


def validate_dataset(data_yaml: Path) -> Dict:
    """Check that the dataset referenced by ``data_yaml`` is complete."""
    import yaml

    if not data_yaml.exists():
        raise FileNotFoundError(f"data.yaml not found at {data_yaml}")

    cfg = yaml.safe_load(data_yaml.read_text()) or {}
    root = (data_yaml.parent / str(cfg.get("path", "."))).resolve()

    names = cfg.get("names")
    if isinstance(names, dict):
        ids = sorted(int(k) for k in names)
    elif isinstance(names, list):
        ids = list(range(len(names)))
    else:
        raise ValueError(f"data.yaml has no usable 'names': {names!r}")
    if ids != [0] or (isinstance(names, dict) and names.get(0) != "barcode"):
        raise ValueError(
            f"expected exactly one class 0 named 'barcode', got {names!r}"
        )

    report: Dict = {"root": str(root), "splits": {}}
    for split in ("train", "val"):
        rel = cfg.get(split)
        if not rel:
            raise ValueError(f"data.yaml missing '{split}' entry")
        img_dir = root / str(rel)
        lab_dir = root / "labels" / split
        if not img_dir.exists():
            raise FileNotFoundError(f"{split} image dir missing: {img_dir}")

        images = [p for p in img_dir.iterdir() if p.is_file()]
        if not images:
            raise ValueError(f"{split} split is empty: {img_dir}")

        positive = negative = 0
        problems: List[str] = []
        for img in images:
            lab = lab_dir / f"{img.stem}.txt"
            if not lab.exists():
                problems.append(f"{img.name}: no label file")
                continue
            if cv2.imread(str(img)) is None:
                problems.append(f"{img.name}: unreadable image")
            if lab.read_text().strip():
                positive += 1
                for lineno, line in enumerate(lab.read_text().splitlines(), 1):
                    parts = line.split()
                    if len(parts) != 5:
                        problems.append(f"{lab.name}:{lineno} expected 5 fields")
            else:
                negative += 1
        report["splits"][split] = {
            "images": len(images),
            "positive": positive,
            "negative": negative,
            "problems": problems,
        }
    logger.info("Dataset OK: %s", json.dumps(report["splits"]))
    return report


# ------------------------------------------------------------ evaluation --


def _decoded_values(image) -> List[str]:
    """pyzbar yields bytes; store text so the report stays JSON-serialisable."""
    import pyzbar.pyzbar as zbar

    values = []
    for obj in zbar.decode(image):
        try:
            values.append(obj.data.decode("utf-8"))
        except UnicodeDecodeError:
            values.append(obj.data.decode("latin-1"))
    return values


def evaluate_asset(model, path: Path) -> Dict:
    """Run the model and the decoder over one image, keeping the stages apart."""
    import numpy as np  # noqa: F401  (kept for parity with the pipeline modules)

    from services.barcode_decoder import BarcodeDecoderService

    image = cv2.imread(str(path))
    if image is None:
        return {"asset": path.name, "readable": False}

    readable = bool(_decoded_values(image))
    result = model(image, conf=0.25, iou=0.45, device=settings.YOLO_DEVICE, verbose=False)[0]
    boxes = []
    if result.boxes is not None and len(result.boxes):
        for b in result.boxes:
            x1, y1, x2, y2 = (int(round(v)) for v in b.xyxy[0].tolist())
            boxes.append({"conf": round(float(b.conf), 3), "box": [x1, y1, x2 - x1, y2 - y1]})

    # Decode from the detection crop and, separately, from the scan region.
    # The crop must be taken the way production takes it -- with the configured
    # margin -- or the gate scores a code path the scanner never runs and
    # rejects a model for producing a tight box.
    decoder = BarcodeDecoderService()
    crop_values: List[str] = []
    for b in boxes:
        x, y, w, h = b["box"]
        margin = settings.SCAN_CROP_MARGIN_RATIO
        x0, y0 = max(0, int(x - w * margin)), max(0, int(y - h * margin))
        x1 = min(image.shape[1], int(x + w * (1 + margin)))
        y1 = min(image.shape[0], int(y + h * (1 + margin)))
        crop = image[y0:y1, x0:x1]
        crop_values += [d.data for d in decoder.decode_with_preprocessing(crop)]

    rx, ry, rw, rh = _scan_region_for(image.shape)
    roi = image[ry : ry + rh, rx : rx + rw]
    region_values = [d.data for d in decoder.decode_with_preprocessing(roi)]

    whole = _decoded_values(image)
    return {
        "asset": path.name,
        "size": [image.shape[1], image.shape[0]],
        "readable": readable,
        "whole_image_value": whole[0] if whole else None,
        "detections": boxes,
        "decode_from_crop": crop_values,
        "decode_from_scan_region": region_values,
    }


def completed_epochs(save_dir: Path) -> int:
    """Epochs actually finished, read from results.csv.

    ``best.pt`` is stripped after training and reports ``epoch = -1``, so the
    checkpoint cannot be used to tell a complete run from an interrupted one.
    results.csv gains one row per completed epoch and is the only reliable
    source.
    """
    results_csv = Path(save_dir) / "results.csv"
    if not results_csv.exists():
        return 0
    lines = [ln for ln in results_csv.read_text().splitlines() if ln.strip()]
    return max(0, len(lines) - 1)


def _scan_region_for(shape) -> tuple:
    from services.scan_geometry import ScanRegion

    h, w = shape[:2]
    r = ScanRegion.for_frame(
        (w, h), settings.SCAN_REGION_WIDTH_RATIO, settings.SCAN_REGION_HEIGHT_RATIO
    )
    return r.rect


def evaluate(model, label: str) -> Dict:
    """Score a model on the real held-out assets plus the negatives."""
    results = {"label": label, "held_out": [], "negatives": [], "train_split_context": []}
    for path in REAL_EVAL_ASSETS:
        if path.exists():
            results["held_out"].append(evaluate_asset(model, path))
        else:
            logger.warning("missing eval asset %s", path)
    for path in NEGATIVE_EVAL_ASSETS:
        if path.exists():
            results["negatives"].append(evaluate_asset(model, path))
    for path in REAL_TRAIN_ASSETS:
        if path.exists():
            results["train_split_context"].append(evaluate_asset(model, path))

    def summarise(items: List[Dict]) -> Dict:
        return {
            "count": len(items),
            "detected": sum(1 for i in items if i["detections"]),
            "false_positives": sum(1 for i in items if not i["readable"] and i["detections"]),
            "decoded_from_crop": sum(1 for i in items if i["decode_from_crop"]),
            "decoded_from_region": sum(1 for i in items if i["decode_from_scan_region"]),
            # One image legitimately yields at most one barcode here. A large
            # per-image box count means the model is firing many overlapping
            # fragments of the same symbol, which is a regression even though
            # the raw detection count still looks "good".
            "max_detections_on_one_image": max((len(i["detections"]) for i in items), default=0),
            "total_detections": sum(len(i["detections"]) for i in items),
            "mean_conf": (
                round(
                    sum(d["conf"] for i in items for d in i["detections"])
                    / max(1, sum(len(i["detections"]) for i in items)),
                    3,
                )
                if items
                else 0.0
            ),
        }

    results["summary"] = {
        "held_out": summarise(results["held_out"]),
        "negatives": summarise(results["negatives"]),
        "train_context": summarise(results["train_split_context"]),
    }
    return results


def pipeline_verification(weights: Path) -> Dict:
    """Full chain on a real card: detect -> gate -> decode -> database lookup."""
    from services.barcode_decoder import BarcodeDecoderService
    from services.scan_pipeline import ScanPipeline, StudentVerifier
    from services.student_service import StudentService
    from services.yolo_detector import YOLODetectorService
    from tests.scanner_fixtures import make_webcam_frame

    detector = YOLODetectorService()
    detector.model = None
    detector.load_model(weights)

    out = []
    for card in CARDS:
        if not card.exists():
            continue
        pipeline = ScanPipeline(
            detector=detector,
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=StudentService().get_student_by_barcode),
            cooldown_seconds=0.0,
        )
        outcome = pipeline.process_frame(make_webcam_frame(card))
        entry = {
            "card": card.name,
            "state": outcome.state.value,
            "decoded": outcome.decoded_value,
            "student": None,
        }
        if outcome.student is not None:
            entry["student"] = outcome.student.full_name
        out.append(entry)
    return out


# -------------------------------------------------------------- promotion --


def backup_active(tag: str) -> Optional[Path]:
    if not ACTIVE_MODEL.exists():
        logger.info("No active model to back up")
        return None
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_ROOT / f"best_{tag}.pt"
    shutil.copy2(ACTIVE_MODEL, dest)
    logger.info("Backed up active model -> %s", dest)
    return dest


def should_promote(active_eval: Optional[Dict], candidate_eval: Dict) -> tuple:
    """Return ``(promote, reason)``.

    Promotion is decided on real held-out behaviour, not on the synthetic
    validation mAP, because that is the axis the previous model was wrong on.
    A candidate must decode the held-out assets at least as well and must not
    introduce false positives on the barcode-free documents.

    A missing active model is *not* permission to promote. "There is nothing to
    compare against" previously returned ``True``, which silently promoted a
    regressed candidate on a machine whose weights had gone missing. Absence of
    a baseline now blocks promotion and says so.
    """
    cn = candidate_eval["summary"]["negatives"]

    # Applies with or without a baseline: never ship a model that fires on a
    # document with no barcode in it.
    if cn["false_positives"]:
        return False, (
            f"candidate fires on barcode-free documents "
            f"({cn['false_positives']} false positives); a baseline comparison "
            f"cannot excuse that"
        )

    if active_eval is None:
        return False, (
            "no active model to compare against; refusing to promote on the "
            "absence of a baseline. Restore or install a known-good model, then "
            "re-run with --evaluate-only."
        )

    a = active_eval["summary"]["held_out"]
    c = candidate_eval["summary"]["held_out"]
    an = active_eval["summary"]["negatives"]

    if c["decoded_from_crop"] < a["decoded_from_crop"]:
        return False, (
            f"candidate decodes fewer held-out crops ({c['decoded_from_crop']}) "
            f"than the active model ({a['decoded_from_crop']})"
        )
    if cn["false_positives"] > an["false_positives"]:
        return False, (
            f"candidate adds false positives on barcode-free documents "
            f"({cn['false_positives']} vs {an['false_positives']})"
        )
    if c["detected"] < a["detected"]:
        return False, (
            f"candidate detects fewer held-out assets ({c['detected']}) "
            f"than the active model ({a['detected']})"
        )
    if c["mean_conf"] < a["mean_conf"]:
        return False, (
            f"candidate mean confidence is lower on held-out assets "
            f"({c['mean_conf']} vs {a['mean_conf']})"
        )
    return True, (
        f"held-out detection {c['detected']}/{c['count']} "
        f"(active {a['detected']}/{a['count']}); "
        f"crop decode {c['decoded_from_crop']} (active {a['decoded_from_crop']}); "
        f"false positives {cn['false_positives']} (active {an['false_positives']}); "
        f"mean conf {c['mean_conf']} (active {a['mean_conf']})"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=project_root / "datasets" / "barcode" / "data.yaml")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default=settings.YOLO_DEVICE)
    ap.add_argument("--name", default="barcode_detector_v2")
    ap.add_argument(
        "--augmentation",
        choices=sorted(AUGMENTATION_PROFILES),
        default="tame",
        help="augmentation profile; 'tame' is the only sensible choice on a 2-image dataset",
    )
    ap.add_argument("--patience", type=int, default=20, help="early-stopping patience in epochs")
    ap.add_argument("--build-dataset", action="store_true", help="rebuild datasets/barcode first")
    ap.add_argument("--promote", action="store_true", help="install the candidate if it evaluates better")
    ap.add_argument("--dry-run", action="store_true", help="validate and report, train nothing")
    ap.add_argument(
        "--evaluate-only",
        type=Path,
        default=None,
        help="skip training; evaluate an existing candidate weights file and apply the promotion gate",
    )
    args = ap.parse_args()

    if args.build_dataset:
        from scripts.build_dataset import build, print_report

        manifest = build(project_root / "datasets" / "barcode")
        print_report(manifest)
        if manifest["problems"]:
            logger.error("Dataset build reported problems; aborting")
            return 1

    logger.info("Validating dataset: %s", args.data)
    dataset_report = validate_dataset(args.data)

    from ultralytics import YOLO

    active_eval = None
    if ACTIVE_MODEL.exists():
        active_eval = evaluate(YOLO(str(ACTIVE_MODEL)), label="active")
        logger.info("Active model on held-out assets: %s", active_eval["summary"]["held_out"])
        logger.info("Active model false positives: %s", active_eval["summary"]["negatives"])

    # Reuse an already-trained candidate instead of retraining.
    if args.evaluate_only:
        weights_path = Path(args.evaluate_only)
        if not weights_path.exists():
            logger.error("Candidate weights not found: %s", weights_path)
            return 1
        return _evaluate_and_maybe_promote(
            weights_path,
            weights_path.parent.parent,
            dataset_report,
            active_eval,
            args,
            run_meta={
                "name": weights_path.parent.parent.name,
                "save_dir": str(weights_path.parent.parent),
                "epochs_requested": None,
                "epochs_completed": completed_epochs(weights_path.parent.parent),
                "reused_existing_run": True,
            },
        )

    try:
        weights = resolve_pretrained()
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1

    logger.info("Ultralytics %s", __import__("ultralytics").__version__)
    logger.info("Active model: %s (exists=%s)", ACTIVE_MODEL, ACTIVE_MODEL.exists())

    if args.dry_run:
        print(json.dumps({
            "dataset": dataset_report,
            "pretrained": str(weights),
            "active_summary": active_eval["summary"] if active_eval else None,
        }, indent=2))
        return 0

    CANDIDATE_ROOT.mkdir(parents=True, exist_ok=True)
    tag = time.strftime("%Y%m%d_%H%M%S")
    run_name = f"{args.name}_{tag}"

    logger.info("Training %s for %d epochs on %s", weights.name, args.epochs, args.device)
    logger.info("Augmentation profile: %s -> %s", args.augmentation, AUGMENTATION_PROFILES[args.augmentation])
    started = time.time()
    try:
        results = YOLO(str(weights)).train(
            data=str(args.data),
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            device=args.device,
            project=str(CANDIDATE_ROOT),
            name=run_name,
            exist_ok=False,
            seed=0,
            patience=args.patience,
            **AUGMENTATION_PROFILES[args.augmentation],
        )
    except Exception as exc:
        logger.error("Training failed: %s", exc)
        return 1

    duration = time.time() - started
    save_dir = Path(results.save_dir)
    best = save_dir / "weights" / "best.pt"
    if not best.exists():
        logger.error("Training finished but %s is missing", best)
        return 1

    return _evaluate_and_maybe_promote(
        best,
        save_dir,
        dataset_report,
        active_eval,
        args,
        run_meta={
            "name": run_name,
            "save_dir": str(save_dir),
            "epochs_requested": args.epochs,
            "epochs_completed": completed_epochs(save_dir),
            "completed": completed_epochs(save_dir) >= args.epochs,
            "imgsz": args.imgsz,
            "batch": args.batch,
            "device": args.device,
            "pretrained": str(weights),
            "augmentation": args.augmentation,
            "augmentation_overrides": AUGMENTATION_PROFILES[args.augmentation],
            "patience": args.patience,
            "duration_seconds": round(duration, 1),
            "reused_existing_run": False,
        },
    )


def _evaluate_and_maybe_promote(
    best: Path,
    save_dir: Path,
    dataset_report: Dict,
    active_eval: Optional[Dict],
    args,
    run_meta: Dict,
) -> int:
    """Score a candidate, run the full pipeline, and gate promotion on it."""
    from ultralytics import YOLO

    epochs_done = run_meta.get("epochs_completed") or 0
    requested = run_meta.get("epochs_requested")
    logger.info("Training ran %s of %s requested epochs", epochs_done, requested)
    if requested and epochs_done < requested:
        logger.warning(
            "Training did not reach the requested epoch count (%d/%d). "
            "Report metrics as incomplete.",
            epochs_done,
            requested,
        )

    ckpt = YOLO(str(best)).ckpt or {}
    metrics = {
        k: v for k, v in (ckpt.get("train_metrics") or {}).items()
        if isinstance(v, (int, float))
    }
    candidate_eval = evaluate(YOLO(str(best)), label="candidate")
    pipeline = pipeline_verification(best)

    report = {
        "run": run_meta,
        "dataset": dataset_report,
        "synthetic_validation_metrics": metrics,
        "active_evaluation": active_eval,
        "candidate_evaluation": candidate_eval,
        "pipeline_verification": pipeline,
    }

    promote, reason = should_promote(active_eval, candidate_eval)
    report["promotion"] = {"promote": promote, "reason": reason, "performed": False}

    if promote and args.promote:
        backup = backup_active(time.strftime("%Y%m%d_%H%M%S"))
        ACTIVE_MODEL.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, ACTIVE_MODEL)
        report["promotion"]["performed"] = True
        report["promotion"]["backup"] = str(backup) if backup else None
        logger.info("Promoted candidate to active model")
    elif args.promote:
        logger.info("Not promoting: %s", reason)
    else:
        logger.info("Promotion not requested (pass --promote to install)")

    out = Path(save_dir) / "evaluation_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    logger.info("Wrote %s", out)

    print(json.dumps({
        "run": run_meta,
        "synthetic_validation_metrics": metrics,
        "active_summary": active_eval["summary"] if active_eval else None,
        "candidate_summary": candidate_eval["summary"],
        "pipeline_verification": pipeline,
        "promotion": report["promotion"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())