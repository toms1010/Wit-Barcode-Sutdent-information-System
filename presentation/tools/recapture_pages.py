#!/usr/bin/env python3
"""Recapture populated Enrollment and Assessment pages (real app, live DB)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

OUT = PROJECT / "presentation" / "screenshots"


def pump(app, seconds=1.0):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main():
    from styles import apply_theme
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    apply_theme(app)
    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    pump(app, 3.0)

    for key, name in (("enrollment", "09_enrollment.png"),
                      ("assessment", "11_assessment_fees.png")):
        win.navigate_to(key)
        pump(app, 1.8)
        page = win.pages[key]
        if page.student_list.rowCount():
            page.student_list.selectRow(0)
        pump(app, 3.0)
        win.grab().save(str(OUT / name), "PNG")
        student = page.current_student
        print(f"[ok] {name}: {student.full_name if student else 'no selection'}")

    win.close()


if __name__ == "__main__":
    main()
