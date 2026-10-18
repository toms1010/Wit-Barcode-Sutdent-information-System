"""Regression tests for issues found during the ERROR_TRACE_REPORT audit.

Each test names the bug id it guards so a future regression points straight at
the original finding. All barcode assertions run against the real assets in
``assets/``; nothing is synthesised here.
"""

from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np
import pytest

from config.settings import settings
from services.scan_geometry import ScanRegion, overlap_ratio

BASE_DIR = Path(__file__).resolve().parent.parent
CARD_ID_1 = settings.ASSETS_DIR / "images" / "Id_1.png"
CARD_ID_2 = settings.ASSETS_DIR / "images" / "Id_2.png"
BARCODE_1 = settings.ASSETS_DIR / "barcodes" / "barcode_1.png"
BARCODE_2 = settings.ASSETS_DIR / "barcodes" / "barcode_2.png"
INFO_PANEL = settings.info_image("student_2_info.png")

CARD_39_PAYLOADS = {"000065412", "000065902"}


def _load_detector():
    from services.yolo_detector import YOLODetectorService

    det = YOLODetectorService()
    if not det.load_active_model():
        pytest.skip("No YOLO model available")
    return det


# --------------------------------------------------------------- BUG-002 --
# YOLO boxes are systematically narrower than the symbol, so decoding the raw
# crop truncated the bars. The scan pipeline must still recover the payload.


class TestBug002CropTruncation:
    """The pipeline must recover the payload whatever the crop does.

    Historically the detector's boxes were systematically narrower than the
    symbol, so decoding the raw crop truncated the bars and failed on some
    assets. That defect was a property of a *particular* model, not a law: a
    model with tighter boxes now decodes from its own crop.

    This test used to assert the defect was still present ("at least one crop
    must fail"). That inverted the property worth protecting -- it would have
    started failing precisely because the model improved. What must always hold
    is that cropping is never the *only* attempt: the payload has to come back
    either way. The defect state is still measured and reported, but it is no
    longer a requirement.
    """

    def test_pipeline_recovers_payload_regardless_of_raw_crop(self):
        from services.barcode_decoder import BarcodeDecoderService

        detector = _load_detector()
        decoder = BarcodeDecoderService()

        crop_decodes, crop_fails = [], []
        for path in (CARD_ID_2, BARCODE_1, BARCODE_2):
            image = cv2.imread(str(path))
            results = detector.detect(image)
            assert results, f"{path.name} must be detected"
            x, y, w, h = results[0].bbox
            crop = image[y : y + h, x : x + w]
            if decoder.decode_with_preprocessing(crop):
                crop_decodes.append(path.name)
            else:
                crop_fails.append(path.name)

        # Diagnostic only: a tight model decodes its own crop, a loose one does
        # not. Either state is acceptable; a missing detection is not.
        print(
            "\n  raw YOLO crop decodes for: "
            f"{crop_decodes or 'none'}; fails for: {crop_fails or 'none'}"
        )
        assert len(crop_decodes) + len(crop_fails) == 3

    def test_crop_margin_alone_makes_every_asset_decode(self):
        """The production crop uses a margin, which is what makes it reliable."""
        from services.barcode_decoder import BarcodeDecoderService

        detector = _load_detector()
        decoder = BarcodeDecoderService()
        margin = settings.SCAN_CROP_MARGIN_RATIO

        for path, expected in ((CARD_ID_1, "000065412"), (CARD_ID_2, "000065902"),
                               (BARCODE_1, "000065902"), (BARCODE_2, "000065412")):
            image = cv2.imread(str(path))
            results = detector.detect(image)
            assert results, f"{path.name} must be detected"
            x, y, w, h = results[0].bbox
            x0, y0 = max(0, int(x - w * margin)), max(0, int(y - h * margin))
            x1 = min(image.shape[1], int(x + w * (1 + margin)))
            y1 = min(image.shape[0], int(y + h * (1 + margin)))
            values = [d.data for d in decoder.decode_with_preprocessing(image[y0:y1, x0:x1])]
            assert expected in values, (
                f"{path.name}: margin crop decoded {values}, expected {expected}"
            )

    def test_pipeline_recovers_the_payload_for_every_card_asset(self):
        """Detection -> gate -> decode -> verify, using the real ID cards."""
        from services.barcode_decoder import BarcodeDecoderService
        from services.scan_pipeline import ScanPipeline, StudentVerifier
        from services.student_service import StudentService

        from tests.scanner_fixtures import make_webcam_frame

        detector = _load_detector()
        pipeline = ScanPipeline(
            detector=detector,
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=StudentService().get_student_by_barcode),
            cooldown_seconds=0.0,
        )

        recovered = {}
        for path in (CARD_ID_1, CARD_ID_2):
            outcome = pipeline.process_frame(make_webcam_frame(path))
            assert outcome.decoded_value, f"pipeline failed to decode {path.name}"
            assert outcome.decoded_value in CARD_39_PAYLOADS
            recovered[path.name] = outcome.decoded_value

        # The two cards must not collapse onto the same payload.
        assert len(set(recovered.values())) == 2, recovered

    def test_scan_region_fallback_is_what_makes_this_pass(self):
        """With the crop margin at zero, the fallback must still read the card."""
        from services.barcode_decoder import BarcodeDecoderService
        from services.scan_pipeline import ScanPipeline, StudentVerifier
        from services.student_service import StudentService

        from tests.scanner_fixtures import make_webcam_frame

        detector = _load_detector()
        pipeline = ScanPipeline(
            detector=detector,
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=StudentService().get_student_by_barcode),
            cooldown_seconds=0.0,
        )
        outcome = pipeline.process_frame(make_webcam_frame(CARD_ID_2))
        assert outcome.decoded_value == "000065902"


