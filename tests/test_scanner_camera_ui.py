"""The scanner's camera viewport and its lifecycle states.

Two defects are pinned here, both of which only appear on a screen at a moment
nobody screenshotted while writing the code:

* **A stopped scanner claimed to be scanning.** The header read "Stopped", the
  viewport read "Camera is off", and the footer read "Waiting for camera..." --
  and then "Searching for barcode..." once anything had ever run. Nothing tied
  the three together, so all three were free to disagree, and did.
* **The placeholder and the live feed shared one widget.** ``setPixmap`` does not
  clear a QLabel's text, so the first rendered frame appeared *beside* the
  sentence saying there was no picture. Two separate widgets in a stack make
  "exactly one of them is showing" structural rather than a matter of ordering.

The motion is asserted as carefully as the copy: it has to stop when the widget
is hidden, and it has to be absent in the error state, because a panel that keeps
sweeping after a failure reads as work in progress.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

import numpy as np

from services.scan_pipeline import ScanOutcome, ScanState
from tests.scanner_fixtures import CARD_ID_1, make_webcam_frame


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(qapp):
    """A real ScannerPage, reset to STOPPED and torn down afterwards.

    Built once per test rather than per module because every test drives it into
    a different state, and a leaked timer or worker from the previous one would
    make the next one's assertions depend on test order.
    """
    from services.student_service import StudentService
    from ui.scanner_page import ScannerPage

    page = ScannerPage(StudentService())
    # Shown, because several assertions below are about what the operator can
    # actually see. A widget hidden under an unshown parent reports
    # ``isVisible()`` False regardless of its own state, which would make a
    # visibility assertion pass for the wrong reason.
    page.resize(1400, 900)
    page.show()
    qapp.processEvents()
    page.camera_placeholder.set_animation_enabled(False)
    yield page
    page.shutdown()
    page.deleteLater()


def searching(frame=None) -> ScanOutcome:
    """The outcome a genuinely running scanner produces with no card in view."""
    return ScanOutcome(state=ScanState.SEARCHING, message="Searching for barcode...")


# --------------------------------------------------------------- lifecycle --


class TestInitialState:
    def test_the_page_opens_stopped_and_says_so_everywhere(self, page):
        from ui.scanner_page import ScannerState

        assert page.scanner_state is ScannerState.STOPPED
        assert "Stopped" in page.status_indicator.text()
        assert page.start_btn.text() == "Start Scanner"

    def test_the_viewport_shows_the_placeholder_not_the_feed(self, page):
        assert page.viewport_stack.currentWidget() is page.camera_placeholder
        assert page.video_label.pixmap().isNull()

    def test_a_stopped_scanner_never_says_it_is_searching(self, page):
        """The exact contradiction that was reported.

        Three surfaces, one fact: stopped. Any of them claiming to be searching
        is the bug.
        """
        blob = " ".join(
            [
                page.status_indicator.text(),
                page.detection_status.text(),
                page.camera_placeholder.title_label.text(),
                page.camera_placeholder.body_label.text(),
            ]
        ).lower()
        assert "searching for barcode" not in blob

    def test_the_footer_is_not_left_saying_waiting_for_camera(self, page):
        assert "waiting for camera" not in page.detection_status.text().lower()

    def test_the_stopped_placeholder_explains_how_to_start(self, page):
        ph = page.camera_placeholder
        assert ph.state == "stopped"
        assert ph.title_label.text() == "Camera is off"
        assert ph.body_label.text(), "the placeholder must say what to do next"
        assert ph.action_btn is not None and ph.action_btn.isVisible()

    def test_the_live_feed_label_is_never_given_text(self, page):
        """A QLabel that is only ever a pixmap has nothing to contradict.

        This is the structural half of the fix: the placeholder's sentence and
        the feed's pixels are two widgets, so they cannot both be on screen.
        """
        assert page.video_label.text() == ""


class TestStartingState:
    def test_starting_shows_the_placeholder_and_never_claims_detection(self, page):
        from ui.scanner_page import ScannerState

        page._set_state(ScannerState.STARTING)
        assert page.status_indicator.text() == "● Starting"
        assert page.viewport_stack.currentWidget() is page.camera_placeholder
        assert page.camera_placeholder.state == "starting"
        blob = " ".join(
            [
                page.detection_status.text(),
                page.camera_placeholder.title_label.text(),
                page.camera_placeholder.body_label.text(),
            ]
        ).lower()
        assert "searching for barcode" not in blob, (
            "a scanner that is still initialising must not claim to be detecting"
        )

    def test_starting_hides_the_start_action(self, page):
        """A "Start Scanner" button during Starting is a lie: it was pressed."""
        from ui.scanner_page import ScannerState

        page._set_state(ScannerState.STARTING)
        assert page.camera_placeholder.action_btn.isVisible() is False

    def test_the_camera_list_is_locked_while_scanning(self, page):
        from ui.scanner_page import ScannerState

        page._set_state(ScannerState.SCANNING)
        assert page.camera_combo.isEnabled() is False
        page._set_state(ScannerState.STOPPED)
        assert page.camera_combo.isEnabled() is True


class TestScanningState:
    def test_scanning_shows_the_feed_and_hides_the_placeholder(self, page):
        from ui.scanner_page import ScannerState

        page._set_state(ScannerState.SCANNING)
        assert page.viewport_stack.currentWidget() is page.video_label
        assert page.start_btn.text() == "Stop Scanner"
        assert page.camera_combo.isEnabled() is False

    def test_stopping_returns_every_surface_to_stopped(self, page):
        from ui.scanner_page import ScannerState

        page._set_state(ScannerState.SCANNING)
        page._set_footer("Searching for barcode...", "#64748B")
        page._stop_scanner()

        assert page.scanner_state is ScannerState.STOPPED
        assert page.status_indicator.text() == "● Stopped"
        assert page.start_btn.text() == "Start Scanner"
        assert page.viewport_stack.currentWidget() is page.camera_placeholder
        assert page.video_label.pixmap().isNull()
        assert "searching for barcode" not in page.detection_status.text().lower()

    def test_a_frame_arriving_after_a_stop_cannot_repaint_the_footer(self, page):
        """The stale-emission guard, which is what left the old message behind.

        A ``frame_result`` already posted to the GUI queue is delivered *after*
        ``_stop_scanner`` has run. Nothing is left running to correct it, so
        without this guard stopping reliably left "Searching for barcode..." on
        screen under a "Stopped" header.
        """
        from ui.scanner_page import ScannerState

        page._set_state(ScannerState.SCANNING)
        page._stop_scanner()
        page._on_frame_result(
            make_webcam_frame(CARD_ID_1), searching(make_webcam_frame(CARD_ID_1))
        )

        assert page.scanner_state is ScannerState.STOPPED
        assert "searching for barcode" not in page.detection_status.text().lower()


class TestErrorState:
    def test_a_worker_failure_becomes_the_error_state(self, page):
        """A dead pipeline must not keep a "Scanning" header over the top of it."""
        from ui.scanner_page import ScannerState

        page._set_state(ScannerState.SCANNING)
        page._on_worker_error("camera disconnected")

        assert page.scanner_state is ScannerState.ERROR
        assert page.status_indicator.text() == "● Error"
        assert "camera disconnected" in page.detection_status.text()
        assert page.viewport_stack.currentWidget() is page.camera_placeholder

    def test_the_error_state_does_not_animate(self, page):
        """A failing panel that keeps sweeping reads as work in progress."""
        from ui.scanner_page import ScannerState

        page.camera_placeholder.set_animation_enabled(True)
        try:
            page._set_state(ScannerState.ERROR)
            assert page.camera_placeholder.sweep_bar.isVisible() is False
            assert page.camera_placeholder._timer.isActive() is False
        finally:
            page.camera_placeholder.set_animation_enabled(False)

    def test_a_camera_that_will_not_open_reports_an_actionable_error(self, page, monkeypatch):
        from ui.scanner_page import ScannerState

        # The failure path raises a modal. A modal in a test blocks the event
        # loop, so it is replaced rather than dismissed.
        shown = []
        monkeypatch.setattr(
            "ui.scanner_page.QMessageBox.critical",
            lambda *a, **k: shown.append(a[1:3]),
        )

        class RefusingCamera:
            def __init__(self):
                self.stops = 0

            def start(self, index=None):
                return False

            def stop(self):
                self.stops += 1

        page.camera_service = RefusingCamera()
        page.yolo_service.is_loaded = True
        page.camera_combo.clear()
        page.camera_combo.addItem("Camera 0", 0)
        page._warm_up_detector = lambda: None

        page._set_state(ScannerState.STOPPED)
        page._start_scanner()

        assert page.scanner_state is ScannerState.ERROR
        assert "Start Scanner" in page.start_btn.text()
        assert page.camera_placeholder.state == "error"
        assert shown, "the operator was not told the camera failed"


class TestDuplicateStart:
    @pytest.mark.parametrize("state_name", ["starting", "scanning"])
    def test_start_is_refused_while_already_starting_or_scanning(self, page, state_name):
        """Two clicks, two cameras, two workers.

        ``_start_scanner`` opens the camera and warms the detector on the GUI
        thread, which is long enough for a second click -- or a "Scan Next
        Student" pressed in the gap -- to arrive and start the whole thing again.
        """
        from ui.scanner_page import ScannerState

        class CountingCamera:
            def __init__(self):
                self.starts = 0

            def start(self, index=None):
                self.starts += 1
                return True

            def stop(self):
                pass

        camera = CountingCamera()
        page.camera_service = camera
        page.yolo_service.is_loaded = True
        page.camera_combo.clear()
        page.camera_combo.addItem("Camera 0", 0)
        page._warm_up_detector = lambda: None

        page._set_state(ScannerState(state_name))
        page._start_scanner()

        assert camera.starts == 0, "a second camera was opened"
        assert page.worker is None

    def test_the_toggle_honours_what_the_button_says(self, page):
        """The button's label is the contract; the toggle has to match it.

        A "Cancel" button wired to a no-op is worse than no button, and a
        "Start Scanner" button shown during Starting is a lie about what has
        already happened.
        """
        from ui.scanner_page import ScannerState

        seen = []
        page._start_scanner = lambda: seen.append("start")
        page._stop_scanner = lambda: seen.append("stop")

        page._set_state(ScannerState.SCANNING)
        assert page.start_btn.text() == "Stop Scanner"
        page._toggle_scanner()

        page._set_state(ScannerState.STARTING)
        assert page.start_btn.text() == "Cancel"
        page._toggle_scanner()

        page._set_state(ScannerState.STOPPED)
        assert page.start_btn.text() == "Start Scanner"
        page._toggle_scanner()

        assert seen == ["stop", "stop", "start"]


class TestStateContract:
    def test_every_lifecycle_state_is_understood_by_the_placeholder(self, page):
        """The enum values are the placeholder's keys; nothing may drift.

        ``scanning`` is the one state with no placeholder: the live feed is on
        screen instead, so there is nothing to describe.
        """
        from ui.scanner_page import ScannerState
        from ui.widgets.camera_placeholder import STATES

        for state in ScannerState:
            if state is ScannerState.SCANNING:
                continue
            assert state.value in STATES, f"{state.value!r} has no placeholder state"

    def test_only_scanning_claims_frames_are_arriving(self, page):
        from ui.scanner_page import ScannerState

        assert ScannerState.SCANNING.is_running is True
        # ERROR is deliberately not "running": an error means no camera, so a
        # frame that somehow lands is stale and must not repaint the page.
        assert ScannerState.ERROR.is_running is False
        assert ScannerState.STARTING.is_running is False
        assert ScannerState.STOPPED.is_running is False

    def test_the_running_flag_cannot_be_written_from_outside(self, page):
        """One place declares the scanner running, or it can disagree with itself."""
        # ``fset`` is present on every property object; it is ``None`` for a
        # read-only one, and that is what makes a write raise.
        assert type(page)._scanner_running.fset is None

    def test_an_unknown_placeholder_state_falls_back_to_stopped(self, page):
        ph = page.camera_placeholder
        ph.set_state("something-invented")
        assert ph.state == "stopped"


class TestPlaceholderMotion:
    def test_the_breathe_runs_only_while_the_placeholder_is_on_screen(self, qapp):
        from ui.widgets.camera_placeholder import CameraPlaceholder

        ph = CameraPlaceholder()
        ph.set_animation_enabled(True)
        ph.show()
        qapp.processEvents()
        assert ph._timer.isActive() is True

        # Hidden for as long as the scanner runs: a timer on an invisible widget
        # is pure wake-ups, and that is the common case.
        ph.hide()
        qapp.processEvents()
        assert ph._timer.isActive() is False
        ph.dispose()

    def test_dispose_stops_the_timer_for_good(self, qapp):
        from ui.widgets.camera_placeholder import CameraPlaceholder

        ph = CameraPlaceholder()
        ph.set_animation_enabled(True)
        ph.show()
        qapp.processEvents()
        assert ph._timer.isActive() is True

        ph.dispose()
        assert ph._timer.isActive() is False

    def test_states_still_read_correctly_with_the_motion_switched_off(self, qapp):
        """Reduced motion must cost information, not meaning."""
        from ui.widgets.camera_placeholder import CameraPlaceholder
        from ui.scanner_page import ScannerState

        ph = CameraPlaceholder()
        ph.set_animation_enabled(False)
        for state in (ScannerState.STOPPED, ScannerState.STARTING, ScannerState.ERROR):
            ph.set_state(state.value)
            assert ph.state == state.value
            assert ph.title_label.text()
            assert ph.disc.text(), "the glyph carries the state without animation"
            assert ph._timer.isActive() is False
        ph.dispose()

    def test_a_stopped_placeholder_swall_slowly_enough_to_read_as_idle(self):
        """A standby animation that moves at progress speed is a progress bar.

        The breathe has to be slower than any progress indicator, or a stopped
        camera looks like a busy one.
        """
        from ui.widgets.camera_placeholder import STANDBY_PERIOD_MS, SWEEP_PERIOD_MS

        assert STANDBY_PERIOD_MS >= 2000, "the standby swell must read as idle"
        assert STANDBY_PERIOD_MS > SWEEP_PERIOD_MS, (
            "the starting sweep must be quicker than the standby breathe"
        )


class TestPreviewComposition:
    def test_the_placeholder_does_not_replace_the_feed(self, page):
        """Rendering must still land on the video label, not on the placeholder."""
        from ui.scanner_page import ScannerState

        frame = make_webcam_frame(CARD_ID_1)
        page._latest_frame = frame
        page._latest_outcome = searching(frame)
        page._set_state(ScannerState.SCANNING)
        page._render_preview()

        assert not page.video_label.pixmap().isNull()
        assert page.viewport_stack.currentWidget() is page.video_label
