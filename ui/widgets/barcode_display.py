from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QFont
from styles.typography import get_open_sauce_family, body_font, body_bold_font, small_label_font, monospace_font
from styles.colors import Colors
from ui.widgets.profile_image import apply_portrait
from models.barcode import Barcode
from models.student import Student
import logging

logger = logging.getLogger(__name__)


class BarcodeDisplay(QFrame):
    print_clicked = Signal()
    save_clicked = Signal()
    regenerate_clicked = Signal()
    
    def __init__(self, barcode: Barcode = None, student: Student = None, parent=None):
        super().__init__(parent)
        self.barcode = barcode
        self.student = student
        self.setProperty("card", "true")
        self.setMinimumHeight(280)
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        
        if self.student:
            student_info = QHBoxLayout()
            student_info.setSpacing(12)
            
            # The student's photograph, loaded from the record through the one
            # shared loader. It was an initials disc, which reads as a portrait
            # without being one.
            avatar = QLabel()
            avatar.setObjectName("studentPortrait")
            avatar.setFixedSize(48, 48)
            avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            avatar.setStyleSheet(
                f"background-color: {Colors.SURFACE_MUTED};"
                "border-radius: 24px;"
                f"border: 1px solid {Colors.BORDER};"
            )
            apply_portrait(avatar, self.student, 48)
            
            info = QVBoxLayout()
            info.setSpacing(2)
            
            name_label = QLabel(self.student.full_name)
            name_label.setFont(body_bold_font(14))
            name_label.setStyleSheet(f"color: {Colors.DARK_TEXT};")
            
            detail_label = QLabel(f"{self.student.student_id} • {self.student.program} • {self.student.year_level.value if hasattr(self.student.year_level, 'value') else self.student.year_level}")
            detail_label.setFont(small_label_font(11))
            detail_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
            
            info.addWidget(name_label)
            info.addWidget(detail_label)
            
            student_info.addWidget(avatar)
            student_info.addLayout(info)
            student_info.addStretch()
            
            layout.addLayout(student_info)
        
        if self.barcode:
            barcode_frame = QFrame()
            barcode_frame.setObjectName("BarcodeFrameSurface")
            barcode_frame.setStyleSheet(f"""
                QFrame#BarcodeFrameSurface {{
                    background-color: {Colors.WHITE};
                    border: 2px solid {Colors.CREAM};
                    border-radius: 8px;
                }}
            """)
            
            barcode_layout = QVBoxLayout(barcode_frame)
            barcode_layout.setContentsMargins(20, 20, 20, 20)
            barcode_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            value_label = QLabel(self.barcode.barcode_value)
            value_label.setFont(monospace_font(18))
            value_label.setStyleSheet(f"color: {Colors.DARK_TEXT}; letter-spacing: 2px;")
            value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            type_label = QLabel(self.barcode.barcode_type.value if hasattr(self.barcode.barcode_type, 'value') else str(self.barcode.barcode_type))
            type_label.setFont(small_label_font(10))
            type_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
            type_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            barcode_layout.addWidget(value_label)
            barcode_layout.addWidget(type_label)
            
            if self.barcode.image_path:
                from pathlib import Path
                img_path = Path(self.barcode.image_path)
                if not img_path.is_absolute():
                    img_path = Path.cwd() / img_path
                
                if img_path.exists():
                    pixmap = QPixmap(str(img_path))
                    if not pixmap.isNull():
                        scaled = pixmap.scaledToWidth(300, Qt.TransformationMode.SmoothTransformation)
                        img_label = QLabel()
                        img_label.setPixmap(scaled)
                        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                        barcode_layout.addWidget(img_label)
            
            layout.addWidget(barcode_frame)
            
            btn_layout = QHBoxLayout()
            btn_layout.setSpacing(12)
            
            print_btn = QPushButton("Print")
            print_btn.setProperty("secondary", "true")
            print_btn.clicked.connect(self.print_clicked.emit)
            
            save_btn = QPushButton("Save Image")
            save_btn.setProperty("secondary", "true")
            save_btn.clicked.connect(self.save_clicked.emit)
            
            regenerate_btn = QPushButton("Regenerate")
            regenerate_btn.setProperty("secondary", "true")
            regenerate_btn.clicked.connect(self.regenerate_clicked.emit)
            
            btn_layout.addStretch()
            btn_layout.addWidget(print_btn)
            btn_layout.addWidget(save_btn)
            btn_layout.addWidget(regenerate_btn)
            
            layout.addLayout(btn_layout)
        else:
            empty_label = QLabel("No barcode generated for this student")
            empty_label.setFont(body_font(13))
            empty_label.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(empty_label)
            layout.addStretch()
    
    def update_barcode(self, barcode: Barcode, student: Student = None):
        self.barcode = barcode
        if student:
            self.student = student
        # Rebuild UI
        for i in reversed(range(self.layout().count())):
            item = self.layout().takeAt(i)
            if item.widget():
                item.widget().deleteLater()
        self._setup_ui()