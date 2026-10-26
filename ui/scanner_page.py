from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QFrame, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMessageBox, QFileDialog, QApplication,
    QStackedWidget
)
from pathlib import Path
from enum import Enum

from PySide6.QtCore import Qt, QTimer, Signal, Slot, QThread
from PySide6.QtGui import QImage, QPixmap, QFont, QBrush
from styles.typography import get_open_sauce_family, get_agrandir_family, body_font, body_bold_font, section_heading_font, page_heading_font, small_label_font, button_font, monospace_font
from styles.colors import Colors
from services.camera_service import CameraService
from services.yolo_detector import YOLODetectorService
from services.barcode_decoder import BarcodeDecoderService
from services.detection_smoother import DetectionSmoother
from services.scan_pipeline import ScanPipeline, ScanOutcome, ScanState
from services.scan_session import ScanPhase, ScanSession
from services.scanner_overlay import OverlayRenderer
from services.student_service import StudentService
from services.assessment_service import AssessmentService
from services.audio_cue import AudioCueService
from services.com_summary import summarize_enrollment, summarize_financials
from services.com_print_service import ComPrintService
from database.repositories import BarcodeScanRepository
from database.connection import DatabaseUnavailableError
from models.barcode import ScanStatus
from config.settings import settings
from ui.widgets.responsive_table import ColumnSpec, configure_table
from ui.widgets.camera_placeholder import CameraPlaceholder
import cv2
import time
from dataclasses import replace
import numpy as np
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

#: One cycle of the detection-box pulse, in milliseconds.
PULSE_DURATION_MS = 2200
#: Preview refresh rate. Deliberately faster than inference so the overlay stays
#: smooth even while YOLO and the decoder are busy in the worker thread.
PREVIEW_INTERVAL_MS = 33


class ScannerState(str, Enum):
    """The scanner's lifecycle, and the one thing the whole page reads.

    This is deliberately *not* :class:`~services.scan_pipeline.ScanState`. That
    enum describes what the pipeline made of a single frame -- searching,
    decoding, verified, error -- and it keeps moving while the camera is on. This
    one describes whether the scanner is running at all, and it only changes when
    the operator or the hardware does.

    Holding both meant the header, the viewport and the footer each read a
    different thing: the header read a boolean, the viewport read whether a
    pixmap had been set, and the footer read whatever the last frame said. All
    three could disagree, and did -- a "Stopped" header sat above a
    "Searching for barcode..." footer with nothing running to correct it.

    The values are the strings :class:`~ui.widgets.camera_placeholder.STATES`
    keys on, which is what lets the viewport placeholder be driven straight from
    this enum instead of through a translation table that could drift.
    """

    #: Camera closed, no frames, no detection.
    STOPPED = "stopped"
    #: Requested, and waiting on the camera and the detection pipeline.
    STARTING = "starting"
    #: Camera live, frames being processed, barcode detection running.
    SCANNING = "scanning"
    #: Camera or initialisation failed. Nothing is running.
    ERROR = "error"

    @property
    def is_running(self) -> bool:
        """Whether frames are expected to be arriving.

        This is the flag the frame handler gates on, and it is false in
        :attr:`ERROR` on purpose: an error state means no camera, so a frame that
        somehow arrives is stale and must not repaint the page.
        """
        return self is ScannerState.SCANNING



def _local_now():
    """Current local wall-clock time, used only as a display fallback."""
    from datetime import datetime

    return datetime.now()


def _student_name(student) -> str:
    return student.full_name if student is not None else ""


def _student_reference(student) -> str:
    return getattr(student, "reference_number", "") or "" if student is not None else ""


class RecordLookupWorker(QThread):
    """Fetches a verified student's enrollment and assessment off the GUI thread.

    Both lookups are optional: either may be missing, and either may fail, but a
    failure must never be reported as "this student has no subjects". The
    failure is carried through :attr:`completed` as text so the panel can show
    an error instead of a fabricated empty result.

    ``completed`` carries ``(enrollment, assessment, error_text)``.
    """

    completed = Signal(object, object, str)

    def __init__(self, enrollment, assessment, student_id: int, parent=None):
        super().__init__(parent)
        self._enrollment_lookup = enrollment
        self._assessment_lookup = assessment
        self._student_id = student_id

    def run(self) -> None:
        enrollment = None
        assessment = None
        errors: List[str] = []

        for label, lookup in (
            ("enrollment", self._enrollment_lookup),
            ("assessment", self._assessment_lookup),
        ):
            if lookup is None:
                continue
            try:
                result = lookup(self._student_id)
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                logger.warning("%s lookup failed: %s", label, exc)
                errors.append(f"{label.capitalize()}: {exc}")
                continue
            if label == "enrollment":
                enrollment = result[0] if result else None
            else:
                assessment = result

        message = "; ".join(errors)
        self.completed.emit(enrollment, assessment, message)


class ScanWorker(QThread):
    """Runs the whole detection pipeline off the GUI thread.

    Inference, cropping, decoding and the database verification all happen here.
    The GUI thread only ever receives finished results, so a slow query or a
    large model cannot freeze the interface.

    A per-frame ``frame_id`` lets the loop skip frames the camera thread has
    already replaced, so the pipeline never re-processes the same image.
    """

    frame_result = Signal(object, object)  # (ndarray frame, ScanOutcome)
    #: Emitted once per outcome that should become a history row, carrying the
    #: outcome itself. Persistence happens on the GUI thread so the database row
    #: and the on-screen row can be produced from the same object.
    scan_accepted = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        camera_service: CameraService,
        yolo_service: YOLODetectorService,
        decoder_service: BarcodeDecoderService,
        overlap_threshold: float = None,
        cooldown_seconds: float = None,
    ):
        super().__init__()
        self.camera_service = camera_service
        self.yolo_service = yolo_service
        self.decoder_service = decoder_service
        self.pipeline = ScanPipeline(
            detector=yolo_service,
            decoder=decoder_service,
            overlap_threshold=overlap_threshold,
            cooldown_seconds=cooldown_seconds,
        )
        self._running = True
        self._last_frame_id = -1
        self._last_error = ""

    def run(self):
        # ``_running`` is set in __init__ rather than here on purpose. Setting it
        # at the top of run() races with stop(): a stop() that lands before the
        # thread body starts would be overwritten, and the worker would never
        # exit.
        self.pipeline.reset()
        interval = 1.0 / max(1, settings.SCAN_TARGET_FPS)
        while self._running:
            frame, frame_id = self.camera_service.get_latest_frame_with_id()
            if frame is None:
                self.msleep(30)
                continue
            if frame_id == self._last_frame_id:
                self.msleep(8)
                continue
            self._last_frame_id = frame_id

            try:
                outcome = self.pipeline.process_frame(frame)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Pipeline frame failed")
                if str(exc) != self._last_error:
                    self._last_error = str(exc)
                    self.error.emit(str(exc))
                self.msleep(int(interval * 1000))
                continue
            self._last_error = ""

            self.frame_result.emit(frame, outcome)
            self.msleep(int(interval * 1000))

    def stop(self, timeout_ms: int = 10000) -> bool:
        """Stop the worker and guarantee the thread is no longer running.

        Returns ``True`` when the thread finished on its own. The loop checks
        ``_running`` between frames, so a normal stop completes in well under a
        frame interval; the measured exception is a worker parked inside
        ``process_frame`` while a camera or database call blocks, which does not
        reach the check.

        Leaving that thread alive is not an option: the page is being destroyed
        and the worker emits into its widgets. Terminating is therefore the last
        resort, and it is safe here because run() only touches Qt through signal
        emission, which Qt drops once the receiver is gone.
        """
        self._running = False
        if not self.isRunning():
            return True
        if self.wait(timeout_ms):
            return True

        logger.warning(
            "Scan worker did not stop within %dms; terminating it", timeout_ms
        )
        self.terminate()
        self.wait(1000)
        return not self.isRunning()


class StillImageWorker(QThread):
    """Runs the real pipeline over one uploaded still image.

    The uploaded image goes through exactly the same detector, decoder and
    database verification as a camera frame -- :meth:`ScanPipeline.process_frame`
    is the same entry point -- so an uploaded card cannot be reported as a
    different result than the same card held up to the webcam. It runs on its own
    thread because one pass is a second or more of YOLO inference plus decoding,
    and on the GUI thread the window would stop repainting for its duration.
    """

    completed = Signal(object)  # ScanOutcome
    failed = Signal(str)

    def __init__(self, pipeline: ScanPipeline, image, parent=None):
        super().__init__(parent)
        self._pipeline = pipeline
        self._image = image

    def run(self) -> None:
        try:
            outcome = self._pipeline.process_frame(self._image)
        except Exception as exc:  # noqa: BLE001 - carried to the UI, not hidden
            logger.exception("Uploaded image could not be processed")
            self.failed.emit(str(exc))
            return
        self.completed.emit(outcome)


