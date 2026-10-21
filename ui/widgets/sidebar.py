from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget, QVBoxLayout, QLabel, QFrame
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon, QFont
from styles.typography import get_open_sauce_family, button_font
import logging

logger = logging.getLogger(__name__)

NAV_ITEMS = [
    ("dashboard", "Dashboard", "📊"),
    ("students", "Students", "👥"),
    ("scanner", "Barcode Scanner", "📷"),
    ("enrollment", "Subject Enrollment", "📚"),
    ("courses", "Courses & Subjects", "📖"),
    ("certificate", "Certificate of Matriculation", "📜"),
    ("assessment", "Assessment & Fees", "💰"),
    ("reports", "Reports & Analytics", "📈"),
    ("training", "Training Center", "🤖"),
    ("settings", "Settings", "⚙️"),
    ("logout", "Logout", "🚪"),
]


class Sidebar(QListWidget):
    navigation_changed = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(260)
        self.setSpacing(4)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameStyle(QFrame.Shape.NoFrame)
        
        self._setup_items()
        self.currentRowChanged.connect(self._on_current_changed)
        
    def _setup_items(self):
        for item_id, label, icon in NAV_ITEMS:
            item = QListWidgetItem(f"  {icon}  {label}")
            item.setData(Qt.ItemDataRole.UserRole, item_id)
            item.setSizeHint(QSize(240, 44))
            item.setFont(button_font())
            self.addItem(item)
        
        self.setCurrentRow(0)
    
    def _on_current_changed(self, row: int):
        if row >= 0:
            item = self.item(row)
            if item:
                item_id = item.data(Qt.ItemDataRole.UserRole)
                self.navigation_changed.emit(item_id)
    
    def set_active(self, item_id: str):
        for i in range(self.count()):
            item = self.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == item_id:
                self.setCurrentRow(i)
                break
    
    def get_active(self) -> str:
        item = self.currentItem()
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return "dashboard"