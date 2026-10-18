"""Regression tests for full-card barcode localisation and live tracking.

Covers the four supplied source images explicitly. Two of the assertions here
exist because they are the failure modes this project has actually hit:

* the detection box must land on the **barcode**, not on the face, the name or
  the whole card. A box that technically contains the barcode but also 90% of
  the card would pass a naive IoU check against nothing, so the size and
  position are asserted too;
* the camera must start and stop cleanly without leaking a thread or a device
  handle, including on an index that does not exist.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from config.settings import settings
from services.scan_geometry import DisplayTransform

BASE_DIR = Path(__file__).resolve().parent.parent

CARD_1 = settings.ASSETS_DIR / "images" / "Id_1.png"
CARD_2 = settings.ASSETS_DIR / "images" / "Id_2.png"
BARCODE_1 = settings.ASSETS_DIR / "barcodes" / "barcode_1.png"
BARCODE_2 = settings.ASSETS_DIR / "barcodes" / "barcode_2.png"
PANEL_1 = settings.info_image("student_1_info.png")
PANEL_2 = settings.info_image("student_2_info.png")

ALL_FOUR = [CARD_1, CARD_2, BARCODE_1, BARCODE_2]

EXPECTED_VALUES = {
    CARD_1: "000065412",
    CARD_2: "000065902",
    BARCODE_1: "000065902",
    BARCODE_2: "000065412",
}

#: Digests recorded at the start of the project; these four must never change.
SOURCE_DIGESTS = {
    "assets/images/Id_1.png": "15de778f2c61c9924b0ed0432d829853",
    "assets/images/Id_2.png": "38e37019c04f9741e2e064e8262cc1e8",
    "assets/barcodes/barcode_1.png": "9adeb2f3248ca80744232465139e5bd9",
    "assets/barcodes/barcode_2.png": "54f238f873fa4c697c4f2206b08717da",
}


def _detector():
    from services.yolo_detector import YOLODetectorService

    detector = YOLODetectorService()
    if not detector.load_model(settings.YOLO_MODEL_PATH):
        pytest.skip("no YOLO model available")
    return detector


def _decoded_extent(image: np.ndarray):
    """The symbol zbar actually reads, as (x1, y1, x2, y2)."""
    from services.barcode_decoder import BarcodeDecoderService

    for item in BarcodeDecoderService().decode_with_preprocessing(image):
        r = item.bbox
        return (r.left, r.top, r.left + r.width, r.top + r.height)
    return None


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


# --------------------------------------------------------- source files --


class TestSourceImagesUnchanged:
    @pytest.mark.parametrize("rel,expected", sorted(SOURCE_DIGESTS.items()))
    def test_digest_matches(self, rel, expected):
        path = BASE_DIR / rel
        assert path.exists(), f"source image missing: {rel}"
        assert hashlib.md5(path.read_bytes()).hexdigest() == expected, (
            f"{rel} was modified"
        )

    @pytest.mark.parametrize("path", ALL_FOUR, ids=lambda p: p.name)
    def test_readable_and_non_trivial(self, path):
        image = cv2.imread(str(path))
        assert image is not None
        assert image.shape[0] > 100 and image.shape[1] > 100


# ------------------------------------------------------ direct decoding ---


class TestAllFourDecodeDirectly:
    """Detection is not required to read these; they are decoded directly."""

    @pytest.mark.parametrize("path", ALL_FOUR, ids=lambda p: p.name)
    def test_decodes_to_expected_value(self, path):
        from services.barcode_decoder import BarcodeDecoderService

        image = cv2.imread(str(path))
        result = BarcodeDecoderService().decode_best(image)
        assert result.ok, f"{path.name} did not decode"
        assert result.data == EXPECTED_VALUES[path], (
            f"{path.name} decoded {result.data}, expected {EXPECTED_VALUES[path]}"
        )

    @pytest.mark.parametrize("path", ALL_FOUR, ids=lambda p: p.name)
    def test_symbology_is_code39(self, path):
        from services.barcode_decoder import BarcodeDecoderService

        result = BarcodeDecoderService().decode_best(cv2.imread(str(path)))
        assert result.type == "CODE39", (
            f"{path.name} is {result.type}; the cards are CODE39 and the generator "
            f"emits CODE128, which is a separate open issue"
        )


# --------------------------------------------------- YOLO localisation ---


class TestDetectionLocatesTheBarcodeOnAllFour:
    @pytest.fixture(scope="class")
    def detector(self):
        return _detector()

    @pytest.mark.parametrize("path", ALL_FOUR, ids=lambda p: p.name)
    def test_detected_and_box_lands_on_the_symbol(self, detector, path):
        image = cv2.imread(str(path))
        detections = detector.detect(image)
        assert detections, f"{path.name}: no barcode detected"

        extent = _decoded_extent(image)
        assert extent is not None
        best = max(detections, key=lambda d: d.confidence)
        x, y, w, h = best.bbox
        box = (x, y, x + w, y + h)
        assert _iou(box, extent) >= 0.85, (
            f"{path.name}: detection box {box} vs real symbol {extent} "
            f"(IoU {_iou(box, extent):.2f})"
        )

    @pytest.mark.parametrize("path", [CARD_1, CARD_2], ids=lambda p: p.name)
    def test_box_is_the_barcode_not_the_whole_card(self, detector, path):
        """The box must not become a card detector."""
        image = cv2.imread(str(path))
        h, w = image.shape[:2]
        detections = detector.detect(image)
        assert detections
        x, y, bw, bh = max(detections, key=lambda d: d.confidence).bbox
        area_fraction = (bw * bh) / float(w * h)
        assert area_fraction < 0.30, (
            f"{path.name}: box covers {area_fraction:.0%} of the card; it is boxing "
            f"the card or the portrait, not the barcode"
        )
        assert y > 0.55 * h, (
            f"{path.name}: box starts at y={y} of {h}; the barcode is at the foot of "
            f"the card, so this box is over the photo or the name"
        )

    @pytest.mark.parametrize("path", [BARCODE_1, BARCODE_2], ids=lambda p: p.name)
    def test_box_on_a_tight_crop_leaves_little_waste(self, detector, path):
        image = cv2.imread(str(path))
        h, w = image.shape[:2]
        x, y, bw, bh = max(detector.detect(image), key=lambda d: d.confidence).bbox
        assert (bw * bh) / float(w * h) > 0.20, (
            f"{path.name}: box is only {(bw * bh) / float(w * h):.0%} of a crop that is "
            f"almost entirely barcode -- it is boxing a fragment"
        )

    @pytest.mark.parametrize("path", [PANEL_1, PANEL_2], ids=lambda p: p.name)
    def test_no_detection_on_a_document_without_a_barcode(self, detector, path):
        image = cv2.imread(str(path))
        assert _decoded_extent(image) is None, "fixture panel should contain no barcode"
        detections = detector.detect(image)
        assert not detections, (
            f"{path.name} contains no barcode but produced "
            f"{[(round(d.confidence, 3), d.bbox) for d in detections]}"
        )

    @pytest.mark.parametrize("path", ALL_FOUR, ids=lambda p: p.name)
    def test_detection_crop_decodes_to_the_same_value(self, detector, path):
        """YOLO box -> crop -> decode must agree with the direct read."""
        from services.barcode_decoder import BarcodeDecoderService

        image = cv2.imread(str(path))
        margin = settings.SCAN_CROP_MARGIN_RATIO
        values = []
        for det in detector.detect(image):
            x, y, w, h = det.bbox
            x0, y0 = max(0, int(x - w * margin)), max(0, int(y - h * margin))
            x1 = min(image.shape[1], int(x + w * (1 + margin)))
            y1 = min(image.shape[0], int(y + h * (1 + margin)))
            values += [d.data for d in BarcodeDecoderService().decode_with_preprocessing(
                image[y0:y1, x0:x1])]
        assert EXPECTED_VALUES[path] in values, (
            f"{path.name}: crop decoded {set(values)}, expected {EXPECTED_VALUES[path]}"
        )


# ------------------------------------------------------------- tracking ---


class TestBoxTracking:
    def _frame_with_card(self, card_path, offset=(0, 0), zoom=1.0):
        from tests.scanner_fixtures import make_webcam_frame

        return make_webcam_frame(card_path, offset=offset, zoom=zoom)

    def test_box_tracks_a_moving_card_without_jumping_away(self):
        from services.detection_smoother import DetectionSmoother
        from services.scan_pipeline import BarcodeCandidate

        smoother = DetectionSmoother(alpha=0.5, grace_seconds=0.5)
        drawn = []
        for i in range(10):
            x = 200 + i * 25
            out = smoother.update(
                [BarcodeCandidate(bbox=(x, 300, 520, 130), confidence=0.9,
                                  class_name="barcode", in_scan_region=True)],
                now=i * 0.05)
            drawn.append(out[0].bbox[0])
        assert drawn == sorted(drawn), "box must move monotonically with the card"
        jumps = [abs(b - a) for a, b in zip(drawn, drawn[1:])]
        assert max(jumps) < 40, f"box jumped by {max(jumps)} px between frames"

    def test_unrelated_region_does_not_inherit_the_previous_box(self):
        from services.detection_smoother import DetectionSmoother
        from services.scan_pipeline import BarcodeCandidate

        smoother = DetectionSmoother(alpha=0.5, grace_seconds=0.0)
        smoother.update([BarcodeCandidate(bbox=(100, 300, 520, 130), confidence=0.9,
                                          class_name="barcode")], now=0.0)
        out = smoother.update([BarcodeCandidate(bbox=(1500, 900, 520, 130), confidence=0.9,
                                                class_name="barcode")], now=0.05)
        assert out[0].bbox[0] == 1500, (
            "a distant detection must start at its own position, not slide from the "
            "previous box"
        )

    def test_repeated_frames_produce_one_verification(self):
        """The same card held in frame must not raise the popup flag repeatedly."""
        from services.detection_smoother import DetectionSmoother  # noqa: F401
        from services.scan_pipeline import ScanPipeline, ScanState, StudentVerifier
        from services.barcode_decoder import BarcodeDecoderService
        from services.student_service import StudentService

        detector = _detector()
        frame = self._frame_with_card(CARD_1)
        clock = {"t": 100.0}
        pipeline = ScanPipeline(
            detector=detector,
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=StudentService().get_student_by_barcode),
            cooldown_seconds=5.0,
            clock=lambda: clock["t"],
        )
        outcomes = []
        for _ in range(8):
            clock["t"] += 0.1
            outcomes.append(pipeline.process_frame(frame))
        verified = [o for o in outcomes if o.state == ScanState.VERIFIED]
        assert verified, "the real card never verified"
        assert sum(1 for o in verified if o.verified_now) == 1, (
            f"popup raised {[o.verified_now for o in verified]} times for one card"
        )

    def test_removing_the_card_drops_the_box_after_the_grace_period(self):
        from services.detection_smoother import DetectionSmoother
        from services.scan_pipeline import BarcodeCandidate

        smoother = DetectionSmoother(alpha=0.5, grace_seconds=0.4)
        smoother.update([BarcodeCandidate(bbox=(200, 300, 520, 130), confidence=0.9,
                                          class_name="barcode")], now=0.0)
        assert smoother.update([], now=0.2), "box should survive a dropped frame"
        assert smoother.update([], now=1.0) == [], "box should not linger"


# --------------------------------------------------------------- camera ---


class TestCameraLifecycle:
    def test_invalid_index_fails_cleanly_without_raising(self):
        from services.camera_service import CameraService

        camera = CameraService()
        started = camera.start(999)
        assert started is False
        assert camera.is_running is False
        camera.stop()  # must be safe when nothing started

    def test_stop_is_idempotent_and_releases_the_thread(self):
        from services.camera_service import CameraService

        camera = CameraService()
        camera.start(0)
        before = threading.active_count()
        camera.stop()
        camera.stop()
        time.sleep(0.3)
        assert camera.is_running is False
        assert camera.get_latest_frame() is None
        # The capture thread must actually end rather than leak.
        leaked = [t for t in threading.enumerate()
                  if t is not threading.current_thread() and t.is_alive()
                  and "capture" in (t.name or "").lower()]
        assert not leaked, f"capture threads still alive: {leaked}"

    def test_start_after_stop_works_again(self):
        """Restarting the scanner is a normal user action."""
        from services.camera_service import CameraService

        camera = CameraService()
        assert camera.start(0) is True
        camera.stop()
        assert camera.start(0) is True, "restarting the camera failed"
        camera.stop()

    @pytest.mark.slow
    def test_real_camera_delivers_frames(self):
        """Requires hardware; skipped when the camera is unavailable."""
        from services.camera_service import CameraService

        camera = CameraService()
        try:
            if not camera.start(0):
                pytest.skip("no camera available in this environment")
            # The UVC device needs a moment to negotiate before frames arrive.
            time.sleep(1.5)
            frame, frame_id = camera.get_latest_frame_with_id()
            assert frame is not None and frame.size > 0, "camera opened but sent no frame"
            assert frame.ndim == 3 and frame.shape[2] == 3
            first = frame_id
            time.sleep(0.5)
            _, second = camera.get_latest_frame_with_id()
            assert second > first, "frame id never advanced; the capture thread is stuck"
        finally:
            camera.stop()


# ------------------------------------------------------------- identity ---


class TestIdentitySafety:
    def test_a_but_empty_lookup_shows_nobody(self):
        from services.scan_pipeline import ScanPipeline, ScanState, StudentVerifier
        from services.barcode_decoder import BarcodeDecoderService
        from tests.scanner_fixtures import make_webcam_frame

        detector = _detector()
        pipeline = ScanPipeline(
            detector=detector,
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=lambda value: None),
            cooldown_seconds=0.0,
        )
        for card in (CARD_1, CARD_2):
            outcome = pipeline.process_frame(make_webcam_frame(card))
            assert outcome.decoded_value, f"{card.name} should still decode"
            assert outcome.state == ScanState.UNKNOWN_BARCODE
            assert outcome.student is None
            assert outcome.verified_now is False

    def test_every_supplied_card_maps_to_its_own_student(self):
        """The two cards must never be cross-matched."""
        from services.scan_pipeline import ScanPipeline, ScanState, StudentVerifier
        from services.barcode_decoder import BarcodeDecoderService
        from services.student_service import StudentService
        from tests.scanner_fixtures import make_webcam_frame

        detector = _detector()
        try:
            service = StudentService()
            service.get_student_by_barcode("")
        except Exception as exc:
            pytest.skip(f"database unavailable: {type(exc).__name__}")

        expected = {CARD_1: "Caporal", CARD_2: "Alemania"}
        for card, family in expected.items():
            pipeline = ScanPipeline(
                detector=detector,
                decoder=BarcodeDecoderService(),
                verifier=StudentVerifier(lookup=service.get_student_by_barcode),
                cooldown_seconds=0.0,
            )
            outcome = pipeline.process_frame(make_webcam_frame(card))
            assert outcome.state == ScanState.VERIFIED, (
                f"{card.name}: {outcome.state.value}"
            )
            assert outcome.student.last_name == family, (
                f"{card.name} showed {outcome.student.last_name!r}, expected {family!r}"
            )


class TestDetectionBoxAlignmentAfterResizing:
    """The drawn rectangle must land on the barcode at every preview size.

    The preview widget is not the camera's native size, so every box is mapped
    through a letterbox transform before it is drawn. Getting that transform
    wrong is invisible in a unit test that only checks that *something* is
    drawn: the box simply sits somewhere else, which looks like a detector
    problem and gets "fixed" by retraining.

    These tests compare the transform against an independently computed
    letterbox mapping for several preview sizes, in both mirror modes.
    """

    FRAME = (1280, 720)

    # A box deliberately off-centre in both axes, so an axis swap or a wrong
    # offset cannot pass.
    BOX = (410, 505, 380, 96)

    @staticmethod
    def _reference(rect, frame, display):
        """Uniform scale + centring offset, computed independently."""
        s = min(display[0] / frame[0], display[1] / frame[1])
        ox = (display[0] - frame[0] * s) / 2.0
        oy = (display[1] - frame[1] * s) / 2.0
        x, y, w, h = rect
        pts = [(x * s + ox, y * s + oy), ((x + w) * s + ox, (y + h) * s + oy)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    @pytest.mark.parametrize("display", [
        (640, 360), (800, 600), (1024, 768), (1280, 720), (1920, 1080), (561, 315),
    ])
    def test_box_maps_to_the_expected_letterbox_rect(self, display):
        t = DisplayTransform(frame_size=self.FRAME, display_size=display, mirrored=False)
        got = t.to_display_rect(self.BOX)
        want = self._reference(self.BOX, self.FRAME, display)
        for g, w in zip(got, want):
            assert abs(g - w) <= 2, f"display {display}: got {got} want {want}"

    def test_mirrored_box_is_reflected_about_the_display_centre(self):
        plain = DisplayTransform(self.FRAME, (1280, 720), mirrored=False)
        flipped = DisplayTransform(self.FRAME, (1280, 720), mirrored=True)
        a = plain.to_display_rect(self.BOX)
        b = flipped.to_display_rect(self.BOX)
        # The mirrored left edge is the canvas width minus the plain right edge.
        assert abs(b[0] - (1280 - (a[0] + a[2]))) <= 3, (a, b)
        assert abs(a[1] - b[1]) <= 1, "mirroring must not move the box vertically"
        assert abs(a[2] - b[2]) <= 1, "mirroring must not resize the box"

    def test_the_box_keeps_its_aspect_ratio(self):
        """A stretched box is the classic symptom of anisotropic scaling."""
        src_ratio = self.BOX[2] / self.BOX[3]
        for display in [(640, 360), (900, 900), (1600, 400)]:
            t = DisplayTransform(self.FRAME, display)
            r = t.to_display_rect(self.BOX)
            # One pixel of rounding tolerance on the smaller axis.
            assert abs((r[2] / r[3]) - src_ratio) / src_ratio < 0.03, (display, r)

    def test_the_box_stays_inside_the_display_canvas(self):
        """A box mapped outside the canvas is clipped away by Qt and the
        operator simply sees no rectangle."""
        for display in [(640, 360), (800, 600), (1920, 1080)]:
            t = DisplayTransform(self.FRAME, display)
            for mirrored in (False, True):
                r = DisplayTransform(self.FRAME, display, mirrored).to_display_rect(self.BOX)
                assert r[0] >= -2 and r[1] >= -2, (display, mirrored, r)
                assert r[0] + r[2] <= display[0] + 2, (display, mirrored, r)
                assert r[1] + r[3] <= display[1] + 2, (display, mirrored, r)

    @pytest.mark.parametrize("display,mirrored", [
        ((900, 900), False), ((900, 900), True), ((640, 360), False), ((1920, 1080), True),
    ])
    def test_a_rect_round_trips_through_the_transform(self, display, mirrored):
        t = DisplayTransform(self.FRAME, display, mirrored=mirrored)
        back = t.to_frame_rect(t.to_display_rect(self.BOX))
        for original, round_tripped in zip(self.BOX, back):
            assert abs(original - round_tripped) <= 4, (display, mirrored, back)
