#!/usr/bin/env python3
"""
Condensed Project_Documentation.docx — 5 to 10 pages with MORE screenshots.
Same formatting: A4, Times New Roman 12pt, 1.5 spacing, justified, 1" margins.
"""

from __future__ import annotations
from pathlib import Path
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Inches, Pt, Cm, RGBColor

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "documentation" / "Project_Documentation.docx"
SCREENSHOTS_DIR = ROOT / "documentation" / "screenshots"
DIAGRAMS_DIR = ROOT / "documentation" / "System_Diagrams"

PAGE_W, PAGE_H = Cm(21.0), Cm(29.7)
MARGIN = Inches(1)

doc = Document()
sec = doc.sections[0]
sec.page_width = PAGE_W
sec.page_height = PAGE_H
sec.top_margin = MARGIN
sec.bottom_margin = MARGIN
sec.left_margin = MARGIN
sec.right_margin = MARGIN

# Styles
style = doc.styles['Normal']
style.font.name = 'Times New Roman'
style.font.size = Pt(12)
style.paragraph_format.line_spacing = 1.5
style.paragraph_format.space_after = Pt(6)
style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

for level, size, bold, italic, sb, sa in [(1, 16, True, False, 18, 8), (2, 14, True, False, 12, 6), (3, 12, True, True, 8, 4)]:
    h = doc.styles[f'Heading {level}']
    h.font.name = 'Times New Roman'
    h.font.size = Pt(size)
    h.font.bold = bold
    h.font.italic = italic
    h.font.color.rgb = RGBColor(0, 0, 0)
    h.paragraph_format.space_before = Pt(sb)
    h.paragraph_format.space_after = Pt(sa)
    h.paragraph_format.keep_with_next = True

# Page numbers in footer
footer = sec.footer
p = footer.paragraphs[0]
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run()
fldChar1 = OxmlElement('w:fldChar')
fldChar1.set(qn('w:fldCharType'), 'begin')
run._r.append(fldChar1)
run2 = p.add_run()
instrText = OxmlElement('w:instrText')
instrText.set(qn('xml:space'), 'preserve')
instrText.text = ' PAGE '
run2._r.append(instrText)
run3 = p.add_run()
fldChar2 = OxmlElement('w:fldChar')
fldChar2.set(qn('w:fldCharType'), 'end')
run3._r.append(fldChar2)

def add_toc():
    p = doc.add_paragraph()
    run = p.add_run()
    f1 = OxmlElement('w:fldChar'); f1.set(qn('w:fldCharType'), 'begin'); run._r.append(f1)
    r2 = p.add_run()
    it = OxmlElement('w:instrText'); it.set(qn('xml:space'), 'preserve'); it.text = ' TOC \\o "1-3" \\h \\z \\u '; r2._r.append(it)
    r3 = p.add_run()
    f2 = OxmlElement('w:fldChar'); f2.set(qn('w:fldCharType'), 'end'); r3._r.append(f2)
    doc.add_page_break()

def add_fig(path, caption, width=4.5, height=2.5):
    """Add a figure with compact dimensions to fit more screenshots."""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    from PIL import Image
    im = Image.open(path)
    iw, ih = im.size
    aspect = iw / ih
    max_w = Inches(width)
    max_h = Inches(height)
    scale = min(max_w / iw * 72, max_h / ih * 72) / 72
    run = p.add_run()
    run.add_picture(str(path), width=Inches(iw * scale))
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cr = cap.add_run(caption)
    cr.italic = True
    cr.font.size = Pt(9)
    cr.font.name = 'Times New Roman'
    cap.paragraph_format.space_after = Pt(6)
    cap.paragraph_format.line_spacing = 1.0

def add_para(text):
    p = doc.add_paragraph(text)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    return p

