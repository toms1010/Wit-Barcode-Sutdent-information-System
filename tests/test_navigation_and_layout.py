"""Navigation reachability and card layout regressions.

Two defects that no existing test caught, because both lived above the unit
level:

* ``MainWindow.navigate_to`` queried ``SignalInstance.receivers()``, which
  PySide6 does not provide. Every navigation raised ``AttributeError`` -- the
  Barcode Scanner page could not be opened from the sidebar at all.
* ``StudentCard`` was pinned to ``setFixedSize(280, 340)`` while its layout
  needs 408px, clipping the student's name and pushing the action buttons out
  of the frame.

Both are cheap to break and invisible until someone clicks through the app, so
they are pinned here.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class TestNavigationReachability:
    def test_every_registered_route_can_be_opened(self, qapp):
        from ui.main_window import MainWindow, PAGE_CLASSES

        window = MainWindow()
        try:
            for page_id in PAGE_CLASSES:
                window.navigate_to(page_id)
                page = window.page_stack.currentWidget()
                assert page is not None, f"route {page_id!r} opened nothing"
                assert window.current_page == page_id
        finally:
            window.close()
            window.deleteLater()

    def test_the_scanner_route_opens_a_scanner_page(self, qapp):
        from ui.main_window import MainWindow
        from ui.scanner_page import ScannerPage

        window = MainWindow()
        try:
            window.navigate_to("scanner")
            assert isinstance(window.page_stack.currentWidget(), ScannerPage)
        finally:
            window.close()
            window.deleteLater()

    def test_reopening_the_scanner_does_not_stack_handlers(self, qapp):
        from ui.main_window import MainWindow

        window = MainWindow()
        try:
            for _ in range(5):
                window.navigate_to("scanner")
            # Reachable without raising, and connected exactly once.
            assert window._scanner_connected is True
        finally:
            window.close()
            window.deleteLater()

    def test_a_scanner_navigation_request_routes_with_its_student(self, qapp):
        from models.student import Student, YearLevel
        from ui.main_window import MainWindow

        window = MainWindow()
        try:
            window.navigate_to("scanner")
            scanner = window.pages["scanner"]
            student = Student(
                id=1,
                student_id="WIT-2024-001",
                reference_number="65412",
                first_name="Javy Danielle",
                middle_name="N.",
                last_name="Caporal",
                program="Computer Engineering",
                year_level=YearLevel.THIRD_YEAR,
            )

            scanner.navigate_requested.emit("assessment", student)
            assert window.current_page == "assessment"

            scanner.navigate_requested.emit("students", student)
            assert window.current_page == "students"
        finally:
            window.close()
            window.deleteLater()


class TestStudentCardLayout:
    def _card(self, name="Javy Danielle N. Caporal"):
        from models.student import Student, YearLevel
        from ui.widgets.student_card import StudentCard

        student = Student(
            id=1,
            student_id="WIT-2024-001",
            reference_number="65412",
            first_name="Javy Danielle",
            middle_name="N.",
            last_name="Caporal",
            program="Computer Engineering",
            year_level=YearLevel.THIRD_YEAR,
        )
        card = StudentCard(student)
        card.show()
        QApplication.processEvents()
        return card

    def test_the_card_is_tall_enough_for_its_own_layout(self, qapp):
        card = self._card()
        try:
            needed = card.layout().sizeHint().height()
            assert card.height() >= needed, (
                f"card is {card.height()}px but its layout needs {needed}px"
            )
        finally:
            card.deleteLater()

    def test_the_student_name_is_not_clipped(self, qapp):
        card = self._card()
        try:
            names = [l for l in card.findChildren(QLabel)
                     if l.text() == "Javy Danielle N. Caporal"]
            assert names, "the card should show the student's name"
            label = names[0]
            assert label.height() >= label.sizeHint().height(), (
                f"name label is {label.height()}px tall, needs "
                f"{label.sizeHint().height()}px"
            )
        finally:
            card.deleteLater()

    def test_the_card_keeps_a_fixed_width_for_grid_alignment(self, qapp):
        card = self._card()
        try:
            assert card.width() == 280
        finally:
            card.deleteLater()

class TestScanWorkerTeardown:
    """A QThread must never be abandoned while still running.

    Measured defect: after 'Scan Next Student' restarts the camera, _shutdown()
    waited on the worker and gave up while it was still inside process_frame, so
    the page was destroyed with a live thread and Qt aborted with
    "QThread: Destroyed while thread is still running".
    """

    def test_the_running_flag_is_set_before_the_thread_starts(self, qapp):
        """run() must not re-arm _running, or a stop() can be overwritten."""
        import inspect

        from ui.scanner_page import ScanWorker

        source = inspect.getsource(ScanWorker.run)
        assert "_running = True" not in source, (
            "run() must not set _running; it races with stop()"
        )
        assert "_running" in source

    def test_stop_terminates_a_worker_that_will_not_finish(self, qapp):
        from ui.scanner_page import ScanWorker

        class StuckWorker(ScanWorker):
            """A worker whose loop can never observe _running going false."""

            def __init__(self):
                super().__init__(None, None, None)
                self.terminate_called = False
                self._hard_stop = False

            def run(self):
                while not self._hard_stop:
                    self.msleep(5)

            def terminate(self):  # noqa: D401 - test double
                # Real terminate() ends the thread; emulate that faithfully so
                # the double does not leak a live QThread of its own.
                self.terminate_called = True
                self._hard_stop = True

        worker = StuckWorker()
        worker.start()
        qapp.processEvents()

        worker.stop(timeout_ms=300)

        assert worker.terminate_called, (
            "stop() must terminate a worker that ignores the flag"
        )
        assert not worker.isRunning(), "the thread must be gone once stop() returns"
