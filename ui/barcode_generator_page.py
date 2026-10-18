from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFrame, QMessageBox, QFileDialog, QProgressBar, QGroupBox, QApplication
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, Slot
from PySide6.QtGui import QPixmap, QFont
from styles.typography import get_open_sauce_family, get_agrandir_family, body_font, body_bold_font, section_heading_font, page_heading_font, small_label_font, button_font, monospace_font
from styles.colors import Colors
from database.connection import DatabaseUnavailableError
from services.student_service import StudentService
from ui.db_error import show_db_error
from ui.widgets.responsive_table import ColumnSpec, configure_table
from services.barcode_generator import BarcodeGeneratorService
from config.settings import settings
from models.student import Student
from models.barcode import Barcode
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class BarcodeGenerateWorker(QThread):
    """Generates barcodes off the GUI thread and reports progress.

    Generation writes an image per student and commits a row, so a bulk run over
    hundreds of students is seconds of work. It ran on the GUI thread, which meant
    the progress bar never repainted (a queued repaint cannot happen while the
    thread that would process it is busy) and the window looked frozen for the
    whole run.
    """

    progress = Signal(int, int)
    #: (created, already_present, failed) counts.
    completed = Signal(int, int, int)
    failed = Signal(str)

    def __init__(self, service: BarcodeGeneratorService, student_ids: list, parent=None):
        super().__init__(parent)
        self._service = service
        self._student_ids = list(student_ids)

    def run(self) -> None:
        created = 0
        existing = 0
        failed = 0
        total = len(self._student_ids)
        for index, student_id in enumerate(self._student_ids, start=1):
            try:
                # `generate_for_student` returns an existing barcode rather than
                # creating a second one, so "returned something" is not the same
                # as "created one". They are counted apart because a page that
                # reports "all generated" after creating nothing is lying.
                already = self._service.get_student_barcode(student_id) is not None
                barcode = self._service.generate_for_student(student_id)
                if barcode is None:
                    failed += 1
                elif already:
                    existing += 1
                else:
                    created += 1
            except DatabaseUnavailableError as exc:
                logger.warning("Generation stopped: database unavailable (%s)", exc)
                self.failed.emit(str(exc))
                return
            except Exception as exc:  # noqa: BLE001 - counted, not hidden
                logger.exception("Barcode generation failed for student %s", student_id)
                failed += 1
            self.progress.emit(index, total)
        self.completed.emit(created, existing, failed)