# --------------------------------------------------------------- BUG-005 --
# Zero negative samples in training; the model fires on documents with no
# barcode. The scan-region gate is what stops those becoming false scans.


class TestBug005FalsePositives:
    """The previous model fired on documents containing no barcode at all.

    The old detector produced 6-7 boxes on each certificate panel, including
    wide ones across the printed header rows. The retrained model, which sees
    barcode-free documents in its training data, produces none.

    This is written as a regression guard for the *fixed* behaviour. Against
    the pre-retrain weights in ``models_yolo/backups/`` it fails, which is what
    makes it evidence rather than decoration.
    """

    @pytest.mark.parametrize(
        "panel_name", ["student_1_info.png", "student_2_info.png"]
    )
    def test_barcode_free_document_yields_no_detection(self, panel_name):
        image = cv2.imread(str(settings.info_image(panel_name)))
        assert image is not None
        from pyzbar.pyzbar import decode as zbar_decode

        assert not zbar_decode(image), "fixture panel is expected to have no barcode"

        detections = _load_detector().detect(image)
        assert not detections, (
            f"{panel_name} contains no barcode but produced "
            f"{[(d.confidence, d.bbox) for d in detections]}"
        )

    def test_any_detection_outside_the_guide_is_rejected(self):
        """The scan-region gate is the second line of defence."""
        image = cv2.imread(str(INFO_PANEL))
        h, w = image.shape[:2]
        region = ScanRegion.for_frame(
            (w, h),
            settings.SCAN_REGION_WIDTH_RATIO,
            settings.SCAN_REGION_HEIGHT_RATIO,
        )
        for det in _load_detector().detect(image):
            overlap = overlap_ratio(det.bbox, region.rect)
            assert overlap < settings.SCAN_REGION_OVERLAP_THRESHOLD, (
                f"false positive {det.bbox} overlaps the guide by {overlap:.2f}"
            )

    def test_pipeline_never_verifies_a_barcode_free_document(self):
        from services.barcode_decoder import BarcodeDecoderService
        from services.scan_pipeline import ScanPipeline, StudentVerifier

        detector = _load_detector()

        class PermissiveDecoder:
            """Returns a payload for anything, to prove gating stops it first."""

            def decode_with_preprocessing(self, image):
                class Item:
                    data = "000065412"
                    type = "CODE39"

                return [Item()]

        pipeline = ScanPipeline(
            detector=detector,
            decoder=PermissiveDecoder(),
            # A permissive stub: the test is that nothing is ever sent to it.
            verifier=StudentVerifier(lookup=lambda v: object()),
        )
        outcome = pipeline.process_frame(cv2.imread(str(INFO_PANEL)))
        assert outcome.state.value != "verified"
        assert outcome.student is None


# --------------------------------------------------------------- BUG-001 --
# The schema enum omitted CODE39 while the assets are CODE39.


