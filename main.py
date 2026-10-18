#!/usr/bin/env python3
"""
WIT Barcode Student Information System
Main entry point
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from config.logging_config import setup_logging
from config.settings import settings
from styles import apply_theme
from ui.main_window import MainWindow


def main():
    # Setup logging first
    logger = setup_logging()
    logger.info("Starting WIT Barcode Student Information System")
    
    # Create application
    app = QApplication(sys.argv)
    app.setApplicationName(settings.APP_NAME)
    app.setApplicationVersion(settings.APP_VERSION)
    app.setOrganizationName("Western Institute of Technology")
    
    # Apply theme (loads fonts and stylesheet)
    apply_theme(app)
    
    # Create and show main window
    window = MainWindow()
    window.show()
    
    logger.info("Application started successfully")
    
    # Run event loop
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())