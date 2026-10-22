#!/usr/bin/env python3
"""Supplemental genuine captures: tabs, ID-card service output, YOLO inference."""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
from PySide6.QtWidgets import QApplication

OUT = PROJECT / "presentation" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)


def pump(app, seconds=1.0):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def save(widget, name):
    widget.grab().save(str(OUT / name), "PNG")
    print(f"[ok] {name} ({widget.width()}x{widget.height()})")


def main():
    from styles import apply_theme
    from ui.main_window import MainWindow
    from services.student_service import StudentService

    app = QApplication(sys.argv)
    apply_theme(app)
    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    pump(app, 3.0)

    students = StudentService().search_students(limit=10)

    # ---- dashboard KPIs (crop above the student table) ---------------------
    try:
        win.navigate_to("dashboard")
        pump(app, 2.0)
        pm = win.grab()
        pm.copy(0, 0, pm.width(), 372).save(str(OUT / "01b_dashboard_kpis.png"), "PNG")
        print("[ok] 01b_dashboard_kpis.png")
    except Exception:
        traceback.print_exc()

    # ---- training center tabs ---------------------------------------------
    try:
        win.navigate_to("training")
        pump(app, 2.0)
        page = win.pages["training"]
        page.tabs.setCurrentIndex(1)
        pump(app, 0.8)
        save(win, "18_training_config.png")
        page.tabs.setCurrentIndex(2)
        pump(app, 1.2)
        save(win, "19_models_registry.png")
        page.tabs.setCurrentIndex(0)
        pump(app, 1.2)
        save(win, "20_dataset_tab.png")
    except Exception:
        traceback.print_exc()

    # ---- settings tabs -----------------------------------------------------
    try:
        win.navigate_to("settings")
        pump(app, 1.5)
        page = win.pages["settings"]
        page.tabs.setCurrentIndex(1)
        pump(app, 0.8)
        save(win, "21_settings_database.png")
        page.tabs.setCurrentIndex(2)
        pump(app, 0.8)
        save(win, "22_settings_scanner.png")
    except Exception:
        traceback.print_exc()

    # ---- genuine ID card service renders (front + back) --------------------
    try:
        from services.id_card_service import IdCardService
        from models.id_card import CardSide

        svc = IdCardService()
        student = students[0]
        for side, fname in [(CardSide.FRONT, "16a_id_card_front.png"),
                            (CardSide.BACK, "16b_id_card_back.png")]:
            content = svc.build_content(student, side)
            result = svc.render(content, dpi=300, verify_barcode=(side is CardSide.FRONT))
            result.image.save(str(OUT / fname), "PNG")
            print(f"[ok] {fname} verified={result.barcode_verified} warnings={result.warnings}")
    except Exception:
        traceback.print_exc()

    # ---- YOLO inference output with bounding boxes ------------------------
    try:
        from services.yolo_detector import YOLODetectorService

        det = YOLODetectorService()
        det.load_active_model()
        card_path = PROJECT / "assets" / "images" / "Id_1.png"
        img = cv2.imread(str(card_path))
        dets = det.detect(img)
        print("detections:", [(d.bbox, round(d.confidence, 4), d.class_name) for d in dets])
        annotated = det.draw_detections(img, dets)
        # Scale up 2x so the label is readable when embedded on a slide.
        annotated = cv2.resize(annotated, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        cv2.imwrite(str(OUT / "23_yolo_inference_boxes.png"), annotated)
        print("[ok] 23_yolo_inference_boxes.png")
    except Exception:
        traceback.print_exc()

    try:
        win.shutdown()
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    main()
