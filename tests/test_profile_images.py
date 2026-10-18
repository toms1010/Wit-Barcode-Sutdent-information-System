"""Profile image mapping: verified record -> correct portrait, or a placeholder.

Covers the scan-to-profile integration:

* each supplied portrait lives where the application expects it and is a real,
  decodable PNG;
* the portrait shown for a student comes from that student's **database
  record**, and the two students never share or swap an image;
* identity is never derived from a filename or from face similarity;
* a missing, unreadable or unset image degrades to a neutral placeholder and is
  reported, rather than silently showing somebody else's photo;
* a record whose barcode payload and reference number disagree is marked
  unresolved instead of verified.

The database-backed tests are skipped when MySQL is unreachable, matching the
convention used by the rest of the suite.
"""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel

from config.settings import settings
from models.student import Student, YearLevel
from ui.widgets.profile_image import apply_portrait, identifier_conflict, load_portrait

# The verified identifier -> portrait mapping, with the ID card each portrait
# was checked against. These are the two real students in this project.
JAVY = {
    "reference": "65412",
    "payload": "000065412",
    "name": "Javy Danielle N. Caporal",
    "card": "assets/images/Id_1.png",
    "portrait": "assets/profile/javy_profile.png",
}
RONNA = {
    "reference": "65902",
    "payload": "000065902",
    "name": "Ronna Mae D. Alemania",
    "card": "assets/images/Id_2.png",
    "portrait": "assets/profile/ronna_profile.png",
}


def _student(**overrides) -> Student:
    values = dict(
        id=1,
        student_id="WIT-2024-001",
        reference_number=JAVY["reference"],
        first_name="Javy Danielle",
        middle_name="N.",
        last_name="Caporal",
        program="Computer Engineering",
        year_level=YearLevel.THIRD_YEAR,
        barcode_payload=JAVY["payload"],
    )
    values.update(overrides)
    return Student(**values)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------- the assets --


class TestProfileAssets:
    def test_both_portraits_exist_where_the_settings_expects_them(self):
        for person in (JAVY, RONNA):
            path = settings.BASE_DIR / person["portrait"]
            assert path.is_file(), f"missing portrait: {path}"
            assert path.parent == settings.PROFILE_DIR, (
                f"{path} is not inside the configured PROFILE_DIR"
            )

    def test_portraits_are_real_pngs(self):
        for person in (JAVY, RONNA):
            header = (settings.BASE_DIR / person["portrait"]).read_bytes()[:8]
            assert header == b"\x89PNG\r\n\x1a\n", f"{person['portrait']} is not a PNG"

    def test_the_two_portraits_are_different_images(self):
        """Guards against both students being mapped to the same file."""
        digests = {
            person["name"]: _sha256(settings.BASE_DIR / person["portrait"])
            for person in (JAVY, RONNA)
        }
        assert len(set(digests.values())) == 2, digests

    def test_a_portrait_is_not_the_information_panel(self):
        """The info panels are documents; using one as an avatar is a defect."""
        for person in (JAVY, RONNA):
            portrait = settings.BASE_DIR / person["portrait"]
            assert portrait.parent != settings.INFO_IMAGES_DIR, (
                f"{portrait} points into the information-panel folder"
            )
            panel = settings.INFO_IMAGES_DIR / portrait.name
            assert not panel.exists(), f"{panel} must not exist as a portrait"


# ------------------------------------------------------ the DB mapping --


class TestDatabaseMapping:
    @staticmethod
    def _students():
        from services.student_service import StudentService

        return StudentService()

    def test_each_verified_payload_resolves_to_its_own_portrait(self):
        try:
            service = self._students()
        except Exception as exc:  # database unavailable is not a mapping failure
            pytest.skip(f"database unavailable: {type(exc).__name__}")

        expected = {JAVY["payload"]: JAVY, RONNA["payload"]: RONNA}
        for payload, person in expected.items():
            try:
                student = service.get_student_by_barcode(payload)
            except Exception as exc:
                pytest.skip(f"database unavailable: {type(exc).__name__}")
            assert student is not None, f"payload {payload} must resolve"
            assert student.full_name == person["name"], (
                f"payload {payload} resolved to {student.full_name!r}, "
                f"expected {person['name']!r}"
            )
            assert student.profile_image_path == person["portrait"], (
                f"{person['name']} points at {student.profile_image_path!r}"
            )

    def test_the_two_students_never_share_a_portrait(self):
        try:
            service = self._students()
            javy = service.get_student_by_barcode(JAVY["payload"])
            ronna = service.get_student_by_barcode(RONNA["payload"])
        except Exception as exc:
            pytest.skip(f"database unavailable: {type(exc).__name__}")

        if javy is None or ronna is None:
            pytest.skip("records not present in this database")
        assert javy.profile_image_path != ronna.profile_image_path

    def test_stored_portrait_paths_resolve_to_real_files(self):
        try:
            service = self._students()
            students = [service.get_student_by_barcode(JAVY["payload"]),
                        service.get_student_by_barcode(RONNA["payload"])]
        except Exception as exc:
            pytest.skip(f"database unavailable: {type(exc).__name__}")

        for student in students:
            if student is None:
                continue
            resolved = settings.resolve_asset(student.profile_image_path)
            assert resolved is not None and resolved.is_file(), (
                f"{student.full_name}: {student.profile_image_path!r} does not resolve"
            )