# ─── TITLE PAGE ──────────────────────────────────────────────────────────
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('WESTERN INSTITUTE OF TECHNOLOGY')
r.bold = True; r.font.size = Pt(16); r.font.name = 'Times New Roman'
p.paragraph_format.space_after = Pt(4)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('WIT Barcode Student Information System')
r.bold = True; r.font.size = Pt(14); r.font.name = 'Times New Roman'
p.paragraph_format.space_after = Pt(4)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('System Analysis, Design, and Implementation Documentation')
r.italic = True; r.font.size = Pt(12); r.font.name = 'Times New Roman'
p.paragraph_format.space_after = Pt(12)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('Version 1.0.0  |  22 October 2026')
r.font.size = Pt(11); r.font.name = 'Times New Roman'
p.paragraph_format.space_after = Pt(12)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('Python 3.14.4 · PySide6 6.12 · MySQL 8.4 · YOLOv8n · Ubuntu 26.04')
r.font.size = Pt(10); r.font.name = 'Times New Roman'
p.paragraph_format.space_after = Pt(20)

doc.add_page_break()

# ─── TABLE OF CONTENTS ──────────────────────────────────────────────────
doc.add_heading('Table of Contents', level=1)
add_toc()

# ─── CHAPTER I: INTRODUCTION ────────────────────────────────────────────
doc.add_heading('CHAPTER I — INTRODUCTION', level=1)

doc.add_heading('1.1 Background', level=2)
add_para('The WIT Barcode Student Information System replaces manual verification of printed '
         'identification panels with a desktop application that holds student, enrolment, assessment, '
         'and payment records in a relational database and verifies a physical panel automatically by '
         'detecting its Code 39 barcode, decoding it, and resolving the decoded payload to exactly '
         'one student row before any identity claim is presented to the operator.')

doc.add_heading('1.2 Objectives', level=2)
add_para('O1 — Analyse the manual workflow. O2 — Design a normalised relational schema. '
         'O3 — Implement the desktop application (10 pages). O4 — Develop the detection and '
         'verification pipeline (YOLOv8n + CODE39 decode + exact SQL lookup). '
         'O5 — Implement detector training and promotion lifecycle. O6 — Verify functional '
         'correctness and scanner against real panels. O7 — Document with models and figure '
         'catalogue. O8 — Identify limitations and discrepancies.')

doc.add_heading('1.3 Scope', level=2)
add_para('Covers: desktop app, MySQL schema (19 tables), service layer, UI (10 pages + 4 dialogs), '
         'detector training tooling, and automated test suite (557 tests). '
         'Excludes: authentication, logout, multi-campus, web/mobile, independent accuracy audit.')

doc.add_heading('1.4 Conceptual Framework', level=2)
add_fig(DIAGRAMS_DIR / "01_system_context.png",
        "Figure 1. System context diagram — actors, system, and external systems.", 5.0, 3.0)

# ─── CHAPTER II: METHODS ─────────────────────────────────────────────────
doc.add_heading('CHAPTER II — METHODS', level=1)

doc.add_heading('2.1 Development Environment', level=2)
add_para('Python 3.14.4 · PySide6 6.12 · MySQL 8.4.11 · Ultralytics 8.4.177 (YOLOv8n) · '
         'PyTorch 2.14 · OpenCV 5.0 · NumPy 2.5 · Pillow 12.3 · zxing-cpp 3.1 · pyzbar 0.1.9 · '
         'ReportLab 5.0 · matplotlib 3.11 · python-dotenv 1.2.4 · pytest 9.1.1 · '
         'Ubuntu 26.04.1 LTS · NVIDIA RTX 2050 (4 GB).')

doc.add_heading('2.2 Architecture', level=2)
add_para('Three-tier layered architecture: Presentation (PySide6 widgets) → Services (pure Python, '
         'Qt-free) → Data Access (mysql.connector pool of 10, raw SQL, no ORM). '
         'No widget writes SQL; no service imports a widget.')

doc.add_heading('2.3 Data Model', level=2)
add_para('19 InnoDB tables with declared foreign keys: users, students, student_contacts, courses, '
         'subjects, enrollments, enrollment_subjects, barcodes, barcode_scans, assessment_categories, '
         'assessments, assessment_items, payments, payment_schedules, audit_logs, system_settings, '
         'yolo_models, id_cards, dataset_images. The Certificate of Matriculation is a view over '
         'these rows; totals are recomputed and cross-checked, not trusted.')

