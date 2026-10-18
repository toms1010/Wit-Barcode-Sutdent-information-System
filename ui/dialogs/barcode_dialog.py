from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFrame, QMessageBox, QProgressBar
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QFont
from styles.typography import get_open_sauce_family, get_agrandir_family, body_font, body_bold_font, section_heading_font, page_heading_font, small_label_font, button_font, monospace_font
from styles.colors import Colors
from models.student import Student
from services.barcode_generator import BarcodeGeneratorService
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class BarcodeGenerateThread(QThread):
    progress = Signal(int, int)
    finished = Signal(list)
    error = Signal(str)
    
    def __init__(self, generator: BarcodeGeneratorService, student_ids: list):
        super().__init__()
        self.generator = generator
        self.student_ids = student_ids
    
    def run(self):
        try:
            results = []
            total = len(self.student_ids)
            for i, student_id in enumerate(self.student_ids):
                barcode = self.generator.generate_for_student(student_id)
                results.append((student_id, barcode))
                self.progress.emit(i + 1, total)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class BarcodeDialog(QDialog):
    def __init__(self, student_service, barcode_service, parent=None):
        super().__init__(parent)
        self.student_service = student_service
        self.barcode_service = barcode_service
        
        self.setWindowTitle("Barcode Generator")
        self.setModal(True)
        self.setMinimumSize(800, 600)
        self.resize(900, 700)
        
        self._setup_ui()
        self._load_students()
    
    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Header
        header = QFrame()
        header.setObjectName("barcodeDialogHeader")
        header.setFixedHeight(70)
        # Scoped by object name so the rule does not cascade onto the title
        # label inside the header (Qt applies a parent's QFrame rule to its
        # children), which framed the heading in a hairline box.
        header.setStyleSheet(f"""
            QFrame#barcodeDialogHeader {{
                background-color: {Colors.ORANGE};
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }}
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 24, 0)
        
        title = QLabel("Barcode Generator")
        title.setFont(page_heading_font(20))
        title.setStyleSheet("color: white;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        main_layout.addWidget(header)
        
        # Toolbar
        toolbar = QFrame()
        toolbar.setStyleSheet(f"background-color: {Colors.WHITE}; border-bottom: 1px solid {Colors.CARD_BORDER};")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(16, 12, 16, 12)
        toolbar_layout.setSpacing(12)
        
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search students...")
        self.search_edit.setFixedWidth(300)
        self.search_edit.textChanged.connect(self._filter_students)
        
        self.generate_selected_btn = QPushButton("Generate Selected")
        self.generate_selected_btn.clicked.connect(self._generate_selected)
        
        self.generate_all_btn = QPushButton("Generate All")
        self.generate_all_btn.setProperty("secondary", "true")
        self.generate_all_btn.clicked.connect(self._generate_all)
        
        toolbar_layout.addWidget(QLabel("Search:"))
        toolbar_layout.addWidget(self.search_edit)
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(self.generate_selected_btn)
        toolbar_layout.addWidget(self.generate_all_btn)
        
        main_layout.addWidget(toolbar)
        
        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "✓", "Student ID", "Reference No.", "Name", "Program", "Year Level", "Barcode Status"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 50)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        # These three calls referenced a bare `table` name that does not exist
        # in this scope: constructing the dialog raised NameError before any of
        # its controls could be used.
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        
        main_layout.addWidget(self.table)
        
        # Progress bar (hidden initially)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {Colors.CREAM};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background-color: {Colors.ORANGE};
                border-radius: 3px;
            }}
        """)
        main_layout.addWidget(self.progress_bar)
        
        # Status label
        self.status_label = QLabel("Ready")
        self.status_label.setFont(small_label_font(11))
        self.status_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT}; padding: 8px 16px;")
        main_layout.addWidget(self.status_label)
        
        self._all_students = []
    
    def _load_students(self):
        self._all_students = self.student_service.search_students(limit=1000)
        self._populate_table(self._all_students)
    
    def _populate_table(self, students: list):
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
            
            # Student ID
            self.table.setItem(row, 1, QTableWidgetItem(student.student_id))
            
            # Reference Number
            self.table.setItem(row, 2, QTableWidgetItem(student.reference_number))
            
            # Name
            self.table.setItem(row, 3, QTableWidgetItem(student.full_name))
            
            # Program
            self.table.setItem(row, 4, QTableWidgetItem(student.program))
            
            # Year Level
            self.table.setItem(row, 5, QTableWidgetItem(student.year_level.value if hasattr(student.year_level, 'value') else str(student.year_level)))
            
            # Barcode Status
            barcode = self.barcode_service.get_student_barcode(student.id)
            status_item = QTableWidgetItem("Generated" if barcode else "Not Generated")
            if barcode:
                status_item.setForeground(Colors.qcolor(Colors.SUCCESS_GREEN))
            else:
                status_item.setForeground(Colors.qcolor(Colors.ORANGE))
            self.table.setItem(row, 6, status_item)
    
    def _filter_students(self, text: str):
        text = text.lower().strip()
        if not text:
            filtered = self._all_students
        else:
            filtered = [
                s for s in self._all_students
                if text in s.full_name.lower() 
                or text in s.student_id.lower()
                or text in s.reference_number.lower()
                or text in s.program.lower()
            ]
        self._populate_table(filtered)
    
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
            QMessageBox.information(self, "No Selection", "Please select at least one student.")
            return
        self._start_generation(student_ids)
    
    def _generate_all(self):
        student_ids = [s.id for s in self._all_students]
        if not student_ids:
            QMessageBox.information(self, "No Students", "No students found.")
            return
        self._start_generation(student_ids)
    
    def _start_generation(self, student_ids: list):
        self.generate_selected_btn.setEnabled(False)
        self.generate_all_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(student_ids))
        self.progress_bar.setValue(0)
        self.status_label.setText(f"Generating barcodes for {len(student_ids)} student(s)...")
        
        self.thread = BarcodeGenerateThread(self.barcode_service, student_ids)
        self.thread.progress.connect(self._on_progress)
        self.thread.finished.connect(self._on_finished)
        self.thread.error.connect(self._on_error)
        self.thread.start()
    
    def _on_progress(self, current: int, total: int):
        self.progress_bar.setValue(current)
        self.status_label.setText(f"Generating... {current}/{total}")
    
    def _on_finished(self, results: list):
        self.generate_selected_btn.setEnabled(True)
        self.generate_all_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        
        success_count = sum(1 for _, barcode in results if barcode)
        self.status_label.setText(f"Completed: {success_count}/{len(results)} barcodes generated")
        
        self._load_students()
        
        if success_count < len(results):
            QMessageBox.warning(self, "Partial Success", f"{success_count} of {len(results)} barcodes generated successfully.")
        else:
            QMessageBox.information(self, "Success", f"All {success_count} barcodes generated successfully!")
    
    def _on_error(self, error: str):
        self.generate_selected_btn.setEnabled(True)
        self.generate_all_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Error occurred")
        logger.error(f"Barcode generation error: {error}")
        QMessageBox.critical(self, "Error", f"Failed to generate barcodes: {error}")