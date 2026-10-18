from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QStackedWidget, QFrame, QStatusBar, QToolBar, QMessageBox, QDialog,
    QLineEdit, QSizePolicy, QApplication
)
from PySide6.QtCore import Qt, QTimer, QDateTime
from PySide6.QtGui import QFont, QIcon, QKeySequence, QPixmap, QShortcut, QAction
from config.settings import settings
from styles.typography import get_open_sauce_family, get_agrandir_family, body_font, body_bold_font, page_heading_font, button_font, small_label_font
from styles.colors import Colors
from ui.widgets.sidebar import Sidebar
from ui.dashboard import DashboardPage
from ui.students_page import StudentsPage
from ui.scanner_page import ScannerPage
from ui.barcode_generator_page import BarcodeGeneratorPage
from ui.enrollment_page import EnrollmentPage
from ui.assessment_page import AssessmentPage
from ui.reports_page import ReportsPage
from ui.courses_page import CoursesPage
from ui.certificate_page import CertificatePage
from ui.training_center import TrainingCenterPage
from ui.settings_page import SettingsPage
from ui.dialogs.student_dialog import StudentDialog
from database.connection import DatabaseUnavailableError
from ui.db_error import show_db_error
from services.student_service import StudentService
from services.barcode_generator import BarcodeGeneratorService
from services.assessment_service import AssessmentService
from services.report_service import ReportService
import logging

logger = logging.getLogger(__name__)

PAGE_CLASSES = {
    "dashboard": DashboardPage,
    "students": StudentsPage,
    "scanner": ScannerPage,
    "generator": BarcodeGeneratorPage,
    "enrollment": EnrollmentPage,
    "assessment": AssessmentPage,
    "reports": ReportsPage,
    "courses": CoursesPage,
    "certificate": CertificatePage,
    "training": TrainingCenterPage,
    "settings": SettingsPage,
}