doc.add_heading('2.4 Barcode Detection Pipeline', level=2)
add_para('YOLOv8n at 0.75 confidence (raised from 0.35 on measured gap: highest false box 0.606, '
         'lowest true box 0.897) → geometric gate (80×24 px minimum, 88% gate ratio) → 30% margin '
         'crop → multi-strategy decode (zxing-cpp then pyzbar, ~15 transforms) → CODE39 '
         'reconstruction from module-width voting → exact SQL lookup → session latch → history row → '
         'success cue → Scan Result panel.')

doc.add_heading('2.5 Verification', level=2)
add_para('Full automated test suite: 557 passed, 2 warnings, 185.29 s (24 test modules). '
         'Scanner verified against real institute panels — every decoded payload resolves to exactly '
         'one student row. Detector: 22.49 ms mean GPU, 74.46 fps live, 9.82× faster than CPU.')

# ─── CHAPTER III: RESULTS ────────────────────────────────────────────────
doc.add_heading('CHAPTER III — RESULTS', level=1)

doc.add_heading('3.1 Dashboard', level=2)
add_para('Aggregate counts (Total/Active/Registered/Missing), searchable & filterable student table, '
         'detail panel with personal/academic/ID info. Payload 000065xxx shown per student. '
         'Defect: "Oktubre" renders under en_PH locale (DEF-02).')
add_fig(SCREENSHOTS_DIR / "01_dashboard.png", "Figure 9. Dashboard — overview and student directory.", 4.5, 2.5)
add_fig(SCREENSHOTS_DIR / "02_dashboard_student_selected.png", "Figure 10. Dashboard — student detail panel.", 4.5, 2.5)

doc.add_heading('3.2 Student Management', level=2)
add_para('Directory with per-row View/Edit/Delete, Add/Edit dialogs with photo & contacts, '
         'mandatory-field validation, audit log for mapping changes.')
add_fig(SCREENSHOTS_DIR / "03_students_directory.png", "Figure 11. Students — searchable directory.", 4.5, 2.5)
add_fig(SCREENSHOTS_DIR / "05_student_dialog_add.png", "Figure 13. Add Student dialog.", 4.5, 2.5)

doc.add_heading('3.3 Enrolment', level=2)
add_para('Student picker → enrolment header (status, credits, date) → subject table with schedule, '
         'room, period, fees. Add Subject dialog captures section, time, days, room, period, lab/other fee.')
add_fig(SCREENSHOTS_DIR / "07_enrollment_select_student.png", "Figure 15. Enrolment — student picker.", 4.5, 2.5)
add_fig(SCREENSHOTS_DIR / "08_enrollment_subjects.png", "Figure 16. Enrolled subjects for the selected student.", 4.5, 2.5)

doc.add_heading('3.4 Certificate of Matriculation', level=2)
add_para('Composed from enrolment rows + assessment items + payment schedule + payments. Shows '
         'student profile, courses (7 subjects, 20 units), assessment breakdown (tuition ₱13,340.00, '
         'misc ₱8,674.72, lab ₱5,618.59), discounts, instalments (4), and totals. '
         'Unresolved totals notice: instalment sum ₱28,122.82 ≠ assessed ₱27,633.31 (gap ₱489.51).')
add_fig(SCREENSHOTS_DIR / "11_certificate_of_matriculation.png", "Figure 19. Certificate of Matriculation.", 4.5, 2.5)

doc.add_heading('3.5 Assessment & Fees', level=2)
add_para('Charges by category (Tuition, Misc, Lab, CDA), instalment schedule with due dates/status, '
         'payment recording (amount, date, method, reference, notes), balance recomputation. '
         'All 4 instalments overdue (no payments recorded).')
add_fig(SCREENSHOTS_DIR / "12_assessment_fees.png", "Figure 20. Assessment and Fees — charges, schedule, payments.", 4.5, 2.5)
add_fig(SCREENSHOTS_DIR / "13_record_payment_dialog.png", "Figure 21. Record Payment dialog.", 4.5, 2.5)

doc.add_heading('3.6 Reports', level=2)
add_para('5 reports: Certificate of Matriculation, Enrollment Summary, Student Barcode Report, '
         'Assessment Report, Barcode Scan Log. Reports read from the same rows as the UI.')
add_fig(SCREENSHOTS_DIR / "14_reports_analytics.png", "Figure 22. Reports and Analytics catalogue.", 4.5, 2.5)

