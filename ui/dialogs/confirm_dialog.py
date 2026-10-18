from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QScrollArea,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from styles.typography import get_open_sauce_family, body_font, body_bold_font, page_heading_font
from styles.colors import Colors


class ConfirmDialog(QDialog):
    """A confirmation with a real title bar, sized to its message.

    Three problems this replaces:

    * ``setFixedSize(400, 200)`` clipped any message longer than about two lines,
      and the delete confirmation is exactly the message that grew longer.
    * ``FramelessWindowHint`` removed the title bar, so the dialog could not be
      moved or closed except with the two buttons.
    * Nothing called ``deleteLater()``, and every caller dropped its reference
      after ``exec()``, so each confirmation leaked a dialog.
    """

    def __init__(self, title: str, message: str, confirm_text: str = "Confirm",
                 cancel_text: str = "Cancel", danger: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumWidth(420)
        # The height is a *minimum*, not a fixed size: QDialog settles on its
        # layout's sizeHint, and a fixed height applied before the layout existed
        # left long messages with nowhere to wrap to.
        self.setMinimumHeight(self._height_for(message))

        self._setup_ui(title, message, confirm_text, cancel_text, danger)
        self.resize(max(460, self.minimumWidth()), self.minimumHeight())

    @staticmethod
    def _height_for(message: str, width: int = 460) -> int:
        """Enough height for the wrapped message plus the title and buttons."""
        chars_per_line = max(20, int(width / 7))
        lines = max(1, -(-len(message) // chars_per_line))
        return min(640, 220 + lines * 22)

    def _setup_ui(self, title: str, message: str, confirm_text: str,
                  cancel_text: str, danger: bool):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # Icon/Title
        title_label = QLabel(title)
        title_label.setFont(page_heading_font(18))
        title_label.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE};")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setWordWrap(True)
        main_layout.addWidget(title_label)

        # Message, scrollable so an unusually long message stays reachable
        # whatever the dialog was sized to.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameStyle(QFrame.Shape.NoFrame)

        holder = QFrame()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)

        msg_label = QLabel(message)
        msg_label.setFont(body_font(13))
        msg_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        msg_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg_label.setWordWrap(True)
        msg_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        holder_layout.addWidget(msg_label)

        scroll.setWidget(holder)
        main_layout.addWidget(scroll, 1)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        cancel_btn = QPushButton(cancel_text)
        cancel_btn.setProperty("secondary", "true")
        cancel_btn.setFixedHeight(40)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)

        confirm_btn = QPushButton(confirm_text)
        confirm_btn.setFixedHeight(40)
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.setDefault(True)
        if danger:
            confirm_btn.setProperty("danger", "true")
        confirm_btn.clicked.connect(self.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(confirm_btn)

        main_layout.addLayout(btn_layout)