class BarcodeGeneratorPage(QWidget):
    def __init__(self, student_service: StudentService, barcode_service: BarcodeGeneratorService, parent=None):
        super().__init__(parent)
        self.student_service = student_service
        self.barcode_service = barcode_service
        self._all_students = []
        self._barcode_status_unavailable = False
        self._worker: BarcodeGenerateWorker | None = None
        self._barcode_status_unavailable = False

        self.setObjectName("barcodeGeneratorPage")
        self._setup_ui()
        self._load_students()
    
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
        
        title = QLabel("Barcode Generator")
        title.setFont(page_heading_font(24))
        title.setStyleSheet("color: white;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        main_layout.addWidget(header)
        
        # Content
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.setSpacing(24)
        
        # Left panel - Student list
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
        
        # Search
        search_layout = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search students...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_students)
        search_layout.addWidget(QLabel("Search:"))
        search_layout.addWidget(self.search_edit)
        left_layout.addLayout(search_layout)
        
        # Table
        self.table = QTableWidget()
        # The shared sizing rules: content-sized columns with a floor, one
        # flexible column. Stretching all six equally is what reduced every name
        # to "Steven ..." and every program to "Computer ...".
        configure_table(
            self.table,
            [
                ColumnSpec("", width=34),
                ColumnSpec("Student ID", width=122),
                ColumnSpec("Reference", width=92),
                ColumnSpec("Name", width=178),
                ColumnSpec("Program", width=150, stretch=True),
                ColumnSpec("Barcode", width=110),
            ],
        )
        if self.table.horizontalHeaderItem(0) is not None:
            self.table.horizontalHeaderItem(0).setText("")
        self.table.horizontalHeaderItem(5).setToolTip(
            "Whether an active barcode image is on file for this student"
        )
        
        left_layout.addWidget(self.table)
        
        # Bulk actions
        bulk_layout = QHBoxLayout()
        self.generate_selected_btn = QPushButton("Generate Selected")
        self.generate_selected_btn.clicked.connect(self._generate_selected)
        
        self.generate_all_btn = QPushButton("Generate All Missing")
        self.generate_all_btn.setProperty("secondary", "true")
        self.generate_all_btn.clicked.connect(self._generate_all_missing)
        
        bulk_layout.addWidget(self.generate_selected_btn)
        bulk_layout.addWidget(self.generate_all_btn)
        left_layout.addLayout(bulk_layout)
        
        content_layout.addWidget(left_panel, 2)
        
        # Right panel - Barcode preview
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
        right_layout.setContentsMargins(20, 20, 20, 20)
        right_layout.setSpacing(20)
        
        # Selected student info
        self.selected_label = QLabel("Select a student to preview barcode")
        self.selected_label.setFont(body_font(13))
        self.selected_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        self.selected_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selected_label.setWordWrap(True)
        right_layout.addWidget(self.selected_label)
        
        # Barcode display
        self.barcode_frame = QFrame()
        self.barcode_frame.setObjectName("BarcodeFrameSurface")
        self.barcode_frame.setStyleSheet(f"""
            QFrame#BarcodeFrameSurface {{
                background-color: {Colors.WHITE};
                border: 2px solid {Colors.CREAM};
                border-radius: 8px;
            }}
        """)
        self.barcode_frame.setMinimumHeight(200)
        barcode_layout = QVBoxLayout(self.barcode_frame)
        barcode_layout.setContentsMargins(20, 20, 20, 20)
        barcode_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.barcode_value_label = QLabel()
        self.barcode_value_label.setFont(monospace_font(18))
        self.barcode_value_label.setStyleSheet(f"color: {Colors.DARK_TEXT}; letter-spacing: 2px;")
        self.barcode_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.barcode_image_label = QLabel()
        self.barcode_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.barcode_image_label.setStyleSheet(f"background: white;")
        
        barcode_layout.addWidget(self.barcode_value_label)
        barcode_layout.addWidget(self.barcode_image_label)
        right_layout.addWidget(self.barcode_frame)
        
        # Actions
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(12)
        
        self.print_btn = QPushButton("Print")
        self.print_btn.setProperty("secondary", "true")
        self.print_btn.clicked.connect(self._print_barcode)
        self.print_btn.setEnabled(False)
        
        self.save_btn = QPushButton("Save Image")
        self.save_btn.setProperty("secondary", "true")
        self.save_btn.clicked.connect(self._save_barcode)
        self.save_btn.setEnabled(False)
        
        self.regenerate_btn = QPushButton("Regenerate")
        self.regenerate_btn.clicked.connect(self._regenerate_barcode)
        self.regenerate_btn.setEnabled(False)
        
        actions_layout.addStretch()
        actions_layout.addWidget(self.print_btn)
        actions_layout.addWidget(self.save_btn)
        actions_layout.addWidget(self.regenerate_btn)
        
        right_layout.addLayout(actions_layout)
        right_layout.addStretch()
        
        content_layout.addWidget(right_panel, 1)
        main_layout.addWidget(content)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {Colors.CREAM};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background-color: {Colors.ORANGE};
                border-radius: 2px;
            }}
        """)
        main_layout.addWidget(self.progress_bar)
        
        # Status
        self.status_label = QLabel("Ready")
        self.status_label.setFont(small_label_font(11))
        self.status_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT}; padding: 8px 16px; background: {Colors.CREAM}; border-top: 1px solid {Colors.CARD_BORDER};")
        main_layout.addWidget(self.status_label)
        
        # Connect table selection
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemChanged.connect(self._on_item_changed)
    
    def _load_students(self):
        try:
            self._all_students = self.student_service.search_students(limit=1000)
        except DatabaseUnavailableError as exc:
            self._all_students = []
            self.table.setRowCount(0)
            self.status_label.setText("Student list unavailable — database not connected")
            show_db_error(self, exc, retry_callback=self._load_students)
            return
        self._populate_table(self._all_students)
    
    def _populate_table(self, students: list[Student]):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for student in students:
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            # Checkbox
            checkbox = QTableWidgetItem()
            checkbox.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            checkbox.setCheckState(Qt.CheckState.Unchecked)
            checkbox.setData(Qt.ItemDataRole.UserRole, student.id)
            self.table.setItem(row, 0, checkbox)
            
            self.table.setItem(row, 1, QTableWidgetItem(student.student_id))
            self.table.setItem(row, 2, QTableWidgetItem(student.reference_number))
            self.table.setItem(row, 3, QTableWidgetItem(student.full_name))
            self.table.setItem(row, 4, QTableWidgetItem(student.program))
            
            # Barcode status. Read through the service so the page never talks
            # to the repository directly, and every failure has somewhere to be
            # reported: an N+1 query per row that raises on the second row would
            # otherwise abort the whole table silently.
            try:
                barcode = self.barcode_service.get_student_barcode(student.id)
            except DatabaseUnavailableError as exc:
                logger.warning("Barcode lookup failed for student %s: %s", student.id, exc)
                self.status_label.setText("Barcode status unavailable — database not connected")
                show_db_error(self, exc, retry_callback=self._load_students)
                barcode = None
                self._barcode_status_unavailable = True
            except Exception as exc:  # noqa: BLE001 - reported, not hidden
                logger.exception("Barcode lookup failed for student %s", student.id)
                self.status_label.setText(f"Barcode status unavailable: {exc}")
                barcode = None
                self._barcode_status_unavailable = True
            status_text = "✓ Generated" if barcode else "✗ Missing"
            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if barcode:
                status_item.setForeground(Colors.qcolor(Colors.SUCCESS_GREEN))
            else:
                status_item.setForeground(Colors.qcolor(Colors.ORANGE))
            self.table.setItem(row, 5, status_item)
        
        self.table.blockSignals(False)

    def _filter_students(self, text: str):
        """Hide non-matching rows.

        Rows are hidden rather than repopulated. Repopulating re-issued one
        barcode query per student on every keystroke, so typing a six-letter name
        ran thirty database round trips and rebuilt the whole table six times.
        """
        needle = text.lower().strip()
        visible = 0
        for row in range(self.table.rowCount()):
            match = not needle
            if needle:
                for column in (1, 2, 3, 4):
                    item = self.table.item(row, column)
                    if item and needle in item.text().lower():
                        match = True
                        break
            self.table.setRowHidden(row, not match)
            visible += 1 if match else 0

        if self._all_students:
            if visible:
                self.status_label.setText(
                    f"Showing {visible} of {len(self._all_students)} students"
                    if needle
                    else f"{len(self._all_students)} students on file"
                )
            else:
                self.status_label.setText(f"No student matches “{text.strip()}”")
                self.status_label.setStyleSheet(
                    f"color: {Colors.WARNING_ORANGE}; padding: 8px 16px;"
                )
    
    def _on_selection_changed(self):
        """Preview the highlighted student's barcode, or clear the panel.

        Deselecting used to leave the previous student's name and payload on
        screen, so the panel described a student who was no longer selected.
        """
        selected = self.table.selectedItems()
        if not selected:
            self.selected_label.setText("Select a student to preview their barcode")
            self.selected_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
            self.barcode_value_label.setText("")
            self.barcode_image_label.setPixmap(QPixmap())
            self.barcode_image_label.setVisible(False)
            self._set_busy(False)
            return

        row = selected[0].row()
        checkbox = self.table.item(row, 0)
        if checkbox:
            student_id = checkbox.data(Qt.ItemDataRole.UserRole)
            if student_id is not None:
                self._show_barcode_preview(student_id)
    
    def _on_item_changed(self, item):
        """Keep the "generate selected" button honest about the checkboxes.

        This slot was connected to ``itemChanged`` and did nothing at all, so the
        button stayed enabled with nothing ticked -- clicking it then produced a
        "select at least one student" message the operator could have been spared.
        """
        if item.column() != 0:
            return
        count = len(self._get_selected_student_ids())
        self.generate_selected_btn.setEnabled(count > 0)
        self.generate_selected_btn.setText(
            f"Generate Selected ({count})" if count else "Generate Selected"
        )
    
    def _show_barcode_preview(self, student_id: int):
        """Show one student's barcode, or explain what is missing.

        Every branch resets the panel first: when the lookup returned nothing the
        previous student's name and payload stayed on screen next to the new
        state, which is how a barcode could be attributed to the wrong student.
        """
        self.barcode_image_label.setVisible(False)
        self.barcode_image_label.setPixmap(QPixmap())
        self.print_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.regenerate_btn.setEnabled(False)

        try:
            student = self.student_service.get_student(student_id)
            barcode = self.barcode_service.get_student_barcode(student_id)
        except DatabaseUnavailableError as exc:
            logger.warning("Barcode preview failed for student %s: %s", student_id, exc)
            self.selected_label.setText("Barcode information unavailable")
            self.selected_label.setStyleSheet(f"color: {Colors.ERROR_RED};")
            show_db_error(self, exc, retry_callback=lambda: self._show_barcode_preview(student_id))
            return
        except Exception as exc:  # noqa: BLE001 - reported on screen
            logger.exception("Barcode preview failed for student %s", student_id)
            self.selected_label.setText(f"Barcode information unavailable: {exc}")
            self.selected_label.setStyleSheet(f"color: {Colors.ERROR_RED};")
            return

        if student is None:
            self.selected_label.setText("That student is no longer on file")
            self.selected_label.setStyleSheet(f"color: {Colors.ERROR_RED};")
            self._load_students()
            return

        self.selected_label.setText(
            f"{student.full_name}\n{student.student_id} • {student.reference_number}"
        )
        self.selected_label.setStyleSheet(f"color: {Colors.DARK_TEXT};")

        if barcode is None:
            self.barcode_value_label.setText("No barcode generated")
            self.selected_label.setText(
                f"{self.selected_label.text()}\nNo barcode has been generated for this student yet."
            )
            self.selected_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
            return

        self.barcode_value_label.setText(barcode.barcode_value)
        self.print_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        self.regenerate_btn.setEnabled(True)

        # Project-root resolution, not the working directory: a stored relative
        # path must resolve the same way whatever directory the app was launched
        # from.
        image_path = settings.resolve_asset(barcode.image_path)
        if image_path is None:
            self.selected_label.setText(
                f"{self.selected_label.text()}\n"
                f"The barcode image file ({barcode.image_path or 'no path recorded'}) "
                f"could not be found. The payload above is still valid."
            )
            self.selected_label.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
            logger.warning(
                "Barcode image for %s not found at %r", student.reference_number,
                barcode.image_path,
            )
            return

        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            self.selected_label.setText(
                f"{self.selected_label.text()}\n"
                f"The barcode image at {image_path.name} could not be decoded."
            )
            self.selected_label.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
            logger.warning("Barcode image could not be decoded: %s", image_path)
            return

        self.barcode_image_label.setPixmap(
            pixmap.scaledToWidth(300, Qt.TransformationMode.SmoothTransformation)
        )
        self.barcode_image_label.setVisible(True)
    
    def _get_selected_student_ids(self) -> list:
        ids = []
        for row in range(self.table.rowCount()):
            checkbox = self.table.item(row, 0)
            if checkbox and checkbox.checkState() == Qt.CheckState.Checked:
                student_id = checkbox.data(Qt.ItemDataRole.UserRole)
                if student_id:
                    ids.append(student_id)
        return ids
    
    def _generate_selected(self):
        student_ids = self._get_selected_student_ids()
        if not student_ids:
            QMessageBox.information(
                self, "No Selection",
                "Tick the students whose barcodes you want to generate.",
            )
            return
        self._start_generation(student_ids)

    def _generate_all_missing(self):
        """Generate for every student without an active barcode, after confirming.

        This writes a file and a database row per student, so it asks first and
        names how many records it is about to touch.
        """
        if self._barcode_status_unavailable:
            QMessageBox.warning(
                self, "Cannot check barcodes",
                "The current barcode status could not be read, so the list of "
                "students needing one is unknown. Retry the page first.",
            )
            return

        try:
            missing = [
                student.id for student in self._all_students
                if self.barcode_service.get_student_barcode(student.id) is None
            ]
        except DatabaseUnavailableError as exc:
            show_db_error(self, exc, retry_callback=self._generate_all_missing)
            return

        if not missing:
            QMessageBox.information(
                self, "Nothing to generate",
                f"All {len(self._all_students)} students on file already have an active barcode.",
            )
            return

        from ui.dialogs.confirm_dialog import ConfirmDialog

        dialog = ConfirmDialog(
            "Generate Missing Barcodes",
            f"Generate a barcode for {len(missing)} student(s) that do not have one?\n\n"
            "Each barcode is written as an image and recorded against the student.",
            "Generate", "Cancel",
            parent=self,
        )
        try:
            if dialog.exec() != ConfirmDialog.DialogCode.Accepted:
                return
        finally:
            dialog.deleteLater()

        self._start_generation(missing)

    def _start_generation(self, student_ids: list):
        """Generate in a worker thread, with the buttons locked for the run."""
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(
                self, "Already running",
                "A barcode run is still in progress. Wait for it to finish.",
            )
            return

        self._set_busy(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(student_ids))
        self.progress_bar.setValue(0)
        self.status_label.setText(f"Generating {len(student_ids)} barcode(s)…")

        self._worker = BarcodeGenerateWorker(self.barcode_service, student_ids, self)
        self._worker.progress.connect(self._on_generation_progress)
        self._worker.completed.connect(self._on_generation_completed)
        self._worker.failed.connect(self._on_generation_failed)
        self._worker.finished.connect(self._on_generation_finished)
        self._worker.start()

    @Slot(int, int)
    def _on_generation_progress(self, current: int, total: int) -> None:
        self.progress_bar.setValue(current)
        self.status_label.setText(f"Generating… {current} of {total}")

    @Slot(int, int, int)
    def _on_generation_completed(self, created: int, existing: int, failed: int) -> None:
        self.progress_bar.setVisible(False)
        total = created + existing + failed
        self.status_label.setText(
            f"{created} barcode(s) created, {failed} failed"
            + (f", {existing} already existed" if existing else "")
        )
        self._load_students()

        if failed:
            QMessageBox.warning(
                self, "Finished with errors",
                f"{created} of {total} barcode(s) were created.\n\n"
                f"{failed} could not be created; see the log for the reasons.",
            )
            return
        if created == 0:
            QMessageBox.information(
                self, "Nothing created",
                f"None of the {total} student(s) needed a new barcode: they already had one.",
            )
            return
        QMessageBox.information(
            self, "Barcodes generated",
            f"{created} barcode image(s) were written to "
            f"{settings.BARCODE_OUTPUT_DIR} and recorded against the students.",
        )

    @Slot(str)
    def _on_generation_failed(self, error: str) -> None:
        self.progress_bar.setVisible(False)
        self.status_label.setText("Generation stopped — the database is unavailable")
        QMessageBox.critical(
            self, "Generation stopped",
            f"The database became unavailable, so generation stopped part-way:\n\n{error}",
        )

    @Slot()
    def _on_generation_finished(self) -> None:
        self._set_busy(False)
        self._worker = None

    def _set_busy(self, busy: bool) -> None:
        """Lock every control that could start or mutate a second run."""
        self.generate_selected_btn.setEnabled(not busy)
        self.generate_all_btn.setEnabled(not busy)
        # Print / Save / Regenerate stay disabled too: they act on the selected
        # student's barcode and were left live during a run, so a click could
        # rewrite the file being generated.
        if busy:
            for button in (self.print_btn, self.save_btn, self.regenerate_btn):
                button.setEnabled(False)
        elif self.barcode_value_label.text() not in ("", "No barcode generated"):
            for button in (self.print_btn, self.save_btn, self.regenerate_btn):
                button.setEnabled(True)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor) if busy else QApplication.restoreOverrideCursor()

    def _selected_student_id(self) -> int | None:
        selected = self.table.selectedItems()
        if not selected:
            return None
        checkbox = self.table.item(selected[0].row(), 0)
        return checkbox.data(Qt.ItemDataRole.UserRole) if checkbox else None

    def _print_barcode(self):
        student_id = self._selected_student_id()
        if student_id is None:
            QMessageBox.information(
                self, "No student selected",
                "Highlight a student row to print their barcode.",
            )
            return

        try:
            barcode = self.barcode_service.get_student_barcode(student_id)
        except DatabaseUnavailableError as exc:
            show_db_error(self, exc, retry_callback=self._print_barcode)
            return

        if barcode is None:
            QMessageBox.information(
                self, "No barcode",
                "That student has no barcode on file. Generate one first.",
            )
            return

        if not self.barcode_service.print_barcode(barcode):
            QMessageBox.warning(
                self, "Could not print the barcode",
                f"The barcode image for {barcode.barcode_value} could not be opened. "
                f"Check that {barcode.image_path or 'the recorded image path'} exists.",
            )
            return

        # Say what actually happened: the service opens the image in the desktop
        # viewer, which is where printing is done from. The previous message
        # claimed "sent to printer".
        QMessageBox.information(
            self, "Barcode opened",
            f"The barcode image for {barcode.barcode_value} was opened in the image "
            f"viewer. Print it from there, or use Save Image to write a copy.",
        )

    def _save_barcode(self):
        student_id = self._selected_student_id()
        if student_id is None:
            QMessageBox.information(
                self, "No student selected",
                "Highlight a student row to save their barcode image.",
            )
            return

        try:
            barcode = self.barcode_service.get_student_barcode(student_id)
        except DatabaseUnavailableError as exc:
            show_db_error(self, exc, retry_callback=self._save_barcode)
            return

        if barcode is None:
            QMessageBox.information(
                self, "No barcode",
                "That student has no barcode on file. Generate one first.",
            )
            return

        suggested = settings.BARCODE_OUTPUT_DIR / f"barcode_{barcode.barcode_value}.png"
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Barcode", str(suggested), "PNG Images (*.png)"
        )
        if not file_path:
            return

        if not self.barcode_service.export_barcode(barcode, Path(file_path)):
            QMessageBox.warning(
                self, "Save failed",
                f"The barcode image could not be written to:\n{file_path}\n\n"
                f"Check that the folder exists and is writable, and that the "
                f"recorded image ({barcode.image_path or 'no path'}) is present.",
            )
            return

        QMessageBox.information(
            self, "Saved", f"The barcode image was written to:\n{file_path}"
        )

    def _regenerate_barcode(self):
        student_id = self._selected_student_id()
        if student_id is None:
            QMessageBox.information(
                self, "No student selected",
                "Highlight a student row to regenerate their barcode.",
            )
            return

        try:
            barcode = self.barcode_service.get_student_barcode(student_id)
        except DatabaseUnavailableError as exc:
            show_db_error(self, exc, retry_callback=self._regenerate_barcode)
            return

        if barcode is None:
            QMessageBox.information(
                self, "No barcode",
                "That student has no barcode on file, so there is nothing to regenerate.",
            )
            return

        from ui.dialogs.confirm_dialog import ConfirmDialog

        dialog = ConfirmDialog(
            "Regenerate Barcode",
            f"Create a new barcode image for {barcode.barcode_value}?\n\n"
            "The stored payload stays the same -- this reprints the image and records "
            "the regeneration in the audit columns.",
            "Regenerate", "Cancel",
            parent=self,
        )
        try:
            confirmed = dialog.exec() == ConfirmDialog.DialogCode.Accepted
        finally:
            dialog.deleteLater()
        if not confirmed:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                new_barcode = self.barcode_service.regenerate_barcode(
                    barcode.id, settings.DEFAULT_USER_ID
                )
            except DatabaseUnavailableError as exc:
                show_db_error(self, exc, retry_callback=self._regenerate_barcode)
                return
            except Exception as exc:  # noqa: BLE001 - reported to the operator
                logger.exception("Barcode regeneration failed")
                QMessageBox.critical(
                    self, "Regeneration failed",
                    f"The barcode was not regenerated.\n\n{exc}",
                )
                return
        finally:
            QApplication.restoreOverrideCursor()

        if new_barcode is None:
            QMessageBox.warning(
                self, "Regeneration failed",
                "The database reported that no barcode was regenerated. Nothing changed.",
            )
            return

        QMessageBox.information(
            self, "Barcode regenerated",
            f"The barcode image for {new_barcode.barcode_value} was rewritten and "
            f"recorded.",
        )
        self._show_barcode_preview(student_id)
        self._load_students()

    def shutdown(self) -> None:
        """Stop a running generation before the page is destroyed."""
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        try:
            if not worker.wait(5000):
                logger.warning("Barcode generation worker was still running at shutdown")
        except Exception:  # noqa: BLE001 - teardown must not raise
            logger.exception("Barcode generation shutdown failed")
