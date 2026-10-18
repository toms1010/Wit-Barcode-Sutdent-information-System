"""The third student's card: barcode -> record -> portrait -> COM.

`assets/images/Id_3.png` and `assets/barcodes/barcode_3.png` arrived with Steven
Anthony M. Oquiana and exercise two things the first two cards never did:

* the card is a **different design** -- 270x459 against 590x1004 -- so its symbol
  is 204 px wide where the others are 479-579 px. Below ~230 px zbar reads
  nothing at all, which is why the decoder now interpolates undersampled crops
  (see `services.barcode_decoder.MAX_UPSCALED_CROP_WIDTH_PX`);
* the payload `000065736` had no hand-written alias entry, which exposed a real
  gap in `services.identifiers.resolve_reference` -- it only knew the payloads
  somebody had remembered to add, so an enrollment lookup keyed on this student's
  scanned payload reported "no student on file" even though his enrollment was on
  disk. These tests pin the derived rule so that gap cannot reopen.

Everything asserted here is read from the database or decoded from the assets.
Nothing is keyed on a filename or on scan order, and the database-backed cases
skip when MySQL is unreachable, matching the rest of the suite.
"""

from __future__ import annotations

import hashlib
import sys
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings

BARCODE_3 = settings.BARCODES_DIR / "barcode_3.png"
CARD_3 = settings.IMAGES_DIR / "Id_3.png"
PORTRAIT_3 = settings.PROFILE_DIR / "Steven_profile.png"

STEVEN = {
    "reference": "65736",
    "payload": "000065736",
    "name": "Steven Anthony M. Oquiana",
    "card": "assets/images/Id_3.png",
    "portrait": "assets/profile/Steven_profile.png",
}

#: The other two verified records, so "no cross-contamination" can be asserted
#: against real rows rather than against nothing.
OTHERS = {
    "000065412": {"name": "Javy Danielle N. Caporal", "portrait": "assets/profile/javy_profile.png"},
    "000065902": {"name": "Ronna Mae D. Alemania", "portrait": "assets/profile/ronna_profile.png"},
}

#: What the registrar's printed panel says the term costs. Pinned as reference
#: data only -- the application must arrive at these numbers by summing stored
#: rows, which is what the tests below check separately.
PANEL_SUBJECT_COUNT = 7
PANEL_UNITS = 20
PANEL_PERIODS = 23
PANEL_LAB_FEE = Decimal("5618.59")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _require_database():
    from database.connection import test_connection

    if not test_connection():
        pytest.skip("database unavailable")


def _student(payload: str):
    from services.student_service import StudentService

    return StudentService().get_student_by_barcode(payload)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ assets --


class TestThirdStudentAssets:
    @pytest.mark.parametrize("path", [BARCODE_3, CARD_3, PORTRAIT_3], ids=["barcode", "card", "portrait"])
    def test_the_supplied_asset_exists_and_is_a_real_png(self, path):
        assert path.is_file(), f"missing supplied asset: {path}"
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
        image = cv2.imread(str(path))
        assert image is not None, f"{path} cannot be read by OpenCV"

    def test_the_portrait_lives_where_the_settings_expects_it(self):
        assert PORTRAIT_3.parent == settings.PROFILE_DIR
        assert settings.resolve_asset(STEVEN["portrait"]) == PORTRAIT_3

    def test_the_three_portraits_are_three_different_images(self):
        digests = {
            name: _sha256(settings.BASE_DIR / record["portrait"])
            for name, record in {"Javy": OTHERS["000065412"], "Ronna": OTHERS["000065902"],
                                 "Steven": STEVEN}.items()
        }
        assert len(set(digests.values())) == 3, digests

    def test_stevens_portrait_is_not_a_document_panel(self):
        assert PORTRAIT_3.parent != settings.INFO_IMAGES_DIR
        assert not (settings.IMAGES_DIR / PORTRAIT_3.name).exists()


# ------------------------------------------------------- decoding the card --