# --------------------------------------------------------- rendering --


class TestPortraitRendering:
    def test_a_recorded_portrait_is_painted_onto_the_label(self, qapp):
        label = QLabel()
        assert apply_portrait(label, _student(profile_image_path=JAVY["portrait"])) is True
        pixmap = label.pixmap()
        assert pixmap is not None and not pixmap.isNull()

    def test_the_avatar_is_square_and_never_stretched(self, qapp):
        label = QLabel()
        apply_portrait(label, _student(profile_image_path=JAVY["portrait"]), size=64)
        pixmap = label.pixmap()
        assert (pixmap.width(), pixmap.height()) == (64, 64)

    def test_a_missing_file_falls_back_to_a_placeholder(self, qapp):
        student = _student(profile_image_path="assets/profile/does_not_exist.png")
        label = QLabel()
        assert apply_portrait(label, student) is False
        pixmap = label.pixmap()
        assert pixmap is not None and not pixmap.isNull(), (
            "a neutral placeholder must still be painted"
        )
        assert "No profile photo" in label.toolTip()

    def test_no_recorded_path_falls_back_to_a_placeholder(self, qapp):
        label = QLabel()
        assert apply_portrait(label, _student(profile_image_path=None)) is False
        assert not label.pixmap().isNull()

    def test_a_missing_asset_is_reported_in_the_log(self, qapp, caplog):
        student = _student(profile_image_path="assets/profile/missing.png")
        with caplog.at_level("WARNING"):
            apply_portrait(QLabel(), student)
        assert any("missing" in record.message.lower() for record in caplog.records), (
            [r.message for r in caplog.records]
        )

    def test_an_undecodable_file_is_treated_as_missing(self, qapp, tmp_path):
        broken = tmp_path / "broken.png"
        broken.write_bytes(b"this is not an image")
        student = _student(profile_image_path=str(broken))
        pixmap, ok = load_portrait(student)
        assert ok is False and pixmap is None

    def test_the_two_students_render_different_images(self, qapp):
        """The real risk this guards: swapping the two portraits."""
        first = QLabel()
        second = QLabel()
        apply_portrait(first, _student(profile_image_path=JAVY["portrait"]), size=64)
        apply_portrait(
            second,
            _student(
                id=2,
                student_id="WIT-2024-002",
                reference_number=RONNA["reference"],
                first_name="Ronna Mae",
                last_name="Alemania",
                profile_image_path=RONNA["portrait"],
            ),
            size=64,
        )
        assert first.pixmap().toImage() != second.pixmap().toImage()

    def test_an_absolute_path_is_used_as_given(self, qapp):
        absolute = settings.BASE_DIR / JAVY["portrait"]
        pixmap, ok = load_portrait(_student(profile_image_path=str(absolute)))
        assert ok is True and pixmap is not None

    def test_paths_resolve_regardless_of_the_working_directory(self, qapp, tmp_path, monkeypatch):
        """A packaged build launches from elsewhere; a relative stored path
        must still resolve against the project root, not the CWD."""
        monkeypatch.chdir(tmp_path)
        pixmap, ok = load_portrait(_student(profile_image_path=JAVY["portrait"]))
        assert ok is True and pixmap is not None


# ------------------------------------------------ identifier resolution --


class TestIdentifierConflict:
    def test_a_consistent_record_is_not_flagged(self):
        assert identifier_conflict(_student()) is None

    def test_a_conflicting_record_is_flagged(self):
        student = _student(reference_number="000001", barcode_payload="000065902")
        warning = identifier_conflict(student)
        assert warning is not None
        assert "000065902" in warning and "000001" in warning

    def test_a_missing_field_is_not_a_conflict(self):
        assert identifier_conflict(_student(barcode_payload=None)) is None
        assert identifier_conflict(_student(reference_number=None)) is None
        assert identifier_conflict(None) is None

    def test_both_real_records_are_consistent(self):
        """Reference 65902 vs the payload 000065902 is the resolved mapping;
        it must not be reported as unresolved."""
        assert identifier_conflict(
            _student(reference_number=RONNA["reference"], barcode_payload=RONNA["payload"])
        ) is None


# ------------------------------------------------------------- in the UI --


class TestModalBadge:
    def _badge_text(self, dialog) -> str:
        from PySide6.QtWidgets import QLabel

        for label in dialog.findChildren(QLabel):
            if "VERIFIED" in (label.text() or "") or "UNRESOLVED" in (label.text() or ""):
                return label.text()
        return ""

    def test_a_consistent_record_is_badge_verified(self, qapp):
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(
            student=_student(profile_image_path=JAVY["portrait"]),
            scanned_value=JAVY["payload"],
        )
        assert "VERIFIED" in self._badge_text(dialog)
        dialog.deleteLater()

    def test_a_conflicting_record_is_not_badge_verified(self, qapp):
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(
            student=_student(
                reference_number="000001",
                barcode_payload="000065902",
                profile_image_path=RONNA["portrait"],
            ),
            scanned_value="000065902",
        )
        text = self._badge_text(dialog)
        assert "UNRESOLVED" in text, text
        assert "VERIFIED" not in text, text
        dialog.deleteLater()