#!/usr/bin/env python3
"""End-to-end verification of the barcode scanning workflow on real assets.

Reports every check as PASS, FAIL or NOT TESTED with the evidence behind it.
Nothing here is synthesised: the frames come from the six approved assets, and
the only image composition performed is pasting a real crop of a real card into
a webcam-sized canvas (see ``tests/scanner_fixtures.make_webcam_frame``).

The point of this script is separation of concerns. A YOLO hit is *not* a
decode, and a decode is *not* a student match. Each is measured on its own and
reported on its own row, so a failure in one stage cannot be hidden by a success
in another.

Usage:
    python -m scripts.verify_scanner
    python -m scripts.verify_scanner --model models_yolo/active/best.pt
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import cv2

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402

logger = setup_logging()

PASS, FAIL, NOT_TESTED = "PASS", "FAIL", "NOT TESTED"

CARD_1 = settings.ASSETS_DIR / "images" / "Id_1.png"
CARD_2 = settings.ASSETS_DIR / "images" / "Id_2.png"
CARD_3 = settings.ASSETS_DIR / "images" / "Id_3.png"
BARCODE_1 = settings.ASSETS_DIR / "barcodes" / "barcode_1.png"
BARCODE_2 = settings.ASSETS_DIR / "barcodes" / "barcode_2.png"
BARCODE_3 = settings.ASSETS_DIR / "barcodes" / "barcode_3.png"
PANEL_1 = settings.info_image("student_1_info.png")
PANEL_2 = settings.info_image("student_2_info.png")

ALL_CARDS = (CARD_1, CARD_2, CARD_3)
ALL_BARCODES = (BARCODE_1, BARCODE_2, BARCODE_3)

#: The payload on each card, established by decoding the assets themselves.
#: Not assumed from filenames -- and the filenames do not agree with the
#: payloads: ``barcode_1.png`` carries the same payload as ``Id_2.png``, and
#: ``barcode_2.png`` the same as ``Id_1.png``. ``barcode_3.png`` and ``Id_3.png``
#: both carry the third student's payload.
REFERENCE_PAYLOADS = {
    BARCODE_1: "000065902",
    BARCODE_2: "000065412",
    BARCODE_3: "000065736",
    CARD_1: "000065412",
    CARD_2: "000065902",
    CARD_3: "000065736",
}

#: Payload -> the student the database must resolve it to. Used to catch a
#: mapping regression rather than merely a decode failure.
EXPECTED_STUDENTS = {
    "000065412": "Javy Danielle N. Caporal",
    "000065902": "Ronna Mae D. Alemania",
    "000065736": "Steven Anthony M. Oquiana",
}


class Results:
    def __init__(self) -> None:
        self.rows: List[Dict] = []

    def add(self, stage: str, check: str, status: str, evidence: str = "", **extra) -> None:
        row = {"stage": stage, "check": check, "status": status, "evidence": evidence}
        row.update(extra)
        self.rows.append(row)
        print(f"  [{status:9s}] {stage}: {check}" + (f" -- {evidence}" if evidence else ""))

    def counts(self) -> Dict[str, int]:
        out = {PASS: 0, FAIL: 0, NOT_TESTED: 0}
        for row in self.rows:
            out[row["status"]] = out.get(row["status"], 0) + 1
        return out


def direct_decode(path: Path) -> List[Dict]:
    """Decode with no YOLO and no preprocessing ensemble.

    These two files are tight crops of a barcode, so a decoder should read them
    on its own. Running a detector first would only add a way to fail.
    """
    from services.barcode_decoder import BarcodeDecoderService

    image = cv2.imread(str(path))
    if image is None:
        return []
    return [
        {"data": item.data, "type": item.type, "rect": list(map(int, item.bbox))}
        for item in BarcodeDecoderService().decode(image)
    ]


def stage_decode_references(res: Results) -> Dict[Path, str]:
    """Stage 1: every reference barcode must decode directly."""
    print("\n[1] Direct decoding of the reference barcodes (no YOLO)")
    decoded: Dict[Path, str] = {}
    for path in ALL_BARCODES:
        if not path.exists():
            res.add("decode", f"{path.name} decodes directly", NOT_TESTED, "asset missing")
            continue
        hits = direct_decode(path)
        expected = REFERENCE_PAYLOADS[path]
        if not hits:
            res.add("decode", f"{path.name} decodes directly", FAIL, "decoder returned nothing")
            continue
        hit = hits[0]
        decoded[path] = hit["data"]
        status = PASS if hit["data"] == expected else FAIL
        res.add(
            "decode",
            f"{path.name} decodes to {expected}",
            status,
            f"got {hit['data']} type={hit['type']} rect={hit['rect']}",
        )
    return decoded


def stage_card_visibility(res: Results) -> None:
    """Stage 2: are the ID images usable detection images at all?"""
    print("\n[2] Barcode visibility in the ID images")
    for card in ALL_CARDS:
        if not card.exists():
            res.add("assets", f"{card.name} contains a decodable barcode", NOT_TESTED, "asset missing")
            continue
        image = cv2.imread(str(card))
        hits = direct_decode(card)
        if not hits:
            # Id_3.png is a 270x459 card whose symbol is 204 px wide: below the
            # ~230 px floor zbar reports nothing, which is a resolution limit
            # rather than a missing barcode. The ordered strategy's
            # interpolation attempt is what reads it, so it is tried here too.
            from services.barcode_decoder import BarcodeDecoderService

            best = BarcodeDecoderService().decode_best(image)
            if best.ok:
                res.add(
                    "assets",
                    f"{card.name} contains a decodable barcode",
                    PASS,
                    f"{best.data} ({best.type}) via '{best.attempt}' after "
                    f"{best.attempts_tried} attempt(s); no plain decode at "
                    f"{image.shape[1]}x{image.shape[0]}",
                    decode_attempt=best.attempt,
                )
                continue
            res.add(
                "assets",
                f"{card.name} contains a decodable barcode",
                FAIL,
                f"no decode ({image.shape[1]}x{image.shape[0]})",
            )
            continue
        hit = hits[0]
        res.add(
            "assets",
            f"{card.name} contains a decodable barcode",
            PASS,
            f"{hit['data']} ({hit['type']}) rect={hit['rect']} in {image.shape[1]}x{image.shape[0]}",
        )


def stage_detection(res: Results, model_path: Path) -> None:
    """Stage 3: YOLO localisation, counted separately from decoding."""
    print(f"\n[3] YOLO detection with {model_path}")
    from services.barcode_decoder import BarcodeDecoderService
    from services.yolo_detector import YOLODetectorService

    detector = YOLODetectorService()
    if not detector.load_model(model_path):
        res.add("detect", "candidate model loads", FAIL, f"could not load {model_path}")
        return
    res.add("detect", "candidate model loads", PASS, f"{detector.get_model_info()}")

    decoder = BarcodeDecoderService()
    # A document with no barcode must produce nothing. A detection on it is a
    # false positive, which is a different (and worse) failure than a failed
    # decode, so the two are judged separately.
    barcode_free = {PANEL_1, PANEL_2}

    for path in (*ALL_CARDS, *ALL_BARCODES, PANEL_1, PANEL_2):
        if not path.exists():
            res.add("detect", f"{path.name} detection", NOT_TESTED, "asset missing")
            continue
        image = cv2.imread(str(path))
        started = time.perf_counter()
        detections = detector.detect(image)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

        # Decode from the detection crop: this is the real handoff.
        decoded = []
        for det in detections:
            x, y, w, h = det.bbox
            margin = settings.SCAN_CROP_MARGIN_RATIO
            x0 = max(0, int(x - w * margin))
            y0 = max(0, int(y - h * margin))
            x1 = min(image.shape[1], int(x + w * (1 + margin)))
            y1 = min(image.shape[0], int(y + h * (1 + margin)))
            crop = image[y0:y1, x0:x1]
            decoded += [d.data for d in decoder.decode_with_preprocessing(crop)]

        conf = max((d.confidence for d in detections), default=0.0)
        names = {d.class_name for d in detections}
        evidence = (
            f"detections={len(detections)} classes={sorted(names)} "
            f"maxconf={conf:.3f} decoded={decoded} {elapsed_ms}ms"
        )

        if path in barcode_free:
            # Correct outcome: nothing detected, nothing decoded.
            status = PASS if not detections and not decoded else FAIL
            check = f"{path.name}: no false detection on a barcode-free document"
        else:
            status = PASS if detections and decoded else FAIL
            check = f"{path.name}: detect -> crop-decode"

        res.add(
            "detect",
            check,
            status,
            evidence,
            detections=len(detections),
            decoded_from_crop=decoded,
            max_confidence=round(conf, 3),
        )


def stage_pipeline(res: Results, model_path: Path) -> None:
    """Stage 4: the real pipeline, including the unknown-barcode path."""
    print("\n[4] Full pipeline states (detect -> gate -> decode -> verify)")
    try:
        from services.scan_pipeline import ScanPipeline, ScanState, StudentVerifier
        from services.barcode_decoder import BarcodeDecoderService
        from services.student_service import StudentService
        from services.yolo_detector import YOLODetectorService
        from tests.scanner_fixtures import make_webcam_frame
    except Exception as exc:  # pragma: no cover - import guard
        res.add("pipeline", "pipeline importable", FAIL, f"{type(exc).__name__}: {exc}")
        return

    detector = YOLODetectorService()
    if not detector.load_model(model_path):
        res.add("pipeline", "pipeline runs", FAIL, "model would not load")
        return

    try:
        service = StudentService()
        lookup = service.get_student_by_barcode
        lookup("")  # touch the database once so a failure is reported as such
    except Exception as exc:
        res.add("pipeline", "database reachable", FAIL, f"{type(exc).__name__}: {exc}")
        lookup = None
    else:
        res.add("pipeline", "database reachable", PASS, settings.get_database_config()["database"])

    def run(frame, name: str, lookup_override=None) -> object:
        pipeline = ScanPipeline(
            detector=detector,
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=lookup if lookup_override is None else lookup_override),
            cooldown_seconds=0.0,
        )
        return pipeline.process_frame(frame)

    # 4a. A real card whose payload is in the database -> verified.
    known = make_webcam_frame(CARD_1)
    outcome = run(known, "card_1")
    student = getattr(outcome, "student", None)
    res.add(
        "pipeline",
        "known card reaches VERIFIED with a student",
        PASS if outcome.state == ScanState.VERIFIED and student else FAIL,
        f"state={outcome.state.value} decoded={outcome.decoded_value} "
        f"student={getattr(student, 'full_name', None)}",
        state=outcome.state.value,
        decoded=outcome.decoded_value,
    )

    # 4b. The unknown-barcode path. The real database now resolves both real
    # cards, so this cannot be faked by pointing at a missing record: a lookup
    # that behaves as if nothing matched is injected instead, which exercises the
    # state machine with a genuine decode.
    unknown = make_webcam_frame(CARD_2)
    outcome = run(unknown, "card_2", lookup_override=lambda value: None)
    res.add(
        "pipeline",
        "a decode with no matching record reports UNKNOWN_BARCODE and shows no student",
        PASS if outcome.state == ScanState.UNKNOWN_BARCODE and not outcome.student else FAIL,
        f"state={outcome.state.value} decoded={outcome.decoded_value} "
        f"student={getattr(outcome.student, 'full_name', None)}",
        state=outcome.state.value,
        decoded=outcome.decoded_value,
    )

    # 4b-2. All three cards must now resolve against the live database.
    for card in ALL_CARDS:
        if not card.exists():
            res.add("pipeline", f"{card.name} resolves to a student", NOT_TESTED, "asset missing")
            continue
        outcome = run(make_webcam_frame(card), card.name)
        expected = EXPECTED_STUDENTS.get(REFERENCE_PAYLOADS.get(card, ""))
        status = (
            PASS
            if outcome.state == ScanState.VERIFIED
            and outcome.student
            and getattr(outcome.student, "full_name", None) == expected
            else FAIL
        )
        res.add(
            "pipeline",
            f"{card.name} resolves to {expected or 'a student'}",
            status,
            f"state={outcome.state.value} decoded={outcome.decoded_value} "
            f"student={getattr(outcome.student, 'full_name', None)}",
            state=outcome.state.value,
            decoded=outcome.decoded_value,
        )

    # 4c. A barcode-free document in frame: nothing may be reported. This is the
    # safety property that matters most -- a false box must never become a
    # false student, even when the detector fires on a document.
    for panel_path in (PANEL_1, PANEL_2):
        panel = cv2.imread(str(panel_path)) if panel_path.exists() else None
        if panel is None:
            res.add("pipeline", f"{panel_path.name} yields no student", NOT_TESTED, "asset missing")
            continue
        outcome = run(panel, panel_path.name)
        res.add(
            "pipeline",
            f"{panel_path.name} yields no student",
            PASS if outcome.student is None and not outcome.decoded_value else FAIL,
            f"state={outcome.state.value} decoded={outcome.decoded_value} "
            f"student={getattr(outcome.student, 'full_name', None)} "
            f"candidates={len(outcome.candidates)}",
            state=outcome.state.value,
        )

    # 4d. The card held away from the guide must not verify.
    offset = make_webcam_frame(CARD_1, offset=(0, -260))
    outcome = run(offset, "card_1_outside_guide")
    res.add(
        "pipeline",
        "card outside the scan guide is not verified",
        PASS if outcome.state != ScanState.VERIFIED else FAIL,
        f"state={outcome.state.value} decoded={outcome.decoded_value}",
        state=outcome.state.value,
    )


def stage_mapping(res: Results) -> None:
    """Stage 5: which decoded payloads actually resolve to a student record."""
    print("\n[5] Barcode -> student mapping against the database")
    from services.student_service import StudentService

    service = StudentService()
    for payload, expected_name in EXPECTED_STUDENTS.items():
        try:
            student = service.get_student_by_barcode(payload)
        except Exception as exc:
            res.add("mapping", f"{payload} resolves", FAIL, f"{type(exc).__name__}: {exc}")
            continue
        if student is None:
            res.add(
                "mapping",
                f"{payload} resolves to a student",
                FAIL,
                "no database record for the payload decoded from the assets",
                payload=payload,
                resolved=False,
            )
            continue
        status = PASS if student.full_name == expected_name else FAIL
        res.add(
            "mapping",
            f"{payload} resolves to {expected_name}",
            status,
            f"{student.full_name} (ref {student.reference_number}, "
            f"portrait {student.profile_image_path})",
            payload=payload,
            resolved=True,
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", type=Path, default=settings.YOLO_MODEL_PATH)
    ap.add_argument("--out", type=Path, default=BASE_DIR / "reports" / "scanner_verification.json")
    args = ap.parse_args()

    res = Results()
    print("=" * 78)
    print("Barcode scanner verification (existing assets only)")
    print("=" * 78)

    stage_decode_references(res)
    stage_card_visibility(res)

    if args.model.exists():
        stage_detection(res, args.model)
        stage_pipeline(res, args.model)
    else:
        for stage in ("detect", "pipeline"):
            res.add(stage, "model-dependent checks", NOT_TESTED, f"{args.model} does not exist")

    stage_mapping(res)

    counts = res.counts()
    print("\n" + "=" * 78)
    print(f"PASS={counts[PASS]}  FAIL={counts[FAIL]}  NOT_TESTED={counts[NOT_TESTED]}")
    print("=" * 78)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"model": str(args.model), "counts": counts, "results": res.rows}, indent=2))
    print(f"wrote {args.out}")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