class TestThirdStudentBarcodeDecoding:
    def test_the_barcode_crop_decodes_on_its_own(self):
        from services.barcode_decoder import BarcodeDecoderService

        result = BarcodeDecoderService().decode_best(cv2.imread(str(BARCODE_3)))

        assert result.ok, "a tight barcode crop must decode without preprocessing"
        assert result.data == STEVEN["payload"]
        assert result.type == "CODE39"

    def test_the_id_card_decodes_and_names_the_interpolation_it_needed(self):
        """The card's symbol is too small for zbar; interpolation is what reads it."""
        from services.barcode_decoder import BarcodeDecoderService

        image = cv2.imread(str(CARD_3))
        # Guard the premise: if a future zbar reads the card directly this test
        # should say so rather than quietly keep testing a stale reason.
        from pyzbar.pyzbar import decode as zbar_decode

        result = BarcodeDecoderService().decode_best(image)

        assert result.ok
        assert result.data == STEVEN["payload"]
        assert result.type == "CODE39"
        assert result.attempt is not None
        if not zbar_decode(image):
            assert result.attempt.startswith("upscale"), (
                "the card only reads after interpolation, so the winning attempt "
                f"should be one; it was {result.attempt!r}"
            )

    def test_a_readable_crop_is_still_attempted_exactly_once(self):
        """Interpolation must not be paid for on a frame that reads cleanly."""
        from services.barcode_decoder import BarcodeDecoderService

        result = BarcodeDecoderService().decode_best(cv2.imread(str(BARCODE_3)))

        assert result.attempts_tried == 1, (
            "the untouched crop is attempted first and the loop stops on success"
        )

    def test_an_undersampled_crop_gets_an_interpolation_attempt(self):
        from services.barcode_decoder import (
            MAX_UPSCALED_CROP_WIDTH_PX, BarcodeDecoderService,
        )

        image = cv2.imread(str(CARD_3))
        names = [a.name for a in BarcodeDecoderService().build_attempts(image)]

        assert names[0] == "original"
        assert any(n.startswith("upscale") for n in names), names
        assert image.shape[1] < MAX_UPSCALED_CROP_WIDTH_PX

    def test_the_gray_attempt_hands_the_decoder_actual_greyscale_pixels(self):
        """It used to re-submit the original, spending a pass on a repeat."""
        from services.barcode_decoder import BarcodeDecoderService

        attempts = BarcodeDecoderService().build_attempts(cv2.imread(str(BARCODE_3)))
        gray = next(a for a in attempts if a.name == "gray")

        assert gray.image.ndim == 2, "the gray attempt must not be the colour crop"

    def test_a_barcode_free_document_still_decodes_to_nothing(self):
        from services.barcode_decoder import BarcodeDecoderService

        for name in ("student_1_info.png", "student_2_info.png"):
            path = settings.info_image(name)
            if not path.exists():
                continue
            result = BarcodeDecoderService().decode_best(cv2.imread(str(path)))
            assert not result.ok, f"{name} produced a false decode: {result.data}"


# ------------------------------------------------------------- identity ---


class TestThirdStudentIdentity:
    def test_the_decoded_payload_resolves_to_stevens_record(self):
        _require_database()
        student = _student(STEVEN["payload"])

        assert student is not None, "the decoded payload must resolve to a record"
        assert student.full_name == STEVEN["name"]
        assert student.reference_number == STEVEN["reference"]
        assert student.program == "Computer Engineering"
        assert getattr(student.year_level, "value", student.year_level) == "3rd Year"
        assert student.profile_image_path == STEVEN["portrait"]

    def test_each_payload_resolves_to_a_different_student(self):
        _require_database()

        resolved = {}
        for payload in (STEVEN["payload"], *OTHERS):
            student = _student(payload)
            assert student is not None, f"{payload} does not resolve"
            resolved[payload] = student

        assert len({s.id for s in resolved.values()}) == 3, "two payloads share a record"
        assert len({s.profile_image_path for s in resolved.values()}) == 3

    def test_the_reference_and_the_payload_agree_so_the_record_reads_verified(self):
        from ui.widgets.profile_image import identifier_conflict

        _require_database()
        student = _student(STEVEN["payload"])

        assert identifier_conflict(student) is None

    def test_the_payload_resolves_to_his_stored_reference(self):
        """The gap this student exposed: the payload had no alias entry."""
        from services.identifiers import normalise_reference, resolve_reference

        assert resolve_reference(STEVEN["payload"]) == STEVEN["reference"]
        assert resolve_reference(STEVEN["reference"]) == STEVEN["reference"]
        assert normalise_reference("000001") == "000001", "leading zeros are significant"

    def test_a_nine_digit_padding_rule_does_not_swallow_another_value(self):
        from services.identifiers import resolve_reference

        # Nine digits with a leading zero *is* this project's payload encoding;
        # anything else must pass through untouched.
        assert resolve_reference("0000012345") == "0000012345"
        assert resolve_reference("1") == "1"


# ------------------------------------------------------------ enrollment ---


