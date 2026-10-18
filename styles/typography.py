from pathlib import Path
from typing import Optional
from PySide6.QtGui import QFont, QFontDatabase
import logging

logger = logging.getLogger(__name__)

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
OPEN_SAUCE_DIR = FONTS_DIR / "open-sauce"
AGRANDIR_DIR = FONTS_DIR / "agrandir"

_open_sauce_families = []
_agrandir_families = []
_fonts_loaded = False


def load_fonts() -> bool:
    global _open_sauce_families, _agrandir_families, _fonts_loaded
    
    if _fonts_loaded:
        return True
    
    success = True
    
    if OPEN_SAUCE_DIR.exists():
        for font_file in OPEN_SAUCE_DIR.rglob("*.ttf"):
            font_id = QFontDatabase.addApplicationFont(str(font_file))
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                _open_sauce_families.extend(families)
                logger.info(f"Loaded Open Sauce font: {font_file.name} -> {families}")
            else:
                logger.warning(f"Failed to load Open Sauce font: {font_file}")
                success = False
    else:
        logger.warning(f"Open Sauce fonts directory not found: {OPEN_SAUCE_DIR}")
        success = False
    
    if AGRANDIR_DIR.exists():
        for font_file in AGRANDIR_DIR.rglob("*.ttf"):
            font_id = QFontDatabase.addApplicationFont(str(font_file))
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                _agrandir_families.extend(families)
                logger.info(f"Loaded Agrandir font: {font_file.name} -> {families}")
            else:
                logger.warning(f"Failed to load Agrandir font: {font_file}")
                success = False
    else:
        logger.warning(f"Agrandir fonts directory not found: {AGRANDIR_DIR}")
        success = False
    
    if not _open_sauce_families:
        _open_sauce_families = ["Segoe UI", "Helvetica Neue", "Arial", "sans-serif"]
        logger.info("Using fallback fonts for Open Sauce")
    
    if not _agrandir_families:
        _agrandir_families = ["Georgia", "Times New Roman", "serif"]
        logger.info("Using fallback fonts for Agrandir")
    
    _fonts_loaded = True
    return success


def get_open_sauce_family() -> str:
    if not _fonts_loaded:
        load_fonts()
    return _open_sauce_families[0] if _open_sauce_families else "Segoe UI"


def get_agrandir_family() -> str:
    if not _fonts_loaded:
        load_fonts()
    return _agrandir_families[0] if _agrandir_families else "Georgia"


def create_font(family: str, size: int, weight: int = QFont.Weight.Normal, italic: bool = False) -> QFont:
    font = QFont(family, size)
    font.setWeight(weight)
    font.setItalic(italic)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return font


def app_title_font(size: int = 32) -> QFont:
    return create_font(get_agrandir_family(), size, QFont.Weight.Bold)


def page_heading_font(size: int = 24) -> QFont:
    return create_font(get_agrandir_family(), size, QFont.Weight.DemiBold)


def section_heading_font(size: int = 18) -> QFont:
    return create_font(get_open_sauce_family(), size, QFont.Weight.DemiBold)


def body_font(size: int = 14) -> QFont:
    return create_font(get_open_sauce_family(), size, QFont.Weight.Normal)


def body_bold_font(size: int = 14) -> QFont:
    return create_font(get_open_sauce_family(), size, QFont.Weight.DemiBold)


def table_header_font(size: int = 12) -> QFont:
    return create_font(get_open_sauce_family(), size, QFont.Weight.DemiBold)


def table_content_font(size: int = 12) -> QFont:
    return create_font(get_open_sauce_family(), size, QFont.Weight.Normal)


def small_label_font(size: int = 11) -> QFont:
    return create_font(get_open_sauce_family(), size, QFont.Weight.Normal)


def button_font(size: int = 13) -> QFont:
    return create_font(get_open_sauce_family(), size, QFont.Weight.Medium)


def monospace_font(size: int = 12) -> QFont:
    return create_font("JetBrains Mono" if QFontDatabase.hasFamily("JetBrains Mono") else "Consolas", size)


FONT_SIZES = {
    "app_title": 32,
    "page_heading": 24,
    "section_heading": 18,
    "subsection_heading": 16,
    "body": 14,
    "body_small": 13,
    "table_header": 12,
    "table_content": 12,
    "label": 11,
    "button": 13,
    "small": 10,
    "tiny": 9,
}

FONT_WEIGHTS = {
    "light": QFont.Weight.Light,
    "normal": QFont.Weight.Normal,
    "medium": QFont.Weight.Medium,
    "demibold": QFont.Weight.DemiBold,
    "bold": QFont.Weight.Bold,
}