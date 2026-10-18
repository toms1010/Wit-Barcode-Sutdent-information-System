from styles.colors import Colors
from styles.typography import (
    load_fonts,
    get_open_sauce_family,
    get_agrandir_family,
    app_title_font,
    page_heading_font,
    section_heading_font,
    body_font,
    body_bold_font,
    table_header_font,
    table_content_font,
    small_label_font,
    button_font,
    monospace_font,
    FONT_SIZES,
    FONT_WEIGHTS,
)

def apply_theme(app):
    load_fonts()
    
    with open(Path(__file__).parent / "theme.qss", "r") as f:
        app.setStyleSheet(f.read())

from pathlib import Path

__all__ = [
    "Colors",
    "load_fonts",
    "get_open_sauce_family",
    "get_agrandir_family",
    "app_title_font",
    "page_heading_font",
    "section_heading_font",
    "body_font",
    "body_bold_font",
    "table_header_font",
    "table_content_font",
    "small_label_font",
    "button_font",
    "monospace_font",
    "FONT_SIZES",
    "FONT_WEIGHTS",
    "apply_theme",
]