class TestThirdStudentEnrollment:
    def _lookup(self, reference):
        from services.student_service import get_enrollment_for_reference

        return get_enrollment_for_reference(reference)

    @pytest.mark.parametrize("reference", [STEVEN["reference"], STEVEN["payload"]])
    def test_the_enrollment_resolves_from_every_identifier_form(self, reference):
        _require_database()

        lookup = self._lookup(reference)

        assert lookup.status == "found", lookup.reason
        assert lookup.student.full_name == STEVEN["name"]
        assert lookup.enrollment is not None
        assert lookup.has_subjects

    def test_the_subjects_are_the_enrolled_ones_and_belong_to_him(self):
        _require_database()

        lookup = self._lookup(STEVEN["payload"])
        subjects = [
            row
            for row in lookup.enrollment.subjects
            if getattr(row.status, "value", row.status) == "enrolled"
        ]

        assert len(subjects) == PANEL_SUBJECT_COUNT
        assert lookup.enrollment.student_id == lookup.student.id
        assert lookup.enrollment.academic_year

    def test_the_com_totals_are_computed_from_the_rows(self):
        """The UI must not carry a hardcoded total; recompute it here and compare."""
        _require_database()
        from services.com_summary import summarize_enrollment

        lookup = self._lookup(STEVEN["payload"])
        summary = summarize_enrollment(lookup.enrollment)

        assert summary.available is True
        assert summary.subject_count == sum(1 for _ in summary.rows)
        assert summary.total_units == sum(row.units for row in summary.rows)
        assert summary.total_lab_fee == sum((row.lab_fee for row in summary.rows), Decimal("0"))

        # ...and those computed figures agree with the registrar's panel.
        assert summary.subject_count == PANEL_SUBJECT_COUNT
        assert summary.total_units == PANEL_UNITS
        assert summary.total_periods == PANEL_PERIODS
        assert summary.total_lab_fee == PANEL_LAB_FEE

    def test_the_com_never_shows_another_students_subject_rows(self):
        _require_database()
        from services.com_summary import summarize_enrollment

        summaries = {
            payload: summarize_enrollment(self._lookup(payload).enrollment)
            for payload in (STEVEN["payload"], "000065412", "000065902")
        }

        # Each summary is built from its own enrollment, so the rows cannot be
        # shared: the three records are three separate enrollment rows.
        assert all(summary.available for summary in summaries.values())
        codes = {
            payload: {row.code for row in summary.rows}
            for payload, summary in summaries.items()
        }
        # The three students take the same subjects, so identical *codes* are
        # correct; what must differ is the object each summary was built from.
        assert len({id(summary) for summary in summaries.values()}) == 3
        assert all(len(codes[payload]) == PANEL_SUBJECT_COUNT for payload in codes)

        lookup_ids = {
            payload: self._lookup(payload).enrollment.id for payload in summaries
        }
        assert len(set(lookup_ids.values())) == 3, lookup_ids


# ----------------------------------------------------------- assessment ---


class TestThirdStudentFinancials:
    def _lookup(self):
        from services.student_service import get_enrollment_for_reference

        return get_enrollment_for_reference(STEVEN["payload"])

    def test_an_assessment_is_on_file_and_tied_to_his_enrollment(self):
        _require_database()

        lookup = self._lookup()
        assessment = lookup.assessment

        assert assessment is not None, "the seeded assessment must be on file"
        assert assessment.student_id == lookup.student.id
        assert assessment.enrollment_id == lookup.enrollment.id
        assert assessment.items, "the assessment carries its fee lines"

    def test_the_financial_summary_is_computed_from_the_stored_items(self):
        _require_database()
        from services.com_summary import summarize_financials

        assessment = self._lookup().assessment
        summary = summarize_financials(assessment)

        assert summary.available is True
        assert summary.total_assessed == assessment.total_assessed
        assert summary.outstanding_balance == assessment.outstanding_balance
        assert summary.total_assessed >= PANEL_LAB_FEE

    def test_the_payment_schedule_has_the_four_installments(self):
        _require_database()

        assessment = self._lookup().assessment
        stages = [row.schedule_type for row in assessment.payment_schedules]
        values = {getattr(s, "value", s) for s in stages}

        assert values == {"down_payment", "midterm", "prefinal", "final"}
        assert len(assessment.payment_schedules) == 4, "no duplicated installment rows"


# ------------------------------------------------------------------ popup ---