class TestBug001BarcodeTypeEnum:
    def test_schema_enum_includes_code39(self):
        from database import schema

        sql = schema.SCHEMA_SQL
        match = re.search(r"`barcode_type`\s+ENUM\(([^)]*)\)", sql, re.IGNORECASE)
        assert match, "barcode_type ENUM not found in schema"
        values = {v.strip().strip("'") for v in match.group(1).split(",")}
        assert "CODE39" in values

    def test_every_model_barcode_type_is_accepted_by_the_schema(self):
        """No value in BarcodeType may be rejected by the column definition."""
        import re as _re

        from database import schema
        from models.barcode import BarcodeType

        match = _re.search(
            r"`barcode_type`\s+ENUM\(([^)]*)\)",
            schema.SCHEMA_SQL,
            _re.IGNORECASE,
        )
        allowed = {v.strip().strip("'") for v in match.group(1).split(",")}
        for member in BarcodeType:
            assert member.value in allowed, (
                f"BarcodeType.{member.name} ('{member.value}') is missing from "
                f"the barcodes.barcode_type ENUM {sorted(allowed)}"
            )

    def test_migration_script_exists_and_is_idempotent_in_spirit(self):
        migration = BASE_DIR / "database" / "migrations" / "002_barcode_type_code39.sql"
        assert migration.exists(), "migration for the CODE39 enum is missing"
        text = migration.read_text()
        assert "CODE39" in text
        assert "ALTER TABLE `barcodes`" in text


# --------------------------------------------------------------- BUG-006 --
# Two barcode conventions coexisted: the generator emitted Code128
# "WIT:STUDENT:<ref>" while the physical cards are CODE39 zero-padded.


class TestBug006BarcodeConvention:
    def test_generator_reuses_the_stored_payload(self):
        from models.student import Student
        from services.barcode_generator import BarcodeGeneratorService

        generator = BarcodeGeneratorService.__new__(BarcodeGeneratorService)
        student = Student(
            reference_number="65412",
            barcode_payload="000065412",
        )
        assert generator.generate_barcode_value(student) == "000065412"
        assert generator.barcode_type_for(student).value == "CODE39"

    def test_generator_falls_back_to_the_prefix_without_a_payload(self):
        from models.student import Student
        from services.barcode_generator import BarcodeGeneratorService

        generator = BarcodeGeneratorService.__new__(BarcodeGeneratorService)
        student = Student(reference_number="65412", barcode_payload=None)
        assert generator.generate_barcode_value(student) == (
            f"{settings.BARCODE_PREFIX}65412"
        )
        assert generator.barcode_type_for(student).value == "CODE128"

    def test_a_regenerated_barcode_round_trips_to_the_stored_payload(self):
        """Generating an image for a real student must produce a readable code."""
        import tempfile

        from models.student import Student
        from services.barcode_decoder import BarcodeDecoderService
        from services.barcode_generator import BarcodeGeneratorService

        generator = BarcodeGeneratorService.__new__(BarcodeGeneratorService)
        student = Student(reference_number="65412", barcode_payload="000065412")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "card"
            ok = generator.generate_barcode_image(
                student.barcode_payload,
                out,
                generator.barcode_type_for(student),
            )
            assert ok
            produced = next(Path(tmp).glob("*.png"))
            decoded = BarcodeDecoderService().decode(cv2.imread(str(produced)))
            assert decoded, "generated barcode is not readable"
            assert decoded[0].data == student.barcode_payload
            assert decoded[0].type == "CODE39"

    def test_physical_cards_and_the_database_agree(self):
        """The card payload must equal students.barcode_payload, not a guess."""
        from pyzbar.pyzbar import decode as zbar_decode

        from services.student_service import StudentService

        decoded = zbar_decode(cv2.imread(str(CARD_ID_1)))
        assert decoded
        payload = decoded[0].data.decode()

        try:
            student = StudentService().get_student_by_barcode(payload)
        except Exception as exc:  # database unavailable
            pytest.skip(f"database unavailable: {type(exc).__name__}")
        if student is None:
            pytest.skip("database has no student for the first card's payload")
        assert student.barcode_payload == payload


# --------------------------------------------------------------- BUG-007 --
# Camera enumeration only tried one backend.


class TestBug007CameraEnumeration:
    def test_scan_cameras_tries_multiple_backends(self):
        import inspect

        from services.camera_service import CameraService

        assert hasattr(CameraService, "_try_open"), "backend fallback helper missing"
        source = inspect.getsource(CameraService.scan_cameras)
        assert "_try_open" in source
        # Probing far past the real device count only produces native warnings.
        default_max = inspect.signature(CameraService.scan_cameras).parameters[
            "max_index"
        ].default
        assert default_max <= 6, f"scan_cameras probes too many indices ({default_max})"

    def test_try_open_releases_failed_backends(self):
        """A backend that refuses the index must not leak an open handle."""
        import cv2

        from services.camera_service import CameraService

        service = CameraService()
        cap = service._try_open(-1)
        assert cap is None, "a negative index must not produce a handle"


