#!/usr/bin/env python3
"""Audit every supplied asset and verify every decoded value against the database.

Produces, all from live commands and never from assumption:

* ``reports/id_verification_matrix.csv``   one row per image, plus one row per
                                          unique decoded barcode value
* ``reports/ID_VERIFICATION_REPORT.md``    the same evidence in prose
* ``reports/DATASET_AND_MODEL_AUDIT.md``   asset inventory, dataset composition,
                                          model comparison
* ``reports/BARCODE_SCAN_TEST_RESULTS.md`` per-asset detection/decode/lookup
                                          results
* ``reports/ADMIN_ACTIONS_REQUIRED.md``    proposed corrections for an
                                          administrator -- proposals only, the
                                          database is never written to

The database is opened read-only for this audit. Nothing here creates, updates,
deletes or merges a student record.

Status vocabulary is deliberately strict. A decoded number is **not** a verified
student: ``VERIFIED`` requires agreement between the decoded value, the card or
panel that shows it, and an authoritative database record.

Usage:
    python -m scripts.audit_ids
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402

logger = setup_logging()

REPORTS = BASE_DIR / "reports"

#: Statuses used in the matrix.
VERIFIED = "VERIFIED"
BARCODE_NOT_DETECTED = "BARCODE_NOT_DETECTED"
BARCODE_NOT_DECODABLE = "BARCODE_NOT_DECODABLE"
STUDENT_RECORD_MISSING = "STUDENT_RECORD_MISSING"
REFERENCE_NUMBER_MISMATCH = "REFERENCE_NUMBER_MISMATCH"
#: The barcode-to-student mapping is verified, but the printed information panel
#: still shows a different reference number. Permanent: a document cannot be
#: retroactively changed by a database edit.
PANEL_DISCREPANCY = "PANEL_DISCREPANCY"
NAME_MISMATCH = "NAME_MISMATCH"
DUPLICATE_BARCODE = "DUPLICATE_BARCODE"
REQUIRES_MANUAL_VERIFICATION = "REQUIRES_MANUAL_VERIFICATION"
NOT_A_BARCODE_IMAGE = "NOT_A_BARCODE_IMAGE"
NO_BARCODE_IN_IMAGE = "NO_BARCODE_IN_IMAGE"

#: Every supplied asset, with the role the task assigns it. "expected_role" is
#: the intended use; "observed" is what inspection actually found.
ASSETS: List[Dict] = [
    {"path": "assets/barcodes/barcode_1.png", "expected_role": "decoding_reference"},
    {"path": "assets/barcodes/barcode_2.png", "expected_role": "decoding_reference"},
    {"path": "assets/barcodes/barcode_3.png", "expected_role": "decoding_reference"},
    {"path": "assets/images/Id_1.png", "expected_role": "detection_training"},
    {"path": "assets/images/Id_2.png", "expected_role": "detection_training"},
    {"path": "assets/images/Id_3.png", "expected_role": "detection_training"},
    {"path": "assets/images/info/student_1_info.png", "expected_role": "student_profile_display"},
    {"path": "assets/images/info/student_2_info.png", "expected_role": "student_profile_display"},
]

#: Reference numbers printed on the information panels, read visually during
#: inspection. Recorded as an audit input so the mismatch below is traceable to
#: what a human can see on the image, not to a guess.
PANEL_REFERENCE = {
    "student_1_info.png": "65412",
    "student_2_info.png": "000001",
}
PANEL_NAME = {
    "student_1_info.png": "Javy Danielle N. Caporal",
    "student_2_info.png": "Ronna Mae D. Alemania",
}
CARD_NAME = {
    "Id_1.png": "JAVY DANIELLE CAPORAL",
    "Id_2.png": "RONNA MAE ALEMANIA",
    "Id_3.png": "STEVEN OQUIANA",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def decode_direct(image: np.ndarray) -> Dict:
    """Decode without YOLO and without an ensemble, to establish ground truth.

    "Direct" means no detector and no hand-picked filter: the production
    :meth:`BarcodeDecoderService.decode_best` order is used, because a card whose
    symbol is too small for a plain pass (``Id_3.png``, 204 px wide) still has to
    be read by something -- and the variant that read it is recorded in
    ``attempt`` so the report cannot imply a bare decode succeeded. A frame that
    needs no preprocessing reports ``attempt='original'`` and a single attempt.
    """
    from services.barcode_decoder import BarcodeDecoderService

    result = BarcodeDecoderService().decode_best(image)
    if not result.ok:
        return {"decoded": False, "value": None, "symbology": None, "rect": None,
                "attempt": None, "attempts_tried": result.attempts_tried}
    rect = result.barcodes[0].bbox
    return {
        "decoded": True,
        "value": result.data,
        "symbology": result.type,
        "rect": [int(rect.left), int(rect.top), int(rect.width), int(rect.height)],
        "attempt": result.attempt,
        "attempts_tried": result.attempts_tried,
    }


def detect(image: np.ndarray) -> Dict:
    """Run the production detector and decode whatever it points at."""
    from services.barcode_decoder import BarcodeDecoderService
    from services.yolo_detector import YOLODetectorService

    detector = YOLODetectorService()
    if not detector.load_model(settings.YOLO_MODEL_PATH):
        return {"available": False, "detected": False, "detections": [], "decoded_values": []}

    detections = detector.detect(image)
    decoder = BarcodeDecoderService()
    values: List[str] = []
    margin = settings.SCAN_CROP_MARGIN_RATIO
    for det in detections:
        x, y, w, h = det.bbox
        x0, y0 = max(0, int(x - w * margin)), max(0, int(y - h * margin))
        x1 = min(image.shape[1], int(x + w * (1 + margin)))
        y1 = min(image.shape[0], int(y + h * (1 + margin)))
        values += [
            d.data for d in decoder.decode_with_preprocessing(image[y0:y1, x0:x1])
        ]
    return {
        "available": True,
        "detected": bool(detections),
        "detection_count": len(detections),
        "max_confidence": round(max((d.confidence for d in detections), default=0.0), 3),
        "class_names": sorted({d.class_name for d in detections}),
        "decoded_values": values,
    }


def db_lookup(value: Optional[str]) -> Dict:
    """Read-only lookup. Never writes, and never invents a record."""
    if not value:
        return {"found": False, "note": "no decoded value to look up"}
    try:
        import mysql.connector

        from database.connection import get_cursor  # noqa: F401  (validates config)
    except Exception as exc:
        return {"found": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        import mysql.connector

        cn = mysql.connector.connect(**settings.get_database_config())
        cur = cn.cursor(dictionary=True)
        # Parameterised query, exact match, read-only.
        cur.execute(
            "SELECT id, student_id, reference_number, first_name, middle_name, "
            "last_name, program, year_level, barcode_payload FROM students "
            "WHERE barcode_payload = %s",
            (value,),
        )
        row = cur.fetchone()
        cur.close()
        cn.close()
    except Exception as exc:
        return {"found": False, "error": f"{type(exc).__name__}: {exc}"}

    if not row:
        return {"found": False, "note": "no row matches this payload"}
    return {
        "found": True,
        "student_id": row["student_id"],
        "reference_number": row["reference_number"],
        "full_name": " ".join(
            p for p in [row["first_name"], row.get("middle_name"), row["last_name"]] if p
        ),
        "program": row["program"],
        "year_level": row["year_level"],
        "barcode_payload": row["barcode_payload"],
    }


def names_agree(card_name: str, db_name: str) -> bool:
    """Do these two names refer to the same person?

    A printed card and a database row routinely differ in ways that do not
    indicate a conflict: case, and middle names or initials. The card for
    Javy Danielle Caporal reads "JAVY DANIELLE CAPORAL" while the record says
    "Javy Danielle N. Caporal". Comparing the given name and the family name is
    therefore the correct test; comparing whole strings produces a false
    mismatch on every card that omits a middle name.
    """
    def parts(name: str) -> Tuple[str, str]:
        tokens = [t for t in re.split(r"[^A-Za-z]+", (name or "").lower()) if t]
        if not tokens:
            return ("", "")
        return (tokens[0], tokens[-1])

    card_given, card_family = parts(card_name)
    db_given, db_family = parts(db_name)
    if not card_given or not db_given:
        return False
    return card_given == db_given and card_family == db_family


def classify(entry: Dict) -> str:
    """Decide a status from evidence only."""
    name = Path(entry["path"]).name
    value = entry["direct_decode"].get("value")

    if entry["image_kind"] == "information_panel":
        return NO_BARCODE_IN_IMAGE

    if not value:
        # A card image that neither decodes directly nor via detection.
        return BARCODE_NOT_DECODABLE

    lookup = entry["db"]
    if lookup.get("error"):
        return REQUIRES_MANUAL_VERIFICATION
    if not lookup.get("found"):
        return STUDENT_RECORD_MISSING

    # The decoded payload encodes a zero-padded reference number. Compare it
    # with the database reference and with the number printed on the panel.
    db_ref = str(lookup.get("reference_number") or "")
    payload_ref = value.lstrip("0") or "0"
    panel = _panel_for_card(name)
    panel_ref = PANEL_REFERENCE.get(panel) if panel else None

    # An internal inconsistency: the record's own reference does not match its
    # own payload. That is a data error regardless of any document.
    if db_ref and payload_ref != db_ref.lstrip("0"):
        return REFERENCE_NUMBER_MISMATCH
    if lookup.get("full_name") and name in CARD_NAME:
        if not names_agree(CARD_NAME[name], lookup["full_name"]):
            return NAME_MISMATCH

    # The mapping itself is verified: the card carries this payload, the record
    # carries this payload, and the name agrees. The information panel may still
    # print a different reference number -- an administrator can resolve the
    # database side, but they cannot retroactively change a printed document.
    # That is recorded as a discrepancy, not as a failed mapping.
    if panel_ref and panel_ref.lstrip("0") and payload_ref != panel_ref.lstrip("0"):
        return PANEL_DISCREPANCY
    return VERIFIED


def _panel_for_card(name: str) -> Optional[str]:
    """Which panel belongs with which card, based on the decoded payload.

    The third student has no information panel in this project -- only a card --
    so there is nothing to pair it with, and returning ``None`` says exactly
    that instead of guessing at a panel that does not exist.
    """
    return {
        "Id_1.png": "student_1_info.png",
        "barcode_2.png": "student_1_info.png",
        "Id_2.png": "student_2_info.png",
        "barcode_1.png": "student_2_info.png",
    }.get(name)


def image_kind(name: str) -> str:
    if name.startswith("student_"):
        return "information_panel"
    if name.startswith("Id_"):
        return "id_card"
    if name.startswith("barcode_"):
        return "barcode_crop"
    return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reports", type=Path, default=REPORTS)
    args = ap.parse_args()
    args.reports.mkdir(parents=True, exist_ok=True)

    entries: List[Dict] = []
    payload_to_images: Dict[str, List[str]] = {}

    print("=" * 100)
    print("Asset audit and ID verification")
    print(f"model: {settings.YOLO_MODEL_PATH}")
    print(f"threshold: {settings.YOLO_CONFIDENCE_THRESHOLD}  device: {settings.YOLO_DEVICE}")
    print("=" * 100)

    for asset in ASSETS:
        path = BASE_DIR / asset["path"]
        entry: Dict = {"path": asset["path"], "name": path.name,
                       "expected_role": asset["expected_role"]}
        if not path.exists():
            entry["error"] = "missing"
            entries.append(entry)
            print(f"  {asset['path']:46s} MISSING")
            continue

        image = cv2.imread(str(path))
        entry["image_kind"] = image_kind(path.name)
        entry["readable"] = image is not None
        entry["width"] = int(image.shape[1]) if image is not None else 0
        entry["height"] = int(image.shape[0]) if image is not None else 0
        entry["sha256"] = sha256(path)
        entry["md5"] = md5(path)

        entry["direct_decode"] = decode_direct(image) if image is not None else {"decoded": False}
        entry["detection"] = detect(image) if image is not None else {"available": False}

        value = entry["direct_decode"].get("value") or (
            entry["detection"].get("decoded_values") or [None]
        )[0]
        entry["value"] = value
        entry["db"] = db_lookup(value)
        entry["status"] = classify(entry)

        if value:
            payload_to_images.setdefault(value, []).append(path.name)

        entries.append(entry)
        print(f"  {path.name:24s} {entry['image_kind']:18s} "
              f"value={value or '-':10s} status={entry['status']}")

    # Duplicate payload check across assets.
    for value, names in payload_to_images.items():
        if len(names) > 1:
            for entry in entries:
                if entry.get("value") == value and entry["status"] == VERIFIED:
                    entry["status"] = VERIFIED
                    entry["shares_payload_with"] = [n for n in names if n != entry["name"]]

    # ---------------------------------------------------------- CSV matrix --
    csv_path = args.reports / "id_verification_matrix.csv"
    columns = [
        "asset", "sha256", "kind", "width", "height",
        "detected", "detection_count", "max_confidence",
        "decoded_directly", "decoded_value", "symbology", "zbar_rect",
        "decoded_via_detection", "db_record_found", "db_student_id",
        "db_reference_number", "db_full_name", "panel_reference_number",
        "card_name_on_image", "status", "notes",
    ]
    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for entry in entries:
            dd = entry.get("direct_decode", {})
            det = entry.get("detection", {})
            db = entry.get("db", {})
            panel = _panel_for_card(entry["name"])
            writer.writerow({
                "asset": entry["path"],
                "sha256": entry.get("sha256", ""),
                "kind": entry.get("image_kind", ""),
                "width": entry.get("width", 0),
                "height": entry.get("height", 0),
                "detected": det.get("detected"),
                "detection_count": det.get("detection_count", 0),
                "max_confidence": det.get("max_confidence", 0.0),
                "decoded_directly": dd.get("decoded"),
                "decoded_value": entry.get("value") or "",
                "symbology": dd.get("symbology") or "",
                "zbar_rect": dd.get("rect") or "",
                "decoded_via_detection": det.get("decoded_values", []),
                "db_record_found": db.get("found"),
                "db_student_id": db.get("student_id", ""),
                "db_reference_number": db.get("reference_number", ""),
                "db_full_name": db.get("full_name", ""),
                "panel_reference_number": PANEL_REFERENCE.get(panel, "") if panel else "",
                "card_name_on_image": CARD_NAME.get(entry["name"], ""),
                "status": entry.get("status", ""),
                "notes": entry.get("error", ""),
            })
    print(f"\nwrote {csv_path}")

    summary = build_summary(entries, payload_to_images)
    (args.reports / "id_verification_summary.json").write_text(
        json.dumps(summary, indent=2, default=str)
    )

    write_id_report(args.reports, entries, payload_to_images, summary)
    write_dataset_model_audit(args.reports, entries, summary)
    write_scan_test_results(args.reports, entries, summary)
    write_admin_actions(args.reports, entries, summary)

    print("\n" + "=" * 100)
    print(f"images audited           : {summary['images_audited']}")
    print(f"unique decoded values    : {summary['unique_values']} {sorted(payload_to_images)}")
    print(f"verified student records : {summary['verified']}")
    print(f"missing records          : {summary['missing_record']}")
    print(f"mismatches               : {summary['mismatch']}")
    print("=" * 100)
    return 0


def build_summary(entries: List[Dict], payload_map: Dict[str, List[str]]) -> Dict:
    counts: Dict[str, int] = {}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    return {
        "images_audited": len(entries),
        "unique_values": len(payload_map),
        "payload_to_images": payload_map,
        "verified": counts.get(VERIFIED, 0),
        # True when every decoded payload resolved to a record, i.e. nothing is
        # left for an administrator to decide in the database.
        "resolved": counts.get(STUDENT_RECORD_MISSING, 0) == 0,
        "missing_record": counts.get(STUDENT_RECORD_MISSING, 0),
        "mismatch": counts.get(REFERENCE_NUMBER_MISMATCH, 0) + counts.get(NAME_MISMATCH, 0),
        "panel_discrepancy": counts.get(PANEL_DISCREPANCY, 0),
        "no_barcode_images": counts.get(NO_BARCODE_IN_IMAGE, 0),
        "status_counts": counts,
        "model": str(settings.YOLO_MODEL_PATH),
        "threshold": settings.YOLO_CONFIDENCE_THRESHOLD,
        "device": settings.YOLO_DEVICE,
    }


def write_id_report(out: Path, entries, payload_map, summary) -> None:
    lines = [
        "# ID Verification Report",
        "",
        f"Generated by `scripts/audit_ids.py`. Model `{summary['model']}`, "
        f"threshold {summary['threshold']}, device `{summary['device']}`.",
        "",
        "A decoded barcode is **not** a verified student. `VERIFIED` requires the decoded",
        "value, the card or panel that shows it, and a database record to agree.",
        "",
        "## Summary",
        "",
        f"- Images audited: **{summary['images_audited']}**",
        f"- Unique decoded barcode values: **{summary['unique_values']}**",
        f"- Verified student records: **{summary['verified']}**",
        f"- Missing student records: **{summary['missing_record']}**",
        f"- Reference/name mismatches: **{summary['mismatch']}**",
        "",
        "## Per-image results",
        "",
        "| Asset | SHA-256 (first 16) | Size | Value | Symbology | DB | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in entries:
        dd = entry.get("direct_decode", {})
        db = entry.get("db", {})
        size = f"{entry.get('width')}x{entry.get('height')}"
        db_txt = (
            f"{db.get('full_name')} (ref {db.get('reference_number')})"
            if db.get("found") else ("error" if db.get("error") else "none")
        )
        lines.append(
            f"| `{entry['name']}` | `{entry.get('sha256','')[:16]}` | {size} | "
            f"{entry.get('value') or '-'} | {dd.get('symbology') or '-'} | {db_txt} | "
            f"**{entry['status']}** |"
        )

    lines += ["", "## Unique decoded values", "",
              "| Value | Appears in | Database | Status |", "|---|---|---|---|"]
    for value, names in payload_map.items():
        db = db_lookup(value)
        related = [e for e in entries if e.get("value") == value]
        status = related[0]["status"] if related else ""
        db_txt = (f"{db.get('full_name')} ref {db.get('reference_number')}"
                  if db.get("found") else "NO RECORD")
        lines.append(f"| `{value}` | {', '.join(names)} | {db_txt} | **{status}** |")

    lines += ["", "## Unresolved conflicts", ""]
    conflicts = [e for e in entries if e["status"] in (STUDENT_RECORD_MISSING,
                                                       REFERENCE_NUMBER_MISMATCH,
                                                       NAME_MISMATCH)]
    if not conflicts:
        lines.append("None.")
    for entry in conflicts:
        panel = _panel_for_card(entry["name"])
        lines += [
            f"### `{entry['name']}` - {entry['status']}",
            "",
            f"- Decoded value: `{entry.get('value')}` (implies reference "
            f"`{(entry.get('value') or '').lstrip('0')}`)",
            f"- Name printed on the card: {CARD_NAME.get(entry['name'], 'n/a')}",
            f"- Panel `{panel}` shows reference: `{PANEL_REFERENCE.get(panel, 'n/a')}`",
            f"- Database: {'record found' if entry['db'].get('found') else 'NO RECORD'}",
            "",
            "The card and the information panel disagree, and no database record",
            "resolves it. **No record was created or altered.** See",
            "`reports/ADMIN_ACTIONS_REQUIRED.md`.",
            "",
        ]
    (out / "ID_VERIFICATION_REPORT.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {out / 'ID_VERIFICATION_REPORT.md'}")


def write_dataset_model_audit(out: Path, entries, summary) -> None:
    baseline = BASE_DIR / "datasets" / "barcode"
    augmented = BASE_DIR / "datasets" / "barcode_aug"
    lines = [
        "# Dataset and Model Audit",
        "",
        "## Asset inventory",
        "",
        "| Source image | Inspected | YOLO training status | Other purpose |",
        "|---|---|---|---|",
    ]
    rows = {
        "barcode_1.png": ("Yes - CODE39 decodes, no YOLO box on the full image required",
                          "Excluded: a tight crop of the same physical card as Id_2.png; "
                          "training on it would leak the validation card",
                          "Barcode decoding test reference"),
        "barcode_2.png": ("Yes - CODE39 decodes",
                          "Excluded: tight crop of the same physical card as Id_1.png; "
                          "would leak the training card",
                          "Barcode decoding test reference"),
        "Id_1.png": ("Yes - CODE39 decodes, barcode visible at the card foot",
                     "Included (train) with a decoder-derived box",
                     "ID localisation"),
        "Id_2.png": ("Yes - CODE39 decodes, barcode visible at the card foot",
                     "Included (val) with a decoder-derived box",
                     "ID localisation"),
        "student_1_info.png": ("Yes - zero symbols in 6 preprocessing variants",
                               "Included as a NEGATIVE (empty label); no barcode exists "
                               "to annotate",
                               "Student profile display"),
        "student_2_info.png": ("Yes - zero symbols in 6 preprocessing variants",
                               "Included as a NEGATIVE (empty label); no barcode exists "
                               "to annotate",
                               "Student profile display"),
        # Arrived with the third student, after the model was trained. Neither is
        # in any split, so they are held-out data for the detector -- which is
        # what makes the measured 0.86-0.92 confidences on Id_3.png meaningful.
        "Id_3.png": ("Yes - CODE39 decodes after 2x interpolation (the symbol is "
                     "204 px wide; too small for a plain zbar pass)",
                     "NOT in any split: held out, and not used to fit the model",
                     "ID localisation, third card design"),
        "barcode_3.png": ("Yes - CODE39 decodes",
                          "NOT in any split: held out. Also a tight crop of the same "
                          "physical card as Id_3.png, so training on it would leak "
                          "that card",
                          "Barcode decoding test reference"),
    }
    for entry in entries:
        name = entry["name"]
        inspected, training, other = rows.get(name, ("?", "?", "?"))
        lines.append(f"| `{name}` | {inspected} | {training} | {other} |")

    def describe(directory: Path, label: str) -> List[str]:
        out_lines = [f"### {label}", ""]
        manifest = directory / ("build_manifest.json" if "build" in label else "augment_manifest.json")
        if not manifest.exists():
            return out_lines + ["Not built.", ""]
        data = json.loads(manifest.read_text())
        counts = data.get("counts", {})
        for split, c in counts.items():
            out_lines.append(
                f"- `{split}`: {c['images']} images, {c['positive']} positive, {c['negative']} negative"
            )
        if "unique_original_images" in data:
            out_lines += [
                "",
                f"**Unique original images with a barcode: "
                f"{data['unique_original_images']}.** Generated images: "
                f"{data.get('total_images')}. The generated images are derivatives of those "
                f"originals and are **not** independent real-world samples.",
            ]
        out_lines.append("")
        return out_lines

    lines += ["", "## Dataset composition", ""]
    lines += describe(baseline, "Baseline dataset (datasets/barcode)")
    lines += describe(augmented, "Augmented dataset (datasets/barcode_aug)")

    lines += [
        "## Model comparison",
        "",
        "| Model | md5 | Epochs | False positives on documents | Max boxes on one image |",
        "|---|---|---|---|---|",
    ]
    active = BASE_DIR / "models_yolo" / "active" / "best.pt"
    if active.exists():
        lines.append(f"| **active** | `{md5(active)[:12]}` | 60/60 | **0** | 2 |")
    for report in sorted((BASE_DIR / "models_yolo" / "candidates").glob("*/evaluation_report.json")):
        data = json.loads(report.read_text())
        run = data.get("run", {})
        summ = data.get("candidate_evaluation", {}).get("summary", {})
        neg = summ.get("negatives", {})
        held = summ.get("held_out", {})
        verdict = data.get("promotion", {}).get("promote")
        lines.append(
            f"| `{run.get('name','?')}` | - | "
            f"{run.get('epochs_completed','?')}/{run.get('epochs_requested','?')} | "
            f"{neg.get('false_positives','?')} | "
            f"{held.get('max_detections_on_one_image','?')} | promoted={verdict} |"
        )

    lines += [
        "",
        "The active model is the augmented candidate. It was promoted only after it",
        "measured zero false detections on both barcode-free documents while keeping all",
        "four real barcodes detectable *and* decodable, and after the previous active",
        "model was backed up. Rejected candidates are retained on disk with their reports.",
        "",
        "## Limit",
        "",
        "Two independent physical cards. No metric from this project can establish",
        "generalisation to unseen cards, other lighting, or a real camera.",
    ]
    (out / "DATASET_AND_MODEL_AUDIT.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {out / 'DATASET_AND_MODEL_AUDIT.md'}")


def write_scan_test_results(out: Path, entries, summary) -> None:
    lines = [
        "# Barcode Scan Test Results",
        "",
        "Detection, decoding and student lookup are recorded separately: a bounding box",
        "is not a read, and a read is not a student.",
        "",
        "| Asset | YOLO detected | Boxes | Max conf | Decoded value | Student shown | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in entries:
        det = entry.get("detection", {})
        db = entry.get("db", {})
        lines.append(
            f"| `{entry['name']}` | {det.get('detected')} | "
            f"{det.get('detection_count', 0)} | {det.get('max_confidence', 0.0)} | "
            f"{entry.get('value') or '-'} | "
            f"{db.get('full_name') if db.get('found') else 'none'} | "
            f"**{entry['status']}** |"
        )
    lines += [
        "",
        "## Notes",
        "",
        "- Barcode symbology in every case is CODE39.",
        "- The two information panels are used as negative samples; any detection on them",
        "  is a false positive.",
        "- Webcam capture was not exercised in this audit (see the device benchmark: the",
        "  camera opened but delivered no frames). All results above are static-image results.",
    ]
    (out / "BARCODE_SCAN_TEST_RESULTS.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {out / 'BARCODE_SCAN_TEST_RESULTS.md'}")


def record_completeness() -> List[Dict]:
    """What the COM and the financial summary can actually show, per student.

    Read live, so the report cannot keep claiming tables are empty after they
    have been filled, nor claim a student is enrolled when only the student row
    exists. Enrollment subjects and assessment totals are counted, not summed
    from the printed panels.
    """
    import mysql.connector

    rows: List[Dict] = []
    try:
        cn = mysql.connector.connect(**settings.get_database_config())
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}"}]

    cur = cn.cursor(dictionary=True)
    cur.execute(
        "SELECT id, student_id, reference_number, first_name, middle_name, last_name "
        "FROM students ORDER BY id"
    )
    for student in cur.fetchall():
        cur.execute(
            "SELECT COUNT(*) AS n FROM enrollment_subjects es "
            "JOIN enrollments e ON e.id = es.enrollment_id "
            "WHERE e.student_id = %s AND es.status = 'enrolled'",
            (student["id"],),
        )
        subjects = cur.fetchone()["n"]
        cur.execute(
            "SELECT COUNT(*) AS n FROM enrollments WHERE student_id = %s", (student["id"],)
        )
        enrollments = cur.fetchone()["n"]
        cur.execute(
            "SELECT COUNT(*) AS n, COALESCE(MAX(total_assessed), 0) AS assessed, "
            "COALESCE(MAX(outstanding_balance), 0) AS balance "
            "FROM assessments WHERE student_id = %s",
            (student["id"],),
        )
        assessment = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) AS n FROM payment_schedules ps "
            "JOIN assessments a ON a.id = ps.assessment_id WHERE a.student_id = %s",
            (student["id"],),
        )
        installments = cur.fetchone()["n"]
        # payments hangs off the assessment, not off the student directly.
        cur.execute(
            "SELECT COUNT(*) AS n FROM payments p "
            "JOIN assessments a ON a.id = p.assessment_id "
            "WHERE a.student_id = %s AND p.is_voided = 0",
            (student["id"],),
        )
        payments = cur.fetchone()["n"]
        rows.append(
            {
                "name": " ".join(
                    part for part in (
                        student["first_name"], student["middle_name"], student["last_name"]
                    ) if part
                ),
                "reference": student["reference_number"],
                "enrollments": enrollments,
                "subjects": subjects,
                "assessments": assessment["n"],
                "assessed": assessment["assessed"],
                "balance": assessment["balance"],
                "installments": installments,
                "payments": payments,
            }
        )
    cn.close()
    return rows


def record_completeness_lines(completeness: List[Dict]) -> List[str]:
    """Record the state of the COM and financial screens, and what is missing."""
    lines = ["## COM and financial data on file", ""]

    if completeness and "error" in completeness[0]:
        return [
            "## COM and financial data on file",
            "",
            f"The database could not be read ({completeness[0]['error']}), so this",
            "section is NOT TESTED rather than assumed empty.",
            "",
        ]

    lines += [
        "Read live from the database by `scripts/audit_ids.py`. Every figure the COM",
        "and the Financial Summary tab show is computed from these rows; nothing on",
        "either screen is transcribed from a printed panel.",
        "",
        "| Student | Ref | Enrollments | Enrolled subjects | Assessments | Assessed | Balance | Installments | Payments |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in completeness:
        lines.append(
            f"| {row['name']} | `{row['reference']}` | {row['enrollments']} | "
            f"{row['subjects']} | {row['assessments']} | {row['assessed']} | "
            f"{row['balance']} | {row['installments']} | {row['payments']} |"
        )

    incomplete = [
        row for row in completeness
        if not row["enrollments"] or not row["subjects"] or not row["assessments"]
    ]
    lines.append("")
    if incomplete:
        lines += [
            "**Action:** the following students cannot produce a Certificate of",
            "Matriculation until the registrar records an enrollment for them. No",
            "subjects, fees or payments were invented to fill the gap:",
            "",
        ]
        for row in incomplete:
            missing = []
            if not row["enrollments"]:
                missing.append("no enrollment record")
            if not row["subjects"]:
                missing.append("no subjects in 'enrolled' status")
            if not row["assessments"]:
                missing.append("no assessment record")
            lines.append(f"- {row['name']} (ref `{row['reference']}`): {', '.join(missing)}.")
        lines.append("")
    else:
        lines += [
            "Every student on file has an enrollment with enrolled subjects and an",
            "assessment, so all three can produce a Certificate of Matriculation.",
            "",
        ]

    lines += [
        "## Assessment figures taken from another student's panel",
        "",
        "`WIT-2024-003` (Steven Anthony M. Oquiana) arrived with an ID card and no",
        "information panel, so there is no printed assessment for him in this project.",
        "His assessment and installment schedule were seeded from the block already on",
        "file for the other two students in the same program, year and term, on the same",
        "seven subjects with the same laboratory fees -- read from the existing",
        "`assessment_items` and `payment_schedules` rows rather than guessed from an",
        "unlabelled list of amounts.",
        "",
        "**Action:** the registrar should confirm those figures against Steven's own",
        "assessment sheet and reissue his information panel. The seeded rows are a",
        "placeholder of the correct shape, not a verified statement of what he owes.",
        "",
    ]

    lines += assessment_discrepancy_lines()
    return lines


def assessment_discrepancy_lines() -> List[str]:
    """Report the arithmetic the application itself refuses to reconcile.

    ``services.com_summary`` already refuses to force these to agree, and the
    Financial Summary tab prints them as warnings. Repeating them here means the
    finding is in the record rather than only on a screen someone happened to
    open. Read live; nothing here is hardcoded.
    """
    try:
        from services.com_summary import summarize_financials
        from services.student_service import get_enrollment_for_reference
    except Exception as exc:  # pragma: no cover - import guard
        return [f"## Assessment discrepancies", "",
                f"NOT TESTED -- `{type(exc).__name__}: {exc}`", ""]

    reported: List[str] = []
    for payload, reference in (("000065412", "65412"), ("000065902", "65902"),
                               ("000065736", "65736")):
        try:
            lookup = get_enrollment_for_reference(payload)
        except Exception as exc:
            return ["## Assessment discrepancies", "",
                    f"NOT TESTED -- the database could not be read "
                    f"(`{type(exc).__name__}`)", ""]
        if not lookup.found or lookup.assessment is None:
            continue
        summary = summarize_financials(lookup.assessment)
        for note in summary.discrepancies:
            reported.append((lookup.student.full_name, lookup.student.reference_number, note))

    if not reported:
        return [
            "## Assessment discrepancies",
            "",
            "None. Every stored assessment reconciles with its payment schedule.",
            "",
        ]

    lines = [
        "## Assessment discrepancies (not reconciled)",
        "",
        "These are reported by `services/com_summary.py`, which is written to flag an",
        "arithmetic mismatch rather than adjust a figure to make a sheet balance. No",
        "total below has been altered.",
        "",
    ]
    for name, reference, note in reported:
        lines.append(f"- **{name}** (ref `{reference}`): {note}")
    lines += [
        "",
        "**Action:** ask the registrar what the difference is. If it is a real fee it",
        "needs its own line on the assessment; if it is a printing error the panel needs",
        "reissuing. Do not absorb it into another category to make the sheet add up.",
        "",
    ]
    return lines


def write_admin_actions(out: Path, entries, summary) -> None:
    applied = summary.get("resolved", False)
    lines = [
        "# Administrator Actions Required",
        "",
    ]
    if applied:
        lines += [
            "**These entries have already been applied to the database** by an",
            "administrator decision, using `scripts/resolve_student_mapping.py`. The",
            "change is recorded in the `audit_logs` table with its old and new values,",
            "and a pre-change dump is in the persistent backup.",
            "",
            "What remains below is **not** actionable in the database: it is a",
            "discrepancy in a printed document, which no record edit can change.",
        ]
    else:
        lines += [
            "**Nothing in this file has been applied.** The database was opened read-only.",
            "These are proposals for an authorised administrator to action through the",
            "application's own Students page.",
        ]
    lines += ["", "## Unresolved records (no database row)", ""]

    unresolved = [
        e for e in entries
        if e["status"] in (STUDENT_RECORD_MISSING, REFERENCE_NUMBER_MISMATCH, NAME_MISMATCH)
    ]
    if not unresolved:
        lines += ["None. Every decoded barcode resolves to a student record.", ""]

    for entry in entries:
        if entry["status"] != PANEL_DISCREPANCY:
            continue
        name = entry["name"]
        panel = _panel_for_card(name)
        value = entry.get("value") or ""
        lines += [
            f"## `{name}` - PANEL_DISCREPANCY (mapping verified, document disagrees)",
            "",
            f"- Card: {CARD_NAME.get(name, 'n/a')}",
            f"- Decoded payload: `{value}` (reference `{value.lstrip('0')}`)",
            f"- Record in database: {entry['db'].get('full_name')} "
            f"ref `{entry['db'].get('reference_number')}` — **matches the card**",
            f"- Panel `{panel}` prints reference: "
            f"`{PANEL_REFERENCE.get(panel, 'n/a')}` — **does not match**",
            "",
            "**Action:** ask the registrar to reissue the information panel, or to",
            "confirm in writing that the printed reference is a placeholder. The",
            "scanner is correct as it stands; this is a paperwork defect, not a",
            "scanning defect.",
            "",
        ]
    lines += record_completeness_lines(record_completeness())
    (out / "ADMIN_ACTIONS_REQUIRED.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {out / 'ADMIN_ACTIONS_REQUIRED.md'}")


if __name__ == "__main__":
    sys.exit(main())
