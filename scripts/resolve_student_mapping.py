#!/usr/bin/env python3
"""Resolve the unresolved barcode-to-student mapping, safely and reversibly.

The situation
-------------
Card ``assets/images/Id_2.png`` shows **Ronna Mae D. Alemania** (photo and name
agree with her information panel) and its CODE39 barcode decodes to
``000065902``. Her information panel ``student_2_info.png`` prints
**Reference Number: 000001**. There is no database row for her.

By this project's own convention a payload is the zero-padded 9-digit reference
number -- that is exactly how the verified student is stored (reference ``65412``
-> payload ``000065412``). Under that convention her payload implies reference
``65902``, which contradicts the ``000001`` printed on her panel. The project's
own seed script records the same contradiction in a comment and never ran for
her.

Why this script refuses to decide
---------------------------------
Which identifier is authoritative is a fact about a real student. It cannot be
read out of the files, and no evidence in this project settles it: the card
carries the barcode, the panel carries the reference number, and there is no
third source. Choosing silently would put an unverified identifier into a
student record, which is exactly what must not happen.

So this script does the safe half of the job and hands the decision back:

* it prints the full evidence and the conflict every time;
* it **refuses to write** unless an administrator supplies the authoritative
  reference number and passes ``--confirm``;
* when it does write, it uses the project's existing schema unchanged, writes an
  ``audit_logs`` entry with the before/after values, and re-verifies the result
  through the real scan pipeline.

Usage:
    python -m scripts.resolve_student_mapping                      # evidence only
    python -m scripts.resolve_student_mapping --apply \
        --reference 65902 --confirm                               # commit

Nothing here invents a student. The field values are the ones already committed
in ``scripts/initialize_database.py`` for this student, transcribed here with
their provenance noted; only ``reference_number`` and ``barcode_payload`` are
under dispute.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402

logger = setup_logging()

CARD = settings.ASSETS_DIR / "images" / "Id_2.png"
CROP = settings.ASSETS_DIR / "barcodes" / "barcode_1.png"
PANEL = settings.info_image("student_2_info.png")

#: The payload is not in dispute: it was decoded from the card twice, and it is
#: the value a scan of that card actually produces.
PAYLOAD = "000065902"

#: The two candidate reference numbers, with where each one comes from.
CANDIDATE_REFERENCES = {
    "65902": "encoded by the barcode on Id_2.png (payload 000065902 zero-padded)",
    "000001": "printed as 'Reference Number' on student_2_info.png",
}

#: Field values transcribed from the project's existing seed data in
#: scripts/initialize_database.py, which itself reads them off the panel. These
#: are project records, not values invented here. Only reference_number and
#: barcode_payload are contested.
STUDENT_FIELDS: Dict[str, object] = {
    "student_id": "WIT-2024-002",
    "reference_number": None,   # supplied by the administrator
    "first_name": "Ronna Mae",
    "middle_name": "D.",
    "last_name": "Alemania",
    "program": "Computer Engineering",
    "year_level": "3rd Year",
    "section": "EP1",
    "date_of_birth": "2003-08-22",
    "gender": "Female",
    "address": "Iloilo City, Philippines",
    "profile_image_path": "assets/images/info/student_2_info.png",
    "enrollment_status": "active",
    "barcode_payload": PAYLOAD,
}

CONTACTS: List[Tuple[str, str, int, Optional[str]]] = [
    ("phone", "09171234567", 1, "Mobile"),
    ("email", "ronna.alemania@wit.edu.ph", 0, "School Email"),
    ("emergency_phone", "09981234567", 0, "Parent"),
]


def connect():
    import mysql.connector

    return mysql.connector.connect(**settings.get_database_config())


def show_evidence() -> Dict:
    """Re-derive every fact from the assets and the database, live."""
    import cv2

    from services.barcode_decoder import BarcodeDecoderService

    decoder = BarcodeDecoderService()
    facts: Dict = {"payload": PAYLOAD}

    for label, path in (("card", CARD), ("barcode_crop", CROP)):
        image = cv2.imread(str(path))
        hits = decoder.decode(image) if image is not None else []
        facts[label] = {
            "path": str(path.relative_to(BASE_DIR)),
            "decoded": [h.data for h in hits],
            "symbology": hits[0].type if hits else None,
        }

    facts["panel"] = {
        "path": str(PANEL.relative_to(BASE_DIR)),
        "printed_reference": "000001",
        "printed_name": "Ronna Mae D. Alemania",
    }

    cn = connect()
    cur = cn.cursor(dictionary=True)
    cur.execute(
        "SELECT id, student_id, reference_number, first_name, middle_name, last_name, "
        "barcode_payload FROM students WHERE barcode_payload = %s",
        (PAYLOAD,),
    )
    facts["existing_row"] = cur.fetchone()
    cur.execute("SELECT COUNT(*) AS c FROM students")
    facts["student_count"] = cur.fetchone()["c"]
    cur.close()
    cn.close()
    return facts


def print_evidence(facts: Dict) -> None:
    print("=" * 78)
    print("Unresolved mapping - evidence (re-derived live)")
    print("=" * 78)
    print(f"  card          {facts['card']['path']}")
    print(f"    decodes to  {facts['card']['decoded']} ({facts['card']['symbology']})")
    print(f"  barcode crop  {facts['barcode_crop']['path']}")
    print(f"    decodes to  {facts['barcode_crop']['decoded']} ({facts['barcode_crop']['symbology']})")
    print(f"  panel         {facts['panel']['path']}")
    print(f"    name        {facts['panel']['printed_name']}")
    print(f"    reference   {facts['panel']['printed_reference']}  <- printed here")
    print(f"  database      rows for payload {PAYLOAD}: "
          f"{facts['existing_row'] if facts['existing_row'] else 'NONE'}")
    print(f"                total students: {facts['student_count']}")
    print()
    print("  THE CONFLICT")
    print(f"    The barcode encodes {PAYLOAD}. Under this project's own convention")
    print("    (reference 65412 -> payload 000065412) that means reference 65902.")
    print(f"    The panel prints reference {facts['panel']['printed_reference']}.")
    print("    Both cannot be right. Nothing in the project settles it.")
    print()


def apply_resolution(reference: str, confirmed: bool, dry_run: bool) -> int:
    if not confirmed:
        print("REFUSING TO WRITE: --confirm was not passed.")
        print("An administrator must supply and confirm the authoritative reference number.")
        return 2

    if reference not in CANDIDATE_REFERENCES:
        print(f"REFUSING TO WRITE: --reference {reference!r} is not one of the two")
        print(f"candidates that appear in evidence: {sorted(CANDIDATE_REFERENCES)}")
        print("If the real value is something else, correct the record through the")
        print("application's Students page instead of forcing it here.")
        return 2

    values = dict(STUDENT_FIELDS)
    values["reference_number"] = reference
    mismatch = reference.lstrip("0") != PAYLOAD.lstrip("0")

    print("=" * 78)
    print("Applying the administrator's decision")
    print("=" * 78)
    print(f"  reference_number : {reference}")
    print(f"    source         : {CANDIDATE_REFERENCES[reference]}")
    print(f"  barcode_payload  : {PAYLOAD}")
    if mismatch:
        print()
        print("  NOTE: this record will hold a reference number that does not match")
        print("  its own payload under the zero-padding convention. That is allowed and")
        print("  is the honest outcome if the panel is authoritative and the card's")
        print("  barcode is stale. The scanner matches on barcode_payload, so scanning")
        print("  the card still resolves to this student.")
        print()

    if dry_run:
        print("DRY RUN: no rows written.")
        return 0

    cn = connect()
    cur = cn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT * FROM students WHERE barcode_payload = %s", (PAYLOAD,)
        )
        existing = cur.fetchone()

        if existing:
            record_id = existing["id"]
            before = {
                k: existing.get(k)
                for k in ("student_id", "reference_number", "barcode_payload",
                          "first_name", "middle_name", "last_name")
            }
            assignments = ", ".join(f"{k} = %s" for k in values)
            cur.execute(
                f"UPDATE students SET {assignments} WHERE id = %s",
                (*values.values(), record_id),
            )
            action = "UPDATE"
            print(f"  {action} students row id={record_id}")
        else:
            columns = ", ".join(values)
            placeholders = ", ".join(["%s"] * len(values))
            cur.execute(
                f"INSERT INTO students ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
            record_id = cur.lastrowid
            before = None
            action = "INSERT"
            print(f"  {action} students row id={record_id}")

        # The barcodes table is empty; a scan-resolvable student should have a
        # row there too, and the symbology must be the one the card really uses.
        cur.execute("SELECT id FROM barcodes WHERE barcode_value = %s", (PAYLOAD,))
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO barcodes (student_id, barcode_value, barcode_type, image_path) "
                "VALUES (%s, %s, %s, %s)",
                (record_id, PAYLOAD, "CODE39", "assets/images/Id_2.png"),
            )
            print(f"  INSERT barcodes row (CODE39, from the card)")
        else:
            print("  barcodes row already present; left untouched")

        cur.execute(
            "INSERT INTO audit_logs (action, table_name, record_id, old_values, new_values) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                "resolve_barcode_mapping",
                "students",
                record_id,
                json.dumps(before, default=str) if before else None,
                json.dumps({k: str(v) for k, v in values.items()}, default=str),
            ),
        )
        print(f"  INSERT audit_logs entry (action=resolve_barcode_mapping)")

        cn.commit()
    except Exception as exc:
        cn.rollback()
        print(f"  ROLLED BACK: {type(exc).__name__}: {exc}")
        return 1
    finally:
        cur.close()
        cn.close()

    # Verify through the real pipeline, not by trusting the write.
    print("\nVerifying through the scan pipeline...")
    try:
        from services.scan_pipeline import ScanPipeline, StudentVerifier
        from services.barcode_decoder import BarcodeDecoderService
        from services.student_service import StudentService
        from services.yolo_detector import YOLODetectorService
        from tests.scanner_fixtures import make_webcam_frame

        detector = YOLODetectorService()
        detector.load_model(settings.YOLO_MODEL_PATH)
        pipeline = ScanPipeline(
            detector=detector,
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=StudentService().get_student_by_barcode),
            cooldown_seconds=0.0,
        )
        outcome = pipeline.process_frame(make_webcam_frame(CARD))
        student = outcome.student
        print(f"  state    {outcome.state.value}")
        print(f"  decoded  {outcome.decoded_value}")
        print(f"  student  {getattr(student, 'full_name', None)}")
        if outcome.state.value == "verified" and student is not None:
            print("\n  PASS: the card now resolves to a verified student.")
            return 0
        print("\n  FAIL: the card still does not resolve. Nothing else was changed.")
        return 1
    except Exception as exc:
        print(f"  verification could not run: {type(exc).__name__}: {exc}")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the resolution (needs --confirm)")
    ap.add_argument("--reference", default=None,
                    help=f"the authoritative reference number; one of {sorted(CANDIDATE_REFERENCES)}")
    ap.add_argument("--confirm", action="store_true",
                    help="assert that you, an administrator, have decided this")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be written without writing it")
    args = ap.parse_args()

    facts = show_evidence()
    print_evidence(facts)

    if not args.apply:
        print("No changes made. To resolve, re-run with:")
        print(f"    python -m scripts.resolve_student_mapping --apply --reference "
              f"{{{' or '.join(sorted(CANDIDATE_REFERENCES))}}} --confirm")
        return 1 if facts["existing_row"] is None else 0

    if not args.reference:
        print("REFUSING TO WRITE: --reference is required.")
        return 2

    return apply_resolution(args.reference, args.confirm, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