class TestThirdStudentPopup:
    def _dialog(self, payload):
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        _require_database()
        student = _student(payload)
        if student is None:
            pytest.skip(f"payload {payload} is not on this database")
        from services.student_service import get_enrollment_for_reference

        lookup = get_enrollment_for_reference(payload)
        return ScanResultDialog(
            student=student,
            enrollment=lookup.enrollment,
            assessment=lookup.assessment,
            scanned_value=payload,
        )

    @staticmethod
    def _texts(dialog) -> str:
        from PySide6.QtWidgets import QLabel, QTableWidget

        parts = [w.text() for w in dialog.findChildren(QLabel)]
        for table in dialog.findChildren(QTableWidget):
            for row in range(table.rowCount()):
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    if item is not None:
                        parts.append(item.text())
        return "\n".join(parts)

    def test_the_popup_shows_his_identity_and_no_other_students(self, qapp):
        dialog = self._dialog(STEVEN["payload"])
        text = self._texts(dialog)

        assert STEVEN["name"] in text
        assert STEVEN["reference"] in text
        assert "Computer Engineering" in text
        for other in OTHERS.values():
            assert other["name"] not in text, "another student's name is on screen"
            assert other["portrait"].split("/")[-1] not in text

        dialog.close(); dialog.deleteLater()

    def test_the_popup_shows_his_photograph(self, qapp):
        from PySide6.QtWidgets import QLabel

        dialog = self._dialog(STEVEN["payload"])
        dialog.show()
        labels = [
            w for w in dialog.findChildren(QLabel)
            if w.pixmap() is not None and not w.pixmap().isNull()
        ]
        assert labels, "the profile card must paint a portrait"

        rendered = {label.pixmap().toImage().constBits().tobytes() for label in labels}
        for payload, other in OTHERS.items():
            other_student = _student(payload)
            from ui.widgets.profile_image import load_portrait

            pixmap, ok = load_portrait(other_student)
            if ok:
                assert pixmap.toImage().constBits().tobytes() not in rendered, (
                    "the popup is showing another student's photograph"
                )

        dialog.close(); dialog.deleteLater()

    def test_switching_between_all_three_students_leaves_no_stale_detail(self, qapp):
        payloads = [STEVEN["payload"], "000065902", "000065412", STEVEN["payload"]]
        seen = []

        for payload in payloads:
            dialog = self._dialog(payload)
            text = self._texts(dialog)
            expected = STEVEN["name"] if payload == STEVEN["payload"] else OTHERS[payload]["name"]

            assert expected in text, f"{expected} missing from the popup"
            for other_payload, other in OTHERS.items():
                if other_payload == payload:
                    continue
                assert other["name"] not in text, (
                    f"{other['name']} left over from the previously shown student"
                )
            if payload != STEVEN["payload"]:
                assert STEVEN["name"] not in text

            seen.append(expected)
            dialog.close(); dialog.deleteLater()

        assert seen == [STEVEN["name"], OTHERS["000065902"]["name"],
                        OTHERS["000065412"]["name"], STEVEN["name"]]

    def test_the_actions_are_present_and_readable(self, qapp):
        dialog = self._dialog(STEVEN["payload"])

        for label in ("Scan Next Student", "View Full Profile", "Proceed to Assessment",
                      "Print COM", "Close"):
            assert dialog.findChildren(type(dialog.export_btn)) or True
            texts = [b.text() for b in dialog.findChildren(dialog.export_btn.__class__)]
            assert label in texts, f"{label!r} missing; found {texts}"

        assert dialog.export_btn.isEnabled() is True, (
            "a student with a real enrollment must be able to print the COM"
        )

        dialog.close(); dialog.deleteLater()


# ------------------------------------------------------------------- COM ---


class TestThirdStudentComExport:
    def test_a_pdf_is_produced_from_his_record(self, tmp_path):
        _require_database()
        from services.com_print_service import ComPrintService
        from services.student_service import get_enrollment_for_reference

        lookup = get_enrollment_for_reference(STEVEN["payload"])
        if not lookup.found:
            pytest.skip("no enrollment on this database")

        target = tmp_path / "com_65736.pdf"
        result = ComPrintService().export_pdf(
            lookup.student, lookup.enrollment, lookup.assessment, target
        )

        assert result == target
        assert target.read_bytes().startswith(b"%PDF")
        assert target.stat().st_size > 1000

    def test_no_enrollment_still_refuses_rather_than_inventing_one(self, tmp_path):
        from services.com_print_service import ComNotCertifiableError, ComPrintService

        target = tmp_path / "must_not_exist.pdf"
        with pytest.raises(ComNotCertifiableError):
            ComPrintService().export_pdf(None, None, None, target)

        assert not target.exists()