doc.add_heading('3.7 Barcode Scanner', level=2)
add_para('Camera (1280×720) or uploaded card image → YOLO detection → gate → crop → decode → '
         'exact SQL lookup → session latch → history row → success cue → Scan Result panel '
         '(identity + subjects + fees). Tested on real panel: payload 000065412 → Javy Danielle N. '
         'Caporal (WIT-2024-001). 49 success / 24 not-found scans in barcode_scans table.')
add_fig(SCREENSHOTS_DIR / "16_scanner_idle.png", "Figure 24. Scanner — idle state.", 4.5, 2.5)
add_fig(SCREENSHOTS_DIR / "17_scanner_verified.png", "Figure 25. Scanner — real card decoded and verified.", 4.5, 2.5)
add_fig(SCREENSHOTS_DIR / "18_scan_result_dialog.png", "Figure 26. Scan Result panel — identity, subjects, fees.", 4.5, 2.5)
add_fig(SCREENSHOTS_DIR / "19_scanner_camera_running.png", "Figure 27. Scanner — live camera running.", 4.5, 2.5)

doc.add_heading('3.8 Training Center', level=2)
add_para('Dataset import/annotation/validation, training (epochs, imgsz, batch, device) with promotion '
         'gate (zero false positives on barcode-free docs + ≥ held-out decode rate), model registry '
         '(activate/deactivate). Note: dataset_images and yolo_models tables empty despite working '
         'corpus and best.pt on disk (DEF-04).')
add_fig(SCREENSHOTS_DIR / "20_training_center_dataset.png", "Figure 28. Training Center — dataset tab.", 4.5, 2.5)
add_fig(SCREENSHOTS_DIR / "21_training_center_training.png", "Figure 29. Training Center — training config.", 4.5, 2.5)

doc.add_heading('3.9 Settings', level=2)
add_para('App name, version, default academic year/semester. No authentication exists (users table '
         'empty, DEFAULT_USER_ID=1; logout reports "to be implemented").')
add_fig(SCREENSHOTS_DIR / "23_settings.png", "Figure 31. Settings page.", 4.5, 2.5)

# ─── CHAPTER IV: DISCUSSION ─────────────────────────────────────────────
doc.add_heading('CHAPTER IV — DISCUSSION', level=1)

doc.add_heading('4.1 Summary', level=2)
add_para('The system is built and running against a live MySQL database. The core mechanism — decode '
         'a printed CODE39 symbol and resolve it to one student row by exact match — was demonstrated '
         'on a real institute asset. 557 tests pass. The normalised schema makes inconsistencies '
         'detectable: the ₱489.51 instalment/assessed-total gap is surfaced, not hidden.')

doc.add_heading('4.2 Key Limitations', level=2)
add_para('Self-reported verification (no independent audit). Only 3 students in the database. '
         'Camera tested under indoor lighting on a built-in webcam. Financial data is seeded, not '
         'verified (registrar must confirm). Barcode Generator page is broken (AttributeError). '
         'No authentication, no logout, no multi-user support.')

doc.add_heading('4.3 Recommendations', level=2)
add_para('R1 Add authentication & role separation. R2 Fix the 2 code defects (barcode generator page, '
         'locale month). R3 Registrar reconciles ₱489.51 gap. R4 Reissue 2 defective ID panels. '
         'R5 Populate model registry & dataset table. R6 Institutionalise threshold re-measurement. '
         'R7 Broaden the corpus. R8 Populate subject catalogue fields.')

doc.add_heading('4.4 Known Defects', level=2)
add_para('DEF-01: Barcode Generator page crash (ui/barcode_generator_page.py:536). '
         'DEF-02: "Oktubre" locale bug (ui/dashboard.py:847,858). '
         'DEF-03: ₱489.51 discrepancy (all 3 students). '
         'DEF-04: Empty yolo_models/dataset_images tables. '
         'DEF-05: No authentication or logout. '
         'DEF-06: Printed reference 000001 ≠ DB 65902 on 2 panels. '
         'DEF-07: Subject hours/department empty. '
         'DEF-08: WIT-2024-003 assessment seeded from another student.')

doc.save(OUTPUT_PATH)
print(f"Saved: {OUTPUT_PATH}")