PAGE_TITLES = {
    "dashboard": "Dashboard",
    "students": "Students",
    "scanner": "Barcode Scanner",
    "generator": "Barcode Generator",
    "enrollment": "Subject Enrollment",
    "courses": "Courses & Subjects",
    "certificate": "Certificate of Matriculation",
    "assessment": "Assessment & Fees",
    "reports": "Reports & Analytics",
    "training": "Training Center",
    "settings": "Settings",
    "logout": "Logout",
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WIT Barcode Student Information System")
        self.setMinimumSize(1280, 800)
        self.resize(1400, 900)
        
        # Initialize services
        self.student_service = StudentService()
        self.barcode_service = BarcodeGeneratorService()
        self.assessment_service = AssessmentService()
        self.report_service = ReportService()
        
        self.current_page = "dashboard"
        self.pages = {}
        # Tracks whether the scanner's cross-page signal has been connected, so
        # re-opening the page does not stack duplicate handlers.
        self._scanner_connected = False
        
        self._setup_ui()
        self._setup_status_bar()
        self._setup_timers()

        # A quit that does not pass through closeEvent (the window manager
        # killing the process, or app.quit() from a script) still has to release
        # the camera and the scan worker.
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        
        # Navigate to dashboard initially
        self.navigate_to("dashboard")
    
    def _setup_ui(self):
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Sidebar
        self.sidebar = Sidebar()
        self.sidebar.navigation_changed.connect(self.navigate_to)
        main_layout.addWidget(self.sidebar)
        
        # Main content area
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        
        # Top bar
        # Object-name scoped on purpose: an unscoped `QFrame { … }` rule is
        # applied by Qt to the bar's own children as well, which drew a hairline
        # box around the logo, the title and the quick-search field.
        self.top_bar = QFrame()
        self.top_bar.setObjectName("windowTopBar")
        self.top_bar.setFixedHeight(60)
        self.top_bar.setStyleSheet(f"""
            QFrame#windowTopBar {{
                background-color: {Colors.WHITE};
                border-bottom: 1px solid {Colors.CARD_BORDER};
            }}
        """)
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(24, 0, 24, 0)
        top_layout.setSpacing(12)
        
        # App logo/title
        self.logo_label = QLabel()
        self.logo_label.setFixedSize(36, 36)
        self.logo_label.setAlignment(Qt.AlignCenter)
        logo_path = settings.LOGO_PATH
        if logo_path.exists():
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                self.logo_label.setPixmap(
                    pixmap.scaled(
                        36, 36,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
            else:
                logger.warning("Logo could not be decoded: %s", logo_path)
        else:
            logger.warning("Logo asset missing: %s", logo_path)
        self.logo_label.setStyleSheet("background: transparent;")
        top_layout.addWidget(self.logo_label)
        
        self.app_title = QLabel("WIT Barcode Student Information System")
        self.app_title.setFont(page_heading_font(18))
        self.app_title.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE};")
        self.app_title.setToolTip("WIT Barcode Student Information System")
        top_layout.addWidget(self.app_title)
        
        top_layout.addStretch()
        
        # Search
        self.global_search = QLineEdit()
        self.global_search.setPlaceholderText("Search students...")
        # Flexible rather than fixed width: the search box yields space first so
        # the application title is never clipped on a narrow window.
        self.global_search.setMinimumWidth(180)
        self.global_search.setMaximumWidth(300)
        self.global_search.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Fixed
        )
        self.global_search.setClearButtonEnabled(True)
        self.global_search.setToolTip(
            "Search students by name, student ID or reference number, then press Enter "
            "(Ctrl+K to jump here)"
        )
        # Enter runs the search; live filtering happens on the page it opens, so
        # typing here does not fire a query per keystroke.
        self.global_search.returnPressed.connect(self._run_global_search)
        top_layout.addWidget(self.global_search)

        # Ctrl+K focuses the search from anywhere in the window. It focuses the
        # existing field rather than opening a dialog, so pressing it repeatedly
        # cannot stack search windows.
        self.quick_search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.quick_search_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.quick_search_shortcut.activated.connect(self._focus_global_search)
        
        # Date/Time
        self.datetime_label = QLabel()
        self.datetime_label.setFont(small_label_font(11))
        self.datetime_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        top_layout.addWidget(self.datetime_label)
        
        # DB Status
        self.db_status = QLabel("● Database: Checking…")
        self.db_status.setFont(small_label_font(11))
        self.db_status.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        top_layout.addWidget(self.db_status)
        
        # User menu
        self.user_btn = QPushButton("Admin ▼")
        self.user_btn.setProperty("ghost", "true")
        self.user_btn.setStyleSheet("QPushButton { color: #4A3520; }")
        top_layout.addWidget(self.user_btn)
        
        content_layout.addWidget(self.top_bar)
        
        # Page stack
        self.page_stack = QStackedWidget()
        content_layout.addWidget(self.page_stack)
        
        main_layout.addWidget(content_widget, 1)
    
    def _setup_status_bar(self):
        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet(f"""
            QStatusBar {{
                background-color: {Colors.WHITE};
                border-top: 1px solid {Colors.CARD_BORDER};
                color: {Colors.MEDIUM_TEXT};
            }}
        """)
        self.setStatusBar(self.status_bar)
        
        self.status_label = QLabel("Ready")
        self.status_bar.addWidget(self.status_label)
        
        self.status_bar.addPermanentWidget(QLabel("WIT Barcode System v1.0.0"))
    
    def _setup_timers(self):
        # DateTime timer
        self.datetime_timer = QTimer()
        self.datetime_timer.timeout.connect(self._update_datetime)
        self.datetime_timer.start(1000)
        self._update_datetime()
        
        # DB connection check timer
        self.db_check_timer = QTimer()
        self.db_check_timer.timeout.connect(self._check_db_connection)
        self.db_check_timer.start(30000)
        # Probe once at startup so the indicator never claims "Connected"
        # before the connection has actually been verified.
        QTimer.singleShot(0, self._check_db_connection)
    
    def _focus_global_search(self):
        """Put the caret in the quick-search field and select what is there."""
        self.global_search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.global_search.selectAll()

    def _run_global_search(self):
        """Open the student list filtered by the quick-search text.

        A reference number that names one student -- the stored form, the
        zero-padded barcode payload, or an alias printed on an older panel --
        opens that student's record instead, because filtering a list to a single
        row and making the operator click it is a pointless extra step.

        Anything else filters the Students page, which already debounces and
        reports the result count, so this only hands the query over rather than
        searching twice.
        """
        query = self.global_search.text().strip()
        if not query:
            self._focus_global_search()
            return

        if self._open_reference_if_it_names_a_student(query):
            return

        self.navigate_to("students")
        page = self.pages.get("students")
        if page is not None and hasattr(page, "apply_search"):
            page.apply_search(query)

    def _open_reference_if_it_names_a_student(self, query: str) -> bool:
        """Route a single-student reference to the certificate page."""
        from services.identifiers import matches_reference, normalise_reference

        if not normalise_reference(query).isdigit():
            return False

        try:
            students = self.student_service.search_students(query=query, limit=50)
        except DatabaseUnavailableError:
            return False

        if not students:
            return False

        student = next((row for row in students if matches_reference(row, query)), None)
        if student is None or len(students) > 1:
            # Ambiguous: several students share the digits. Let the list show them.
            return False

        self.navigate_to("certificate", context=student)
        page = self.pages.get("certificate")
        if page is not None and hasattr(page, "load_reference"):
            lookup = page.load_reference(student.reference_number)
            return lookup.student is not None
        return True

    def _update_datetime(self):
        now = QDateTime.currentDateTime()
        self.datetime_label.setText(now.toString("MMM d, yyyy • hh:mm AP"))
    
    def _check_db_connection(self):
        from database.connection import test_connection
        if test_connection():
            self.db_status.setText("● Database: Connected")
            self.db_status.setStyleSheet(f"color: {Colors.SUCCESS_GREEN};")
        else:
            self.db_status.setText("● Database: Disconnected")
            self.db_status.setStyleSheet(f"color: {Colors.ERROR_RED};")
    
    def navigate_to(self, page_id: str, context: object = None):
        """Open a page, optionally preselecting a student.

        Parameters
        ----------
        page_id:
            Key of :data:`PAGE_CLASSES`, as emitted by the sidebar.
        context:
            Optional routing hint. A ``Student`` is forwarded to the page's
            ``preload_student`` hook, which re-reads the record from the database
            before displaying it -- the object is never trusted as the source of
            the data itself.
        """
        if page_id == "logout":
            self._logout()
            return

        if page_id in self.pages:
            page = self.pages[page_id]
        else:
            page_class = PAGE_CLASSES.get(page_id)
            if not page_class:
                logger.warning(f"Unknown page: {page_id}")
                return
            
            # Create page with appropriate services
            if page_id == "dashboard":
                page = page_class(self.student_service, self.barcode_service)
            elif page_id == "students":
                page = page_class(self.student_service)
            elif page_id == "scanner":
                page = page_class(self.student_service)
            elif page_id == "generator":
                page = page_class(self.student_service, self.barcode_service)
            elif page_id == "enrollment":
                page = page_class(self.student_service)
            elif page_id == "assessment":
                page = page_class(self.student_service, self.assessment_service)
            elif page_id == "reports":
                page = page_class(self.student_service, self.report_service)
            elif page_id == "courses":
                page = page_class(self.student_service)
            elif page_id == "certificate":
                page = page_class(self.student_service, self.assessment_service, self.report_service)
            elif page_id == "training":
                page = page_class()
            elif page_id == "settings":
                page = page_class()
            else:
                page = page_class()
            
            try:
                self.pages[page_id] = page
                self.page_stack.addWidget(page)
            except DatabaseUnavailableError as exc:
                # Construction failed on the database; discard the half-built
                # widget so the window stays usable and the user can retry.
                logger.warning("Page '%s' could not be initialised: %s", page_id, exc)
                page.deleteLater()
                show_db_error(self, exc, retry_callback=lambda: self.navigate_to(page_id))
                self.page_stack.setCurrentIndex(max(0, self.page_stack.count() - 1))
                self.status_label.setText(
                    f"Could not open {PAGE_TITLES.get(page_id, page_id.title())}"
                )
                return
        
        self.page_stack.setCurrentWidget(page)
        self.current_page = page_id
        self.sidebar.set_active(page_id)

        # The scanner raises cross-page requests (open Assessment & Fees for the
        # student just verified). Connecting here rather than at construction
        # keeps the scanner page unaware of the window's routing.
        #
        # The connection is made exactly once per scanner instance. PySide6's
        # SignalInstance has no `receivers()` method, so the "already connected?"
        # question is answered by an explicit flag instead; querying the signal
        # raised AttributeError, which meant navigating to the scanner page
        # always failed and the page was unreachable from the sidebar.
        if isinstance(page, ScannerPage) and not self._scanner_connected:
            page.navigate_requested.connect(self._on_scanner_navigation_request)
            self._scanner_connected = True

        # Update top bar title.
        # The active page name is intentionally NOT appended here: the sidebar
        # already highlights it and each page renders its own header, so
        # appending it only crowded the title and clipped it on narrow windows.
        self.current_page_title = PAGE_TITLES.get(page_id, page_id.title())
        
        # Refresh page if it has refresh method
        if hasattr(page, 'refresh'):
            try:
                page.refresh()
            except DatabaseUnavailableError as exc:
                logger.warning("Refresh of '%s' failed: %s", page_id, exc)
                show_db_error(self, exc, retry_callback=page.refresh)

        self._apply_navigation_context(page, context)

        self.status_label.setText(f"Viewing: {self.current_page_title}")

    def _on_scanner_navigation_request(self, page_id: str, student) -> None:
        """Handle a request raised by the scanner after a verified scan."""
        if student is None:
            return
        logger.info(
            "Scanner requested '%s' for reference %s", page_id, student.reference_number
        )
        self.navigate_to(page_id, context=student)

    def _apply_navigation_context(self, page, context):
        """Hand a routing hint to a page that knows how to consume one."""
        if context is None:
            return
        preload = getattr(page, "preload_student", None)
        if not callable(preload):
            logger.debug(
                "Page %s does not support navigation context", type(page).__name__
            )
            return
        try:
            preload(context)
        except DatabaseUnavailableError as exc:
            logger.warning("Preloading a student on '%s' failed: %s", page.objectName(), exc)
            show_db_error(self, exc, retry_callback=lambda: preload(context))
    
    def show_student_dialog(self, student_id: int = None):
        if student_id:
            student = self.student_service.get_student(student_id)
            dialog = StudentDialog(student=student, parent=self)
        else:
            dialog = StudentDialog(parent=self)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            student = dialog.get_student()
            try:
                if student.id:
                    self.student_service.update_student(student)
                else:
                    self.student_service.create_student(student)
                
                # Refresh current page if it's students or dashboard
                if self.current_page in self.pages:
                    page = self.pages[self.current_page]
                    if hasattr(page, 'refresh'):
                        page.refresh()
                
                QMessageBox.information(self, "Success", "Student saved successfully!")
            except Exception as e:
                logger.error(f"Save student error: {e}")
                QMessageBox.critical(self, "Error", f"Failed to save student: {e}")
    
    def _logout(self):
        reply = QMessageBox.question(
            self, "Logout",
            "Are you sure you want to logout?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            QMessageBox.information(self, "Logout", "Logout functionality to be implemented.")
            # Could close window or show login screen
    
    def shutdown(self) -> None:
        """Release everything the pages own, once.

        Called from :meth:`closeEvent` and from ``QApplication.aboutToQuit``.
        Pages that own threads or hardware (the scanner's worker, the camera, the
        audio cue) must be told to stop before the interpreter tears the process
        down, or Qt aborts with "QThread: Destroyed while thread is still
        running". Idempotent, so a window closed and then a quit signal both
        calling it is safe.
        """
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True

        for page_id, page in list(self.pages.items()):
            closer = getattr(page, "shutdown", None)
            if callable(closer):
                try:
                    closer()
                    continue
                except Exception:  # noqa: BLE001 - teardown must continue
                    logger.exception("Shutdown of page '%s' failed", page_id)

            close_event = getattr(page, "closeEvent", None)
            if callable(close_event):
                try:
                    from PySide6.QtGui import QCloseEvent

                    close_event(QCloseEvent())
                except Exception:  # noqa: BLE001 - teardown must continue
                    logger.exception("closeEvent of page '%s' failed", page_id)

        logger.info("Window shutdown complete")

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
