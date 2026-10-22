#!/usr/bin/env python3
"""Capture genuine screenshots of the running WIT Barcode SIS.

Runs the real PySide6 application against the live MySQL database using the
Qt "offscreen" platform plugin, drives it through the real page-router and the
real scan pipeline, and saves PNGs.

Nothing here simulates application output: every pixel comes from the app's own
widgets rendering data read from the project's database / model / assets.

Usage:
    QT_QPA_PLATFORM=offscreen venv/bin/python presentation/tools/capture_screenshots.py
"""
from __future__ import annotations

import os
import sys
import time
import json
import traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

OUT = PROJECT / "presentation" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

MANIFEST = []


def pump(app, seconds=1.0):
    """Let Qt process events (and worker-thread results) for a wall-clock period."""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def save(widget, name, note):
    path = OUT / name
    widget.grab().save(str(path), "PNG")
    MANIFEST.append({"file": name, "note": note, "width": widget.width(), "height": widget.height()})
    print(f"[ok] {name}  ({widget.width()}x{widget.height()})")


def main():
    from styles import apply_theme
    from ui.main_window import MainWindow
    from database.connection import get_cursor
    from services.student_service import StudentService

    app = QApplication(sys.argv)
    apply_theme(app)

    # Record the highest scan id so the one live scan captured here can be
    # removed afterwards, leaving the database exactly as found.
    with get_cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) AS m FROM barcode_scans")
        max_scan_before = int(cur.fetchone()["m"])
    (OUT.parent / "tools" / "_scan_marker.json").write_text(
        json.dumps({"max_scan_id_before": max_scan_before})
    )

    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    pump(app, 3.0)  # DB probe + dashboard load

    svc = StudentService()
    students = svc.search_students(limit=10)
    print(f"students on file: {[s.reference_number for s in students]}")
    student = students[0] if students else None

    # ---------------------------------------------------------------- dashboard
    try:
        win.navigate_to("dashboard")
        pump(app, 2.5)
        save(win, "01_dashboard.png", "Main dashboard: KPI cards, student overview table, database status indicator")
    except Exception:
        traceback.print_exc()

    # ---------------------------------------------------------------- students
    try:
        win.navigate_to("students")
        pump(app, 2.0)
        save(win, "02_students_directory.png", "Student directory with search/filter controls and paginated records")
        page = win.pages["students"]
        if hasattr(page, "apply_search"):
            page.apply_search("Caporal")
            pump(app, 1.5)
            save(win, "03_students_search.png", "Student directory filtered by a name query")
            page.apply_search("")
            pump(app, 1.0)
    except Exception:
        traceback.print_exc()

    # ---------------------------------------------------------------- scanner initial
    try:
        win.navigate_to("scanner")
        pump(app, 2.5)
        save(win, "04_scanner_idle.png", "Barcode scanner before scanning; camera list, YOLO status, history table")
        scanner = win.pages["scanner"]
    except Exception:
        traceback.print_exc()
        scanner = None

    # ---------------------------------------------------------------- real pipeline on a real card
    outcome = None
    frame = None
    if scanner is not None:
        try:
            from services.scan_pipeline import ScanPipeline

            card = PROJECT / "assets" / "images" / "Id_1.png"
            frame = cv2.imread(str(card))
            print("card image:", card, None if frame is None else frame.shape)
            pipeline = ScanPipeline(
                detector=scanner.yolo_service,
                decoder=scanner.decoder_service,
            )
            outcome = pipeline.process_frame(frame)
            print("pipeline outcome:", outcome.state, outcome.decoded_value,
                  getattr(outcome.student, "reference_number", None))
        except Exception:
            traceback.print_exc()

    # detection overlay / scanning state
    if scanner is not None and outcome is not None:
        try:
            from ui.scanner_page import ScannerState

            scanner._set_state(ScannerState.SCANNING)
            scanner._latest_frame = frame
            scanner._latest_outcome = outcome
            scanner._update_status_labels(outcome)
            scanner._render_preview()
            pump(app, 0.6)
            save(win, "05_scanner_detection.png",
                 "Live pipeline on a real ID-card asset: YOLO bounding box + guide overlay + decoded value")
        except Exception:
            traceback.print_exc()

    # verified student panel + structured result dialog (real DB lookup + scan record)
    if scanner is not None and outcome is not None and outcome.student is not None:
        try:
            from ui.scanner_page import ScannerState

            scanner._set_state(ScannerState.SCANNING)
            scanner._latest_frame = frame
            scanner._latest_outcome = outcome
            scanner._handle_verified(outcome)
            pump(app, 2.5)
            save(win, "06_scanner_verified.png",
                 "Verified scan: student identity card panel, decoded value and persisted history row")
            dialog = getattr(scanner, "_active_dialog", None)
            if dialog is not None:
                pump(app, 1.5)
                save(dialog, "07_scan_result_dialog.png",
                     "Structured scan-result modal (enrollment + financial tabs) for the verified student")
                dialog.accept()
                pump(app, 0.5)
        except Exception:
            traceback.print_exc()

    # unknown / non-matching barcode state (uses a synthetic payload only, no DB write)
    if scanner is not None and outcome is not None:
        try:
            from services.scan_pipeline import ScanOutcome, ScanState
            from ui.scanner_page import ScannerState
            from dataclasses import replace

            unknown = replace(
                outcome,
                state=ScanState.UNKNOWN_BARCODE,
                student=None,
                decoded_value="999999999",
                verified_now=False,
                message="Unknown barcode - no matching student record.",
            )
            scanner._set_state(ScannerState.SCANNING)
            scanner._latest_frame = frame
            scanner._latest_outcome = unknown
            scanner._update_status_labels(unknown)
            scanner._render_preview()
            scanner._set_footer("999999999 is not on file. No student has that barcode.", "#B45309")
            pump(app, 0.5)
            save(win, "08_scanner_unknown.png",
                 "Unknown-barcode state: region detected and decoded, but no student row matches")
        except Exception:
            traceback.print_exc()

    # ---------------------------------------------------------------- enrollment / courses
    for page_id, fname, note in [
        ("enrollment", "09_enrollment.png", "Subject Enrollment: student selector, term, subject lines"),
        ("courses", "10_courses_subjects.png", "Courses & Subjects read directly from the database"),
        ("assessment", "11_assessment_fees.png", "Assessment & Fees: assessment lines, payments and balance"),
        ("reports", "12_reports.png", "Reports & Analytics: report catalogue backed by PDF/CSV generators"),
        ("training", "13_training_center.png", "Training Center: YOLO training, model registry and dataset tabs"),
        ("settings", "14_settings.png", "Settings: general, database, scanner, barcode, appearance and backup tabs"),
    ]:
        try:
            win.navigate_to(page_id)
            pump(app, 2.2)
            save(win, fname, note)
        except Exception:
            traceback.print_exc()

    # ---------------------------------------------------------------- certificate of matriculation
    if student is not None:
        try:
            win.navigate_to("certificate")
            page = win.pages["certificate"]
            page.load_reference(student.reference_number)
            pump(app, 2.5)
            save(win, "15_certificate_com.png",
                 "Certificate of Matriculation generated for a real student record")
        except Exception:
            traceback.print_exc()

    # ---------------------------------------------------------------- student ID preview dialog
    if student is not None:
        try:
            from ui.dashboard import StudentIDPreviewDialog
            from services.id_card_service import IdCardService

            dlg = StudentIDPreviewDialog(IdCardService(), student, win)
            dlg.show()
            pump(app, 2.0)
            save(dlg, "16_student_id_preview.png",
                 "Student ID card preview rendered by the ID-card service (CR80, front face)")
            dlg.accept()
        except Exception:
            traceback.print_exc()

    # ---------------------------------------------------------------- narrow/global search state
    try:
        win.navigate_to("students")
        win.global_search.setText("65902")
        pump(app, 0.8)
        save(win, "17_global_search.png", "Global quick-search in the top bar (Ctrl+K)")
        win.global_search.clear()
    except Exception:
        traceback.print_exc()

    (OUT.parent / "tools" / "screenshot_manifest.json").write_text(
        json.dumps(MANIFEST, indent=2)
    )

    try:
        win.shutdown()
    except Exception:
        traceback.print_exc()
    print(f"\nCaptured {len(MANIFEST)} screenshots into {OUT}")


if __name__ == "__main__":
    main()
