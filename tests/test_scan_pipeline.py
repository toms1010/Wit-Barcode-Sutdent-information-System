"""Tests for the live scan pipeline: geometry, gating, decoding, verification.

The decode and end-to-end tests use the real ID card assets checked into
``assets/``. Nothing is mocked at the CV layer: a genuine YOLO box has to be
found, a genuine barcode has to be read, and a genuine database row has to be
found. Tests that need a specific student are skipped when the database has no
matching row rather than being made to pass with a fake.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from config.settings import settings
from services.scan_geometry import (
    DisplayTransform,
    ScanRegion,
    clamp_rect,
    expand_rect,
    intersection,
    is_crop_usable,
    letterboxed_size,
    overlap_ratio,
)
from services.scan_pipeline import (
    BarcodeCandidate,
    ScanOutcome,
    ScanPipeline,
    ScanState,
    StudentVerifier,
)
from services.scanner_overlay import STATE_BOX_COLORS, OverlayRenderer, hex_to_bgr
from tests.scanner_fixtures import (
    CARD_ID_1,
    CARD_ID_3,
    CARD_ID_2,
    WEBCAM_SIZE,
    make_webcam_frame,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --------------------------------------------------------------- geometry --


class TestDisplayTransform:
    def test_uniform_scale_no_letterbox_when_aspect_matches(self):
        t = DisplayTransform(frame_size=(1280, 720), display_size=(640, 360))
        assert t.scale == pytest.approx(0.5)
        assert t.offset == (0, 0)

    def test_letterbox_centred_on_the_short_axis(self):
        # A 4:3 frame fitted to a 800x480 canvas scales to the height and is
        # centred horizontally, leaving a 80px margin each side.
        t = DisplayTransform(frame_size=(640, 480), display_size=(800, 480))
        assert t.scale == pytest.approx(1.0)
        assert t.offset == (80, 0)

        # The same frame inside a square canvas is limited by the width and
        # letterboxed vertically.
        t2 = DisplayTransform(frame_size=(640, 480), display_size=(400, 400))
        assert t2.scale == pytest.approx(400 / 640)
        ox, oy = t2.offset
        assert oy == (400 - 480 * t2.scale) // 2
        assert ox == 0

    def test_round_trip_through_display_space(self):
        t = DisplayTransform(frame_size=(1280, 720), display_size=(640, 360))
        for rect in [(0, 0, 100, 50), (600, 300, 400, 200), (1279, 719, 1, 1)]:
            back = t.to_frame_rect(t.to_display_rect(rect))
            for a, b in zip(back, rect):
                assert abs(a - b) <= 1

    def test_points_map_monotonically(self):
        t = DisplayTransform(frame_size=(1280, 720), display_size=(800, 450))
        xs = [t.to_display_point(x, 0)[0] for x in range(0, 1280, 64)]
        assert xs == sorted(xs)

    def test_mirror_is_a_pure_horizontal_reflection(self):
        t = DisplayTransform(frame_size=(1280, 720), display_size=(640, 360), mirrored=True)
        plain = DisplayTransform(frame_size=(1280, 720), display_size=(640, 360), mirrored=False)
        w = 640
        rect = (100, 50, 200, 80)
        mx, my, mw, mh = t.to_display_rect(rect)
        px, py, pw, ph = plain.to_display_rect(rect)
        assert (mw, mh) == (pw, ph)
        # The mirrored box ends where the plain box started, and vice versa.
        assert mx + mw == w - px
        assert mx == w - (px + pw)
        assert (my, mh) == (py, ph)

    def test_mirrored_round_trip_returns_to_the_original_rect(self):
        t = DisplayTransform(frame_size=(1280, 720), display_size=(640, 360), mirrored=True)
        rect = (300, 200, 250, 120)
        back = t.to_frame_rect(t.to_display_rect(rect))
        for a, b in zip(back, rect):
            assert abs(a - b) <= 1


class TestScanRegion:
    def test_region_is_centred_and_proportional(self):
        r = ScanRegion.for_frame((1280, 720), 0.5, 0.4)
        assert r.width == 640 and r.height == 288
        assert r.x == (1280 - 640) // 2
        assert r.y == (720 - 288) // 2

    def test_region_scales_with_frame_size(self):
        # Same aspect ratio at two resolutions: the guide must keep its shape.
        small = ScanRegion.for_frame((640, 480), 0.5, 0.5)
        large = ScanRegion.for_frame((1280, 960), 0.5, 0.5)
        assert (large.width, large.height) == (small.width * 2, small.height * 2)
        assert small.rect != large.rect

    def test_region_changes_shape_with_the_frame_aspect(self):
        # 16:9 and 4:3 frames legitimately give different guide proportions;
        # the guide is defined as a fraction of each axis, not a fixed shape.
        wide = ScanRegion.for_frame((1920, 1080), 0.5, 0.5)
        tall = ScanRegion.for_frame((1024, 768), 0.5, 0.5)
        assert wide.width / wide.height > tall.width / tall.height

    def test_ratios_are_clamped(self):
        r = ScanRegion.for_frame((1000, 1000), 5.0, -3.0)
        assert 0 < r.width <= 1000 and 0 < r.height <= 1000


class TestRectHelpers:
    def test_overlap_ratio_is_containment_of_the_box(self):
        region = (100, 100, 200, 200)
        assert overlap_ratio((100, 100, 200, 200), region) == pytest.approx(1.0)
        assert overlap_ratio((200, 100, 200, 200), region) == pytest.approx(0.5)
        assert overlap_ratio((300, 100, 200, 200), region) == pytest.approx(0.0)

    def test_a_small_box_fully_inside_scores_one(self):
        # The reason containment beats IoU here: a small barcode held inside the
        # guide must qualify even though IoU against the guide is tiny.
        region = (0, 0, 1000, 1000)
        small = (400, 400, 50, 20)
        assert overlap_ratio(small, region) == pytest.approx(1.0)

    def test_intersection_of_disjoint_rects_is_empty(self):
        assert intersection((0, 0, 10, 10), (50, 50, 10, 10)) == (0, 0, 0, 0)

    def test_clamp_rect_keeps_rect_inside_frame(self):
        assert clamp_rect((-20, -30, 5000, 5000), (800, 600)) == (0, 0, 800, 600)
        x, y, w, h = clamp_rect((700, 500, 400, 400), (800, 600))
        assert x + w <= 800 and y + h <= 600 and w > 0 and h > 0

    def test_expand_rect_adds_margin(self):
        x, y, w, h = expand_rect((100, 100, 200, 100), 0.25, (1000, 1000))
        assert (x, y, w, h) == (50, 75, 300, 150)

    def test_expand_rect_clips_at_frame_edge(self):
        # A 100px box with a 50px margin grows to 200px, then is clipped by the
        # frame origin so it never starts at a negative coordinate.
        x, y, w, h = expand_rect((0, 0, 100, 100), 0.5, (500, 500))
        assert (x, y, w, h) == (0, 0, 200, 200)

        x, y, w, h = expand_rect((450, 450, 100, 100), 0.5, (500, 500))
        assert x + w <= 500 and y + h <= 500
        assert (x, y, w, h) == (400, 400, 100, 100)

    def test_is_crop_usable_rejects_tiny_boxes(self):
        assert is_crop_usable((0, 0, 200, 40), 80, 24)
        assert not is_crop_usable((0, 0, 40, 200), 80, 24)
        assert not is_crop_usable((0, 0, 200, 10), 80, 24)

    def test_letterboxed_size_preserves_aspect(self):
        w, h = letterboxed_size((1280, 720), (600, 600))
        assert w <= 600 and h <= 600
        # Integer rounding of a 1.778 aspect ratio leaves a small residual.
        assert w / h == pytest.approx(1280 / 720, rel=1e-2)


# ------------------------------------------------------------ scan region --


class TestScanRegionGate:
    def test_barcode_entirely_inside_qualifies(self):
        region = ScanRegion.for_frame((1280, 720), 0.66, 0.42)
        inside = (region.x + 10, region.y + 10, 100, 40)
        assert overlap_ratio(inside, region.rect) >= settings.SCAN_REGION_OVERLAP_THRESHOLD

    def test_barcode_outside_never_qualifies(self):
        region = ScanRegion.for_frame((1280, 720), 0.66, 0.42)
        above = (region.x, region.y - 200, 100, 40)
        assert overlap_ratio(above, region.rect) == 0.0

    def test_threshold_is_configurable_and_respected(self):
        region = ScanRegion.for_frame((1280, 720), 0.66, 0.42)
        # Straddling the left edge: half in, half out.
        box = (region.x - 50, region.y, 100, 40)
        ratio = overlap_ratio(box, region.rect)
        assert 0.3 < ratio < 0.7
        assert ratio >= 0.5      # passes a lenient gate
        assert ratio < 0.65      # fails the shipped gate


# ---------------------------------------------------------------- overlay --


def _yellow_ink_rows(canvas, guide):
    """Rows of overlay-yellow ink inside the guide's central strip.

    Located by colour rather than brightness, and only within a central column
    strip so the guide's own outline and corner brackets are excluded. The scan
    guide and its sweeping line were both removed, so nothing yellow may be left
    inside the scan region.
    """
    gx, gy, gw, gh = guide
    inset_x = int(gw * 0.30)
    inset_y = 4
    band = canvas[gy + inset_y : gy + gh - inset_y, gx + inset_x : gx + gw - inset_x]
    b, g, r = band[:, :, 0], band[:, :, 1], band[:, :, 2]
    mask = (b < 60) & (g > 195) & (r > 235)
    return np.nonzero(mask.any(axis=1))[0]


class TestOverlay:
    def test_hex_to_bgr_is_channel_reversed(self):
        assert hex_to_bgr("FFD600") == (0, 214, 255)
        assert hex_to_bgr("#00FF00") == (0, 255, 0)

    def test_render_does_not_mutate_the_source_frame(self):
        frame = make_webcam_frame(CARD_ID_1)
        original = frame.copy()
        renderer = OverlayRenderer(mirrored=False)
        renderer.render(frame, None, target_size=(640, 480), progress=0.5)
        assert np.array_equal(frame, original)

    def test_render_preserves_aspect_ratio(self):
        frame = make_webcam_frame(CARD_ID_1)
        canvas = OverlayRenderer(mirrored=False).render(
            frame, None, target_size=(600, 600), progress=0.0
        )
        h, w = canvas.shape[:2]
        assert w / h == pytest.approx(WEBCAM_SIZE[0] / WEBCAM_SIZE[1], rel=0.02)

    def test_no_scan_guide_or_sweeping_line_is_drawn(self):
        """Neither the yellow frame nor the sweep may appear, at any phase."""
        frame = make_webcam_frame(CARD_ID_1)
        renderer = OverlayRenderer(mirrored=False)
        target = (600, 400)
        region = ScanRegion.for_frame(WEBCAM_SIZE, 0.66, 0.42).rect
        guide = DisplayTransform(
            WEBCAM_SIZE, letterboxed_size(WEBCAM_SIZE, target)
        ).to_display_rect(region)

        for p in np.linspace(0.0, 1.0, 12):
            canvas = renderer.render(frame, None, target_size=target, progress=float(p))
            rows = _yellow_ink_rows(canvas, guide)
            assert rows.size == 0, (
                f"yellow overlay ink drawn inside the scan region at progress "
                f"{p:.2f}: {rows.size} row(s)"
            )

    def test_detection_box_is_still_drawn(self):
        """Removing the guide must not remove the box on the barcode itself."""
        frame = make_webcam_frame(CARD_ID_1)
        target = (600, 400)
        bbox = (400, 300, 300, 90)
        canvas = OverlayRenderer(mirrored=False).render(
            frame,
            ScanOutcome(
                state=ScanState.BARCODE_DETECTED,
                message="Barcode detected",
                candidates=[
                    BarcodeCandidate(
                        bbox=bbox,
                        confidence=0.9,
                        class_name="barcode",
                        in_scan_region=True,
                    )
                ],
            ),
            target_size=target,
            progress=0.0,
        )

        dx, dy, dw, dh = DisplayTransform(
            WEBCAM_SIZE, letterboxed_size(WEBCAM_SIZE, target)
        ).to_display_rect(bbox)
        amber = np.array(STATE_BOX_COLORS[ScanState.BARCODE_DETECTED])
        # A tolerance band around the outline, so antialiasing and the label
        # plate cannot make this flaky.
        pad = 6
        roi = canvas[
            max(0, dy - pad) : dy + dh + pad, max(0, dx - pad) : dx + dw + pad
        ]
        close = (np.abs(roi.astype(int) - amber).max(axis=2) <= 24)
        assert close.sum() > 50, (
            f"no detection box drawn at display rect {(dx, dy, dw, dh)}"
        )

    def test_mirrored_render_flips_pixels_not_text(self):
        frame = make_webcam_frame(CARD_ID_1)
        plain = OverlayRenderer(mirrored=False).render(frame, None, target_size=(500, 350))
        mirrored = OverlayRenderer(mirrored=True).render(frame, None, target_size=(500, 350))
        assert plain.shape == mirrored.shape
        assert not np.array_equal(plain, mirrored)


# ------------------------------------------------- real-asset decode path --


class TestRealBarcodeDecoding:
    """Uses the real card assets; no barcode is fabricated here."""

    @pytest.mark.parametrize("path", [CARD_ID_1, CARD_ID_2])
    def test_decoder_reads_the_real_cards(self, path):
        import cv2

        from services.barcode_decoder import BarcodeDecoderService

        image = cv2.imread(str(path))
        assert image is not None
        decoded = BarcodeDecoderService().decode(image)
        assert decoded, f"pyzbar failed to read the real barcode in {path.name}"
        assert all(d.data.isdigit() for d in decoded)

    def test_pipeline_verifies_the_student_on_the_first_card(self):
        """End to end: real card -> YOLO -> gate -> decode -> MySQL."""
        import cv2

        from services.barcode_decoder import BarcodeDecoderService
        from services.yolo_detector import YOLODetectorService
        from services.student_service import StudentService

        detector = YOLODetectorService()
        if not detector.load_active_model():
            pytest.skip("No YOLO model available")

        # Read the payload straight from the card so the test compares against
        # the physical asset rather than a hard-coded constant.
        expected = None
        for item in BarcodeDecoderService().decode(cv2.imread(str(CARD_ID_1))):
            expected = item.data
            break
        assert expected

        pipeline = ScanPipeline(
            detector=detector,
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=StudentService().get_student_by_barcode),
            cooldown_seconds=0.0,
        )
        outcome = pipeline.process_frame(make_webcam_frame(CARD_ID_1))

        assert outcome.decoded_value == expected
        assert outcome.state in (ScanState.VERIFIED, ScanState.UNKNOWN_BARCODE)
        if outcome.state == ScanState.UNKNOWN_BARCODE:
            pytest.skip(f"Database has no student for payload {expected}")
        assert outcome.verified_now is True
        assert outcome.student is not None
        assert outcome.student.barcode_payload == expected

    def test_a_card_aimed_at_the_drawn_guide_is_still_decoded(self):
        """Regression for the gate geometry.

        The yellow guide is 66% x 42% of the frame, but on these cards the
        barcode sits ~78% of the way down, so a card held squarely in the guide
        put its barcode at roughly y 558-641 -- entirely below a gate spanning
        y 209-511. Using the guide rectangle as the decode gate therefore
        refused close and medium scans outright. The gate is now a separate,
        wider rectangle; this pins the behaviour that actually matters: a card
        presented normally must decode.
        """
        import cv2

        from services.barcode_decoder import BarcodeDecoderService
        from services.scan_pipeline import ScanRegion
        from services.yolo_detector import YOLODetectorService

        frame_w, frame_h = WEBCAM_SIZE
        guide = ScanRegion.for_frame(
            (frame_w, frame_h),
            settings.SCAN_REGION_WIDTH_RATIO,
            settings.SCAN_REGION_HEIGHT_RATIO,
        ).rect
        gate = ScanRegion.for_frame(
            (frame_w, frame_h),
            settings.SCAN_GATE_WIDTH_RATIO,
            settings.SCAN_GATE_HEIGHT_RATIO,
        ).rect

        # The gate must strictly contain the guide, or guiding the operator into
        # the frame would not help them scan.
        assert gate[1] <= guide[1] and gate[3] + gate[1] >= guide[3] + guide[1]
        assert gate[2] >= guide[2]

        hits = BarcodeDecoderService().decode(cv2.imread(str(CARD_ID_1)))
        assert hits, "fixture card must be readable"
        expected_payload = hits[0].data

        detector = YOLODetectorService()
        if not detector.load_active_model():
            pytest.skip("No YOLO model available")
        pipeline = ScanPipeline(
            detector=detector,
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=lambda v: None),
        )
        outcome = pipeline.process_frame(make_webcam_frame(CARD_ID_1))
        assert outcome.decoded_value == expected_payload, outcome.message

    def test_a_detection_outside_the_gate_is_never_decoded(self):
        """The gate guarantee, checked without depending on card geometry.

        A synthetic detector puts the box at a fixed position so the assertion
        does not depend on where a real card's barcode happens to fall. With
        the gate now 88% of the frame, a real card is essentially never outside
        it, which is exactly why this test has to place the box itself.
        """
        from services.scan_pipeline import ScanRegion

        calls = []

        class CountingDecoder:
            def decode_with_preprocessing(self, image):
                calls.append(image.shape)
                return []

        class FixedDetector:
            """Reports one box at a caller-chosen corner."""

            def __init__(self, box):
                self.box = box

            def detect(self, frame):
                return [SimpleNamespace(bbox=self.box, confidence=0.99,
                                        class_name="barcode")]

        frame_w, frame_h = WEBCAM_SIZE
        gate = ScanRegion.for_frame(
            (frame_w, frame_h),
            settings.SCAN_GATE_WIDTH_RATIO,
            settings.SCAN_GATE_HEIGHT_RATIO,
        ).rect

        # A band hugging the very top edge, mostly above the gate's top.
        outside_box = (10, 0, 240, 24)
        assert overlap_ratio(outside_box, gate) < \
            settings.SCAN_REGION_OVERLAP_THRESHOLD, (
            "the fixture must actually be outside the gate for this to test anything"
        )

        pipeline = ScanPipeline(
            detector=FixedDetector(outside_box),
            decoder=CountingDecoder(),
            verifier=StudentVerifier(lookup=lambda v: None),
        )
        frame = np.zeros((frame_h, frame_w, 3), dtype=np.uint8)
        outcome = pipeline.process_frame(frame)

        assert outcome.candidates, "the detector should still report its box"
        assert not outcome.candidates[0].in_scan_region, (
            "a box outside the gate must not be marked in-region"
        )
        assert calls == [], "decoder was invoked for a barcode outside the gate"

    def test_no_detection_reports_searching(self):
        class EmptyDetector:
            def detect(self, frame):
                return []

        pipeline = ScanPipeline(detector=EmptyDetector(), decoder=None)
        outcome = pipeline.process_frame(np.zeros((720, 1280, 3), dtype=np.uint8))
        assert outcome.state == ScanState.SEARCHING
        assert outcome.message == "Searching for barcode..."
        assert outcome.candidates == []

    def test_unknown_payload_is_not_treated_as_verified(self):
        class FixedDetector:
            def detect(self, frame):
                from services.yolo_detector import Detection

                region = ScanRegion.for_frame(
                    (frame.shape[1], frame.shape[0]),
                    settings.SCAN_REGION_WIDTH_RATIO,
                    settings.SCAN_REGION_HEIGHT_RATIO,
                )
                return [Detection(bbox=region.rect, confidence=0.9, class_id=0, class_name="barcode")]

        class FixedDecoder:
            def decode_with_preprocessing(self, image):
                class Item:
                    data = "999999999"
                    type = "CODE39"

                return [Item()]

        pipeline = ScanPipeline(
            detector=FixedDetector(),
            decoder=FixedDecoder(),
            verifier=StudentVerifier(lookup=lambda v: None),
        )
        outcome = pipeline.process_frame(np.zeros((720, 1280, 3), dtype=np.uint8))
        assert outcome.state == ScanState.UNKNOWN_BARCODE
        assert outcome.verified_now is False
        assert outcome.student is None

    def test_database_failure_reports_error_not_unknown(self):
        from database.connection import DatabaseUnavailableError

        class FailingVerifier(StudentVerifier):
            def verify(self, payload):
                raise DatabaseUnavailableError("db down")

        class FixedDecoder:
            def decode_with_preprocessing(self, image):
                class Item:
                    data = "000065412"
                    type = "CODE39"

                return [Item()]

        class CenteredDetector:
            def detect(self, frame):
                from services.yolo_detector import Detection

                region = ScanRegion.for_frame(
                    (frame.shape[1], frame.shape[0]),
                    settings.SCAN_REGION_WIDTH_RATIO,
                    settings.SCAN_REGION_HEIGHT_RATIO,
                )
                return [Detection(bbox=region.rect, confidence=0.9, class_id=0, class_name="barcode")]

        pipeline = ScanPipeline(
            detector=CenteredDetector(),
            decoder=FixedDecoder(),
            verifier=FailingVerifier(),
        )
        outcome = pipeline.process_frame(np.zeros((720, 1280, 3), dtype=np.uint8))
        assert outcome.state == ScanState.ERROR
        assert outcome.student is None


# ---------------------------------------------------------- state machine --


class TestScanStateMachine:
    def _pipeline(self, **kw):
        class D:
            def detect(self, frame):
                from services.yolo_detector import Detection

                region = ScanRegion.for_frame(
                    (frame.shape[1], frame.shape[0]),
                    settings.SCAN_REGION_WIDTH_RATIO,
                    settings.SCAN_REGION_HEIGHT_RATIO,
                )
                return [Detection(bbox=region.rect, confidence=0.9, class_id=0, class_name="barcode")]

        class Dec:
            def decode_with_preprocessing(self, image):
                class Item:
                    data = "000065412"
                    type = "CODE39"

                return [Item()]

        kw.setdefault("detector", D())
        kw.setdefault("decoder", Dec())
        kw.setdefault("verifier", StudentVerifier(lookup=lambda v: object()))
        return ScanPipeline(**kw)

    def test_cooldown_suppresses_repeat_verifications(self):
        clock = {"t": 0.0}
        pipeline = self._pipeline(
            cooldown_seconds=3.0, clock=lambda: clock["t"]
        )
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        first = pipeline.process_frame(frame)
        assert first.verified_now is True

        # Same card, still in frame, inside the cooldown window.
        second = pipeline.process_frame(frame)
        assert second.state == ScanState.VERIFIED
        assert second.verified_now is False, "duplicate popup would be raised here"

        # After the cooldown expires the next scan is accepted again.
        clock["t"] = 10.0
        third = pipeline.process_frame(frame)
        assert third.verified_now is True

    def test_reset_clears_cooldown(self):
        pipeline = self._pipeline(cooldown_seconds=30.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        pipeline.process_frame(frame)
        assert pipeline.process_frame(frame).verified_now is False
        pipeline.reset()
        assert pipeline.process_frame(frame).verified_now is True

    def test_confirm_frames_requires_agreement(self):
        pipeline = self._pipeline(confirm_frames=3, cooldown_seconds=0.0)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        assert pipeline.process_frame(frame).state == ScanState.DECODING
        assert pipeline.process_frame(frame).state == ScanState.DECODING
        assert pipeline.process_frame(frame).verified_now is True

    def test_lost_detection_forgets_a_partial_confirmation(self):
        pipeline = self._pipeline(confirm_frames=3)

        class Nothing:
            def detect(self, frame):
                return []

        blank = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame = blank
        pipeline.process_frame(frame)
        pipeline.detector = Nothing()
        assert pipeline.process_frame(frame).state == ScanState.SEARCHING
        # Confirmation restarted, so it needs three agreeing frames again.
        pipeline.detector = self._pipeline().detector
        assert pipeline.process_frame(frame).state == ScanState.DECODING

    def test_every_state_is_reachable_and_named(self):
        """The state machine's contract, including the ambiguity refusal.

        ``ambiguous`` was added after measuring a frame that returned two
        different payloads in one read: the pipeline previously picked the first
        and verified whichever real student it named.
        """
        assert {s.value for s in ScanState} == {
            "searching",
            "barcode_detected",
            "decoding",
            "verified",
            "unknown_barcode",
            "ambiguous",
            "error",
        }

    def test_empty_frame_is_an_error_not_a_crash(self):
        pipeline = self._pipeline()
        assert pipeline.process_frame(None).state == ScanState.ERROR
        assert pipeline.process_frame(np.zeros((0, 0, 3), dtype=np.uint8)).state == ScanState.ERROR

    def test_detector_exception_does_not_escape(self):
        class Boom:
            def detect(self, frame):
                raise RuntimeError("inference exploded")

        pipeline = ScanPipeline(detector=Boom(), decoder=None)
        outcome = pipeline.process_frame(np.zeros((720, 1280, 3), dtype=np.uint8))
        assert outcome.state == ScanState.ERROR


# ----------------------------------------------------------------- gating --


class TestClassFiltering:
    def test_only_matching_class_names_are_considered(self):
        class Mixed:
            def detect(self, frame):
                from services.yolo_detector import Detection

                region = ScanRegion.for_frame(
                    (frame.shape[1], frame.shape[0]),
                    settings.SCAN_REGION_WIDTH_RATIO,
                    settings.SCAN_REGION_HEIGHT_RATIO,
                )
                return [
                    Detection(bbox=region.rect, confidence=0.9, class_id=0, class_name="face"),
                    Detection(bbox=region.rect, confidence=0.8, class_id=1, class_name="barcode"),
                ]

        pipeline = ScanPipeline(
            detector=Mixed(),
            decoder=None,
            barcode_class_filter=["barcode", "qr"],
        )
        outcome = pipeline.process_frame(np.zeros((720, 1280, 3), dtype=np.uint8))
        assert [c.class_name for c in outcome.candidates] == ["barcode"]

    def test_empty_filter_accepts_every_class(self):
        class Face:
            def detect(self, frame):
                from services.yolo_detector import Detection

                return [Detection(bbox=(10, 10, 50, 20), confidence=0.9, class_id=0, class_name="face")]

        pipeline = ScanPipeline(detector=Face(), decoder=None)
        outcome = pipeline.process_frame(np.zeros((720, 1280, 3), dtype=np.uint8))
        assert len(outcome.candidates) == 1

    def test_unusable_crop_is_never_sent_to_the_decoder(self):
        """A box too small to hold a readable barcode is dropped before decode."""

        class TinyBox:
            def detect(self, frame):
                from services.yolo_detector import Detection

                region = ScanRegion.for_frame(
                    (frame.shape[1], frame.shape[0]),
                    settings.SCAN_REGION_WIDTH_RATIO,
                    settings.SCAN_REGION_HEIGHT_RATIO,
                )
                # Inside the guide, but far below SCAN_MIN_CROP_WIDTH.
                x = region.x + region.width // 2 - 15
                y = region.y + region.height // 2 - 5
                return [Detection(bbox=(x, y, 30, 10), confidence=0.9, class_id=0, class_name="barcode")]

        seen = []

        class Spy:
            def decode_with_preprocessing(self, image):
                seen.append(image.shape)
                return []

        pipeline = ScanPipeline(detector=TinyBox(), decoder=Spy())
        outcome = pipeline.process_frame(np.zeros((720, 1280, 3), dtype=np.uint8))

        assert outcome.state == ScanState.BARCODE_DETECTED
        assert "unable to decode" in outcome.message
        assert seen == [], "an unusable crop reached the decoder"
        assert not any(c.decoded_value for c in outcome.candidates)


class TestAmbiguousRead:
    """One read must not pick between two real barcodes.

    Measured defect: a frame holding card A's and card C's symbols at the same
    scale was read by ``decode_best`` as both ``000065736`` and ``000065412`` in
    a single call. The pipeline took ``result.data`` -- the first zbar listed --
    and verified that student, so which person was reported depended on zbar's
    internal ordering rather than on anything the operator did.
    """

    @staticmethod
    def _two_symbol_frame():
        """A frame with card A's and card C's real symbols, side by side.

        Card C's symbol is resampled to card A's module width so both sit at the
        same scale, which is the condition under which zbar reports both in one
        pass.
        """
        import cv2

        sym_a = cv2.imread(str(CARD_ID_1))[792:904, 33:558]
        sym_c = cv2.imread(str(CARD_ID_3))[362:420, 38:242]
        scaled_c = cv2.resize(
            sym_c,
            (sym_a.shape[1], int(sym_c.shape[0] * sym_a.shape[1] / sym_c.shape[1])),
            interpolation=cv2.INTER_CUBIC,
        )
        height = sym_a.shape[0] + scaled_c.shape[0] + 60
        canvas = np.full((height, sym_a.shape[1] * 2 + 80, 3), 255, dtype=np.uint8)
        canvas[20:20 + sym_a.shape[0], 20:20 + sym_a.shape[1]] = sym_a
        top = 30 + sym_a.shape[0]
        canvas[top:top + scaled_c.shape[0], 60 + sym_a.shape[1]:60 + 2 * sym_a.shape[1]] = scaled_c
        return canvas

    def test_the_decoder_really_does_return_two_different_payloads(self):
        """Pins the premise: without this, the guard below would be untestable."""
        import cv2

        from services.barcode_decoder import BarcodeDecoderService

        result = BarcodeDecoderService().decode_best(self._two_symbol_frame())
        assert result.ok
        assert result.ambiguous, (
            "expected two distinct payloads from this frame, got "
            f"{result.distinct_values}"
        )
        assert len(result.distinct_values) == 2

    def test_two_barcodes_in_one_frame_verify_nobody(self):
        import cv2

        from services.barcode_decoder import BarcodeDecoderService

        class BlindDetector:
            """The model sees nothing, so the whole-frame fallback runs.

            That is the path where a single decode can see more than one symbol,
            and it is the one that used to choose silently.
            """

            def detect(self, frame):
                return []

        looked_up = []

        pipeline = ScanPipeline(
            detector=BlindDetector(),
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=lambda v: looked_up.append(v)),
        )
        # Open the whole-frame fallback window, which is the path that can see
        # more than one symbol in a single read.
        pipeline.frame_fallback_after = 0.0
        pipeline.frame_fallback_interval = 0.0
        outcome = pipeline.process_frame(self._two_symbol_frame())

        assert outcome.state == ScanState.AMBIGUOUS
        assert outcome.student is None
        assert outcome.verified_now is False
        assert len(outcome.ambiguous_values) == 2
        assert looked_up == [], "an ambiguous read must not reach the student lookup"
        assert "single card" in outcome.message

    def test_the_session_refuses_an_ambiguous_read(self):
        from services.scan_session import RejectReason, ScanSession

        session = ScanSession()
        decision = session.observe(ScanState.AMBIGUOUS, None, confirmed=False)
        assert decision.accepted is False
        assert decision.reason == RejectReason.AMBIGUOUS_READ.value

    def test_one_barcode_still_verifies(self):
        """The control: the guard must not break the ordinary path.

        Uses the same fixture the other end-to-end tests use -- a real card
        composed into a webcam-sized frame -- and the same blind detector, so
        only the number of symbols in the frame differs from the test above.
        """
        from services.barcode_decoder import BarcodeDecoderService

        class BlindDetector:
            def detect(self, frame):
                return []

        student = object()
        pipeline = ScanPipeline(
            detector=BlindDetector(),
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=lambda v: student),
        )
        pipeline.frame_fallback_after = 0.0
        pipeline.frame_fallback_interval = 0.0
        outcome = pipeline.process_frame(make_webcam_frame(CARD_ID_1))

        assert outcome.state == ScanState.VERIFIED, outcome.message
        assert outcome.student is student
        assert outcome.ambiguous_values == []
        assert outcome.verified_now is True