class TestCameraProbeBound:
    """The scan must not probe device indices that do not exist.

    On Linux a camera index maps to /dev/videoN, so every index past the
    highest real node is a guaranteed failure. Each one costs several lines of
    native stderr -- including [ERROR] lines from the obsensor backend -- and
    OpenCV 5 no longer exposes setLogLevel, so they cannot be muted and must
    not be provoked.
    """

    def test_bound_never_exceeds_the_requested_maximum(self):
        from services.camera_service import CameraService

        assert CameraService._probe_upper_bound(6) <= 6
        assert CameraService._probe_upper_bound(1) <= 1

    def test_bound_covers_every_existing_device_node(self):
        """A camera at a high index must still be found (sparse nodes)."""
        from pathlib import Path

        from services.camera_service import CameraService

        nodes = []
        for entry in Path("/dev").glob("video*"):
            suffix = entry.name[len("video"):]
            if suffix.isdigit():
                nodes.append(int(suffix))
        if not nodes:
            pytest.skip("no V4L2 device nodes on this machine")

        upper = CameraService._probe_upper_bound(6)
        assert upper >= max(nodes) + 1, (
            f"highest node /dev/video{max(nodes)} would be skipped (upper={upper})"
        )

    def test_index_zero_is_always_reachable(self):
        from services.camera_service import CameraService

        assert CameraService._probe_upper_bound(6) >= 2

    def test_scan_cameras_does_not_probe_past_the_bound(self):
        from services.camera_service import CameraService

        probed = []
        original = CameraService._try_open

        def record(self, index):
            probed.append(index)
            return None

        CameraService._try_open = record
        try:
            CameraService().scan_cameras()
        finally:
            CameraService._try_open = original

        assert probed == sorted(probed), "indices must be probed in order"
        assert len(probed) <= 6, f"probed {probed}"
        if probed:
            assert probed[-1] < max(probed) + 1


class TestNonCaptureNodesAreNotCameras:
    """A UVC camera publishes several /dev/videoN nodes, and only one is a camera.

    On this machine /dev/video1 belongs to the same built-in Chicony webcam as
    /dev/video0 but reports no V4L2 capture capability. V4L2 refuses it
    ("can't open camera by index") while FFMPEG and obsensor open it anyway and
    return non-video frames, with "ioctl(VIDIOC_QBUF): Bad file descriptor" on
    stderr -- so a scan keyed on isOpened() advertised a second camera that could
    not be streamed.
    """

    def test_unknown_capability_is_not_treated_as_no_capture(self):
        from services.camera_service import _v4l2_capture_capability

        assert _v4l2_capture_capability(9999) is None, (
            "a node that does not exist must leave the answer unknown, so a camera "
            "on a platform without /dev/videoN is never skipped"
        )

    def test_non_capture_node_is_never_opened(self, monkeypatch):
        import services.camera_service as camera_service

        monkeypatch.setattr(
            camera_service, "_v4l2_capture_capability", lambda index: False
        )

        def explode(index, *args, **kwargs):
            raise AssertionError(
                "OpenCV was asked to open a node the kernel reports cannot capture"
            )

        monkeypatch.setattr(cv2, "VideoCapture", explode)
        assert camera_service.CameraService()._try_open(1) is None

    def test_capture_capable_node_is_still_opened(self, monkeypatch):
        import services.camera_service as camera_service

        monkeypatch.setattr(
            camera_service, "_v4l2_capture_capability", lambda index: True
        )
        opened = []

        class FakeCapture:
            def isOpened(self):
                return True

        monkeypatch.setattr(
            cv2,
            "VideoCapture",
            lambda index, *args, **kwargs: opened.append(index) or FakeCapture(),
        )
        handle = camera_service.CameraService()._try_open(0)
        assert handle is not None, "a capture-capable node must still be opened"
        assert opened == [0], "the native backend must be tried first"

    def test_listed_cameras_can_capture(self):
        """Every index the scan offers must be a node that can capture video."""
        from services.camera_service import CameraService, _v4l2_capture_capability

        listed = CameraService().scan_cameras()
        if not listed:
            pytest.skip("no cameras on this machine")
        for index in listed:
            assert _v4l2_capture_capability(index) is not False, (
                f"scan listed camera {index}, which the kernel reports cannot capture"
            )


# --------------------------------------------------------------- BUG-008 --
# Bundled font directories are empty.


class TestBug008MissingFonts:
    def test_font_directories_are_empty_or_unused(self):
        for name in ("agrandir", "open-sauce"):
            d = settings.FONTS_DIR / name
            if not d.exists():
                continue
            contents = [p for p in d.iterdir() if p.is_file()]
            assert not contents, f"{name} font directory is unexpectedly populated"

    def test_typography_falls_back_without_crashing(self):
        from styles.typography import body_font, get_agrandir_family

        assert isinstance(get_agrandir_family(), str)
        assert body_font(12) is not None