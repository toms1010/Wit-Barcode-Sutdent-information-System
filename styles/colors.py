class Colors:
    # --- Institutional brand -------------------------------------------
    # The original WIT brand orange. Kept for the logo, document rules and
    # anything an official document must not restyle.
    ORANGE = "#EE6B05"
    GOLDEN_YELLOW = "#FFE300"
    CREAM = "#FFEACB"
    BRIGHT_YELLOW = "#FFFE00"
    CORAL_ORANGE = "#FC8F4B"
    DARK_BURNT_ORANGE = "#AD4D05"
    SOFT_GOLD = "#FFDC93"
    VIVID_ORANGE_RED = "#FF4300"
    WARM_BEIGE = "#CCA18D"

    # --- Interface accent ----------------------------------------------
    # A slightly deeper, less saturated orange for the running interface. A
    # full-strength brand orange used as a page header, a table header and a
    # button at once made every screen look like a warning; the accent is now
    # reserved for what is interactive or currently selected, and the brand
    # orange survives on documents and the logo.
    ACCENT = "#F97316"
    ACCENT_STRONG = "#C2410C"
    ACCENT_PALE = "#FFF7ED"
    ACCENT_SUBTLE = "#FFEDD5"

    # --- Neutral administrative surfaces --------------------------------
    PAGE_BG = "#F8FAFC"
    SURFACE = "#FFFFFF"
    SURFACE_MUTED = "#F1F5F9"
    SURFACE_HOVER = "#F8FAFC"
    TEXT_PRIMARY = "#0F172A"
    TEXT_SECONDARY = "#64748B"
    BORDER = "#E2E8F0"
    BORDER_STRONG = "#CBD5E1"
    
    WHITE = "#FFFFFF"
    LIGHT_CREAM = "#FFFEF7"
    DARK_TEXT = "#0F172A"
    MEDIUM_TEXT = "#475569"
    LIGHT_TEXT = "#64748B"
    BORDER_LIGHT = "#E2E8F0"

    SUCCESS_GREEN = "#16A34A"
    ERROR_RED = "#DC2626"
    WARNING_ORANGE = "#D97706"
    INFO_BLUE = "#2563EB"

    OVERLAY_WHITE = "rgba(255, 247, 237, 0.82)"
    OVERLAY_LIGHT = "rgba(248, 250, 252, 0.9)"

    SIDEBAR_BG = "#FFFFFF"
    SIDEBAR_ACTIVE = "#F97316"
    SIDEBAR_HOVER = "#FFF7ED"
    SIDEBAR_TEXT = "#0F172A"
    SIDEBAR_TEXT_ACTIVE = "#FFFFFF"

    CARD_BG = "#FFFFFF"
    CARD_BORDER = "#E2E8F0"
    CARD_SHADOW = "rgba(15, 23, 42, 0.06)"

    # Data tables are read, not admired: a white header with dark text and a
    # single accent rule keeps the numbers the loudest thing on screen and
    # reserves the saturated colour for the selection.
    TABLE_HEADER_BG = "#F1F5F9"
    TABLE_HEADER_TEXT = "#0F172A"
    TABLE_ROW_ALT = "#F8FAFC"
    TABLE_BORDER = "#E2E8F0"
    TABLE_SELECTED = "#FFEDD5"

    BUTTON_PRIMARY_BG = "#F97316"
    BUTTON_PRIMARY_HOVER = "#EA580C"
    BUTTON_PRIMARY_PRESSED = "#C2410C"
    BUTTON_PRIMARY_TEXT = "#FFFFFF"

    BUTTON_SECONDARY_BG = "#FFFFFF"
    BUTTON_SECONDARY_HOVER = "#F8FAFC"
    BUTTON_SECONDARY_PRESSED = "#F1F5F9"
    BUTTON_SECONDARY_TEXT = "#0F172A"

    BUTTON_DANGER_BG = "#DC2626"
    BUTTON_DANGER_HOVER = "#B91C1C"
    BUTTON_DANGER_TEXT = "#FFFFFF"

    INPUT_BG = "#FFFFFF"
    INPUT_BORDER = "#CBD5E1"
    INPUT_BORDER_FOCUS = "#F97316"
    INPUT_TEXT = "#0F172A"
    INPUT_PLACEHOLDER = "#94A3B8"

    SCROLLBAR_BG = "#F1F5F9"
    SCROLLBAR_HANDLE = "#CBD5E1"
    SCROLLBAR_HANDLE_HOVER = "#94A3B8"

    TOOLTIP_BG = "#0F172A"
    TOOLTIP_TEXT = "#FFFFFF"

    MODAL_OVERLAY = "rgba(15, 23, 42, 0.5)"

    #: Tints for the status pill and the installment states. Each pair is a
    #: background/text combination that keeps 4.5:1 contrast on the surface it
    #: is placed on.
    SUCCESS_BG = "#DCFCE7"
    WARNING_BG = "#FEF3C7"
    ERROR_BG = "#FEE2E2"
    INFO_BG = "#DBEAFE"
    NEUTRAL_BG = "#E2E8F0"

    #: Colour per tone word returned by
    #: :func:`services.com_summary.installment_state`. Kept with the palette so
    #: every screen that shows an installment state uses the same colour instead
    #: of re-deriving it from the stored status string.
    TONES = {
        "success": SUCCESS_GREEN,
        "warning": WARNING_ORANGE,
        "error": ERROR_RED,
        "neutral": TEXT_SECONDARY,
    }

    CHART_COLORS = [
        "#F97316",
        "#FB923C",
        "#FDBA74",
        "#C2410C",
        "#64748B",
        "#2563EB",
        "#16A34A",
        "#D97706",
    ]

    @classmethod
    def qcolor(cls, hex_color: str):
        from PySide6.QtGui import QColor
        return QColor(hex_color)

    @classmethod
    def rgba(cls, hex_color: str, alpha: float):
        hex_color = hex_color.lstrip("#")
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return f"rgba({r}, {g}, {b}, {alpha})"