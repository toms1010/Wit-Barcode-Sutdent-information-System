from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFrame, QMessageBox, QFileDialog, QProgressBar, QGroupBox, QApplication
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, Slot
from PySide6.QtGui import QPixmap, QFont
from styles.typography import get_open_sauce_family, get_agrandir_family, body_font, body_bold_font, section_heading_font, page_heading_font, small_label_font, button_font, monospace_font, table_content_font
from styles.colors import Colors
from database.connection import DatabaseUnavailableError
from services.student_service import StudentService
from ui.db_error import show_db_error
from ui.widgets.responsive_table import ColumnSpec, configure_table
from services.barcode_generator import BarcodeGeneratorService
from config.settings import settings
from models.student import Student
from models.barcode import Barcode
from ui.widgets.profile_image import apply_portrait
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
        # Load custom fonts first
        from styles.typography import load_fonts
        load_fonts()
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # ===== HEADER =====
        header = QFrame()
        header.setFixedHeight(72)
        header.setStyleSheet(f"""
            background-color: {Colors.ORANGE};
            border-top-left-radius: 12px;
            border-top-right-radius: 12px;
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 24, 0)
        header_layout.setSpacing(16)
        
        # Title with icon area
        title_container = QWidget()
        title_layout = QHBoxLayout(title_container)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(10)
        
        # Icon placeholder (could be replaced with actual icon)
        icon_label = QLabel("▤")
        icon_label.setFont(QFont("Segoe UI Emoji", 20))
        icon_label.setStyleSheet("color: white;")
        title_layout.addWidget(icon_label)
        
        title = QLabel("Barcode Generator")
        title.setFont(page_heading_font(24))
        title.setStyleSheet("color: white;")
        title_layout.addWidget(title)
        
        header_layout.addWidget(title_container)
        header_layout.addStretch()
        
        # Student count badge in header
        self.header_count_badge = QLabel("0 students")
        self.header_count_badge.setFont(body_font(12))
        self.header_count_badge.setStyleSheet(f"""
            color: rgba(255, 255, 255, 0.9);
            background-color: rgba(255, 255, 255, 0.15);
            padding: 4px 12px;
            border-radius: 12px;
        """)
        self.header_count_badge.setVisible(False)
        header_layout.addWidget(self.header_count_badge)
        
        main_layout.addWidget(header)
        
        # ===== CONTENT AREA =====
        content = QWidget()
        content.setStyleSheet(f"background-color: {Colors.PAGE_BG};")
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(20)
        
        # ===== LEFT PANEL - Student List =====
        left_panel = self._create_card()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        
        # Panel header
        left_header = self._create_panel_header("Students", "Manage and generate barcodes")
        left_layout.addWidget(left_header)
        
        # Search section
        search_section = QWidget()
        search_section.setStyleSheet(f"background: {Colors.SURFACE_MUTED}; border-bottom: 1px solid {Colors.BORDER};")
        search_layout = QHBoxLayout(search_section)
        search_layout.setContentsMargins(16, 12, 16, 12)
        search_layout.setSpacing(10)
        
        search_label = QLabel("🔍")
        search_label.setFont(QFont("Segoe UI Emoji", 14))
        search_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        search_layout.addWidget(search_label)
        
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search by name, ID, reference, or program...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_students)
        self.search_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {Colors.INPUT_BG};
                border: 1px solid {Colors.INPUT_BORDER};
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 13px;
                color: {Colors.INPUT_TEXT};
            }}
            QLineEdit:focus {{
                border: 1px solid {Colors.INPUT_BORDER_FOCUS};
                background: {Colors.WHITE};
            }}
            QLineEdit::placeholder {{
                color: {Colors.INPUT_PLACEHOLDER};
            }}
        """)
        search_layout.addWidget(self.search_edit)
        left_layout.addWidget(search_section)
        
        # Table container
        table_container = QWidget()
        table_container.setStyleSheet("background: transparent;")
        table_container_layout = QVBoxLayout(table_container)
        table_container_layout.setContentsMargins(0, 0, 0, 0)
        table_container_layout.setSpacing(0)
        
        self.table = QTableWidget()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        
        # Enhanced table styling
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {Colors.CARD_BG};
                border: none;
                outline: none;
            }}
            QTableWidget::item {{
                padding: 10px 12px;
                border-bottom: 1px solid {Colors.TABLE_BORDER};
                color: {Colors.DARK_TEXT};
            }}
            QTableWidget::item:selected {{
                background-color: {Colors.TABLE_SELECTED};
                color: {Colors.DARK_TEXT};
            }}
            QTableWidget::item:hover:!selected {{
                background-color: {Colors.SURFACE_HOVER};
            }}
            QHeaderView::section {{
                background-color: {Colors.TABLE_HEADER_BG};
                color: {Colors.TABLE_HEADER_TEXT};
                padding: 10px 12px;
                border: none;
                border-bottom: 2px solid {Colors.ACCENT};
                font-weight: 600;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}
            QTableWidget::indicator {{
                width: 18px;
                height: 18px;
            }}
            QTableWidget::indicator:unchecked {{
                border: 2px solid {Colors.BORDER_STRONG};
                border-radius: 4px;
                background: {Colors.WHITE};
            }}
            QTableWidget::indicator:unchecked:hover {{
                border-color: {Colors.ACCENT};
            }}
            QTableWidget::indicator:checked {{
                border: 2px solid {Colors.ACCENT};
                border-radius: 4px;
                background: {Colors.ACCENT};
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTIiIGhlaWdodD0iOSIgdmlld0JveD0iMCAwIDEyIDkiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxwYXRoIGQ9Ik0xIDQuNUw0LjUgOEwxMSAxIiBzdHJva2U9IndoaXRlIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPgo8L3N2Zz4=);
            }}
        """)
        
        configure_table(
            self.table,
            [
                ColumnSpec("", width=40),
                ColumnSpec("Student ID", width=120),
                ColumnSpec("Reference", width=100),
                ColumnSpec("Name", width=200),
                ColumnSpec("Program", width=160, stretch=True),
                ColumnSpec("Barcode", width=120),
            ],
        )
        if self.table.horizontalHeaderItem(0) is not None:
            self.table.horizontalHeaderItem(0).setText("")
        self.table.horizontalHeaderItem(5).setToolTip(
            "Whether an active barcode image is on file for this student"
        )
        
        table_container_layout.addWidget(self.table)
        left_layout.addWidget(table_container, 1)
        
        # Bulk actions footer
        footer = QWidget()
        footer.setStyleSheet(f"background: {Colors.SURFACE_MUTED}; border-top: 1px solid {Colors.BORDER}; border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 12, 16, 12)
        footer_layout.setSpacing(10)
        
        self.generate_selected_btn = self._create_button("Generate Selected", primary=True)
        self.generate_selected_btn.clicked.connect(self._generate_selected)
        self.generate_selected_btn.setEnabled(False)
        
        self.generate_all_btn = self._create_button("Generate All Missing", primary=False)
        self.generate_all_btn.clicked.connect(self._generate_all_missing)
        
        footer_layout.addStretch()
        footer_layout.addWidget(self.generate_selected_btn)
        footer_layout.addWidget(self.generate_all_btn)
        left_layout.addWidget(footer)
        
        content_layout.addWidget(left_panel, 2)
        
        # ===== RIGHT PANEL - Barcode Preview =====
        right_panel = self._create_card()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        
        # Panel header
        right_header = self._create_panel_header("Barcode Preview", "Select a student to view details")
        right_layout.addWidget(right_header)
        
        # Scrollable content for right panel
        from PySide6.QtWidgets import QScrollArea
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet(f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: {Colors.SCROLLBAR_BG};
                width: 8px;
                border-radius: 4px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {Colors.SCROLLBAR_HANDLE};
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {Colors.SCROLLBAR_HANDLE_HOVER};
            }}
        """)
        
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        self.right_content_layout = QVBoxLayout(scroll_content)
        self.right_content_layout.setContentsMargins(20, 20, 20, 20)
        self.right_content_layout.setSpacing(20)
        
        # Student info card
        self.student_info_widget = QWidget()
        self.student_info_widget.setStyleSheet("background: transparent;")
        self.student_info_layout = QVBoxLayout(self.student_info_widget)
        self.student_info_layout.setContentsMargins(0, 0, 0, 0)
        self.student_info_layout.setSpacing(16)
        
        # Header with avatar and name
        header_layout = QHBoxLayout()
        header_layout.setSpacing(14)
        
        self.student_avatar = QLabel()
        self.student_avatar.setObjectName("studentPortrait")
        self.student_avatar.setFixedSize(72, 72)
        self.student_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.student_avatar.setStyleSheet(f"""
            background-color: {Colors.SURFACE_MUTED};
            border-radius: 36px;
            border: 2px solid {Colors.BORDER};
        """)
        header_layout.addWidget(self.student_avatar)
        
        name_info_layout = QVBoxLayout()
        name_info_layout.setSpacing(4)
        
        self.student_name_label = QLabel("Select a student")
        self.student_name_label.setFont(body_bold_font(18))
        self.student_name_label.setStyleSheet(f"color: {Colors.DARK_TEXT};")
        self.student_name_label.setWordWrap(True)
        name_info_layout.addWidget(self.student_name_label)
        
        self.student_id_label = QLabel("Click a row in the table to view details")
        self.student_id_label.setFont(body_font(13))
        self.student_id_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        name_info_layout.addWidget(self.student_id_label)
        
        # Status badge
        self.student_status_badge = QLabel()
        self.student_status_badge.setFont(small_label_font(10))
        self.student_status_badge.setVisible(False)
        name_info_layout.addWidget(self.student_status_badge)
        
        header_layout.addLayout(name_info_layout)
        header_layout.addStretch()
        
        self.student_info_layout.addLayout(header_layout)
        
        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"background: {Colors.BORDER}; border: none; max-height: 1px;")
        self.student_info_layout.addWidget(divider)
        
        # Details grid
        self.details_widget = QWidget()
        self.details_layout = QVBoxLayout(self.details_widget)
        self.details_layout.setContentsMargins(0, 0, 0, 0)
        self.details_layout.setSpacing(10)
        self.student_info_layout.addWidget(self.details_widget)
        
        self.right_content_layout.addWidget(self.student_info_widget)
        
        # Barcode display card
        self.barcode_frame = QFrame()
        self.barcode_frame.setObjectName("BarcodeFrameSurface")
        self.barcode_frame.setStyleSheet(f"""
            QFrame#BarcodeFrameSurface {{
                background-color: {Colors.WHITE};
                border: 2px solid {Colors.CREAM};
                border-radius: 12px;
            }}
        """)
        self.barcode_frame.setMinimumHeight(220)
        barcode_layout = QVBoxLayout(self.barcode_frame)
        barcode_layout.setContentsMargins(24, 24, 24, 24)
        barcode_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        barcode_layout.setSpacing(12)
        
        # Barcode value
        self.barcode_value_label = QLabel()
        self.barcode_value_label.setFont(monospace_font(20))
        self.barcode_value_label.setStyleSheet(f"color: {Colors.DARK_TEXT}; letter-spacing: 3px;")
        self.barcode_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.barcode_value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        barcode_layout.addWidget(self.barcode_value_label)
        
        # Barcode type badge
        self.barcode_type_label = QLabel()
        self.barcode_type_label.setFont(small_label_font(10))
        self.barcode_type_label.setStyleSheet(f"""
            color: {Colors.ACCENT_STRONG};
            background: {Colors.ACCENT_PALE};
            padding: 4px 10px;
            border-radius: 10px;
        """)
        self.barcode_type_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.barcode_type_label.setVisible(False)
        barcode_layout.addWidget(self.barcode_type_label)
        
        # Barcode image
        self.barcode_image_label = QLabel()
        self.barcode_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.barcode_image_label.setStyleSheet(f"""
            background: {Colors.WHITE};
            border: 1px dashed {Colors.BORDER};
            border-radius: 8px;
            padding: 16px;
        """)
        self.barcode_image_label.setMinimumHeight(100)
        barcode_layout.addWidget(self.barcode_image_label, 1)
        
        self.right_content_layout.addWidget(self.barcode_frame)
        
        # Actions bar
        actions_widget = QWidget()
        actions_widget.setStyleSheet(f"background: {Colors.SURFACE_MUTED}; border-radius: 8px;")
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(12, 10, 12, 10)
        actions_layout.setSpacing(10)
        
        self.print_btn = self._create_button("🖨  Print", primary=False, compact=True)
        self.print_btn.clicked.connect(self._print_barcode)
        self.print_btn.setEnabled(False)
        
        self.save_btn = self._create_button("💾  Save Image", primary=False, compact=True)
        self.save_btn.clicked.connect(self._save_barcode)
        self.save_btn.setEnabled(False)
        
        self.regenerate_btn = self._create_button("🔄  Regenerate", primary=True, compact=True)
        self.regenerate_btn.clicked.connect(self._regenerate_barcode)
        self.regenerate_btn.setEnabled(False)
        
        actions_layout.addStretch()
        actions_layout.addWidget(self.print_btn)
        actions_layout.addWidget(self.save_btn)
        actions_layout.addWidget(self.regenerate_btn)
        
        self.right_content_layout.addWidget(actions_widget)
        self.right_content_layout.addStretch()
        
        scroll_area.setWidget(scroll_content)
        right_layout.addWidget(scroll_area, 1)
        
        content_layout.addWidget(right_panel, 1)
        main_layout.addWidget(content, 1)
        
        # ===== PROGRESS BAR =====
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {Colors.CREAM};
                border: none;
                border-radius: 0px;
            }}
            QProgressBar::chunk {{
                background-color: {Colors.ACCENT};
                border-radius: 0px;
            }}
        """)
        main_layout.addWidget(self.progress_bar)
        
        # ===== STATUS BAR =====
        status_bar = QWidget()
        status_bar.setFixedHeight(44)
        status_bar.setStyleSheet(f"background: {Colors.SURFACE_MUTED}; border-top: 1px solid {Colors.BORDER};")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(16, 0, 16, 0)
        
        self.status_label = QLabel("Ready")
        self.status_label.setFont(small_label_font(11))
        self.status_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        status_layout.addWidget(self.status_label)
        
        status_layout.addStretch()
        
        # Version/info on right
        self.version_label = QLabel("v1.0")
        self.version_label.setFont(small_label_font(10))
        self.version_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        status_layout.addWidget(self.version_label)
        
        main_layout.addWidget(status_bar)
        
        # Connect table selection
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemChanged.connect(self._on_item_changed)
    
    def _create_card(self) -> QFrame:
        """Create a styled card panel."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.CARD_BORDER};
                border-radius: 12px;
            }}
        """)
        # Add subtle shadow effect via stylesheet (Qt doesn't support real shadows in stylesheet)
        return card
    
    def _create_panel_header(self, title: str, subtitle: str) -> QWidget:
        """Create a consistent panel header with title and subtitle."""
        header = QWidget()
        header.setFixedHeight(64)
        header.setStyleSheet(f"""
            background: {Colors.SURFACE_MUTED};
            border-top-left-radius: 12px;
            border-top-right-radius: 12px;
            border-bottom: 1px solid {Colors.BORDER};
        """)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(8)
        
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        
        title_label = QLabel(title)
        title_label.setFont(section_heading_font(16))
        title_label.setStyleSheet(f"color: {Colors.DARK_TEXT};")
        text_layout.addWidget(title_label)
        
        subtitle_label = QLabel(subtitle)
        subtitle_label.setFont(small_label_font(11))
        subtitle_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        text_layout.addWidget(subtitle_label)
        
        layout.addLayout(text_layout)
        layout.addStretch()
        
        return header
    
    def _create_button(self, text: str, primary: bool = False, compact: bool = False) -> QPushButton:
        """Create a consistently styled button."""
        btn = QPushButton(text)
        btn.setFont(button_font(12 if compact else 13))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        
        if compact:
            btn.setFixedHeight(36)
            btn.setContentsMargins(12, 0, 12, 0)
        else:
            btn.setFixedHeight(42)
            btn.setContentsMargins(16, 0, 16, 0)
        
        if primary:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.BUTTON_PRIMARY_BG};
                    color: {Colors.BUTTON_PRIMARY_TEXT};
                    border: none;
                    border-radius: 8px;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background-color: {Colors.BUTTON_PRIMARY_HOVER};
                }}
                QPushButton:pressed {{
                    background-color: {Colors.BUTTON_PRIMARY_PRESSED};
                }}
                QPushButton:disabled {{
                    background-color: {Colors.BORDER_STRONG};
                    color: {Colors.LIGHT_TEXT};
                }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {Colors.BUTTON_SECONDARY_BG};
                    color: {Colors.BUTTON_SECONDARY_TEXT};
                    border: 1px solid {Colors.BORDER};
                    border-radius: 8px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {Colors.BUTTON_SECONDARY_HOVER};
                    border-color: {Colors.BORDER_STRONG};
                }}
                QPushButton:pressed {{
                    background-color: {Colors.BUTTON_SECONDARY_PRESSED};
                }}
                QPushButton:disabled {{
                    background-color: {Colors.SURFACE_MUTED};
                    color: {Colors.LIGHT_TEXT};
                    border-color: {Colors.BORDER};
                }}
            """)
        
        return btn
    
    def _load_students(self):
        try:
            self._all_students = self.student_service.search_students(limit=1000)
        except DatabaseUnavailableError as exc:
            self._all_students = []
            self.table.setRowCount(0)
            self.status_label.setText("Student list unavailable — database not connected")
            self.header_count_badge.setVisible(False)
            show_db_error(self, exc, retry_callback=self._load_students)
            return
        self._populate_table(self._all_students)
        self.header_count_badge.setText(f"{len(self._all_students)} students")
        self.header_count_badge.setVisible(True)
    
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
            
            # Student ID
            id_item = QTableWidgetItem(student.student_id)
            id_item.setFont(table_content_font(12))
            self.table.setItem(row, 1, id_item)
            
            # Reference
            ref_item = QTableWidgetItem(student.reference_number)
            ref_item.setFont(table_content_font(12))
            self.table.setItem(row, 2, ref_item)
            
            # Name
            name_item = QTableWidgetItem(student.full_name)
            name_item.setFont(table_content_font(12))
            self.table.setItem(row, 3, name_item)
            
            # Program
            prog_item = QTableWidgetItem(student.program)
            prog_item.setFont(table_content_font(12))
            self.table.setItem(row, 4, prog_item)
            
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
            
            # Status with better visual
            if barcode:
                status_widget = self._create_status_badge("Generated", True)
            else:
                status_widget = self._create_status_badge("Missing", False)
            self.table.setCellWidget(row, 5, status_widget)
        
        self.table.blockSignals(False)
    
    def _create_status_badge(self, text: str, success: bool) -> QWidget:
        """Create a styled status badge for the table."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        badge = QLabel(text)
        badge.setFont(small_label_font(10))
        if success:
            badge.setStyleSheet(f"""
                color: {Colors.SUCCESS_GREEN};
                background: {Colors.SUCCESS_BG};
                padding: 3px 10px;
                border-radius: 10px;
                font-weight: 500;
            """)
        else:
            badge.setStyleSheet(f"""
                color: {Colors.WARNING_ORANGE};
                background: {Colors.WARNING_BG};
                padding: 3px 10px;
                border-radius: 10px;
                font-weight: 500;
            """)
        layout.addWidget(badge)
        return widget

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
                self.status_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
            else:
                self.status_label.setText(f"No student matches “{text.strip()}”")
                self.status_label.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
    
    def _on_selection_changed(self):
        """Preview the highlighted student's barcode, or clear the panel.

        Deselecting used to leave the previous student's name and payload on
        screen, so the panel described a student who was no longer selected.
        """
        selected = self.table.selectedItems()
        if not selected:
            self._clear_student_preview()
            return

        row = selected[0].row()
        checkbox = self.table.item(row, 0)
        if checkbox:
            student_id = checkbox.data(Qt.ItemDataRole.UserRole)
            if student_id is not None:
                self._show_barcode_preview(student_id)
    
    def _clear_student_preview(self):
        """Clear all student information from the preview panel."""
        self.student_name_label.setText("Select a student")
        self.student_name_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        self.student_id_label.setText("Click a row in the table to view details")
        self.student_id_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        self.student_status_badge.setVisible(False)
        self.student_status_badge.setText("")
        self.student_avatar.clear()
        self.student_avatar.setStyleSheet(f"""
            background-color: {Colors.SURFACE_MUTED};
            border-radius: 36px;
            border: 2px solid {Colors.BORDER};
        """)
        # Clear details
        for i in reversed(range(self.details_layout.count())):
            item = self.details_layout.takeAt(i)
            if item.widget():
                item.widget().deleteLater()
        # Clear barcode
        self.barcode_value_label.setText("")
        self.barcode_type_label.setText("")
        self.barcode_type_label.setVisible(False)
        self.barcode_image_label.setPixmap(QPixmap())
        self.barcode_image_label.setVisible(False)
        self.barcode_image_label.setStyleSheet(f"""
            background: {Colors.WHITE};
            border: 1px dashed {Colors.BORDER};
            border-radius: 8px;
            padding: 16px;
        """)
        self.print_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.regenerate_btn.setEnabled(False)
        self._set_busy(False)
    
    def _add_detail_row(self, label_text: str, value_text: str):
        """Add a label-value row to the details layout."""
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 2, 0, 2)
        row_layout.setSpacing(12)
        
        label = QLabel(label_text)
        label.setFont(small_label_font(10))
        label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        label.setFixedWidth(130)
        
        value = QLabel(value_text)
        value.setFont(body_font(12))
        value.setStyleSheet(f"color: {Colors.DARK_TEXT};")
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        
        row_layout.addWidget(label)
        row_layout.addWidget(value, 1)
        
        self.details_layout.addWidget(row)
    
    def _add_section_header(self, title: str):
        """Add a section header to the details layout."""
        header = QLabel(title)
        header.setFont(body_bold_font(11))
        header.setStyleSheet(f"color: {Colors.DARK_TEXT}; margin-top: 8px; margin-bottom: 4px;")
        self.details_layout.addWidget(header)
    
    def _show_barcode_preview(self, student_id: int):
        """Show one student's barcode with comprehensive student information."""
        # Clear previous data
        self.barcode_image_label.setVisible(False)
        self.barcode_image_label.setPixmap(QPixmap())
        self.barcode_image_label.setStyleSheet(f"""
            background: {Colors.WHITE};
            border: 1px dashed {Colors.BORDER};
            border-radius: 8px;
            padding: 16px;
        """)
        self.print_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.regenerate_btn.setEnabled(False)
        self.barcode_type_label.setVisible(False)
        # Clear details
        for i in reversed(range(self.details_layout.count())):
            item = self.details_layout.takeAt(i)
            if item.widget():
                item.widget().deleteLater()

        try:
            student = self.student_service.get_student(student_id)
            barcode = self.barcode_service.get_student_barcode(student_id)
        except DatabaseUnavailableError as exc:
            logger.warning("Barcode preview failed for student %s: %s", student_id, exc)
            self.student_name_label.setText("Barcode information unavailable")
            self.student_name_label.setStyleSheet(f"color: {Colors.ERROR_RED};")
            self.student_id_label.setText("")
            show_db_error(self, exc, retry_callback=lambda: self._show_barcode_preview(student_id))
            return
        except Exception as exc:  # noqa: BLE001 - reported on screen
            logger.exception("Barcode preview failed for student %s", student_id)
            self.student_name_label.setText(f"Barcode information unavailable: {exc}")
            self.student_name_label.setStyleSheet(f"color: {Colors.ERROR_RED};")
            self.student_id_label.setText("")
            return

        if student is None:
            self.student_name_label.setText("That student is no longer on file")
            self.student_name_label.setStyleSheet(f"color: {Colors.ERROR_RED};")
            self.student_id_label.setText("")
            self._load_students()
            return

        # Update student header
        self.student_name_label.setText(student.full_name)
        self.student_name_label.setStyleSheet(f"color: {Colors.DARK_TEXT};")
        self.student_id_label.setText(f"{student.student_id}  •  {student.reference_number}")
        self.student_id_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        
        # Load avatar (72x72 for new UI)
        apply_portrait(self.student_avatar, student, 72)

        # Enrollment status badge
        enrollment_status = student.enrollment_status.value if hasattr(student.enrollment_status, 'value') else str(student.enrollment_status)
        status_colors = {
            'active': (Colors.SUCCESS_BG, Colors.SUCCESS_GREEN),
            'inactive': (Colors.NEUTRAL_BG, Colors.TEXT_SECONDARY),
            'graduated': (Colors.INFO_BG, Colors.INFO_BLUE),
            'dropped': (Colors.ERROR_BG, Colors.ERROR_RED),
            'on_leave': (Colors.WARNING_BG, Colors.WARNING_ORANGE),
        }
        bg_color, text_color = status_colors.get(enrollment_status.lower(), (Colors.NEUTRAL_BG, Colors.TEXT_SECONDARY))
        self.student_status_badge.setText(f"  {enrollment_status.title()}  ")
        self.student_status_badge.setStyleSheet(f"""
            background-color: {bg_color};
            color: {text_color};
            padding: 2px 8px;
            border-radius: 10px;
        """)
        self.student_status_badge.setVisible(True)

        # --- Academic Details Section ---
        self._add_section_header("Academic Information")
        self._add_detail_row("Program:", student.program)
        
        year_level = student.year_level.value if hasattr(student.year_level, 'value') else str(student.year_level)
        self._add_detail_row("Year Level:", year_level)
        
        if student.section:
            self._add_detail_row("Section:", student.section)
        
        enrollment_status = student.enrollment_status.value if hasattr(student.enrollment_status, 'value') else str(student.enrollment_status)
        self._add_detail_row("Enrollment:", enrollment_status.title())
        
        if student.date_enrolled:
            enrolled_str = student.date_enrolled.strftime("%B %d, %Y")
            self._add_detail_row("Date Enrolled:", enrolled_str)

        # --- Personal Details Section ---
        self._add_section_header("Personal Information")
        
        if student.date_of_birth:
            dob_str = student.date_of_birth.strftime("%B %d, %Y")
            age = student.age
            age_str = f" ({age} years old)" if age is not None else ""
            self._add_detail_row("Date of Birth:", f"{dob_str}{age_str}")
        
        if student.gender:
            gender = student.gender.value if hasattr(student.gender, 'value') else str(student.gender)
            self._add_detail_row("Gender:", gender)
        
        if student.address:
            self._add_detail_row("Address:", student.address)

        # --- Barcode Details Section ---
        self._add_section_header("Barcode Information")
        
        if student.barcode_payload:
            self._add_detail_row("Payload:", student.barcode_payload)

        # --- Contacts Section ---
        if student.contacts:
            self._add_section_header("Contact Information")
            primary_contacts = [c for c in student.contacts if c.is_primary]
            other_contacts = [c for c in student.contacts if not c.is_primary]
            
            for contact in primary_contacts:
                label = contact.contact_type.value.replace('_', ' ').title()
                if contact.label:
                    label += f" ({contact.label})"
                self._add_detail_row(f"{label} (Primary):", contact.contact_value)
            
            for contact in other_contacts:
                label = contact.contact_type.value.replace('_', ' ').title()
                if contact.label:
                    label += f" ({contact.label})"
                self._add_detail_row(f"{label}:", contact.contact_value)

        # Barcode information
        if barcode is None:
            self.barcode_value_label.setText("No barcode generated")
            self.barcode_type_label.setText("")
            self.barcode_type_label.setVisible(False)
            self.student_name_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
            self.barcode_image_label.setVisible(True)
            self.barcode_image_label.setText("No barcode generated for this student")
            self.barcode_image_label.setFont(body_font(13))
            self.barcode_image_label.setStyleSheet(f"""
                color: {Colors.MEDIUM_TEXT};
                background: {Colors.SURFACE_MUTED};
                border: 1px dashed {Colors.BORDER};
                border-radius: 8px;
                padding: 16px;
            """)
            self.barcode_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return

        self.barcode_value_label.setText(barcode.barcode_value)
        barcode_type = barcode.barcode_type.value if hasattr(barcode.barcode_type, 'value') else str(barcode.barcode_type)
        self.barcode_type_label.setText(f"  {barcode_type}  ")
        self.barcode_type_label.setVisible(True)
        self.print_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        self.regenerate_btn.setEnabled(True)

        # Project-root resolution, not the working directory: a stored relative
        # path must resolve the same way whatever directory the app was launched
        # from.
        image_path = settings.resolve_asset(barcode.image_path)
        if image_path is None:
            self.student_name_label.setText(
                f"{self.student_name_label.text()}\n"
                f"The barcode image file ({barcode.image_path or 'no path recorded'}) "
                f"could not be found. The payload above is still valid."
            )
            self.student_name_label.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
            logger.warning(
                "Barcode image for %s not found at %r", student.reference_number,
                barcode.image_path,
            )
            self.barcode_image_label.setVisible(True)
            self.barcode_image_label.setText("Barcode image file not found")
            self.barcode_image_label.setFont(body_font(13))
            self.barcode_image_label.setStyleSheet(f"""
                color: {Colors.WARNING_ORANGE};
                background: {Colors.WARNING_BG};
                border: 1px solid {Colors.WARNING_ORANGE};
                border-radius: 8px;
                padding: 16px;
            """)
            self.barcode_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return

        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            self.student_name_label.setText(
                f"{self.student_name_label.text()}\n"
                f"The barcode image at {image_path.name} could not be decoded."
            )
            self.student_name_label.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
            logger.warning("Barcode image could not be decoded: %s", image_path)
            self.barcode_image_label.setVisible(True)
            self.barcode_image_label.setText("Could not decode barcode image")
            self.barcode_image_label.setFont(body_font(13))
            self.barcode_image_label.setStyleSheet(f"""
                color: {Colors.WARNING_ORANGE};
                background: {Colors.WARNING_BG};
                border: 1px solid {Colors.WARNING_ORANGE};
                border-radius: 8px;
                padding: 16px;
            """)
            self.barcode_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return

        self.barcode_image_label.setPixmap(
            pixmap.scaledToWidth(320, Qt.TransformationMode.SmoothTransformation)
        )
        self.barcode_image_label.setStyleSheet(f"""
            background: {Colors.WHITE};
            border: 1px solid {Colors.BORDER};
            border-radius: 8px;
            padding: 16px;
        """)
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
