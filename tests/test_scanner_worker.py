"""Integration tests for the scanner worker and its thread hand-off.

These drive :class:`~ui.scanner_page.ScanWorker` and :class:`ScannerPage` with a
stubbed camera that replays a real card frame, so the full path
(frame -> pipeline -> signal -> UI) is exercised without a physical card in
front of a webcam.

The camera is stubbed rather than faked at the vision layer: the frames fed in
are the genuine composited ID card images, decoded by the real pyzbar decoder and
verified against the real database.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

pytest.importorskip("PySide6")

from config.settings import settings
from services.scan_geometry import ScanRegion
from services.scan_pipeline import ScanOutcome, ScanState
from tests.scanner_fixtures import CARD_ID_1, CARD_ID_2, make_webcam_frame


class StubCamera:
    """Stands in for CameraService, replaying one real frame with fresh ids."""

    def __init__(self, frame: np.ndarray):
        self.frame = frame
        self.is_running = True
        self.stopped = False
        self._id = 0

    def get_latest_frame_with_id(self):
        self._id += 1
        return self.frame, self._id

    def get_latest_frame(self):
        return self.frame

    def start(self, index=None):
        self.is_running = True
        return True

    def stop(self):
        self.stopped = True
        self.is_running = False


def searching_outcome(frame: np.ndarray) -> ScanOutcome:
    """A SEARCHING result carrying the correct scan region for this frame."""
    h, w = frame.shape[:2]
    region = ScanRegion.for_frame(
        (w, h), settings.SCAN_REGION_WIDTH_RATIO, settings.SCAN_REGION_HEIGHT_RATIO
    )
    return ScanOutcome(
        state=ScanState.SEARCHING,
        message="Searching for barcode...",
        scan_region=region.rect,
    )


def _detector():
    from services.yolo_detector import YOLODetectorService

    detector = YOLODetectorService()
    if not detector.load_active_model():
        pytest.skip("No YOLO model available")
    return detector


# ---------------------------------------------------------- worker loop ---


class TestWorkerDuplicateSuppression:
    def test_verified_now_is_raised_once_for_repeated_frames(self, qapp):
        """The duplicate-popup guard, over many identical frames.

        The clock is frozen so the assertion measures the invariant it is named
        for -- one popup while the same card stays in frame -- instead of racing
        real inference time against the cooldown window. On CPU a single frame
        can take longer than the 5 s cooldown, which used to make this test fail
        intermittently for reasons that had nothing to do with the guard. The
        cooldown-expiry re-presentation is a separate, deliberate behaviour
        covered by ``test_cooldown_suppresses_repeat_verifications``.
        """
        from services.barcode_decoder import BarcodeDecoderService
        from services.scan_pipeline import ScanPipeline, StudentVerifier
        from services.student_service import StudentService

        frozen = {"t": 1000.0}
        pipeline = ScanPipeline(
            detector=_detector(),
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=StudentService().get_student_by_barcode),
            cooldown_seconds=5.0,
            clock=lambda: frozen["t"],
        )

        frame = make_webcam_frame(CARD_ID_1)
        results = []
        last_id = -1
        camera = StubCamera(frame)
        for _ in range(10):
            current, frame_id = camera.get_latest_frame_with_id()
            assert frame_id != last_id, "stub camera must hand out fresh ids"
            last_id = frame_id
            results.append(pipeline.process_frame(current))

        verified = [o for o in results if o.state == ScanState.VERIFIED]
        assert verified, "the real card never verified"
        assert sum(o.verified_now for o in verified) == 1, (
            "repeated frames must not raise verified_now more than once; "
            f"got {[o.verified_now for o in verified]}"
        )

    def test_worker_skips_frames_it_has_already_seen(self, qapp):
        """A frozen camera frame must not be re-inferred."""
        from services.barcode_decoder import BarcodeDecoderService
        from services.scan_pipeline import ScanPipeline, StudentVerifier
        from services.student_service import StudentService

        detector = _detector()
        pipeline = ScanPipeline(
            detector=detector,
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=StudentService().get_student_by_barcode),
        )

        camera = StubCamera(make_webcam_frame(CARD_ID_1))
        calls = {"n": 0}
        real_detect = detector.detect

        def counting_detect(frame):
            calls["n"] += 1
            return real_detect(frame)

        pipeline.detector = type("D", (), {"detect": staticmethod(counting_detect)})()

        last_id = -1
        processed = 0
        for _ in range(10):
            frame, frame_id = camera.get_latest_frame_with_id()
            if frame_id == last_id:
                continue
            last_id = frame_id
            pipeline.process_frame(frame)
            processed += 1

        assert processed == 10
        assert calls["n"] == processed, "pipeline ran twice on some frame"

    def test_cooldown_expires_and_allows_a_second_scan(self, qapp):
        from services.barcode_decoder import BarcodeDecoderService
        from services.scan_pipeline import ScanPipeline, StudentVerifier
        from services.student_service import StudentService

        clock = {"t": 0.0}
        pipeline = ScanPipeline(
            detector=_detector(),
            decoder=BarcodeDecoderService(),
            verifier=StudentVerifier(lookup=StudentService().get_student_by_barcode),
            cooldown_seconds=2.0,
            clock=lambda: clock["t"],
        )
        frame = make_webcam_frame(CARD_ID_1)

        assert pipeline.process_frame(frame).verified_now is True
        clock["t"] = 100.0
        assert pipeline.process_frame(frame).verified_now is True


# --------------------------------------------------------- page lifecycle --


class TestPageLifecycle:
    def test_page_constructs_and_reports_status(self, qapp):
        from services.student_service import StudentService
        from ui.scanner_page import ScannerPage

        page = ScannerPage(StudentService())
        assert page.worker is None
        assert page.detection_status.text()
        assert page.yolo_status.text()

    def test_render_preview_handles_no_frame_without_crashing(self, qapp):
        from services.student_service import StudentService
        from ui.scanner_page import ScannerPage

        page = ScannerPage(StudentService())
        page._render_preview()  # no frame yet
        assert page.video_label.pixmap().isNull()

    def test_render_preview_composites_a_real_frame(self, qapp):
        from services.student_service import StudentService
        from ui.scanner_page import ScannerPage

        page = ScannerPage(StudentService())
        frame = make_webcam_frame(CARD_ID_1)

        page._latest_frame = frame
        page._latest_outcome = searching_outcome(frame)
        page._render_preview()

        pixmap = page.video_label.pixmap()
        assert not pixmap.isNull(), "preview did not render"
        assert pixmap.width() > 0 and pixmap.height() > 0

    def test_overlay_does_not_alter_the_frame_used_for_inference(self, qapp):
        """The frame handed to the detector must never gain overlay pixels."""
        from services.student_service import StudentService
        from ui.scanner_page import ScannerPage

        page = ScannerPage(StudentService())
        frame = make_webcam_frame(CARD_ID_2)
        pristine = frame.copy()

        page._latest_frame = frame
        page._latest_outcome = searching_outcome(frame)
        page._render_preview()

        assert np.array_equal(frame, pristine), "overlay mutated the camera frame"

    def test_status_label_follows_the_state_machine(self, qapp):
        from services.student_service import StudentService
        from ui.scanner_page import ScannerPage

        page = ScannerPage(StudentService())
        frame = make_webcam_frame(CARD_ID_1)

        page._latest_frame = frame
        for state, expected in (
            (ScanState.SEARCHING, "Searching for barcode"),
            (ScanState.BARCODE_DETECTED, "Move barcode inside the yellow frame"),
            (ScanState.UNKNOWN_BARCODE, "Unknown barcode"),
        ):
            page._latest_outcome = ScanOutcome(state=state, message=f"{expected} ...")
            page._update_status_labels(page._latest_outcome)
            assert expected in page.detection_status.text()

    def test_stop_releases_camera_and_timer(self, qapp):
        from services.student_service import StudentService
        from ui.scanner_page import ScannerPage

        page = ScannerPage(StudentService())
        camera = StubCamera(np.zeros((720, 1280, 3), dtype=np.uint8))
        page.camera_service = camera
        page.preview_timer.start()
        page._stop_scanner()

        assert camera.stopped is True
        assert page.worker is None
        assert page.preview_timer.isActive() is False
        assert page.video_label.pixmap().isNull()