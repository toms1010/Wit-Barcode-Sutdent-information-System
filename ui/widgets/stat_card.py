from typing import Optional

from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QWidget, QSizePolicy
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Property
from PySide6.QtGui import QFont, QPixmap, QPainter, QColor, QBrush, QPen
from styles.typography import get_open_sauce_family, get_agrandir_family, body_font, app_title_font
from styles.colors import Colors
import logging

logger = logging.getLogger(__name__)


class StatCard(QFrame):
    #: Minimum height. A fixed height was used before and it clipped the value
    #: and the wrapped title at 125%/150% display scaling; the card now grows
    #: with its content and still lines up in a row of tiles.
    MINIMUM_HEIGHT = 132

    def __init__(self, title: str, value: str, icon: str = "", trend: str = "", trend_up: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setProperty("stat-card", "true")
        self.setMinimumHeight(self.MINIMUM_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        self._value = value
        self._anim_value = 0.0
        self._target_value = self._parse_value(value)

        self._setup_ui(title, value, icon, trend, trend_up)
        self._animate_value()

    def _parse_value(self, value: str) -> Optional[float]:
        """Numeric target for the count-up, or ``None`` for non-numeric text.

        Returning ``None`` matters: the previous implementation caught every
        exception and returned ``0.0``, so a card told to show ``"—"`` or
        ``"N/A"`` animated to the number zero and displayed that instead.
        """
        if value is None:
            return None
        text = str(value).replace(",", "").replace("₱", "").replace("%", "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    
    def _setup_ui(self, title: str, value: str, icon: str, trend: str, trend_up: bool):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(16)
        
        icon_label = QLabel(icon)
        icon_label.setFixedSize(56, 56)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(f"""
            QLabel {{
                background-color: {Colors.CREAM};
                border-radius: 28px;
                font-size: 24px;
                color: {Colors.ORANGE};
            }}
        """)
        
        content_layout = QVBoxLayout()
        content_layout.setSpacing(4)
        content_layout.setContentsMargins(0, 0, 0, 0)
        
        self.value_label = QLabel("—")
        self.value_label.setFont(app_title_font(28))
        self.value_label.setWordWrap(False)
        self.value_label.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE};")
        
        title_label = QLabel(title)
        title_label.setFont(body_font(13))
        title_label.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        title_label.setWordWrap(True)
        
        content_layout.addWidget(self.value_label)
        content_layout.addWidget(title_label)
        content_layout.addStretch()
        
        if trend:
            trend_layout = QHBoxLayout()
            trend_layout.setContentsMargins(0, 0, 0, 0)
            
            trend_icon = "▲" if trend_up else "▼"
            trend_color = Colors.SUCCESS_GREEN if trend_up else Colors.ERROR_RED
            
            trend_label = QLabel(f"{trend_icon} {trend}")
            trend_label.setFont(body_font(11))
            trend_label.setStyleSheet(f"color: {trend_color}; font-weight: 600;")
            
            trend_layout.addStretch()
            trend_layout.addWidget(trend_label)
            content_layout.addLayout(trend_layout)
        
        layout.addWidget(icon_label)
        layout.addLayout(content_layout)
    
    def _animate_value(self):
        self._animation = QPropertyAnimation(self, b"anim_value")
        self._animation.setDuration(1000)
        self._animation.setStartValue(0)
        self._animation.setEndValue(self._target_value or 0.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_value_changed)
        self._animation.start()
        if self._target_value is None:
            # Nothing numeric to count up to: show the text itself immediately
            # instead of leaving the card claiming "0" until something changes.
            self._on_value_changed(self._target_value)

    def _on_value_changed(self, value):
        if value is None:
            self._anim_value = 0.0
            self.value_label.setText(self._value)
            return
        self._anim_value = value
        self.value_label.setText(self._format_value(value))

    def _format_value(self, value: float) -> str:
        if self._value.startswith("₱"):
            return f"₱{value:,.2f}"
        elif self._value.endswith("%"):
            return f"{value:.1f}%"
        elif "," in self._value or "." in self._value:
            # Only a *thousands* separator or an explicit decimal point earns
            # grouping. Sniffing for "." used to round "12.5" to "13".
            if "." in self._value:
                places = len(self._value.split(".")[-1])
                return f"{value:,.{places}f}"
            return f"{value:,.0f}"
        else:
            return f"{int(round(value)):,}"

    def _get_anim_value(self):
        return self._anim_value

    def _set_anim_value(self, value):
        self._anim_value = value

    anim_value = Property(float, _get_anim_value, _set_anim_value)

    def update_value(self, new_value: str):
        """Replace the figure.

        Non-numeric text (``"—"``, ``"N/A"``) is displayed as given: it means
        the record is unavailable, and rendering it as ``0`` would state the
        opposite.
        """
        self._value = "" if new_value is None else str(new_value)
        target = self._parse_value(self._value)
        self._target_value = target
        self._animation.stop()
        if target is None:
            self.value_label.setText(self._value)
            self.value_label.setToolTip("")
            return
        self.value_label.setToolTip("")
        self._animation.setStartValue(self._anim_value)
        self._animation.setEndValue(target)
        self._animation.start()