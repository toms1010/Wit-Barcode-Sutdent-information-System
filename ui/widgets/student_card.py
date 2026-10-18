from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QFont, QPainter, QColor, QBrush
from styles.typography import get_open_sauce_family, body_font, body_bold_font, small_label_font
from styles.colors import Colors
from models.student import Student
import logging

logger = logging.getLogger(__name__)


class StudentCard(QFrame):
    view_clicked = Signal(int)
    edit_clicked = Signal(int)
    barcode_clicked = Signal(int)
    
    def __init__(self, student: Student, parent=None):
        super().__init__(parent)
        self.student = student
        self.setProperty("card", "true")
        # The width stays fixed so a grid of cards lines up, but the height is
        # driven by the content. A hard 280x340 was 68px shorter than the layout
        # needs, so the student's name was vertically clipped (17px of a 26px
        # label) and the action buttons were pushed out of the frame entirely.
        self.setFixedWidth(280)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.MinimumExpanding
        )
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Profile image section
        image_frame = QFrame()
        image_frame.setFixedHeight(180)
        image_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.CREAM};
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
            }}
        """)
        
        image_layout = QVBoxLayout(image_frame)
        image_layout.setContentsMargins(16, 16, 16, 16)
        image_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.photo_label = QLabel()
        self.photo_label.setFixedSize(100, 100)
        self.photo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_label.setStyleSheet(f"""
            QLabel {{
                background-color: {Colors.WARM_BEIGE};
                border-radius: 50px;
                border: 3px solid {Colors.WHITE};
            }}
        """)
        self._load_photo()
        
        image_layout.addWidget(self.photo_label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(image_frame)
        
        # Info section
        info_frame = QFrame()
        info_frame.setObjectName("InfoFrameSurface")
        info_frame.setStyleSheet(f"""
            QFrame#InfoFrameSurface {{
                background-color: {Colors.WHITE};
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
            }}
        """)
        
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(16, 16, 16, 16)
        info_layout.setSpacing(8)
        
        name_label = QLabel(self.student.full_name)
        name_label.setFont(body_bold_font(14))
        name_label.setStyleSheet(f"color: {Colors.DARK_TEXT};")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setWordWrap(True)
        
        id_label = QLabel(f"ID: {self.student.student_id}")
        id_label.setFont(small_label_font(11))
        id_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        id_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        ref_label = QLabel(f"Ref: {self.student.reference_number}")
        ref_label.setFont(small_label_font(11))
        ref_label.setStyleSheet(f"color: {Colors.ORANGE}; font-weight: 600;")
        ref_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        program_label = QLabel(f"{self.student.program} • {self.student.year_level.value if hasattr(self.student.year_level, 'value') else self.student.year_level}")
        program_label.setFont(small_label_font(11))
        program_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        program_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        status_label = QLabel(self.student.enrollment_status.value.title() if hasattr(self.student.enrollment_status, 'value') else str(self.student.enrollment_status).title())
        status_label.setFont(small_label_font(10))
        status_color = Colors.SUCCESS_GREEN if (self.student.enrollment_status.value if hasattr(self.student.enrollment_status, 'value') else str(self.student.enrollment_status)) == "active" else Colors.WARNING_ORANGE
        status_label.setStyleSheet(f"""
            QLabel {{
                color: {status_color};
                background-color: {Colors.rgba(status_color, 0.1)};
                padding: 4px 12px;
                border-radius: 12px;
                font-weight: 600;
            }}
        """)
        status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        info_layout.addWidget(name_label)
        info_layout.addWidget(id_label)
        info_layout.addWidget(ref_label)
        info_layout.addWidget(program_label)
        info_layout.addWidget(status_label)
        info_layout.addStretch()
        
        # Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(12, 0, 12, 12)
        btn_layout.setSpacing(8)
        
        view_btn = QPushButton("View Full Profile")
        view_btn.setProperty("secondary", "true")
        view_btn.setFixedHeight(32)
        view_btn.clicked.connect(lambda: self.view_clicked.emit(self.student.id))
        
        edit_btn = QPushButton("Edit")
        edit_btn.setProperty("secondary", "true")
        edit_btn.setFixedHeight(32)
        edit_btn.clicked.connect(lambda: self.edit_clicked.emit(self.student.id))
        
        barcode_btn = QPushButton("Barcode")
        barcode_btn.setFixedHeight(32)
        barcode_btn.clicked.connect(lambda: self.barcode_clicked.emit(self.student.id))
        
        btn_layout.addWidget(view_btn)
        btn_layout.addWidget(edit_btn)
        btn_layout.addWidget(barcode_btn)
        
        info_layout.addLayout(btn_layout)
        layout.addWidget(info_frame)
    
    def _load_photo(self):
        # Shared loader: project-root path resolution, cover-crop, and a neutral
        # placeholder plus a logged warning when the asset is missing.
        from ui.widgets.profile_image import apply_portrait

        apply_portrait(self.photo_label, self.student, size=100)
        self.photo_label.setStyleSheet(
            f"background-color: {Colors.SURFACE_MUTED};"
            "border-radius: 50px;"
            f"border: 3px solid {Colors.WHITE};"
        )