class ScannerPage(QWidget):
    #: Ask the window to open another page with a student preselected.
    #: ``page_id`` is one of ``MainWindow.PAGE_CLASSES`` and ``student`` is a
    #: *verified* :class:`~models.student.Student`; the destination re-reads the
    #: record from the database, so the object is only a routing hint.
    navigate_requested = Signal(str, object)

    def __init__(self, student_service: StudentService, parent=None):
        super().__init__(parent)
        self.student_service = student_service
        self.assessment_service = AssessmentService()
        self.camera_service = CameraService()
        self.yolo_service = YOLODetectorService()
        self.decoder_service = BarcodeDecoderService()
        self.scan_repo = BarcodeScanRepository()
        self.overlay = OverlayRenderer()
        #: Content-aware duplicate suppression. Owns the Ready -> Detecting ->
        #: Decoding -> Verifying -> Success -> Ready transitions and decides
        #: which outcomes may become history rows, modals and beeps.
        self.scan_session = ScanSession()
        #: Success cue. Filled with the platform beep unless an approved
        #: ``assets/audio/success_beep.*`` file is present.
        self.audio_cue = AudioCueService()
        # Temporal smoothing for the drawn box: removes per-frame jitter and
        # holds the box briefly through a dropped detection so the preview does
        # not strobe. Fed from the GUI thread only.
        self.box_smoother = DetectionSmoother(
            alpha=settings.SCAN_BOX_SMOOTHING,
            grace_seconds=settings.SCAN_BOX_GRACE_SECONDS,
            clock=time.monotonic,
        )
        self.worker: ScanWorker = None
        #: Worker for one uploaded still image, tracked so shutdown can wait for it.
        self._image_worker: StillImageWorker = None

        self.current_student = None
        self.current_barcode = None
        self.last_scanned_value = ""
        #: The modal currently on screen, if any. Camera callbacks cannot open a
        #: second one: :meth:`_present_scan_result` returns early while this is
        #: set, which is what stops a burst of frames stacking dialogs.
        self._active_dialog = None
        #: Background record lookups feeding open panels. Tracked so
        #: :meth:`_shutdown` can wait for them instead of letting a thread touch
        #: a destroyed widget.
        self._record_workers: set = set()
        #: Most recent scan outcome that produced a history row, so a repeated
        #: presentation of the same card refreshes the panel without duplicating
        #: the row.
        self._presented_value = None

        # Latest pipeline result, shared with the preview timer. Only the GUI
        # thread reads these, and it only ever writes them from the queued
        # signal handler, so no locking is needed.
        self._latest_frame: np.ndarray = None
        self._latest_outcome: ScanOutcome = None
        self._pulse_phase = 0.0

        #: The scanner's lifecycle, and the single source of truth for the
        #: header, the viewport stack, the footer and the Start/Stop button.
        #: Read it rather than testing for a live worker: a worker that is
        #: stopping is still ``isRunning()`` for a few milliseconds, which is
        #: long enough to paint a frame and claim the scanner is scanning.
        self._state = ScannerState.STOPPED

        #: True once the scanner has been stopped and not started again.
        #:
        #: A frame that arrives after a stop belongs to a session that is
        #: over, whether it was queued by the worker or handed over directly.
        #: Before the first start there is no session to be over, so a frame
        #: handed over then is current.
        self._stopped_since_start = False

        self.setObjectName("scannerPage")
        self._setup_ui()
        self._load_cameras()
        self._load_yolo_model()
        # Draw the initial state once the widgets and the camera scan exist.
        # ``_load_cameras`` may already have moved to ERROR on a machine with no
        # camera, and either way the header, viewport and footer have to be
        # brought into agreement by something other than the first click --
        # otherwise the page opens showing "Waiting for camera..." under a
        # "Stopped" header with no camera running at all.
        self._apply_state()

        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(PREVIEW_INTERVAL_MS)
        self.preview_timer.timeout.connect(self._render_preview)

    # ------------------------------------------------------------------ UI --

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header
        header = QFrame()
        header.setFixedHeight(80)
        header.setStyleSheet(f"background-color: {Colors.ORANGE}; border-top-left-radius: 8px; border-top-right-radius: 8px;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 24, 0)

        title = QLabel("Barcode Scanner")
        title.setFont(page_heading_font(24))
        title.setStyleSheet("color: white;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        self.status_indicator = QLabel("● Stopped")
        self.status_indicator.setFont(small_label_font(11))
        self.status_indicator.setStyleSheet("color: white;")
        header_layout.addWidget(self.status_indicator)

        # Success-cue toggle. The cue is off by default only if the operator
        # asks for it; the setting persists in-process for the session.
        self.sound_btn = QPushButton("🔊 Sound On")
        self.sound_btn.setProperty("ghost", "true")
        self.sound_btn.setFont(small_label_font(11))
        self.sound_btn.setStyleSheet(
            "QPushButton { color: white; background: rgba(255,255,255,0.1); "
            "border: 1px solid white; } "
            "QPushButton:hover { background: rgba(255,255,255,0.2); }"
        )
        self.sound_btn.setCheckable(True)
        self.sound_btn.setChecked(self.audio_cue.enabled)
        self.sound_btn.setToolTip(
            f"Play a short tone after a verified scan (source: {self.audio_cue.source})"
        )
        self.sound_btn.setAccessibleName("Toggle scan success sound")
        self.sound_btn.toggled.connect(self._on_sound_toggled)
        header_layout.addWidget(self.sound_btn)

        main_layout.addWidget(header)

        # Content
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.setSpacing(24)

        # Left panel - Camera feed
        left_panel = QFrame()
        left_panel.setObjectName("LeftPanelSurface")
        left_panel.setStyleSheet(f"""
            QFrame#LeftPanelSurface {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.CARD_BORDER};
                border-radius: 12px;
            }}
        """)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(16)

        cam_controls = QHBoxLayout()
        cam_controls.setSpacing(12)

        self.camera_combo = QComboBox()
        self.camera_combo.setMinimumWidth(200)

        self.start_btn = QPushButton("Start Scanner")
        self.start_btn.setMinimumWidth(140)
        self.start_btn.clicked.connect(self._toggle_scanner)

        # Image upload runs the same pipeline as the camera, for a card that is
        # photographed rather than held up to the webcam.
        self.upload_btn = QPushButton("Upload Card Image")
        self.upload_btn.setProperty("secondary", "true")
        self.upload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.upload_btn.setToolTip(
            "Decode a saved card image (ID photo or barcode file) through the same "
            "detector and decoder the camera uses"
        )
        self.upload_btn.clicked.connect(self._upload_image)

        cam_controls.addWidget(QLabel("Camera:"))
        cam_controls.addWidget(self.camera_combo)
        cam_controls.addStretch()
        self.upload_btn.setEnabled(False)
        cam_controls.addWidget(self.upload_btn)
        cam_controls.addWidget(self.start_btn)

        left_layout.addLayout(cam_controls)

        # The viewport is a two-page stack rather than one label that is
        # sometimes a pixmap and sometimes a sentence.
        #
        # As a single QLabel the placeholder text was never cleared by
        # setPixmap, so the first live frame appeared *beside* "Camera is off",
        # and nothing tied either to whether the scanner was actually running.
        # Two pages make "exactly one of them is showing" structural.
        self.viewport_stack = QStackedWidget()
        self.viewport_stack.setObjectName("cameraViewportStack")
        self.viewport_stack.setMinimumSize(640, 400)

        self.camera_placeholder = CameraPlaceholder(
            action_text="Start Scanner",
            action=self._toggle_scanner,
            parent=self,
        )
        self.viewport_stack.addWidget(self.camera_placeholder)

        self.video_label = QLabel()
        self.video_label.setObjectName("cameraViewport")
        self.video_label.setMinimumSize(640, 400)
        # The live surface itself is never given text, so it cannot describe a
        # state: a QLabel that is only ever a pixmap has nothing to contradict.
        self.video_label.setStyleSheet(f"""
            QLabel#cameraViewport {{
                background-color: {Colors.TEXT_PRIMARY};
                border: 1px solid {Colors.CARD_BORDER};
                border-radius: 8px;
            }}
        """)
        self.viewport_stack.addWidget(self.video_label)
        self.viewport_stack.setCurrentWidget(self.camera_placeholder)

        left_layout.addWidget(self.viewport_stack, 1)

        self.detection_status = QLabel("")
        self.detection_status.setFont(small_label_font(11))
        self.detection_status.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        self.detection_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.detection_status)

        # Session phase: Ready -> Detecting -> Decoding -> Verifying -> Success.
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addStretch()

        self._phase_label = QLabel(ScanPhase.READY.value.title())
        self._phase_label.setFont(small_label_font(10))
        self._phase_label.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE};")
        self._phase_label.setAccessibleName("Scan session phase")

        self._debounce_label = QLabel("")
        self._debounce_label.setFont(small_label_font(10))
        self._debounce_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        # Hidden while empty: an empty label in a layout still reserves its height
        # and its border, which is what left a small empty box beside the phase
        # indicator when nothing was being debounced.
        self._debounce_label.setVisible(False)

        # Armed for the next student without going through the modal: this is the
        # same action as the dialog's Scan Next Student, available while no dialog
        # is open.
        self.scan_next_btn = QPushButton("Scan Next Student")
        self.scan_next_btn.setProperty("secondary", "true")
        self.scan_next_btn.setFont(small_label_font(11))
        self.scan_next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scan_next_btn.setToolTip(
            "Release the current card so the same barcode can be scanned again"
        )
        self.scan_next_btn.clicked.connect(self._on_scan_next_requested)

        status_row.addWidget(self._phase_label)
        status_row.addWidget(self._debounce_label)
        status_row.addStretch()
        status_row.addWidget(self.scan_next_btn)
        left_layout.addLayout(status_row)

        content_layout.addWidget(left_panel, 2)

        # Right panel - Results
        right_panel = QFrame()
        right_panel.setObjectName("RightPanelSurface")
        right_panel.setStyleSheet(f"""
            QFrame#RightPanelSurface {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.CARD_BORDER};
                border-radius: 12px;
            }}
        """)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(16)

        decode_group = QGroupBox("Last Decoded")
        decode_group.setFont(section_heading_font(14))
        decode_layout = QVBoxLayout(decode_group)

        self.decoded_value_label = QLabel("No barcode detected")
        self.decoded_value_label.setFont(monospace_font(16))
        self.decoded_value_label.setStyleSheet(f"color: {Colors.DARK_TEXT}; letter-spacing: 1px;")
        self.decoded_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.decoded_value_label.setWordWrap(True)

        self.decoder_status = QLabel("Decoder: Idle")
        self.decoder_status.setFont(small_label_font(10))
        self.decoder_status.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        self.decoder_status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        decode_layout.addWidget(self.decoded_value_label)
        decode_layout.addWidget(self.decoder_status)
        right_layout.addWidget(decode_group)

        # Student lookup result
        self.student_group = QGroupBox("Student Found")
        self.student_group.setFont(section_heading_font(14))
        self.student_group.setVisible(False)
        student_layout = QVBoxLayout(self.student_group)
        student_layout.setContentsMargins(16, 16, 16, 16)
        student_layout.setSpacing(12)

        student_header = QHBoxLayout()
        student_header.setSpacing(16)

        self.student_avatar = QLabel()
        self.student_avatar.setFixedSize(80, 80)
        self.student_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.student_avatar.setStyleSheet(f"""
            QLabel {{
                background-color: {Colors.WARM_BEIGE};
                border-radius: 40px;
                border: 3px solid {Colors.WHITE};
                color: {Colors.DARK_BURNT_ORANGE};
            }}
        """)

        student_details = QVBoxLayout()
        student_details.setSpacing(4)

        self.student_name_label = QLabel()
        self.student_name_label.setFont(body_bold_font(16))
        self.student_name_label.setStyleSheet(f"color: {Colors.DARK_TEXT};")
        self.student_name_label.setWordWrap(True)

        self.student_id_label = QLabel()
        self.student_id_label.setFont(small_label_font(11))
        self.student_id_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")

        self.student_program_label = QLabel()
        self.student_program_label.setFont(small_label_font(11))
        self.student_program_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")

        self.student_barcode_label = QLabel()
        self.student_barcode_label.setFont(monospace_font(11))
        self.student_barcode_label.setStyleSheet(f"color: {Colors.ORANGE};")

        student_details.addWidget(self.student_name_label)
        student_details.addWidget(self.student_id_label)
        student_details.addWidget(self.student_program_label)
        student_details.addWidget(self.student_barcode_label)
        student_details.addStretch()

        student_header.addWidget(self.student_avatar)
        student_header.addLayout(student_details, 1)
        student_layout.addLayout(student_header)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.view_profile_btn = QPushButton("View Full Profile")
        self.view_profile_btn.setProperty("secondary", "true")
        self.view_profile_btn.clicked.connect(self._view_full_profile)

        self.view_enrollment_btn = QPushButton("View Enrollment")
        self.view_enrollment_btn.setProperty("secondary", "true")
        self.view_enrollment_btn.clicked.connect(self._view_enrollment)

        self.view_assessment_btn = QPushButton("View Assessment")
        self.view_assessment_btn.setProperty("secondary", "true")
        self.view_assessment_btn.clicked.connect(self._view_assessment)

        btn_layout.addWidget(self.view_profile_btn)
        btn_layout.addWidget(self.view_enrollment_btn)
        btn_layout.addWidget(self.view_assessment_btn)
        btn_layout.addStretch()

        # Size each button to its own label so the full text always shows and the
        # buttons stay neatly aligned within the narrow right panel.
        for button in (
            self.view_profile_btn,
            self.view_enrollment_btn,
            self.view_assessment_btn,
        ):
            button.setFixedWidth(
                button.fontMetrics().horizontalAdvance(button.text()) + 30
            )

        student_layout.addLayout(btn_layout)
        right_layout.addWidget(self.student_group)

        # Scan history
        history_group = QGroupBox("Scan History")
        history_group.setFont(section_heading_font(14))
        history_layout = QVBoxLayout(history_group)

        self.history_table = QTableWidget()
        self.history_table.setObjectName("scanHistoryTable")
        # Sized by the shared helper rather than by ``Stretch``.
        #
        # This table was the one place that asked ``Stretch`` to divide its width
        # between four columns, so each got exactly a quarter of the viewport no
        # matter what it held -- 75 px at the default window and 55 px at the
        # window's own ``setMinimumSize``. ``Time`` needs 75 px for "19:05:26"
        # and the status pill needs up to 102 px for "  Not Found  ", so both
        # were being cut mid-word into "19:…" and "  Su…".
        #
        # ``Student`` is the one flexible column, and horizontal scrolling is
        # left on: when the content genuinely does not fit, scrolling is a
        # reversible inconvenience, whereas a truncated timestamp in a scan
        # history is a record that cannot be read.
        configure_table(
            self.history_table,
            [
                ColumnSpec("Time", width=86),
                ColumnSpec("Barcode", width=112),
                ColumnSpec("Student", width=150, stretch=True),
                ColumnSpec("Status", width=112),
            ],
        )
        self.history_table.setMaximumHeight(220)
        self.history_table.setAccessibleName("Scan history")

        self.history_label = QLabel()
        self.history_label.setFont(small_label_font(10))
        self.history_label.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
        self.history_label.setWordWrap(True)
        # Same reason as the debounce label: an empty label still reserved a
        # blank band under the "Scan History" heading.
        self.history_label.setVisible(False)

        history_layout.addWidget(self.history_label)
        history_layout.addWidget(self.history_table)
        right_layout.addWidget(history_group)

        right_layout.addStretch()
        content_layout.addWidget(right_panel, 1)

        main_layout.addWidget(content)

        # YOLO status bar
        yolo_bar = QFrame()
        yolo_bar.setStyleSheet(f"background-color: {Colors.CREAM}; border-top: 1px solid {Colors.CARD_BORDER};")
        yolo_layout = QHBoxLayout(yolo_bar)
        yolo_layout.setContentsMargins(16, 8, 16, 8)
        yolo_layout.setSpacing(16)

        self.yolo_status = QLabel("YOLO: Not loaded")
        self.yolo_status.setFont(small_label_font(11))
        self.yolo_status.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")

        self.confidence_label = QLabel(
            f"Confidence: {settings.YOLO_CONFIDENCE_THRESHOLD:.0%}  •  "
            f"Scan region: {settings.SCAN_REGION_OVERLAP_THRESHOLD:.0%}"
        )
        self.confidence_label.setFont(small_label_font(11))
        self.confidence_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")

        yolo_layout.addWidget(self.yolo_status)
        yolo_layout.addStretch()
        yolo_layout.addWidget(self.confidence_label)

        main_layout.addWidget(yolo_bar)

    def _load_cameras(self):
        cameras = self.camera_service.scan_cameras()
        self.camera_combo.clear()
        for cam in cameras:
            self.camera_combo.addItem(f"Camera {cam}", cam)
        if not cameras:
            self.camera_combo.addItem("No cameras found", -1)
            self.start_btn.setEnabled(False)
            # An absent camera is the ERROR state, not a note in the footer: it
            # is the one thing on this page that will not fix itself, and leaving
            # the header reading "Stopped" hid it behind a state that implies
            # nothing is wrong.
            self._set_state(
                ScannerState.ERROR,
                body=(
                    "No camera was found on this system. Connect one and choose "
                    "it in the list, then start the scanner."
                ),
            )

    # --------------------------------------------------------------- state --

    @property
    def scanner_state(self) -> ScannerState:
        """The scanner's current lifecycle state."""
        return self._state

    @property
    def _scanner_running(self) -> bool:
        """Whether frames are expected to be arriving right now.

        Kept as a read-only view of :attr:`scanner_state` because the frame
        handler gates on it. Writing to it is deliberately not possible: a
        second place that could declare the scanner running is the second place
        that could declare it stopped while it was not.
        """
        return self._state.is_running

    def _set_state(
        self,
        state: ScannerState,
        *,
        footer: str = "",
        footer_colour: str = Colors.LIGHT_TEXT,
        body: str = "",
        title: str = "",
    ) -> None:
        """Move to ``state`` and redraw everything that describes it.

        Every transition goes through here. The header indicator, the viewport
        stack, the footer and the Start/Stop button are four views of one value,
        and the bug this replaces was four independent writes that could disagree.
        """
        self._state = state
        self._apply_state(footer=footer, footer_colour=footer_colour,
                          body=body, title=title)

    def _apply_state(
        self,
        *,
        footer: str = "",
        footer_colour: str = Colors.LIGHT_TEXT,
        body: str = "",
        title: str = "",
    ) -> None:
        """Draw ``self._state``. Called on every transition and on construction."""
        state = self._state

        # -- header indicator ------------------------------------------------
        indicator = {
            ScannerState.STOPPED: ("● Stopped", "rgba(255,255,255,0.75)"),
            ScannerState.STARTING: ("● Starting", Colors.SOFT_GOLD),
            ScannerState.SCANNING: ("● Scanning", "#7BE49B"),
            ScannerState.ERROR: ("● Error", "#FFB4B4"),
        }[state]
        self.status_indicator.setText(indicator[0])
        self.status_indicator.setStyleSheet(f"color: {indicator[1]};")
        self.status_indicator.setAccessibleName(f"Scanner status: {indicator[0][2:]}")

        # -- viewport --------------------------------------------------------
        # The placeholder is shown for every state that has no live feed. While
        # scanning the feed is on screen, and the placeholder -- with its copy,
        # its glyph and its button -- is not reachable from the page at all, so
        # there is nothing left to contradict it.
        if state is ScannerState.SCANNING:
            self.viewport_stack.setCurrentWidget(self.video_label)
        else:
            self.camera_placeholder.set_state(
                state.value, title=title, body=body,
                action_text="Start Scanner", action=self._toggle_scanner,
            )
            if state is ScannerState.STOPPED:
                self.video_label.setPixmap(QPixmap())
            self.viewport_stack.setCurrentWidget(self.camera_placeholder)

        # -- Start/Stop ------------------------------------------------------
        running = state.is_running
        if state is ScannerState.STARTING:
            # The start has already been pressed, so offering "Start Scanner"
            # again is a lie, and a toggle that does nothing is worse. This is
            # the escape hatch out of a start that is taking too long.
            self.start_btn.setText("Cancel")
        else:
            self.start_btn.setText("Stop Scanner" if running else "Start Scanner")
        self.start_btn.setProperty("danger", "true" if running else "false")
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)
        # The camera list is fixed for the life of a run: changing it mid-scan
        # silently kept streaming from the old device.
        self.camera_combo.setEnabled(
            state in (ScannerState.STOPPED, ScannerState.ERROR)
        )

        # -- footer ----------------------------------------------------------
        if footer:
            self._set_footer(footer, footer_colour)
        elif state is ScannerState.STOPPED:
            self._set_footer(
                "Scanner stopped — press Start Scanner to begin",
                Colors.LIGHT_TEXT,
            )
        elif state is ScannerState.STARTING:
            self._set_footer(
                "Opening the camera and loading the detection model…",
                Colors.WARNING_ORANGE,
            )
        else:
            # Scanning: the footer belongs to the last frame, and
            # _update_status_labels writes it. Clearing it here would flicker the
            # message away on every transition back to scanning.
            pass

    def _set_error_state(self, message: str) -> None:
        """Show ``message`` as the ERROR state, in the header, viewport and footer."""
        self._set_state(
            ScannerState.ERROR,
            footer=message,
            footer_colour=Colors.ERROR_RED,
            body=message,
        )

    def _load_yolo_model(self):
        if self.yolo_service.load_active_model():
            classes = ", ".join(self.yolo_service.class_names) or "none"
            self.yolo_status.setText(f"YOLO: Loaded ({classes})")
            self.yolo_status.setStyleSheet(f"color: {Colors.SUCCESS_GREEN};")
            if self.yolo_service.class_names == ["barcode"]:
                # The detector reports the whole symbol, so the ROI gate is the
                # only thing standing between a detection and a decode.
                logger.info("Model detects barcodes directly; ROI gating applies.")
        else:
            self.yolo_status.setText("YOLO: No model loaded")
            self.yolo_status.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")

    # ------------------------------------------------------------- control --

    def _toggle_scanner(self):
        # STARTING counts as "on" here. It is not yet running, but the start has
        # been committed to and the button says Cancel, so the toggle has to
        # honour that; treating it as off would make the button do nothing.
        if self._state in (ScannerState.STARTING, ScannerState.SCANNING):
            self._stop_scanner()
        else:
            self._start_scanner()

    def _start_scanner(self):
        # Re-entrancy guard. ``_start_scanner`` opens the camera and warms the
        # detector on the GUI thread, which takes long enough for a second click
        # -- or a "Scan Next Student" pressed in the gap -- to arrive and open a
        # *second* camera and a second worker. The state is the guard: STARTING
        # and SCANNING both refuse a start, and only the first caller gets past.
        if self._state in (ScannerState.STARTING, ScannerState.SCANNING):
            logger.debug("Start requested while already %s; ignoring", self._state.value)
            return

        camera_index = self.camera_combo.currentData()
        if camera_index is None or camera_index < 0:
            QMessageBox.warning(self, "No Camera", "Please select a valid camera.")
            self._set_error_state("No camera is selected, so the scanner cannot start.")
            return

        if not self.yolo_service.is_loaded and not self._load_yolo_model():
            message = (
                "No YOLO model is loaded, so no barcode can be located.\n"
                f"Expected: {settings.YOLO_MODEL_PATH}"
            )
            QMessageBox.critical(self, "Model Error", message)
            self._set_error_state(
                "No detection model is loaded, so no barcode can be located."
            )
            return

        # From here the scanner is initialising, and the page says so. It used to
        # sit on "Camera is off" for the whole of the model warm-up -- the
        # longest single step -- with a Stopped header, which is exactly the
        # contradiction this state exists to remove.
        self._set_state(ScannerState.STARTING)

        # Opening the camera and warming the model both block the GUI thread, and
        # a state that is set but never painted is not a state the operator ever
        # sees: without this the window sits on the previous state, frozen, for
        # the whole start and then jumps straight to Scanning.
        #
        # Re-entrancy is the price of a nested event loop, and it is already
        # paid for: the STARTING state is set *before* this call, so a click
        # delivered inside it reaches the guard at the top of ``_start_scanner``
        # and is refused rather than opening a second camera.
        QApplication.processEvents()

        if not self.camera_service.start(camera_index):
            message = "The camera could not be opened. Check that it is connected and not in use by another application."
            QMessageBox.critical(self, "Camera Error", f"Failed to start camera.\n\n{message}")
            self._set_error_state(message)
            return

        self._latest_frame = None
        self._latest_outcome = None
        self.box_smoother.reset()
        self._pulse_phase = 0.0
        self.current_student = None
        self.current_barcode = None
        self.last_scanned_value = ""
        self._presented_value = None
        self.scan_session.reset()
        self.student_group.setVisible(False)
        self.upload_btn.setEnabled(True)

        # Warm the detector up first. The first inference on a new model pays for
        # CUDA context creation and kernel selection, which on this machine took
        # longer than the worker's shutdown budget: the loop was inside
        # process_frame, the stop timed out, and the thread had to be terminated
        # -- which aborts the process.
        try:
            self._warm_up_detector()
        except Exception as exc:  # noqa: BLE001 - reported to the operator
            logger.warning("Detector warm-up failed: %s", exc)
            self.camera_service.stop()
            message = f"The detector could not be started, so scanning is not available: {exc}"
            QMessageBox.critical(
                self, "Model Error",
                f"The detector could not be started, so scanning is not available:\n\n{exc}",
            )
            self._set_error_state(message)
            return

        self.worker = ScanWorker(
            self.camera_service,
            self.yolo_service,
            self.decoder_service,
        )
        self.worker.frame_result.connect(self._on_frame_result)
        self.worker.error.connect(self._on_worker_error)
        self.worker.start()
        self._stopped_since_start = False

        # Scanning is the only state in which frames are expected, and the frame
        # handler gates on exactly this. The preview timer starts here rather
        # than at the top of the method, so the compositor never runs against a
        # None frame while the model is still loading.
        self._set_state(ScannerState.SCANNING)
        self.preview_timer.start()

        # "Searching for barcode..." is set here rather than left to the first
        # frame, because the first frame can be a second away: the scanner is
        # genuinely scanning in the meantime, and the footer should say so. It is
        # cleared by every transition out of SCANNING, which is what stops it
        # outliving the run.
        self._set_footer("Searching for barcode...", Colors.LIGHT_TEXT)

        self._load_history()

    def _upload_image(self) -> None:
        """Decode a card image through the real pipeline.

        The file is read here and processed by :class:`StillImageWorker`, which
        calls the same :meth:`ScanPipeline.process_frame` the camera frames go
        through. Nothing about the outcome is simulated: the result shown is what
        the detector, decoder and database returned for that file.
        """
        if self._image_worker is not None and self._image_worker.isRunning():
            self._set_footer("Still reading the previous image…", Colors.LIGHT_TEXT)
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Upload a card or barcode image",
            str(settings.ASSETS_DIR),
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)",
        )
        if not path:
            return

        image = cv2.imread(str(path))
        if image is None:
            QMessageBox.warning(
                self, "Cannot read the image",
                f"{Path(path).name} could not be read as an image. It may be a "
                f"corrupt file, or an unsupported format.",
            )
            self._set_footer(
                "That file could not be read as an image", Colors.ERROR_RED
            )
            return

        if not self.yolo_service.is_loaded and not self._load_yolo_model():
            QMessageBox.critical(
                self, "Model Error",
                "No YOLO model is loaded, so no barcode can be located in the image.\n"
                f"Expected: {settings.YOLO_MODEL_PATH}",
            )
            return

        self.scan_session.begin_verification("")
        self._update_phase_label(self.scan_session.phase)
        self.decoded_value_label.setText("Reading image…")
        self.decoder_status.setText("Decoder: working")
        self.decoder_status.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
        self._set_footer(f"Reading {Path(path).name}…", Colors.MEDIUM_TEXT)
        self.upload_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        pipeline = ScanPipeline(
            detector=self.yolo_service,
            decoder=self.decoder_service,
        )
        self._image_worker = StillImageWorker(pipeline, image, self)
        self._image_worker.completed.connect(
            lambda outcome: self._on_image_processed(outcome, path)
        )
        self._image_worker.failed.connect(self._on_image_failed)
        self._image_worker.finished.connect(self._on_image_worker_finished)
        self._image_worker.start()

    def _on_image_worker_finished(self) -> None:
        self._image_worker = None
        self.upload_btn.setEnabled(True)
        QApplication.restoreOverrideCursor()

    def _on_image_failed(self, error: str) -> None:
        self.decoded_value_label.setText("No barcode detected")
        self.decoder_status.setText("Decoder: error")
        self.decoder_status.setStyleSheet(f"color: {Colors.ERROR_RED};")
        self._set_footer(
            f"The image could not be processed: {error}", Colors.ERROR_RED
        )

    @Slot(object, str)
    def _on_image_processed(self, outcome: ScanOutcome, path: str) -> None:
        """Show the pipeline's own result for an uploaded image."""
        self._latest_outcome = outcome
        self.video_label.setPixmap(QPixmap())

        name = Path(path).name
        if outcome.state == ScanState.VERIFIED and outcome.student is not None:
            self.decoded_value_label.setText(outcome.decoded_value)
            self.decoder_status.setText(f"Decoded: {outcome.decoded_type or 'barcode'}")
            self.decoder_status.setStyleSheet(f"color: {Colors.SUCCESS_GREEN};")
            self._set_footer(
                f"{name}: verified against the student records", Colors.SUCCESS_GREEN
            )
            self._handle_verified(outcome)
            return

        self.current_student = None
        self.current_barcode = None
        self.student_group.setVisible(False)
        self._update_phase_label(self.scan_session.phase)

        if outcome.decoded_value:
            self.decoded_value_label.setText(outcome.decoded_value)
            self.decoder_status.setText(f"Decoded: {outcome.decoded_type or 'barcode'}")
            self.decoder_status.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
        else:
            self.decoded_value_label.setText("No barcode detected")
            self.decoder_status.setText("Decoder: nothing read")
            self.decoder_status.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")

        if outcome.state == ScanState.UNKNOWN_BARCODE:
            message = (
                f"{name}: {outcome.decoded_value} is not on file. No student has that "
                f"barcode."
            )
            colour = Colors.WARNING_ORANGE
            self._record_scan(outcome)
        elif outcome.state == ScanState.ERROR:
            message = f"{name}: {outcome.message}"
            colour = Colors.ERROR_RED
        else:
            message = (
                f"{name}: no barcode was found. Use a clear photo of the whole card, "
                f"with the barcode in focus."
            )
            colour = Colors.WARNING_ORANGE

        self._set_footer(message, colour)

    def _warm_up_detector(self) -> None:
        """Run one inference on a blank frame before the worker starts."""
        import numpy as _np

        frame = _np.zeros((480, 640, 3), dtype=_np.uint8)
        self.yolo_service.detect(frame)

    def _stop_scanner(self):
        """Stop the pipeline, releasing the camera first.

        The order matters. The worker polls the camera for frames, so a worker
        parked inside a blocking capture cannot observe ``_running`` going false
        until the camera lets go; stopping the pipeline first means waiting out
        the full timeout and then terminating a thread, which aborts the process.

        The state is moved first, before anything else, so a frame already queued
        on the GUI thread is dropped by :meth:`_on_frame_result` instead of
        overwriting the state this method is about to establish. Moving the state
        *first* is also what redraws the header and the viewport immediately: the
        teardown below can take the better part of a second, and during it the
        page used to keep claiming to be scanning.
        """
        was_running = self._state.is_running
        self._set_state(ScannerState.STOPPED)
        self._stopped_since_start = True
        self.preview_timer.stop()
        self.camera_service.stop()
        if self.worker:
            # Disconnect before dropping the reference. A queued ``frame_result``
            # still fires on ``self`` even after the worker object is gone, and
            # its handler would otherwise repaint the footer with a pre-stop
            # message.
            for signal, slot in (
                (self.worker.frame_result, self._on_frame_result),
                (self.worker.error, self._on_worker_error),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            self.worker.stop()
            self.worker = None

        self._latest_frame = None
        self._latest_outcome = None
        self.box_smoother.reset()

        self._set_footer(
            "Scanner stopped — press Start Scanner to begin", Colors.LIGHT_TEXT
        )
        self.student_group.setVisible(False)
        self.decoded_value_label.setText("No barcode detected")
        self.decoder_status.setText("Decoder: Idle")
        self.decoder_status.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        self._update_phase_label(self.scan_session.phase)

    def _set_footer(self, message: str, colour: str) -> None:
        """Write the footer, and hide it when there is nothing to say.

        An empty footer used to reserve a blank band under the viewport while
        still claiming a state, which is how "Searching for barcode..." could sit
        next to a "Stopped" header.
        """
        text = message or ""
        self.detection_status.setText(text)
        self.detection_status.setStyleSheet(f"color: {colour};")
        self.detection_status.setVisible(bool(text))

    # -------------------------------------------------------------- render --

    @Slot(object, object)
    def _on_frame_result(self, frame: np.ndarray, outcome: ScanOutcome):
        """Queued on the GUI thread; only stores state and updates labels.

        The single decision point for the post-scan workflow: the session
        decides whether this outcome may become a history row, a modal and a
        beep. Everything downstream of that decision happens exactly once.

        The guard at the top is load-bearing. A frame already posted to the GUI
        event queue is still delivered *after* :meth:`_stop_scanner` has run --
        the worker is stopped and dropped, but a queued emission is unaffected.
        Without this, stopping the scanner reliably left the header reading
        "Stopped", the viewport reading "Camera is off", and the footer reading
        "Searching for barcode..." from that last in-flight frame, with nothing
        left running to correct it.

        What makes a frame stale is *which worker sent it* and *whether the
        scanner is still the session it belongs to* -- not whether the scanner
        happens to be running at this instant. A frame from a worker that has
        since been stopped or replaced belongs to a session that is over; a
        frame handed over directly while the scanner has never run is current
        by definition and is delivered.
        """
        sender = self.sender()
        if sender is not None and sender is not self.worker:
            # A frame from a worker that has been stopped or replaced. Its
            # payload describes a session that no longer exists, and drawing
            # it would put the page back into a state it has already left.
            return
        if self._stopped_since_start:
            # The scanner was stopped, so this frame describes a session that
            # is over -- whichever worker handed it over.
            return

        self._latest_frame = frame
        self._latest_outcome = outcome

        self._update_status_labels(outcome)

        decision = self.scan_session.observe(
            outcome.state,
            outcome.decoded_value,
            confirmed=outcome.verified_now and outcome.student is not None,
        )
        self._update_phase_label(decision.phase)

        if outcome.state == ScanState.DECODING and outcome.decoded_value:
            self.scan_session.begin_verification(outcome.decoded_value)

        if decision.accepted:
            self._handle_verified(outcome)
            return

        if outcome.state in (
            ScanState.UNKNOWN_BARCODE,
            ScanState.AMBIGUOUS,
            ScanState.ERROR,
        ):
            self.current_student = None
            self.current_barcode = None
            self.student_group.setVisible(False)
            if outcome.state == ScanState.UNKNOWN_BARCODE and outcome.decoded_value:
                self._record_scan(outcome)
        elif outcome.state == ScanState.SEARCHING:
            # The card has left the frame. The session has already released its
            # latch; the presentation guard is cleared so the same card speaks up
            # again when it comes back.
            self._presented_value = None

    def _update_phase_label(self, phase: ScanPhase) -> None:
        """Show the scan session phase, and how long is left of the debounce."""
        self._phase_label.setText(phase.value.title())
        self._phase_label.setToolTip(f"Scan session phase: {phase.value}")
        remaining = self.scan_session.debounce_remaining()
        if remaining > 0.05:
            self._debounce_label.setText(f"• re-scan ready in {remaining:.1f}s")
            self._debounce_label.setVisible(True)
        else:
            self._debounce_label.setText("")
            self._debounce_label.setVisible(False)

    def _update_status_labels(self, outcome: ScanOutcome):
        colors = {
            ScanState.SEARCHING: Colors.LIGHT_TEXT,
            ScanState.BARCODE_DETECTED: Colors.WARNING_ORANGE,
            ScanState.DECODING: Colors.MEDIUM_TEXT,
            ScanState.VERIFIED: Colors.SUCCESS_GREEN,
            ScanState.UNKNOWN_BARCODE: Colors.ERROR_RED,
            # Ambiguous reads are a warning, not an error: nothing is wrong with
            # the scanner, the operator simply has two cards in view.
            ScanState.AMBIGUOUS: Colors.WARNING_ORANGE,
            ScanState.ERROR: Colors.ERROR_RED,
        }
        self._set_footer(
            outcome.message, colors.get(outcome.state, Colors.LIGHT_TEXT)
        )

        if outcome.decoded_value:
            self.decoded_value_label.setText(outcome.decoded_value)
        elif outcome.state in (ScanState.SEARCHING,):
            self.decoded_value_label.setText("No barcode detected")

        if outcome.state == ScanState.BARCODE_DETECTED and outcome.candidates:
            self.decoder_status.setText("Decoder: Waiting to read")
            self.decoder_status.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
        elif outcome.decoded_value:
            self.decoder_status.setText(f"Decoded: {outcome.decoded_type or 'barcode'}")
            self.decoder_status.setStyleSheet(f"color: {Colors.SUCCESS_GREEN};")
        elif outcome.state == ScanState.SEARCHING:
            self.decoder_status.setText("Decoder: Idle")
            self.decoder_status.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")

    def _smoothed_outcome(self) -> Optional[ScanOutcome]:
        """The latest outcome with temporally smoothed detection boxes.

        Returns ``None`` when there is nothing to show, which makes the overlay
        drop its box entirely rather than freezing the last one.
        """
        outcome = self._latest_outcome
        if outcome is None:
            self.box_smoother.reset()
            return None

        smoothed = self.box_smoother.update(outcome.candidates)
        if not smoothed:
            return None

        if len(smoothed) == len(outcome.candidates):
            # Same boxes, possibly moved: copy is cheap and keeps the outcome
            # object distinct from the pipeline's.
            return replace(outcome, candidates=smoothed)
        return replace(outcome, candidates=smoothed)

    def _render_preview(self):
        """Composites the overlay at display rate so the box pulse stays smooth."""
        frame = self._latest_frame
        if frame is None:
            self._pulse_phase = (self._pulse_phase + PREVIEW_INTERVAL_MS / PULSE_DURATION_MS) % 1.0
            return

        try:
            target = self.video_label.size()
            # Smooth the boxes for *display only*. The pipeline keeps its own
            # untouched candidates, so presentation never feeds back into
            # detection, gating or decoding.
            outcome = self._smoothed_outcome()
            canvas = self.overlay.render(
                frame,
                outcome,
                target_size=(target.width(), target.height()),
                progress=self._pulse_phase,
            )
        except Exception:
            logger.exception("Overlay render failed")
            return

        self._pulse_phase = (self._pulse_phase + PREVIEW_INTERVAL_MS / PULSE_DURATION_MS) % 1.0

        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        qt_image = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888)
        # Copy out of the temporary buffer before the numpy array is released.
        pixmap = QPixmap.fromImage(qt_image.copy())
        self.video_label.setPixmap(
            pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # ------------------------------------------------------------- results --

    def _handle_verified(self, outcome: ScanOutcome):
        """Act on a scan the session has accepted.

        The order matters: the history row goes in first so the operator sees
        the record even if loading enrollment or assessment data is slow or
        fails, then the audio cue confirms the read, then the structured modal.
        """
        student = outcome.student
        if student is None:
            # Cannot happen for an accepted decision: the session only accepts a
            # payload the pipeline confirmed against a student row. Guarding it
            # keeps that invariant explicit.
            logger.error("Accepted scan without a verified student; ignoring")
            return

        self.current_student = student
        self.last_scanned_value = outcome.decoded_value or ""
        self.current_barcode = outcome.decoded_value

        self.student_group.setVisible(True)
        self._load_student_photo(self.student_avatar, student)
        self.student_name_label.setText(student.full_name)
        self.student_id_label.setText(
            f"Student ID: {student.student_id}  •  Ref: {student.reference_number}"
        )
        year = getattr(student.year_level, "value", student.year_level)
        self.student_program_label.setText(f"{student.program}  •  {year}")
        self.student_barcode_label.setText(f"Barcode: {outcome.decoded_value}")

        # A record whose barcode payload and reference number disagree is shown
        # but explicitly not confirmed: the identity behind the card is in
        # question and the operator has to know that.
        from ui.widgets.profile_image import identifier_conflict

        conflict = identifier_conflict(student)
        if conflict:
            logger.warning("Unresolved student record: %s", conflict)
            self.student_barcode_label.setText(
                f"Barcode: {outcome.decoded_value}  •  ⚠ UNRESOLVED RECORD"
            )
            self.student_barcode_label.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
            self.student_barcode_label.setToolTip(conflict)
        else:
            self.student_barcode_label.setStyleSheet("")
            self.student_barcode_label.setToolTip("")

        # 1. Persist and show the history row immediately -- no page refresh.
        self._record_scan(outcome)

        # 2. Confirm the read audibly. Only here, after decoding *and* the
        #    database match, so the beep means "verified", not "something moved".
        self.audio_cue.play_success()

        # 3. Structured detail panel.
        self._present_scan_result(outcome, student)

    def _record_scan(self, outcome: ScanOutcome) -> None:
        """Persist one scan and append it to the history table.

        Runs on the GUI thread, at most once per accepted scan, so the row the
        operator sees and the row in ``barcode_scans`` describe the same event.
        """
        if not outcome.decoded_value:
            return

        success = outcome.state == ScanState.VERIFIED and outcome.student is not None
        status = ScanStatus.SUCCESS if success else ScanStatus.NOT_FOUND
        cand = outcome.primary_candidate
        bbox = None
        if cand is not None:
            bbox = {"x": cand.bbox[0], "y": cand.bbox[1], "w": cand.bbox[2], "h": cand.bbox[3]}

        from models.barcode import BarcodeScan

        scan = BarcodeScan(
            scanned_value=outcome.decoded_value,
            student_id=getattr(outcome.student, "id", None),
            scan_status=status,
            confidence_score=cand.confidence if cand else None,
            detection_bbox=bbox,
            camera_index=self.camera_combo.currentData(),
        )

        try:
            scan_id = self.scan_repo.create(scan)
        except DatabaseUnavailableError:
            logger.warning("Scan not recorded: database unavailable")
            self._history_notice(
                "History unavailable — the database is not connected", Colors.WARNING_ORANGE
            )
            # Still show the row: the scan happened, it simply is not persisted.
            self._append_history_row(
                value=outcome.decoded_value,
                student_name=_student_name(outcome.student),
                reference=_student_reference(outcome.student),
                status=status,
                scanned_at=_local_now(),
            )
            return

        persisted = self._fetch_scan_by_id(scan_id) if scan_id is not None else None
        self._append_history_row(
            value=outcome.decoded_value,
            student_name=_student_name(outcome.student),
            reference=_student_reference(outcome.student),
            status=status,
            # Prefer the database's own timestamp; fall back to the local clock
            # only if the row could not be read back.
            scanned_at=persisted.scanned_at if persisted else _local_now(),
        )

    def _fetch_scan_by_id(self, scan_id: int):
        """Fetch one persisted scan. Returns ``None`` on any failure."""
        try:
            scans = self.scan_repo.get_recent(limit=max(1, settings.SCAN_HISTORY_DISPLAY_LIMIT))
        except DatabaseUnavailableError:
            return None
        for scan in scans:
            if scan.id == scan_id:
                return scan
        return None

    def _append_history_row(self, value: str, student_name: str, reference: str,
                            status: ScanStatus, scanned_at) -> None:
        """Insert one row at the top of the Scan History table.

        Newest first, matching the order the repository reads in, so the operator
        sees the scan they just made at the top without scrolling.
        """
        table = self.history_table
        table.insertRow(0)

        time_item = QTableWidgetItem(scanned_at.strftime("%H:%M:%S") if scanned_at else "N/A")
        time_item.setToolTip(
            scanned_at.strftime("%Y-%m-%d %H:%M:%S") if scanned_at else "Timestamp unavailable"
        )
        table.setItem(0, 0, time_item)

        value_item = QTableWidgetItem(value or "")
        value_item.setFont(monospace_font(11))
        value_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setItem(0, 1, value_item)

        name_item = QTableWidgetItem(student_name or "Unknown")
        if reference:
            name_item.setToolTip(f"Reference No. {reference}")
        table.setItem(0, 2, name_item)

        table.setItem(0, 3, self._status_badge(status))

        limit = max(1, settings.SCAN_HISTORY_DISPLAY_LIMIT)
        while table.rowCount() > limit:
            table.removeRow(table.rowCount() - 1)

        table.scrollToItem(time_item)

    @staticmethod
    def _status_badge(status: ScanStatus) -> QTableWidgetItem:
        """A coloured status pill for the history table."""
        label = status.value.replace("_", " ").title() if hasattr(status, "value") else str(status)
        colors = {
            ScanStatus.SUCCESS: (Colors.SUCCESS_GREEN, "#E8F5E9"),
            ScanStatus.NOT_FOUND: (Colors.ERROR_RED, "#FDECEA"),
            ScanStatus.INVALID: (Colors.ERROR_RED, "#FDECEA"),
            ScanStatus.DUPLICATE: (Colors.WARNING_ORANGE, "#FFF3E0"),
            ScanStatus.ERROR: (Colors.MEDIUM_TEXT, "#F5E6D3"),
        }
        foreground, background = colors.get(status, (Colors.MEDIUM_TEXT, "#F5E6D3"))
        item = QTableWidgetItem(f"  {label}  ")
        item.setForeground(Colors.qcolor(foreground))
        item.setBackground(Colors.qcolor(background))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        # Screen readers announce the plain status, not the padded label.
        item.setData(Qt.ItemDataRole.AccessibleTextRole, label)
        return item

    def _present_scan_result(self, outcome: ScanOutcome, student) -> None:
        """Open the structured detail panel, at most one at a time."""
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        if self._active_dialog is not None:
            # A modal is already up. Camera callbacks keep firing underneath it;
            # stacking a second one would bury the operator.
            logger.debug("Scan result panel already open; not opening another")
            return

        value = outcome.decoded_value or ""
        if self._presented_value == value:
            # Same payload presented again in a new session. The panel was
            # already shown for this card; the history row is what changed.
            return

        self._presented_value = value

        # The identity is already verified at this point, so the panel opens
        # immediately with a loading state and the enrollment/assessment
        # records arrive afterwards. Fetching them first blocked the GUI thread
        # on two database queries: with a slow or unreachable database the popup
        # never appeared at all and the whole window froze, which is exactly the
        # "decoded but nothing happened" symptom this replaces.
        dialog = ScanResultDialog(
            student=student,
            enrollment=None,
            assessment=None,
            scanned_value=value,
            success=True,
            print_service=self._print_service(),
            loading=True,
            parent=self,
        )
        self._wire_result_dialog(dialog)
        self._fetch_records_async(dialog, student)

    def _fetch_records_async(self, dialog, student) -> None:
        """Load enrollment/assessment off the GUI thread into an open panel."""
        worker = RecordLookupWorker(
            enrollment=self.student_service.get_enrollments_by_student,
            assessment=self.assessment_service.get_latest_assessment,
            student_id=student.id,
        )
        worker.completed.connect(
            lambda enrollment, assessment, error, d=dialog: self._on_records_loaded(d, enrollment, assessment, error)
        )
        self._record_workers.add(worker)
        worker.finished.connect(lambda: self._record_workers.discard(worker))
        # Hand ownership to Qt: without this the worker is destroyed as soon as
        # the set drops it on `finished`, which is still inside run() and makes
        # Qt abort with "QThread: Destroyed while thread is still running".
        worker.finished.connect(worker.deleteLater)
        worker.start()

    @Slot(object, object, object)
    def _on_records_loaded(self, dialog, enrollment, assessment, error) -> None:
        """GUI thread: hand the fetched records to the panel, if it is still up."""
        if self._active_dialog is not dialog:
            # The operator closed or moved on while the lookup was running.
            # Updating a dead panel would be wasted work at best.
            return
        if error:
            logger.warning(
                "Records for student %s could not be loaded: %s",
                getattr(getattr(dialog, "student", None), "student_id", "?"),
                error,
            )
        dialog.apply_records(enrollment, assessment, error or "")

    def _wire_result_dialog(self, dialog) -> None:
        """Connect a result dialog and show it without blocking the GUI thread.

        ``QDialog.exec()`` runs a nested event loop, which parks the caller until
        the dialog closes. Inside a camera callback that is worse than a hang: the
        capture thread keeps queueing frames that nothing drains, and the preview
        timer stops compositing, so the preview freezes behind the very modal
        that is meant to sit next to it. ``open()`` keeps the dialog modal to the
        window while returning immediately, so the preview keeps rendering and
        ``_active_dialog`` still blocks a second dialog from stacking.
        """
        dialog.assessment_requested.connect(self._on_assessment_requested)
        dialog.profile_requested.connect(self._on_profile_requested)
        dialog.scan_next_requested.connect(self._on_scan_next_requested)
        dialog.finished.connect(lambda _code=0, d=dialog: self._on_dialog_finished(d))

        self._active_dialog = dialog
        dialog.open()

    def _on_dialog_finished(self, dialog) -> None:
        """Release the presentation guard once the dialog is gone.

        Clearing only *this* dialog's guard keeps a newer modal (if one was
        opened after this one closed) from being cancelled by a late signal.
        """
        if self._active_dialog is dialog:
            self._active_dialog = None
        dialog.deleteLater()

    def _print_service(self):
        if not hasattr(self, "_com_print_service"):
            try:
                self._com_print_service = ComPrintService()
            except Exception as exc:
                logger.warning("COM print service unavailable: %s", exc)
                self._com_print_service = None
        return self._com_print_service

    def _load_student_records(self, student):
        """Fetch enrollment and assessment for the verified student.

        Both are optional. A failure here is shown as missing data, never as an
        invented zero.
        """
        enrollment = None
        assessment = None
        try:
            enrollments = self.student_service.get_enrollments_by_student(student.id)
            enrollment = enrollments[0] if enrollments else None
        except DatabaseUnavailableError:
            logger.warning("Enrollment unavailable for student %s", student.id)
        except Exception:
            logger.exception("Enrollment lookup failed")

        try:
            assessment = self.assessment_service.get_latest_assessment(student.id)
        except DatabaseUnavailableError:
            logger.warning("Assessment unavailable for student %s", student.id)
        except Exception:
            logger.exception("Assessment lookup failed")

        return enrollment, assessment

    # ------------------------------------------------------ modal actions --

    def _on_assessment_requested(self, student) -> None:
        """Route to the existing Assessment & Fees page with this student."""
        self.navigate_requested.emit("assessment", student)

    def _on_profile_requested(self, student) -> None:
        """Route to the existing Students page with this student."""
        self.navigate_requested.emit("students", student)

    def _on_scan_next_requested(self) -> None:
        """Arm the scanner for the next student without touching history."""
        self.scan_session.start_new_session()
        self._presented_value = None
        self.current_student = None
        self.current_barcode = None
        self.last_scanned_value = ""
        self.student_group.setVisible(False)
        self._update_phase_label(self.scan_session.phase)
        if self.worker is None or not self.worker.isRunning():
            # The camera was stopped while the panel was open; restart it so the
            # operator can continue without touching the whole application.
            self._start_scanner()
        else:
            self.video_label.setFocus(Qt.FocusReason.OtherFocusReason)

    @Slot(bool)
    def _on_sound_toggled(self, checked: bool) -> None:
        self.audio_cue.set_enabled(checked)
        self.sound_btn.setText("🔊 Sound On" if checked else "🔇 Sound Off")

    def _load_student_photo(self, avatar_label: QLabel, student):
        """Show the verified student's portrait, or a neutral placeholder."""
        from ui.widgets.profile_image import apply_portrait

        apply_portrait(avatar_label, student)

    def _view_full_profile(self):
        if self.current_student:
            from ui.dialogs.student_dialog import StudentDialog
            dialog = StudentDialog(student=self.current_student, parent=self)
            dialog.exec()

    def _view_enrollment(self):
        """Re-open the detail panel for the student already on screen."""
        if not self.current_student:
            return
        enrollment, assessment = self._load_student_records(self.current_student)
        self._open_result_panel(
            self.current_student,
            enrollment,
            assessment,
            self.current_barcode or "",
        )

    def _view_assessment(self):
        """Route to the Assessment & Fees page for the verified student."""
        if self.current_student:
            self._on_assessment_requested(self.current_student)

    def _open_result_panel(self, student, enrollment, assessment, scanned_value: str) -> None:
        from ui.dialogs.scan_result_dialog import ScanResultDialog

        dialog = ScanResultDialog(
            student=student,
            enrollment=enrollment,
            assessment=assessment,
            scanned_value=scanned_value,
            success=True,
            print_service=self._print_service(),
            parent=self,
        )
        self._wire_result_dialog(dialog)

    def _history_notice(self, message: str, colour: str) -> None:
        """Show a message above the history table, or hide the label."""
        if not message:
            self.history_label.setVisible(False)
            return
        self.history_label.setText(message)
        self.history_label.setStyleSheet(f"color: {colour};")
        self.history_label.setVisible(True)

    def _show_history_empty(self, message: str) -> None:
        """Put one explanatory row in an empty history table.

        A zero-row table cannot be told apart from a table that never loaded, so
        the table says what it is.
        """
        table = self.history_table
        if table.rowCount():
            return
        table.insertRow(0)
        item = QTableWidgetItem(message)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(Colors.qcolor(Colors.LIGHT_TEXT))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        table.setSpan(0, 0, 1, 4)

    def _load_history(self):
        """Reload the whole history from the database.

        Used on scanner start. Live scans append a single row instead, so the
        table never blanks and re-fills while the operator is watching.
        """
        self.history_table.setRowCount(0)
        try:
            scans = self.scan_repo.get_recent(limit=settings.SCAN_HISTORY_DISPLAY_LIMIT)
        except DatabaseUnavailableError:
            self._history_notice(
                "History unavailable — the database is not connected", Colors.WARNING_ORANGE
            )
            self._show_history_empty("No scans recorded yet")
            return
        except Exception:
            logger.exception("Failed to load scan history")
            self._history_notice("History could not be loaded", Colors.ERROR_RED)
            self._show_history_empty("Scan history could not be loaded")
            return

        if not scans:
            self._show_history_empty(
                "No scans recorded yet — scan a card, or upload a card image, to fill "
                "this table."
            )
            return

        # Reverse so the newest scan lands on top, matching what a live append
        # produces.
        name_cache: dict = {}
        for scan in reversed(scans):
            name = None
            reference = ""
            if scan.student_id:
                if scan.student_id not in name_cache:
                    name_cache[scan.student_id] = self._safe_student(scan.student_id)
                student = name_cache[scan.student_id]
                if student is not None:
                    name = student.full_name
                    reference = student.reference_number
            self._append_history_row(
                value=scan.scanned_value,
                student_name=name or "Unknown",
                reference=reference,
                status=scan.scan_status,
                scanned_at=scan.scanned_at,
            )

    def _safe_student(self, student_id: int):
        """Student for a scan row, or ``None`` when the lookup fails."""
        try:
            return self.student_service.get_student(student_id)
        except DatabaseUnavailableError:
            return None
        except Exception:
            logger.exception("Student lookup failed for scan history")
            return None

    @Slot(str)
    def _on_worker_error(self, error: str):
        """A failure inside the detection loop.

        This used to write the error into the footer and nothing else, so a dead
        pipeline kept a "Scanning" header and a live-looking viewport over the
        top of it. The camera and the worker are torn down here instead: a loop
        that raised is not going to recover on its own, and leaving it "running"
        is how a page ends up claiming to scan while nothing is being decoded.
        """
        logger.error("Scanner worker error: %s", error)
        self._stop_scanner()
        self._set_error_state(f"Scanning stopped: {error}")

    # ------------------------------------------------------------ cleanup --

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        """Public teardown, also called by the window's aboutToQuit handler."""

        # The historical name, kept because tests and the window's teardown both
        # refer to it; it is the same routine.
        self._shutdown()

    def _shutdown(self) -> None:
        """Release every timer, thread and audio resource this page owns.

        Called on close and on application teardown. Without it the preview
        timer and the worker thread outlive the widget: the timer keeps firing
        into a deleted label and the thread keeps decoding frames into a
        half-destroyed pipeline.
        """
        self.preview_timer.stop()
        # The viewport placeholder owns a timer of its own. It stops itself when
        # hidden, but the page can be torn down while it is still on screen, and
        # a live timer would then repaint into a deleted label.
        try:
            self.camera_placeholder.dispose()
        except (RuntimeError, AttributeError):
            pass
        # Only disconnected when it is connected. Calling disconnect() on an
        # already-disconnected signal raises a libpyside RuntimeWarning, which
        # tests and log files then had to be read past on every shutdown.
        try:
            if self.preview_timer.receivers(self.preview_timer.timeout):
                self.preview_timer.timeout.disconnect(self._render_preview)
        except (RuntimeError, TypeError, AttributeError):
            pass

        image_worker = self._image_worker
        self._image_worker = None
        if image_worker is not None:
            try:
                if not image_worker.wait(3000):
                    logger.warning(
                        "Image upload worker was still running at shutdown"
                    )
            except Exception:
                logger.exception("Image upload shutdown failed")

        worker = self.worker
        self.worker = None
        if worker is not None:
            try:
                worker.frame_result.disconnect(self._on_frame_result)
                worker.error.disconnect(self._on_worker_error)
            except (RuntimeError, TypeError, AttributeError):
                pass

        # Release the camera *before* waiting on the worker. The worker polls the
        # camera for a new frame, so leaving it streaming keeps the worker inside
        # process_frame for longer than the shutdown budget and forces the
        # terminate path that stop() otherwise never needs.
        try:
            self.camera_service.stop()
        except Exception:
            logger.exception("Camera shutdown failed")

        if worker is not None:
            try:
                if not worker.stop():
                    logger.error("Scan worker thread did not terminate")
            except Exception:
                logger.exception("Worker shutdown failed")

        try:
            self.audio_cue.dispose()
        except Exception:
            logger.exception("Audio cue shutdown failed")

        # Teardown runs on close and at interpreter shutdown, so it must not be
        # able to raise: an exception here leaks the preview timer and the worker
        # thread, which is the exact failure this method exists to prevent.
        for lookup_worker in list(self._record_workers):
            try:
                if not lookup_worker.wait(2000):
                    # A database that is still answering. Say so rather than
                    # dropping the last reference to a running QThread, which
                    # aborts the interpreter.
                    logger.warning(
                        "Record lookup for student %s was still running at shutdown",
                        lookup_worker._student_id,
                    )
            except Exception:
                logger.exception("Record lookup shutdown failed")
        self._record_workers.clear()

        # Workers release themselves with deleteLater(). If the event loop never
        # runs another pass those C++ objects outlive their Python wrappers and
        # Qt aborts with "QThread: Destroyed while thread is still running".
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication

        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

        dialog = self._active_dialog
        self._active_dialog = None
        if dialog is not None:
            try:
                dialog.close()
            except Exception:
                logger.exception("Result dialog shutdown failed")
