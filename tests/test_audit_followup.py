"""Regression tests for the issues found in the 2026-10-15 follow-up audit.

Each class names the finding it guards. Everything runs against the project's own
assets; no barcode is synthesised and no external file is read.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import cv2
import numpy as np
import pytest

from config.settings import settings

BASE_DIR = Path(__file__).resolve().parent.parent
CARD_1 = settings.ASSETS_DIR / "images" / "Id_1.png"
CARD_2 = settings.ASSETS_DIR / "images" / "Id_2.png"
BARCODE_1 = settings.ASSETS_DIR / "barcodes" / "barcode_1.png"
BARCODE_2 = settings.ASSETS_DIR / "barcodes" / "barcode_2.png"
PANEL_1 = settings.info_image("student_1_info.png")
PANEL_2 = settings.info_image("student_2_info.png")


# ------------------------------------------------------ asset integrity --

#: Recorded at the start of the audit. Any drift means an original asset was
#: modified, which the task forbids outright.
ASSET_DIGESTS = {
    "assets/barcodes/barcode_1.png": "9adeb2f3248ca80744232465139e5bd9",
    "assets/barcodes/barcode_2.png": "54f238f873fa4c697c4f2206b08717da",
    "assets/images/Id_1.png": "15de778f2c61c9924b0ed0432d829853",
    "assets/images/Id_2.png": "38e37019c04f9741e2e064e8262cc1e8",
    "assets/images/info/student_1_info.png": "6f6160d4e5c72fbbdd18d34e7722823a",
    "assets/images/info/student_2_info.png": "57c4262c9838999937d71d8e3ce2b834",
}


class TestAssetIntegrity:
    @pytest.mark.parametrize("rel,expected", sorted(ASSET_DIGESTS.items()))
    def test_original_asset_is_unchanged(self, rel, expected):
        path = BASE_DIR / rel
        assert path.exists(), f"original asset is missing: {rel}"
        actual = hashlib.md5(path.read_bytes()).hexdigest()
        assert actual == expected, f"{rel} was modified (md5 {actual})"

    def test_all_six_assets_are_readable(self):
        for rel in ASSET_DIGESTS:
            image = cv2.imread(str(BASE_DIR / rel))
            assert image is not None, f"{rel} does not decode as an image"

    def test_info_panels_resolve_through_the_tolerant_lookup(self):
        """The panels moved into images/info/; both layouts must work."""
        for name in ("student_1_info.png", "student_2_info.png"):
            resolved = settings.info_image(name)
            assert resolved.exists(), f"{name} not found at {resolved}"


# -------------------------------------------------- barcode <-> identity --


class TestBarcodeIdentity:
    """The payload numbering does not follow the filenames."""

    def test_barcode_1_belongs_to_card_2(self):
        from services.barcode_decoder import BarcodeDecoderService

        decoder = BarcodeDecoderService()
        crop = decoder.decode(cv2.imread(str(BARCODE_1)))
        card = decoder.decode(cv2.imread(str(CARD_2)))
        assert crop and card, "both assets must decode"
        assert crop[0].data == card[0].data, (
            f"barcode_1.png ({crop[0].data}) is expected to be a crop of "
            f"Id_2.png ({card[0].data}), not of Id_1.png"
        )

    def test_barcode_2_belongs_to_card_1(self):
        from services.barcode_decoder import BarcodeDecoderService

        decoder = BarcodeDecoderService()
        crop = decoder.decode(cv2.imread(str(BARCODE_2)))
        card = decoder.decode(cv2.imread(str(CARD_1)))
        assert crop and card
        assert crop[0].data == card[0].data

    @pytest.mark.parametrize("panel", [PANEL_1, PANEL_2])
    def test_info_panels_contain_no_barcode(self, panel):
        from pyzbar.pyzbar import decode as zbar_decode

        assert not zbar_decode(cv2.imread(str(panel))), (
            f"{panel.name} is used as a negative sample and must not decode"
        )


# ------------------------------------------------------------- decoding --


class TestOrderedDecoding:
    def test_original_crop_is_tried_first(self):
        """Ordering is the point: a readable crop must not be degraded first."""
        from services.barcode_decoder import BarcodeDecoderService

        result = BarcodeDecoderService().decode_best(cv2.imread(str(BARCODE_1)))
        assert result.ok
        assert result.attempt == "original"
        assert result.attempts_tried == 1, (
            "early exit is required; running every variant per frame is what "
            f"made a frame cost 520 ms (tried {result.attempts_tried})"
        )

    def test_attempt_cap_is_respected(self):
        from services.barcode_decoder import BarcodeDecoderService

        blank = np.full((120, 400, 3), 200, dtype=np.uint8)
        result = BarcodeDecoderService().decode_best(blank, max_attempts=2)
        assert not result.ok
        assert result.attempts_tried == 2

    def test_degenerate_rects_are_discarded(self):
        """zbar emits 0x0 rects on variants it cannot read."""
        from services.barcode_decoder import MIN_SYMBOL_SIDE_PX, BarcodeDecoderService

        assert MIN_SYMBOL_SIDE_PX >= 2
        blank = np.full((80, 300, 3), 128, dtype=np.uint8)
        for item in BarcodeDecoderService().decode(blank):
            assert item.bbox.width >= MIN_SYMBOL_SIDE_PX
            assert item.bbox.height >= MIN_SYMBOL_SIDE_PX

    def test_rotation_preserves_the_whole_symbol(self):
        """Rotating inside a fixed canvas would cut the outer bars off."""
        from services.barcode_decoder import rotate_preserve

        image = cv2.imread(str(CARD_1))
        crop = image[792:904, 33:558]
        rotated = rotate_preserve(crop, 6.0)
        assert rotated is not None
        assert rotated.shape[0] >= crop.shape[0], "canvas must grow, not crop"
        assert rotated.shape[1] >= crop.shape[1]

    @pytest.mark.parametrize("angle", [-9.0, 9.0])
    def test_moderate_tilt_still_decodes(self, angle):
        from services.barcode_decoder import BarcodeDecoderService, rotate_preserve

        image = cv2.imread(str(CARD_1))
        crop = image[780:920, 20:570]
        result = BarcodeDecoderService().decode_best(rotate_preserve(crop, angle))
        assert result.ok and result.data == "000065412", (
            f"{angle:+.0f} degrees was expected to remain readable"
        )

    def test_severe_tilt_is_not_claimed_to_work(self):
        """Documents the limit rather than pretending rotations rescue it."""
        from services.barcode_decoder import BarcodeDecoderService, rotate_preserve

        image = cv2.imread(str(CARD_1))
        crop = image[780:920, 20:570]
        result = BarcodeDecoderService().decode_best(
            rotate_preserve(crop, 35.0), rotations=True
        )
        assert not result.ok, (
            "a 35 degree tilt is outside the measured limit; if this now "
            "passes the claim in ERROR_TRACE_REPORT needs updating"
        )


# ------------------------------------------------------ pipeline wiring --


class TestDuplicateCandidateSuppression:
    """Overlapping boxes on one symbol must not all reach the decoder."""

    def _candidates(self, n=6):
        from services.scan_pipeline import BarcodeCandidate

        base = (300, 300, 400, 120)
        out = []
        for i in range(n):
            jitter = i * 6
            out.append(
                BarcodeCandidate(
                    bbox=(base[0] + jitter, base[1] + jitter // 2, base[2], base[3]),
                    confidence=0.9 - i * 0.05,
                    class_name="barcode",
                    in_scan_region=True,
                    crop=(base[0] + jitter, base[1] + jitter // 2, base[2], base[3]),
                )
            )
        return out

    def test_near_duplicate_boxes_are_recognised(self):
        from services.scan_pipeline import ScanPipeline

        pipeline = ScanPipeline()
        assert pipeline._is_duplicate_of((10, 10, 100, 50), (16, 12, 100, 50), 0.70)

    def test_separate_boxes_are_not_duplicates(self):
        from services.scan_pipeline import ScanPipeline

        pipeline = ScanPipeline()
        assert not pipeline._is_duplicate_of((10, 10, 100, 50), (400, 400, 100, 50), 0.70)

    def test_only_one_of_six_overlapping_boxes_is_decoded(self):
        from services.barcode_decoder import BarcodeDecoderService
        from services.scan_pipeline import ScanPipeline

        frame = cv2.imread(str(BARCODE_1))
        pipeline = ScanPipeline(detector=None, decoder=BarcodeDecoderService())

        class CountingDecoder:
            def __init__(self):
                self.calls = 0

            def decode_best(self, roi, rotations=False, max_attempts=None):
                self.calls += 1
                from services.barcode_decoder import BarcodeDecoderService

                return BarcodeDecoderService().decode_best(
                    roi, rotations=rotations, max_attempts=max_attempts
                )

        counting = CountingDecoder()
        pipeline.decoder = counting
        candidates = self._candidates()
        for cand in candidates:
            x, y, w, h = cand.bbox
            m = settings.SCAN_CROP_MARGIN_RATIO
            cand.crop = (
                max(0, int(x - w * m)), max(0, int(y - h * m)),
                min(frame.shape[1] - max(0, int(x - w * m)), int(w * (1 + 2 * m))),
                min(frame.shape[0] - max(0, int(y - h * m)), int(h * (1 + 2 * m))),
            )
        # A region rect far away so the scan-region fallback is not used.
        region = (0, 0, 10, 10)
        pipeline._decode_candidates(frame, candidates, region)
        assert counting.calls <= 2, (
            f"six overlapping boxes should collapse to one decode attempt, got {counting.calls}"
        )


class TestDecoderApiCompatibility:
    def test_pipeline_accepts_a_legacy_decoder(self):
        """A decoder exposing only decode_with_preprocessing still works."""
        from services.scan_pipeline import ScanPipeline

        class LegacyDecoder:
            def decode_with_preprocessing(self, image):
                class Item:
                    data = "000065412"
                    type = "CODE39"

                return [Item()]

        frame = np.zeros((300, 400, 3), dtype=np.uint8)
        pipeline = ScanPipeline(decoder=LegacyDecoder())
        assert pipeline._decode_roi(frame, 2) == ("000065412", "CODE39", "decode_with_preprocessing", 0)


# ------------------------------------------------- detection presentation --


class TestDetectionSmoother:
    def _cand(self, x, y=200, w=300, h=90, conf=0.9):
        from services.scan_pipeline import BarcodeCandidate

        return BarcodeCandidate(bbox=(x, y, w, h), confidence=conf, class_name="barcode")

    def test_jitter_is_reduced(self):
        from services.detection_smoother import DetectionSmoother

        smoother = DetectionSmoother(alpha=0.45, grace_seconds=0.6)
        raw, smoothed = [], []
        for i in range(8):
            x = 100 + (i % 2) * 4
            out = smoother.update([self._cand(x)], now=i * 0.05)
            raw.append(x)
            smoothed.append(out[0].bbox[0])
        assert (max(smoothed) - min(smoothed)) < (max(raw) - min(raw)), (
            "smoothing must reduce frame-to-frame jitter"
        )

    def test_box_is_kept_through_the_grace_period_then_dropped(self):
        from services.detection_smoother import DetectionSmoother

        smoother = DetectionSmoother(alpha=0.45, grace_seconds=0.6)
        smoother.update([self._cand(100)], now=0.0)
        assert smoother.update([], now=0.3), "box must survive a single dropped frame"
        assert smoother.update([], now=1.2) == [], "box must not linger forever"
        assert smoother.is_empty

    def test_no_box_when_nothing_detected(self):
        from services.detection_smoother import DetectionSmoother

        smoother = DetectionSmoother(alpha=0.45, grace_seconds=0.0)
        assert smoother.update(None, now=0.0) == []

    def test_smoothing_does_not_mutate_pipeline_candidates(self):
        from services.detection_smoother import DetectionSmoother

        smoother = DetectionSmoother(alpha=0.5, grace_seconds=0.6)
        original = self._cand(100)
        before = tuple(original.bbox)
        smoother.update([original], now=0.0)
        smoother.update([self._cand(300)], now=0.05)
        assert tuple(original.bbox) == before, "the pipeline's object was mutated"


class TestOverlayStateAnimation:
    def test_every_state_renders_its_own_box_colour(self):
        from services.scanner_overlay import STATE_BOX_COLORS, OverlayRenderer
        from services.scan_pipeline import BarcodeCandidate, ScanOutcome, ScanState

        frame = cv2.imread(str(CARD_1))
        renderer = OverlayRenderer()
        colours = set()
        for state in ScanState:
            candidate = BarcodeCandidate(
                bbox=(33, 792, 525, 112), confidence=0.9, class_name="barcode",
                in_scan_region=True,
            )
            outcome = ScanOutcome(state=state, message="", candidates=[candidate],
                                  scan_region=(100, 300, 700, 300))
            canvas = renderer.render(frame, outcome, target_size=(480, 640), progress=0.3)
            assert canvas is not None and canvas.size > 0
            colours.add(STATE_BOX_COLORS[state])
        assert len(colours) >= 4, "each outcome needs a visually distinct box"

    def test_reduced_motion_is_honoured(self):
        from services.scanner_overlay import OverlayRenderer
        from services.scan_pipeline import ScanOutcome, ScanState

        frame = cv2.imread(str(CARD_1))
        for reduced in (True, False):
            renderer = OverlayRenderer(reduced_motion=reduced)
            canvas = renderer.render(
                frame, ScanOutcome(state=ScanState.SEARCHING, message=""),
                target_size=(480, 640), progress=0.25,
            )
            assert canvas is not None


# ------------------------------------------------------- audit classifier --


class TestAuditNameComparison:
    """The card omits the middle initial the database record carries."""

    def test_middle_initial_difference_is_not_a_mismatch(self):
        from scripts.audit_ids import names_agree

        assert names_agree("JAVY DANIELLE CAPORAL", "Javy Danielle N. Caporal")

    def test_different_people_do_not_agree(self):
        from scripts.audit_ids import names_agree

        assert not names_agree("JAVY DANIELLE CAPORAL", "Ronna Mae D. Alemania")

    def test_different_family_name_does_not_agree(self):
        from scripts.audit_ids import names_agree

        assert not names_agree("RONNA MAE ALEMANIA", "Javy Danielle Caporal")


# --------------------------------------------------------- device choice --


class TestDeviceFallback:
    def test_detector_falls_back_to_cpu_when_the_gpu_errors(self):
        """A broken GPU must degrade to slow, never to a dead scanner."""
        from services.yolo_detector import Detection, YOLODetectorService

        detector = YOLODetectorService()
        detector.device = "0"
        calls = []

        class FlakyModel:
            def __call__(self, frame, conf=None, iou=None, device=None, verbose=False):
                calls.append(device)
                if device != "cpu":
                    raise RuntimeError("CUDA driver error")
                return []

        detector.model = FlakyModel()
        detector.is_loaded = True
        assert detector.detect(np.zeros((10, 10, 3), dtype=np.uint8)) == []
        assert detector.device == "cpu", "must stay on CPU after falling back"
        assert "cpu" in calls


# ------------------------------------------------- resolved mapping lock --


class TestResolvedStudentMapping:
    """Guards the 2026-10-15 administrative resolution of payload 000065902.

    Ronna Mae D. Alemania's card encodes 000065902 while her printed panel shows
    reference 000001. An administrator confirmed the card's value (65902) as
    authoritative; see scripts/resolve_student_mapping.py and
    reports/ADMIN_ACTIONS_REQUIRED.md. These tests fail if that record is
    removed, altered or re-pointed at the wrong person.
    """

    RESOLVED_PAYLOAD = "000065902"

    def test_resolved_payload_returns_the_right_student(self):
        from services.student_service import StudentService

        try:
            student = StudentService().get_student_by_barcode(self.RESOLVED_PAYLOAD)
        except Exception as exc:  # database unavailable is not a mapping failure
            pytest.skip(f"database unavailable: {type(exc).__name__}")

        assert student is not None, (
            "payload 000065902 was resolved on 2026-10-15 and must resolve again"
        )
        assert student.last_name == "Alemania", (
            f"payload 000065902 must map to Alemania, got {student.last_name!r}"
        )
        assert student.reference_number == "65902", (
            "the administrator confirmed the card's value as authoritative; "
            f"record holds {student.reference_number!r}"
        )

    def test_both_supplied_cards_resolve_end_to_end(self):
        """Scanning each real card must show that card's own student."""
        import cv2

        from services.scan_pipeline import ScanPipeline, ScanState, StudentVerifier
        from services.barcode_decoder import BarcodeDecoderService
        from services.yolo_detector import YOLODetectorService
        from tests.scanner_fixtures import make_webcam_frame

        detector = YOLODetectorService()
        if not detector.load_model(settings.YOLO_MODEL_PATH):
            pytest.skip("no model available")

        try:
            from services.student_service import StudentService

            lookup = StudentService().get_student_by_barcode
        except Exception as exc:
            pytest.skip(f"database unavailable: {type(exc).__name__}")

        expected = {CARD_1: "Caporal", CARD_2: "Alemania"}
        for card, family in expected.items():
            pipeline = ScanPipeline(
                detector=detector,
                decoder=BarcodeDecoderService(),
                verifier=StudentVerifier(lookup=lookup),
                cooldown_seconds=0.0,
            )
            outcome = pipeline.process_frame(make_webcam_frame(card))
            assert outcome.state == ScanState.VERIFIED, (
                f"{card.name} did not verify: {outcome.state.value}"
            )
            assert outcome.student is not None
            assert outcome.student.last_name == family, (
                f"{card.name} must show {family}, showed "
                f"{outcome.student.last_name!r} -- cards must not be cross-matched"
            )

    def test_no_record_means_unknown_never_a_wrong_student(self):
        """An unmatched payload must show nobody, not the nearest match."""
        from services.scan_pipeline import ScanPipeline, ScanState, StudentVerifier
        from services.barcode_decoder import BarcodeDecoderService
        from services.yolo_detector import YOLODetectorService
        from tests.scanner_fixtures import make_webcam_frame

        detector = YOLODetectorService()
        if not detector.load_model(settings.YOLO_MODEL_PATH):
            pytest.skip("no model available")

        pipeline = ScanPipeline(
            detector=detector,
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=lambda value: None),
            cooldown_seconds=0.0,
        )
        outcome = pipeline.process_frame(make_webcam_frame(CARD_1))
        assert outcome.state == ScanState.UNKNOWN_BARCODE
        assert outcome.student is None
        assert outcome.verified_now is False
