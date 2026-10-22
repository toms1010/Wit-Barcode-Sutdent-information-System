#!/usr/bin/env python3
"""Build the 35-slide WIT Barcode SIS technical deck from captured evidence.

Every image embedded here was produced by the running application, the live
database, the checked-in model, or matplotlib plots of measured values. Text
claims are tied to the same evidence; known defects are stated on the slide
where they matter.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[2]
PRES = ROOT / "presentation"
SHOTS = PRES / "screenshots"
CHARTS = PRES / "charts"
DIAGRAMS = PRES / "diagrams"
OUT = PRES / "WIT_Barcode_Student_Information_System_Technical_Presentation.pptx"

W, H = 13.333, 7.5
M = 0.62

INK = RGBColor(0x0F, 0x17, 0x2A)
SLATE = RGBColor(0x47, 0x55, 0x69)
MUTED = RGBColor(0x64, 0x74, 0x8B)
ORANGE = RGBColor(0xF9, 0x73, 0x16)
DARK = RGBColor(0xAD, 0x4D, 0x05)
GOLD = RGBColor(0xFF, 0xD2, 0x4D)
CREAM = RGBColor(0xFF, 0xF7, 0xED)
PAPER = RGBColor(0xFF, 0xFF, 0xFF)
BORDER = RGBColor(0xE2, 0xE8, 0xF0)
SOFT = RGBColor(0xF8, 0xFA, 0xFC)
GREEN = RGBColor(0x16, 0xA3, 0x4A)
PALE_GREEN = RGBColor(0xDC, 0xFC, 0xE7)
RED = RGBColor(0xDC, 0x26, 0x26)
PALE_RED = RGBColor(0xFE, 0xE2, 0xE2)
BLUE = RGBColor(0x25, 0x63, 0xEB)
PALE_BLUE = RGBColor(0xDB, 0xEA, 0xFE)
PALE_GOLD = RGBColor(0xFE, 0xF3, 0xC7)

FONT = "Arial"
TOTAL = 35


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def rgb_hex(color: RGBColor) -> str:
    return f"{color[0]:02X}{color[1]:02X}{color[2]:02X}"


def rect(slide, x, y, w, h, fill=None, line=None, radius=False, lw=0.8):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        shape.fill.background()
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(lw)
    if radius:
        try:
            shape.adjustments[0] = 0.08
        except Exception:
            pass
    shape.shadow.inherit = False
    return shape


def tx(slide, x, y, w, h, text, size=14, color=INK, bold=False, italic=False,
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font=FONT, spacing=1.0,
       margin=0.0, space_after=0):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(margin)
    tf.margin_right = Inches(margin)
    tf.margin_top = Inches(0)
    tf.margin_bottom = Inches(0)
    tf.vertical_anchor = anchor
    lines = str(text).split("\n")
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        p.space_after = Pt(space_after)
        r = p.add_run()
        r.text = line
        r.font.name = font
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.italic = italic
        r.font.color.rgb = color
    return box


def bullets(slide, x, y, w, h, items, size=14, color=SLATE, gap=8, bullet_color=ORANGE,
            bold_lead=False, line_spacing=1.05):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(gap)
        p.line_spacing = line_spacing
        if isinstance(item, tuple):
            lead, rest = item
        else:
            lead, rest = None, item
        b = p.add_run()
        b.text = "•  "
        b.font.name = FONT
        b.font.size = Pt(size)
        b.font.bold = True
        b.font.color.rgb = bullet_color
        if lead:
            r1 = p.add_run()
            r1.text = lead
            r1.font.name = FONT
            r1.font.size = Pt(size)
            r1.font.bold = bold_lead
            r1.font.color.rgb = INK
        r = p.add_run()
        r.text = rest
        r.font.name = FONT
        r.font.size = Pt(size)
        r.font.color.rgb = color
    return box


def header(slide, number, kicker, title, subtitle=None):
    rect(slide, 0, 0, W, 0.075, fill=ORANGE)
    rect(slide, M, 0.30, 0.055, 0.27, fill=ORANGE)
    tx(slide, M + 0.13, 0.27, 10.5, 0.28, kicker.upper(), size=8.8, color=DARK, bold=True)
    tx(slide, M, 0.53, 12.1, 0.62, title, size=24.5, color=INK, bold=True, spacing=0.92)
    if subtitle:
        tx(slide, M, 1.13, 12.1, 0.42, subtitle, size=10.8, color=SLATE, spacing=1.0)
    footer(slide, number)


def footer(slide, number):
    rect(slide, M, 7.10, W - 2 * M, 0.012, fill=BORDER)
    tx(slide, M, 7.16, 8.6, 0.22,
       "WIT Barcode Student Information System  ·  Technical Presentation  ·  October 2026",
       size=7.8, color=MUTED)
    tx(slide, W - M - 1.3, 7.16, 1.3, 0.22, f"{number:02d} / {TOTAL}", size=7.8,
       color=MUTED, align=PP_ALIGN.RIGHT)


def kpi(slide, x, y, w, h, value, label, note="", accent=ORANGE, fill=CREAM):
    rect(slide, x, y, w, h, fill=fill, line=BORDER, radius=True)
    rect(slide, x, y + 0.12, 0.06, h - 0.24, fill=accent)
    tx(slide, x + 0.20, y + 0.18, w - 0.35, 0.52, value, size=25, color=INK, bold=True,
       spacing=0.9)
    tx(slide, x + 0.20, y + 0.70, w - 0.35, 0.28, label.upper(), size=8.4, color=DARK, bold=True)
    if note:
        tx(slide, x + 0.20, y + 0.98, w - 0.35, h - 1.02, note, size=8.6, color=SLATE,
           spacing=1.0)


def chip(slide, x, y, w, text, fill=CREAM, color=DARK, size=8.6):
    rect(slide, x, y, w, 0.30, fill=fill, line=BORDER, radius=True)
    tx(slide, x + 0.05, y + 0.045, w - 0.10, 0.22, text, size=size, color=color, bold=True,
       align=PP_ALIGN.CENTER)


def image_panel(slide, path, x, y, w, h, pad=0.06, border=BORDER, fill=PAPER):
    rect(slide, x, y, w, h, fill=fill, line=border, radius=False)
    iw, ih = Image.open(path).size
    scale = min((w - 2 * pad) / iw, (h - 2 * pad) / ih)
    dw, dh = iw * scale, ih * scale
    dx = x + (w - dw) / 2
    dy = y + (h - dh) / 2
    slide.shapes.add_picture(str(path), Inches(dx), Inches(dy), Inches(dw), Inches(dh))


def caption(slide, text, x=None, y=6.82, w=None, color=MUTED):
    tx(slide, x if x is not None else M, y, w if w is not None else W - 2 * M, 0.24,
       text, size=7.4, color=color, italic=True, spacing=1.0)


def section_tag(slide, text, x, y, w=2.0):
    tx(slide, x, y, w, 0.24, text.upper(), size=8.2, color=ORANGE, bold=True)


def table(slide, x, y, w, h, rows, col_widths, header_fill=INK, header_color=PAPER,
          size=9.0, header_size=8.6, row_h=0.32, zebra=True, align=None):
    nrows, ncols = len(rows), len(rows[0])
    shape = slide.shapes.add_table(nrows, ncols, Inches(x), Inches(y), Inches(w), Inches(h))
    tbl = shape.table
    for i, cw in enumerate(col_widths):
        tbl.columns[i].width = Inches(cw)
    for r in range(nrows):
        tbl.rows[r].height = Inches(row_h if r else max(row_h, 0.34))
        for c in range(ncols):
            cell = tbl.cell(r, c)
            cell.margin_left = Inches(0.07)
            cell.margin_right = Inches(0.05)
            cell.margin_top = Inches(0.03)
            cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            if r == 0:
                cell.fill.fore_color.rgb = header_fill
            else:
                cell.fill.fore_color.rgb = PAPER if (not zebra or r % 2) else SOFT
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = (align[c] if align else PP_ALIGN.LEFT)
            run = p.add_run()
            run.text = str(rows[r][c])
            run.font.name = FONT
            run.font.size = Pt(header_size if r == 0 else size)
            run.font.bold = r == 0
            run.font.color.rgb = header_color if r == 0 else SLATE if c else INK
    return tbl


def notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


def new_slide(prs):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = PAPER
    return slide


# --------------------------------------------------------------------------- #
# 01 title
# --------------------------------------------------------------------------- #
def slide_title(prs):
    s = new_slide(prs)
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = INK
    rect(s, 0, 0, 0.12, H, fill=ORANGE)
    rect(s, 0.75, 1.05, 1.55, 0.055, fill=GOLD)
    tx(s, 0.75, 1.22, 7.0, 0.32, "TECHNICAL SYSTEM PRESENTATION  ·  2026", size=10.5,
       color=GOLD, bold=True)
    tx(s, 0.75, 1.72, 7.35, 2.15,
       "WIT Barcode\nStudent Information\nSystem", size=39, color=PAPER, bold=True,
       spacing=0.88)
    tx(s, 0.75, 4.20, 6.55, 1.15,
       "Computer-vision student identification, subject enrollment, assessment and "
       "documentation — implemented as a local desktop application.",
       size=13.2, color=RGBColor(0xCB, 0xD5, 0xE1), spacing=1.12)
    rect(s, 0.75, 5.55, 5.55, 0.012, fill=RGBColor(0x33, 0x41, 0x55))
    tx(s, 0.75, 5.72, 6.2, 0.70,
       "Western Institute of Technology  ·  Computer Engineering\n"
       "Built from repository commit 521e66c  ·  20 October 2026",
       size=9.6, color=RGBColor(0x94, 0xA3, 0xB8), spacing=1.22)
    for i, (label, x) in enumerate([("PySide6 6.12", 0.75), ("MySQL 8.4", 2.52),
                                    ("YOLOv8n", 4.06), ("CODE39", 5.52)]):
        chip(s, x, 6.60, 1.55, label, fill=RGBColor(0x1E, 0x29, 0x3B),
             color=RGBColor(0xE2, 0xE8, 0xF0), size=8.2)

    # identity card visual
    rect(s, 8.42, 0.95, 4.46, 5.62, fill=RGBColor(0x1E, 0x29, 0x3B),
         line=RGBColor(0x33, 0x41, 0x55))
    image_panel(s, SHOTS / "16a_id_card_front.png", 8.62, 1.18, 4.06, 2.58, pad=0.02)
    image_panel(s, SHOTS / "16b_id_card_back.png", 8.62, 3.95, 4.06, 2.58, pad=0.02)
    tx(s, 8.62, 6.52, 4.06, 0.30, "CR80 card · 300 DPI service render", size=8.0,
       color=RGBColor(0x94, 0xA3, 0xB8), italic=True)
    notes(s, "Opening frame: the deck is a technical account of the system in this repository, "
             "not a product brochure. The ID cards on the right are 300 DPI renders produced by the "
             "application's own IdCardService from a real student record. The four version chips are "
             "the runtimes that were actually verified on this machine.")


# --------------------------------------------------------------------------- #
# 02 agenda
# --------------------------------------------------------------------------- #
def slide_agenda(prs):
    s = new_slide(prs)
    header(s, 2, "Navigation", "What this deck covers",
           "Five sections, 35 slides. Numbers are traced to the repository, the live database, training artefacts or captured UI.")
    parts = [
        ("01", "Context & objectives", "3–6", "Problem, goals, scope and what is deliberately not delivered."),
        ("02", "System design", "7–10", "Context boundary, stack, layered architecture and module map."),
        ("03", "Product walkthrough", "11–25", "Every working page, the scan pipeline and the documents it produces."),
        ("04", "Data & model", "26–33", "Schema, ERD, data flow, 2,551-image dataset, training and inference."),
        ("05", "Verification & roadmap", "34–35", "Test results, documented defects, limitations and next steps."),
    ]
    y = 1.72
    for i, (num, title, pages, desc) in enumerate(parts):
        rect(s, M, y, 12.1, 0.88, fill=CREAM if i % 2 == 0 else PAPER, line=BORDER, radius=True)
        tx(s, M + 0.20, y + 0.22, 0.62, 0.42, num, size=19, color=ORANGE, bold=True)
        tx(s, M + 0.96, y + 0.17, 3.35, 0.34, title, size=14.2, color=INK, bold=True)
        tx(s, M + 0.96, y + 0.51, 3.35, 0.24, desc, size=8.8, color=SLATE)
        tx(s, W - M - 1.28, y + 0.26, 1.05, 0.30, pages, size=10.0, color=DARK, bold=True,
           align=PP_ALIGN.RIGHT)
        y += 0.99
    tx(s, M, 6.72, 12.0, 0.30,
       "Evidence policy: no placeholder screenshots, no invented metrics, no claim that a module works when it is unrouted or crashing.",
       size=8.8, color=MUTED, italic=True)
    notes(s, "The deck moves from why the system exists, through how it is built and what it does, "
             "to the data and model evidence, and ends with an unvarnished test and limitations slide. "
             "The page ranges are the actual slide numbers in this file.")


# --------------------------------------------------------------------------- #
# 03 executive summary
# --------------------------------------------------------------------------- #
def slide_exec(prs):
    s = new_slide(prs)
    header(s, 3, "01 · Executive summary", "A working desktop SIS with a real vision pipeline",
           "Eight operational modules, 19 database tables and a fine-tuned detector — plus a short, documented list of gaps.")
    tiles = [
        ("45,978", "Python LOC", "122 first-party files, measured with wc -l"),
        ("19", "Database tables", "230 columns, 33 foreign keys, InnoDB"),
        ("2,551", "Dataset images", "2,515 barcode boxes across 8 variants"),
        ("531", "Tests passing", "of 553 collected; 22 post-scan failures"),
    ]
    x = M
    for value, label, note in tiles:
        kpi(s, x, 1.62, 2.92, 1.28, value, label, note)
        x += 3.06
    section_tag(s, "What is proven", M, 3.18)
    bullets(s, M, 3.46, 5.75, 2.45, [
        ("Identity path: ", "YOLO locates a card barcode, pyzbar decodes it and an exact SQL match resolves the student."),
        ("Records: ", "students, enrollment, subjects, assessment items, schedules and scan history persist in MySQL."),
        ("Documents: ", "Certificate of Matriculation, assessment reports, barcode reports and CSV scan logs."),
        ("Honesty: ", "unknown, duplicate and multi-card reads are distinct states, not false successes."),
    ], size=10.2, gap=7)
    section_tag(s, "What is not", M + 6.25, 3.18)
    rect(s, M + 6.25, 3.46, 5.85, 2.72, fill=PALE_RED, line=RGBColor(0xFE, 0xCA, 0xCA), radius=True)
    bullets(s, M + 6.45, 3.62, 5.45, 2.45, [
        ("No authentication: ", "logout is a stub and the app opens straight into the dashboard."),
        ("Generator not routed: ", "BarcodeGeneratorPage exists but no sidebar route reaches it."),
        ("Two UI defects: ", "dashboard student table and ID-card preview dialog both raise exceptions."),
        ("Core dataset has no test split: ", "the active model was selected on validation only."),
    ], size=10.0, gap=7, bullet_color=RED)
    tx(s, M, 6.44, 12.0, 0.35,
       "Bottom line: the core identification and records workflow is real and demonstrable; the hardening work is identifiable and finite.",
       size=9.2, color=DARK, bold=True)
    notes(s, "This is the claim the rest of the deck supports: a real, working local system, not a mock-up. "
             "The four KPIs are measured. The red panel is deliberately on the summary slide so the audience "
             "does not have to wait until the end to hear the limitations.")


# --------------------------------------------------------------------------- #
# 04 problem
# --------------------------------------------------------------------------- #
def slide_problem(prs):
    s = new_slide(prs)
    header(s, 4, "01 · Problem", "Why a barcode-first student system",
           "Three operational frictions the project targets: slow identity checks, fragmented records and document assembly by hand.")
    blocks = [
        ("01", "Identity checks are manual",
         "A registrar compares a printed name and reference against a card. It is slow at a counter and easy to misread when cards look alike.",
         "Target: locate the symbol, decode the payload, resolve a database row."),
        ("02", "Records live in separate places",
         "Student details, subject lines, fees, schedules and scan history are usually kept in spreadsheets or paper forms, so one fact has several versions.",
         "Target: 19 related tables with foreign keys and uniqueness constraints."),
        ("03", "Documents are assembled repeatedly",
         "Certificates, assessment statements and scan logs are retyped for each request, which repeats work and introduces transcription errors.",
         "Target: generate from the same records the UI reads."),
    ]
    x = M
    for num, title, body, target in blocks:
        rect(s, x, 1.66, 3.90, 4.52, fill=SOFT, line=BORDER, radius=True)
        tx(s, x + 0.26, 1.84, 0.55, 0.45, num, size=20, color=ORANGE, bold=True)
        tx(s, x + 0.26, 2.36, 3.38, 0.70, title, size=14.5, color=INK, bold=True, spacing=0.96)
        tx(s, x + 0.26, 3.10, 3.38, 1.48, body, size=10.2, color=SLATE, spacing=1.12)
        rect(s, x + 0.26, 4.72, 3.36, 0.012, fill=BORDER)
        tx(s, x + 0.26, 4.86, 3.36, 1.05, target, size=9.5, color=DARK, bold=True, spacing=1.05)
        x += 4.10
    tx(s, M, 6.48, 12.05, 0.48,
       "The demonstration database holds 3 students, 21 subject lines, 3 assessments and 65 scan records — enough to exercise every workflow end to end.",
       size=9.0, color=MUTED, italic=True)
    notes(s, "The problem statement is operational rather than theoretical. Each block ends with the concrete "
             "design response that appears later in the deck. The row counts at the bottom come from the live "
             "database after the screenshot scan was removed.")


# --------------------------------------------------------------------------- #
# 05 objectives
# --------------------------------------------------------------------------- #
def slide_objectives(prs):
    s = new_slide(prs)
    header(s, 5, "01 · Objectives", "Success criteria and where they stand",
           "Each objective is paired with the implementation that serves it and the evidence that can be inspected.")
    rows = [
        ["Objective", "Implementation", "Evidence", "Status"],
        ["Identify a student from a physical card", "YOLOv8n → ROI gate → pyzbar → exact SQL match", "3/3 real cards decoded; audit report", "Met"],
        ["Never present a decode as a student", "VERIFIED requires a matching students row", "scan_pipeline.py state machine", "Met"],
        ["One source of truth for records", "19 tables, 34 FKs, UNIQUE constraints", "SHOW CREATE TABLE; ERD", "Met"],
        ["Produce documents from live data", "ReportLab COM, assessment, barcode and CSV", "reports/ artefacts; Reports page", "Met"],
        ["Run without a server or network", "PySide6 + local MySQL + local files", "Context and deployment diagrams", "Met"],
        ["Make the pipeline testable offline", "No Qt/threads in ScanPipeline; injectable services", "553 tests collected", "Partial"],
        ["Protect access with authentication", "users table exists; no login flow", "Logout stub in main_window.py", "Not met"],
        ["Route every built screen", "Barcode generator page built but unrouted", "No route in PAGE_CLASSES", "Not met"],
    ]
    table(s, M, 1.62, 12.05, 4.95, rows, [2.65, 4.20, 3.45, 1.75], size=9.2, row_h=0.50,
          align=[PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.CENTER])
    tx(s, M, 6.64, 12.0, 0.35,
       "“Met” means observed in the running application or measured from the live database — not inferred from a README.",
       size=8.8, color=MUTED, italic=True)
    notes(s, "The status column is intentionally blunt. The two 'Not met' rows and the 'Partial' row are "
             "developed on slides 34 and 35. Everything marked Met has a screenshot, a database query or a "
             "source file behind it.")


# --------------------------------------------------------------------------- #
# 06 scope
# --------------------------------------------------------------------------- #
def slide_scope(prs):
    s = new_slide(prs)
    header(s, 6, "01 · Scope", "Delivered versus partial — at a glance",
           "Module status was checked against source imports and routes, then confirmed in the running application.")
    image_panel(s, CHARTS / "chart_module_status.png", M, 1.58, 7.15, 4.85, pad=0.10)
    section_tag(s, "Delivered", 8.02, 1.62)
    bullets(s, 8.02, 1.90, 4.65, 2.35, [
        "Dashboard, Students, Scanner",
        "Enrollment, Courses & Subjects",
        "Assessment & Fees, Reports",
        "Certificate of Matriculation",
        "YOLO scan pipeline and 19-table schema",
    ], size=10.3, gap=6)
    section_tag(s, "Partial or absent", 8.02, 4.34)
    bullets(s, 8.02, 4.62, 4.65, 2.05, [
        ("Training Center: ", "reads empty DB tables, ignores 2,551 files on disk"),
        ("Settings: ", "general tab works; several controls are inert"),
        ("Barcode Generator: ", "complete page, no route"),
        ("Authentication: ", "users table but no login"),
        ("ID-card preview: ", "service renders; dialog raises QImage error"),
    ], size=9.5, gap=4, bullet_color=RED)
    notes(s, "The module-status chart counts 13 modules: 8 implemented, 3 partial, 2 stubs or unrouted. "
             "The partial column names the specific defect rather than saying 'needs work'.")


# --------------------------------------------------------------------------- #
# 07 context
# --------------------------------------------------------------------------- #
def slide_context(prs):
    s = new_slide(prs)
    header(s, 7, "02 · System design", "System context — one workstation, no network tier",
           "The application is a monolithic PySide6 desktop client that owns the camera, the model, the database connection and the filesystem.")
    image_panel(s, DIAGRAMS / "WIT_System_Context.png", M, 1.55, 8.25, 4.95, pad=0.03)
    rect(s, 9.08, 1.62, 3.60, 4.80, fill=CREAM, line=BORDER, radius=True)
    tx(s, 9.30, 1.82, 3.18, 0.30, "Boundaries that matter", size=11.5, color=DARK, bold=True)
    bullets(s, 9.30, 2.20, 3.18, 4.05, [
        ("Operator ", "is the only human actor."),
        ("Camera ", "is a local V4L2 device; an uploaded image is the fallback."),
        ("MySQL ", "runs on the same workstation. There is no API server."),
        ("Model and datasets ", "are files on disk, not services."),
        ("Outputs ", "land in reports/ and generated_id_cards/."),
    ], size=9.4, gap=7)
    caption(s, "Source: ui/main_window.py routes, services/camera_service.py, database/connection.py, models_yolo/active/best.pt. "
               "No HTTP client, web framework or remote host appears in the dependency set.")
    notes(s, "This boundary explains several later limitations: there is no session, no role check and no "
             "remote audit surface because there is no server. It also explains the deployment story: copy the "
             "repository, a Python environment and a MySQL instance onto one machine.")


# --------------------------------------------------------------------------- #
# 08 stack
# --------------------------------------------------------------------------- #
def slide_stack(prs):
    s = new_slide(prs)
    header(s, 8, "02 · Technology", "Verified runtime and libraries",
           "Versions were read from the project virtual environment and from live imports, not from a requirements wish-list.")
    groups = [
        ("Application & UI", ORANGE, [
            ("Python", "3.14.4"), ("PySide6", "6.12.0"), ("Qt", "6.12.0"),
            ("python-dotenv", "configured"), ("pytest", "9.1.1"),
        ]),
        ("Data & storage", BLUE, [
            ("MySQL", "8.4.11"), ("mysql-connector-python", "26.7.0"),
            ("InnoDB", "utf8mb4"), ("connection pool", "size 10"),
            ("ReportLab", "5.0.1"),
        ]),
        ("Vision & identity", GREEN, [
            ("Ultralytics", "8.4.177"), ("PyTorch", "2.14.1"),
            ("OpenCV", "5.0.0"), ("pyzbar", "0.1.9"),
            ("python-barcode", "0.16.1"),
        ]),
    ]
    x = M
    for title, accent, items in groups:
        rect(s, x, 1.62, 3.86, 4.55, fill=PAPER, line=BORDER, radius=True)
        rect(s, x, 1.62, 3.86, 0.09, fill=accent)
        tx(s, x + 0.24, 1.86, 3.38, 0.32, title, size=12.8, color=INK, bold=True)
        yy = 2.38
        for name, version in items:
            tx(s, x + 0.24, yy, 2.20, 0.27, name, size=9.8, color=SLATE)
            tx(s, x + 2.42, yy, 1.20, 0.27, version, size=9.2, color=DARK, bold=True,
               align=PP_ALIGN.RIGHT)
            rect(s, x + 0.24, yy + 0.34, 3.35, 0.008, fill=BORDER)
            yy += 0.55
        x += 4.10
    chip(s, M, 6.38, 2.28, "CUDA · RTX 2050", fill=PALE_GREEN, color=GREEN)
    chip(s, M + 2.42, 6.38, 2.28, "CPU fallback", fill=SOFT, color=SLATE)
    chip(s, M + 4.84, 6.38, 2.28, "Linux workstation", fill=PALE_BLUE, color=BLUE)
    tx(s, 8.12, 6.36, 4.55, 0.44,
       "No web framework, no HTTP client, no cloud SDK in the dependency set.",
       size=9.0, color=MUTED, italic=True)
    notes(s, "The unusual item is Python 3.14.4, which is newer than most Qt tutorials assume; the environment "
             "still imports and runs cleanly. MySQL 8.4.11 is the live server. The vision stack is the heaviest "
             "dependency and is the reason the deck reports measured latency rather than a guess.")


# --------------------------------------------------------------------------- #
# 09 architecture
# --------------------------------------------------------------------------- #
def slide_architecture(prs):
    s = new_slide(prs)
    header(s, 9, "02 · Architecture", "Three tiers inside one process",
           "Presentation, application services and the local data tier. Arrows are synchronous calls or data, not network messages.")
    image_panel(s, DIAGRAMS / "WIT_System_Architecture.png", M, 1.48, 12.08, 5.18, pad=0.02)
    caption(s, "Each tier is a Python package boundary, not a deployment boundary: ui/ → services/ → database/ and models/. "
               "The GPU is an optional accelerator; YOLODetectorService falls back to CPU for the rest of the session.")
    notes(s, "The important architectural claim is that there is no hidden service layer. The UI calls services "
             "directly; services use repositories and the connection pool. That makes the system easy to run but "
             "means the UI thread discipline and error handling live in the page code, which is where two of the "
             "three known defects are found.")


# --------------------------------------------------------------------------- #
# 10 module map
# --------------------------------------------------------------------------- #
def slide_modules(prs):
    s = new_slide(prs)
    header(s, 10, "02 · Codebase", "Module map and implementation state",
           "Thirteen user-facing modules, checked against the router, the page classes and the running window.")
    rows = [
        ["Module", "State", "What is actually there"],
        ["Dashboard", "Working", "KPI cards and DB status; student table raises TypeError"],
        ["Students", "Working", "Search, filters, pagination, create/edit/delete dialog"],
        ["Barcode Scanner", "Working", "Live camera, YOLO, decode, student panel, history, result modal"],
        ["Subject Enrollment", "Working", "Student selector, enrollment tabs, subject add/edit"],
        ["Courses & Subjects", "Working", "Read-only catalogue from courses/subjects"],
        ["Assessment & Fees", "Working", "Assessment lines, schedule, balances, payment dialog"],
        ["Certificate of Matriculation", "Working", "Live preview, Save PDF, Print"],
        ["Reports & Analytics", "Working", "Five report cards, background generation"],
        ["Training Center", "Partial", "Config/registry UI; dataset counts read empty DB tables"],
        ["Settings", "Partial", "General tab persists; several controls are placeholders"],
        ["ID Card Preview", "Partial", "Service renders CR80; preview dialog raises QImage error"],
        ["Barcode Generator", "Unrouted", "Full page class, no entry in PAGE_CLASSES"],
        ["Authentication / Logout", "Stub", "users table exists; no login, logout says “to be implemented”"],
    ]
    table(s, M, 1.55, 12.05, 5.35, rows, [2.58, 1.05, 8.42], size=8.8, row_h=0.385,
          align=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.LEFT])
    tx(s, M, 6.83, 12.0, 0.24,
       "Counts: 122 first-party Python files and 45,978 lines in config/, database/, models/, scripts/, "
       "services/, styles/, tests/, ui/ and main.py (wc -l; venv/, .git/ and presentation/ excluded).",
       size=8.0, color=MUTED, italic=True)
    notes(s, "This table is the ground truth for module claims. 'Working' means the primary workflow was exercised "
             "for the screenshots. 'Partial' means part of the screen functions and part does not. 'Unrouted' is "
             "not the same as missing: the generator page is 1,402 lines of implemented UI that a user cannot reach.")


# --------------------------------------------------------------------------- #
# 11 dashboard
# --------------------------------------------------------------------------- #
def slide_dashboard(prs):
    s = new_slide(prs)
    header(s, 11, "03 · Walkthrough", "Dashboard and global navigation",
           "The landing page shows four KPI cards, a database health indicator and a global quick-search available from every page.")
    image_panel(s, SHOTS / "01b_dashboard_kpis.png", M, 1.58, 7.75, 2.14, pad=0.03)
    image_panel(s, SHOTS / "17_global_search.png", 8.55, 1.58, 4.18, 3.62, pad=0.03)
    section_tag(s, "Observed", M, 3.92)
    bullets(s, M, 4.20, 7.55, 2.25, [
        ("Live counts: ", "3 total, 3 active, 3 registered, 0 missing barcodes."),
        ("Health probe: ", "the top bar reports “Database: Connected” after a real query."),
        ("Quick search: ", "Ctrl+K searches name, student ID or reference from any page."),
        ("Navigation: ", "nine sidebar routes, including the stub logout."),
    ], size=10.2, gap=6)
    rect(s, M, 6.00, 7.75, 0.72, fill=PALE_RED, line=RGBColor(0xFE, 0xCA, 0xCA), radius=True)
    tx(s, M + 0.16, 6.10, 7.45, 0.56,
       "The student table below this KPI strip raises TypeError: set_cell() got an unexpected keyword argument 'family'. "
       "The screenshot is cropped above the fault; slide 34 shows the real error.",
       size=8.4, color=RGBColor(0x99, 0x1B, 0x1B), spacing=1.05)
    caption(s, "Captured at 1600×1000 from the offscreen Qt platform against the live MySQL database.")
    notes(s, "The dashboard is the first thing a user sees, and it is also one of the two visible defects. "
             "The KPI strip itself is correct and reads the same student rows the Students page uses. The crop is "
             "honest: the deck does not hide the failure, it isolates the working part and shows the failure later.")


# --------------------------------------------------------------------------- #
# 12 students
# --------------------------------------------------------------------------- #
def slide_students(prs):
    s = new_slide(prs)
    header(s, 12, "03 · Walkthrough", "Student directory and record management",
           "The directory is the administrative backbone: search, filter, page and edit the same rows the scanner resolves.")
    image_panel(s, SHOTS / "02_students_directory.png", M, 1.56, 8.05, 4.95, pad=0.03)
    image_panel(s, SHOTS / "03_students_search.png", 8.84, 1.56, 3.88, 2.42, pad=0.03)
    section_tag(s, "Capabilities", 8.84, 4.10)
    bullets(s, 8.84, 4.38, 3.88, 2.10, [
        "Search by name, ID or reference",
        "Status / program / year filters",
        "25-row pagination",
        "Create, edit and delete dialog",
        "Portrait resolved from assets/profile/",
    ], size=9.3, gap=4)
    caption(s, "Three real records, all Computer Engineering, 3rd Year. The filter example is the query “Caporal”.")
    notes(s, "Student records are the join point for every other module: enrollment, assessment, barcode and ID card "
             "all reference students.id. The directory search is also used by the scan result dialog, so a failure "
             "here would propagate widely.")


# --------------------------------------------------------------------------- #
# 13 barcodes
# --------------------------------------------------------------------------- #
def slide_barcodes(prs):
    s = new_slide(prs)
    header(s, 13, "03 · Walkthrough", "Identity design: CODE39 payloads on the physical cards",
           "The implementation uses CODE39 with a zero-padded nine-digit reference, matching the cards in assets/images/.")
    cards = [
        ("Javy Danielle N. Caporal", "WIT-2024-001", "65412", "000065412", "barcode_2.png"),
        ("Ronna Mae D. Alemania", "WIT-2024-002", "65902", "000065902", "barcode_1.png"),
        ("Steven Anthony M. Oquiana", "WIT-2024-003", "65736", "000065736", "barcode_3.png"),
    ]
    y = 1.66
    for name, sid, ref, payload, asset in cards:
        rect(s, M, y, 7.55, 1.42, fill=PAPER, line=BORDER, radius=True)
        tx(s, M + 0.20, y + 0.16, 3.55, 0.30, name, size=11.4, color=INK, bold=True)
        tx(s, M + 0.20, y + 0.52, 3.55, 0.54, f"{sid}   ·   reference {ref}\npayload {payload}",
           size=8.8, color=SLATE, spacing=1.12)
        image_panel(s, ROOT / "assets" / "barcodes" / asset, M + 3.95, y + 0.20, 3.40, 1.02, pad=0.02)
        y += 1.53
    rect(s, 8.42, 1.66, 4.28, 4.42, fill=CREAM, line=BORDER, radius=True)
    tx(s, 8.64, 1.86, 3.84, 0.30, "Why CODE39, not CODE128", size=11.8, color=DARK, bold=True)
    bullets(s, 8.64, 2.24, 3.84, 3.60, [
        ("Physical cards already use CODE39; ", "the decoder must match them."),
        ("Nine digits at module width 2 ", "still fit a CR80 card with a 2 mm quiet zone."),
        ("The payload is the key: ", "students.barcode_payload is UNIQUE and is what the scanner queries."),
        ("README mismatch: ", "the README still advertises CODE128 and a WIT:STUDENT: prefix; the code and database use CODE39."),
    ], size=9.2, gap=6)
    tx(s, M, 6.44, 12.0, 0.34,
       "BarcodeGeneratorPage is implemented (1,402 lines) but has no sidebar route, so this identity path is used only by pre-existing cards and the ID-card service.",
       size=8.6, color=MUTED, italic=True)
    notes(s, "The barcode images shown are the project's own generated assets. The filename mismatch is a trap: "
             "barcode_1.png belongs to student 2 and barcode_2.png to student 1. Nothing in the application keys on "
             "filename; it keys on the decoded payload. The README/code mismatch is a documentation defect, not a "
             "runtime one.")


# --------------------------------------------------------------------------- #
# 14 scanner
# --------------------------------------------------------------------------- #
def slide_scanner(prs):
    s = new_slide(prs)
    header(s, 14, "03 · Walkthrough", "The scanner experience",
           "Live camera and image-upload paths share one pipeline. A verified read fills the student panel and appends a history row.")
    image_panel(s, SHOTS / "04_scanner_idle.png", M, 1.56, 5.90, 3.78, pad=0.03)
    image_panel(s, SHOTS / "06_scanner_verified.png", 6.66, 1.56, 6.02, 3.78, pad=0.03)
    tx(s, M, 5.44, 5.80, 0.26, "IDLE", size=8.6, color=DARK, bold=True)
    tx(s, 6.66, 5.44, 5.90, 0.26, "VERIFIED", size=8.6, color=GREEN, bold=True)
    bullets(s, M, 5.74, 5.85, 1.16, [
        "Camera list, YOLO status, live history",
        "Upload Card Image as a fallback",
        "Scan Next Student resets the latch",
    ], size=8.8, gap=2)
    bullets(s, 6.66, 5.74, 5.95, 1.16, [
        "Decoded 000065412 and matched Javy Danielle N. Caporal",
        "History row persisted; success tone and result modal fire once",
        "Yellow guide is a hint; the gate is a separate 0.88×0.88 region",
    ], size=8.8, gap=2)
    caption(s, "Both frames captured from the running app. The verified frame came from feeding the real Id_1.png through ScanPipeline; the resulting DB row was removed afterwards.")
    notes(s, "The scanner is the centre of the project. The idle frame shows the operator affordances: camera selection, "
             "model status, and a history table. The verified frame is the proof that detection, decoding and database "
             "lookup agree on the same person. The database was restored after capture, so the deck's scan counts are "
             "the pre-capture counts.")


# --------------------------------------------------------------------------- #
# 15 pipeline
# --------------------------------------------------------------------------- #
def slide_pipeline(prs):
    s = new_slide(prs)
    header(s, 15, "03 · Scanner internals", "Detection, decoding and lookup are separate stages",
           "A YOLO hit identifies a region; it says nothing about the payload or the student. Each stage can fail without faking the next.")
    image_panel(s, DIAGRAMS / "WIT_Barcode_Pipeline.png", M, 1.48, 12.08, 5.12, pad=0.02)
    caption(s, "Verified in source: services/scan_pipeline.py, services/yolo_detector.py, services/barcode_decoder.py, "
               "database/repositories/student_repository.py. Thresholds are the runtime values in .env.")
    notes(s, "The separation is the most important design decision in the scanner. It is what allows the UI to say "
             "'barcode found but not decodable' instead of 'unknown student', and what makes the pipeline testable "
             "without a camera.")


# --------------------------------------------------------------------------- #
# 16 YOLO architecture
# --------------------------------------------------------------------------- #
def slide_yolo(prs):
    s = new_slide(prs)
    header(s, 16, "03 · Detection model", "YOLOv8n fine-tuned for one class: barcode",
           "A nano detector keeps the model at 6.48 MB and fast enough for a 12 FPS preview on the available GPU.")
    image_panel(s, DIAGRAMS / "WIT_YOLO_Architecture.png", M, 1.52, 8.55, 4.72, pad=0.02)
    rows = [
        ["Property", "Value"],
        ["Checkpoint", "models_yolo/active/best.pt"],
        ["Size / parameters", "6,484,970 B · 3.2 M"],
        ["Classes", "1 — barcode"],
        ["Input", "640 × 640 letterboxed"],
        ["Confidence", "0.75 (runtime .env)"],
        ["NMS IoU", "0.45"],
        ["Device", "CUDA:0 → CPU fallback"],
    ]
    table(s, 9.36, 1.58, 3.32, 4.40, rows, [1.18, 2.14], size=8.4, row_h=0.44,
          align=[PP_ALIGN.LEFT, PP_ALIGN.LEFT])
    caption(s, "Architecture diagram is conceptual: CBS/C2f/SPPF backbone, PAN-FPN neck, decoupled anchor-free head. "
               "Variants confirmed from the checkpoint metadata.")
    notes(s, "YOLOv8n was chosen for latency and size, not because it is the most accurate option. The one-class "
             "problem is easy enough that a nano model reaches a high validation mAP. The 0.75 confidence threshold "
             "is a runtime choice recorded in .env after measurement; the code default is 0.5.")


# --------------------------------------------------------------------------- #
# 17 detection evidence
# --------------------------------------------------------------------------- #
def slide_detection(prs):
    s = new_slide(prs)
    header(s, 17, "03 · Detection evidence", "The detector finds all three real cards",
           "Inference on the checked-in card images, using the active checkpoint and the runtime threshold.")
    image_panel(s, SHOTS / "23_yolo_inference_boxes.png", M, 1.52, 3.62, 5.40, pad=0.02)
    rows = [
        ["Card", "Frame", "Box (x, y, w, h)", "Confidence"],
        ["Id_1.png", "590 × 1004", "19, 789, 540, 111", "0.9545"],
        ["Id_2.png", "590 × 1004", "46, 784, 501, 130", "0.9480"],
        ["Id_3.png", "270 × 459", "13, 359, 239, 64", "0.9213"],
    ]
    table(s, 4.58, 1.64, 4.72, 2.06, rows, [1.02, 1.02, 1.64, 1.04], size=9.0, row_h=0.40,
          align=[PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.CENTER])
    section_tag(s, "What the numbers say", 4.58, 3.94)
    bullets(s, 4.58, 4.22, 4.70, 2.15, [
        "Every card clears 0.92 confidence — a wide margin over 0.75.",
        "Id_3 is the smallest card and still detects cleanly.",
        "A detection is only a region: decode and SQL lookup are still required.",
        "The annotated crop on the left is the real model output, scaled 2× for legibility.",
    ], size=9.3, gap=5)
    rect(s, 9.55, 1.64, 3.12, 4.72, fill=CREAM, line=BORDER, radius=True)
    tx(s, 9.76, 1.84, 2.70, 0.30, "Model identity", size=11.2, color=DARK, bold=True)
    bullets(s, 9.76, 2.20, 2.70, 3.95, [
        "Trained 2026-10-15 22:23",
        "Run: v7-distance",
        "Ultralytics 8.4.177",
        "Best epoch 34 of 64",
        "mAP50 0.995",
        "mAP50-95 0.8075",
    ], size=9.0, gap=4)
    tx(s, M, 6.53, 12.0, 0.30,
       "Reproduce: load assets/images/Id_N.png through services/yolo_detector.py with YOLO_CONFIDENCE_THRESHOLD=0.75.",
       size=8.2, color=MUTED, italic=True)
    notes(s, "This is not a synthetic demo: the three coordinates and confidences were printed by the active model "
             "on the three card assets. The tall image on the left is the model's own annotated output. Notice that "
             "detection succeeds even on the smallest card, which is the case that used to fail before the decoder "
             "was given upscaling attempts.")


# --------------------------------------------------------------------------- #
# 18 decoding
# --------------------------------------------------------------------------- #
def slide_decoding(prs):
    s = new_slide(prs)
    header(s, 18, "03 · Decoding", "From pixels to a payload, with a bounded attempt budget",
           "The ROI gate decides which detections reach pyzbar; the decoder tries a small ordered set of preparations and records which one worked.")
    image_panel(s, SHOTS / "05_scanner_detection.png", M, 1.56, 6.05, 3.78, pad=0.03)
    section_tag(s, "Gate", 6.92, 1.60)
    bullets(s, 6.92, 1.88, 5.70, 1.95, [
        ("Overlap ≥ 0.65 ", "with the scan region, measured as intersection over the detection box."),
        ("Crop margin 0.20, ", "minimum 80 × 24 px; unusable crops are skipped rather than decoded."),
        ("Candidate de-duplication ", "at IoU 0.70, so one symbol is not decoded repeatedly."),
    ], size=9.6, gap=6)
    section_tag(s, "Decode", 6.92, 3.90)
    bullets(s, 6.92, 4.18, 5.70, 1.95, [
        ("Ordered attempts, ≤ 6: ", "original colour, greyscale, CLAHE, Otsu, 2× and 3× upscale variants."),
        ("Every payload is kept, ", "not just the first: two distinct values make the read AMBIGUOUS."),
        ("Method and attempt count ", "travel with the result, so the UI can be honest about a difficult read."),
    ], size=9.6, gap=6)
    tx(s, 6.92, 6.24, 5.75, 0.44,
       "Why upscaling exists: Id_3.png supplies only ~1.85 px per narrow CODE39 element at native size.",
       size=8.6, color=DARK, italic=True)
    caption(s, "Screenshot: live detection with the bounding box, guide overlay and decoded value 000065412.")
    notes(s, "The decoder is deliberately bounded: six attempts per crop, not an unbounded search over every "
             "filter combination. That keeps a frame from stalling on a hopeless crop. The ambiguity rule is a "
             "safety property: if two different payloads are read from the same crop, the system refuses to choose.")


# --------------------------------------------------------------------------- #
# 19 outcomes
# --------------------------------------------------------------------------- #
def slide_outcomes(prs):
    s = new_slide(prs)
    header(s, 19, "03 · Outcomes", "Verified, unknown and ambiguous are different states",
           "The UI never collapses a failed lookup into a success. The demonstration data records both outcomes.")
    image_panel(s, SHOTS / "06_scanner_verified.png", M, 1.56, 4.00, 2.86, pad=0.03)
    image_panel(s, SHOTS / "08_scanner_unknown.png", 4.86, 1.56, 4.00, 2.86, pad=0.03)
    image_panel(s, SHOTS / "07_scan_result_dialog.png", 9.10, 1.56, 2.95, 2.95, pad=0.03)
    tx(s, M, 4.52, 3.90, 0.26, "VERIFIED — student resolved", size=8.8, color=GREEN, bold=True)
    tx(s, 4.86, 4.52, 3.90, 0.26, "UNKNOWN — decoded, no DB row", size=8.8, color=RED, bold=True)
    tx(s, 9.10, 4.52, 2.95, 0.26, "RESULT MODAL", size=8.8, color=DARK, bold=True)
    bullets(s, M, 4.84, 3.90, 1.80, [
        "Student panel, portrait and reference",
        "Success tone + one history insert",
        "COM and financial tabs open",
    ], size=8.8, gap=3)
    bullets(s, 4.86, 4.84, 4.00, 1.80, [
        "Decoded value is shown",
        "not_found row is written",
        "No student panel, no success tone",
        "Database failure is a separate ERROR state",
    ], size=8.8, gap=3)
    rect(s, 9.10, 4.88, 2.95, 1.56, fill=CREAM, line=BORDER, radius=True)
    tx(s, 9.24, 4.98, 2.68, 1.36,
       "65 scans on file\n41 success · 24 not found\n\n"
       "The not-found rows are test reads against unknown payloads, not silent failures.",
       size=8.4, color=SLATE, spacing=1.08)
    caption(s, "Scan counts are the live barcode_scans table after removing the one capture scan. Success rate is 63.1%.")
    notes(s, "The unknown state is important because it proves the system does not treat a decode as identity. The "
             "42/24 split in the original evidence became 41/24 after the screenshot row was removed. The modal on "
             "the right is the structured result view; it is a separate dialog from the student panel.")


# --------------------------------------------------------------------------- #
# 20 flowchart
# --------------------------------------------------------------------------- #
def slide_flowchart(prs):
    s = new_slide(prs)
    header(s, 20, "03 · End-to-end flow", "Complete scan workflow, including failure branches",
           "Green is the verified path; red branches are implemented failure handling, not hypothetical edge cases.")
    image_panel(s, DIAGRAMS / "WIT_System_Flowchart.png", M, 1.48, 12.08, 5.12, pad=0.02)
    caption(s, "Every branch maps to a ScanState or ScanSession rejection in services/scan_pipeline.py and services/scan_session.py. "
               "The duplicate latch is content-aware, not just a timer.")
    notes(s, "The flowchart is the best single answer to 'what happens when it goes wrong'. Camera missing, box outside "
             "the gate, decode failure, unknown payload, two cards in frame, duplicate frame and database outage each "
             "have a distinct path and message.")


# --------------------------------------------------------------------------- #
# 21 enrollment
# --------------------------------------------------------------------------- #
def slide_enrollment(prs):
    s = new_slide(prs)
    header(s, 21, "03 · Walkthrough", "Subject enrollment",
           "A student is selected once, then every enrollment for that student is shown as a tab with subject lines and fees.")
    image_panel(s, SHOTS / "09_enrollment.png", M, 1.56, 8.30, 5.02, pad=0.03)
    section_tag(s, "Live record", 9.10, 1.62)
    kpi(s, 9.10, 1.90, 1.70, 1.04, "3", "Enrollments", "", fill=CREAM)
    kpi(s, 10.94, 1.90, 1.70, 1.04, "21", "Subject lines", "", fill=CREAM)
    bullets(s, 9.10, 3.12, 3.55, 3.10, [
        ("Steven Anthony M. Oquiana ", "is enrolled in 2024–2025, 1st Semester."),
        ("7 subjects, 20 credits, ", "with section, room, days, time and period."),
        ("Status and per-subject fees ", "are persisted, not computed in the widget."),
        ("New Enrollment and Add Subject ", "open modal dialogs backed by the same service."),
    ], size=9.3, gap=6)
    caption(s, "The screenshot is populated by selecting the first row of the real student list; the earlier capture showed the unselected "
               "empty state.")
    notes(s, "Enrollment is where the barcode identity becomes an academic record. The page reads enrollments by student and "
             "renders one tab per enrollment. The counts on the right come from the live tables: three enrollments, "
             "twenty-one subject lines.")


# --------------------------------------------------------------------------- #
# 22 courses
# --------------------------------------------------------------------------- #
def slide_courses(prs):
    s = new_slide(prs)
    header(s, 22, "03 · Walkthrough", "Courses and subjects catalogue",
           "The catalogue is read-only in the UI and populated from the courses and subjects tables.")
    image_panel(s, SHOTS / "10_courses_subjects.png", M, 1.56, 8.35, 5.02, pad=0.03)
    kpi(s, 9.18, 1.62, 3.48, 1.16, "1 course", "Computer Engineering", "One programme row", fill=PALE_BLUE)
    kpi(s, 9.18, 2.94, 3.48, 1.16, "7 subjects", "CPE 311–315, COGELEC1, MATH E4", "Credits, lecture/lab hours, department", fill=CREAM)
    bullets(s, 9.18, 4.32, 3.45, 2.10, [
        "Referenced by enrollment_subjects",
        "Subject codes are UNIQUE",
        "No create/edit path in this screen",
        "Course/subject integrity is enforced by FKs",
    ], size=9.2, gap=5)
    caption(s, "The page renders the real catalogue; all seven subjects are attached to the three demonstration enrollments.")
    notes(s, "This screen is deliberately simple. It exists so an operator can verify what is in the catalogue before "
             "adding a subject line. Editing is out of scope for the current build, which is why the page is read-only.")


# ------------------------------------------------------------------------- #
# 23 assessment
# ------------------------------------------------------------------------- #
def slide_assessment(prs):
    s = new_slide(prs)
    header(s, 22, "03 · Walkthrough", "Assessment, fees and balances",
           "Charges, discounts, payments and installment schedules are separate tables joined to one assessment per term.")
    image_panel(s, SHOTS / "11_assessment_fees.png", M, 1.56, 8.35, 5.02, pad=0.03)
    kpi(s, 9.18, 1.62, 3.48, 1.16, "PHP 82,899.93", "Assessed across 3 students", "33 assessment items", fill=CREAM)
    kpi(s, 9.18, 2.94, 3.48, 1.16, "PHP 0.00", "Payments recorded", "12 schedules, all pending", fill=PALE_RED, accent=RED)
    bullets(s, 9.18, 4.32, 3.45, 2.10, [
        "Tuition, miscellaneous and lab fees are verified line items",
        "Outstanding balance is persisted on assessments",
        "Schedule table drives due dates and status",
        "Payment dialog exists; demo data has no payments",
    ], size=9.2, gap=5)
    caption(s, "The visible student is Steven Anthony M. Oquiana; his assessment totals PHP 27,633.31. Database totals cover all three students.")
    notes(s, "The financial module is complete enough to create an assessment, show line items and schedules, and record a "
             "payment. What the demo data does not contain is an actual payment, so the paid and balance columns are "
             "honest zeros on the outstanding side. That is a data limitation, not a code claim.")


# --------------------------------------------------------------------------- #
# 24 certificate
# --------------------------------------------------------------------------- #
def slide_certificate(prs):
    s = new_slide(prs)
    header(s, 23, "03 · Walkthrough", "Certificate of Matriculation",
           "The certificate is rendered from the same enrollment and subject rows the page displays, then saved as A4 PDF or printed.")
    image_panel(s, SHOTS / "15_certificate_com.png", M, 1.56, 8.20, 5.05, pad=0.03)
    section_tag(s, "Document facts", 9.02, 1.62)
    bullets(s, 9.02, 1.90, 3.65, 3.60, [
        ("Live student: ", "Steven Anthony M. Oquiana, reference 65736."),
        ("7 subjects, 20 credits, ", "with code, schedule, room, periods and fees."),
        ("Actions: ", "Save PDF, Print COM, and a student-profile jump."),
        ("Artifact: ", "reports/certificate_65412_20261017_112221.pdf was produced by the same service."),
        ("Threading: ", "report builds run off the GUI thread and surface write failures."),
    ], size=9.3, gap=6)
    rect(s, 9.02, 5.62, 3.65, 1.10, fill=PALE_GREEN, line=RGBColor(0xBB, 0xF7, 0xD0), radius=True)
    tx(s, 9.18, 5.74, 3.35, 0.88,
       "The certificate is not a separate data entry form. It reads enrollment and assessment records by student ID.",
       size=8.6, color=RGBColor(0x16, 0x65, 0x34), spacing=1.06)
    caption(s, "Screenshot shows the live preview, not a PDF opened in a viewer.")
    notes(s, "The COM is a good example of the project's 'one source of truth' goal: the page and the PDF share the "
             "same service calls. The saved artifact in reports/ proves the PDF path runs. The on-screen preview is "
             "the clearest evidence that the layout is generated, not pasted from a template.")


# --------------------------------------------------------------------------- #
# 25 ID cards
# --------------------------------------------------------------------------- #
def slide_idcards(prs):
    s = new_slide(prs)
    header(s, 24, "03 · Walkthrough", "Student ID card service — render works, preview dialog does not",
           "The service produces physically correct CR80 cards at 150/300/600 DPI. The UI path that should preview them raises an exception.")
    image_panel(s, SHOTS / "16a_id_card_front.png", M, 1.58, 5.95, 3.76, pad=0.03)
    image_panel(s, SHOTS / "16b_id_card_back.png", 6.72, 1.58, 5.98, 3.76, pad=0.03)
    tx(s, M, 5.44, 5.85, 0.26, "FRONT · 1011 × 638 px at 300 DPI", size=8.4, color=DARK, bold=True)
    tx(s, 6.72, 5.44, 5.90, 0.26, "BACK · CR80 85.6 × 54 mm", size=8.4, color=DARK, bold=True)
    rect(s, M, 5.72, 5.85, 1.00, fill=PALE_RED, line=RGBColor(0xFE, 0xCA, 0xCA), radius=True)
    tx(s, M + 0.14, 5.81, 5.56, 0.84,
       "Preview dialog: “cannot access local variable 'QImage' where it is not associated with a value.” "
       "The dialog is unusable, although the service render is correct.",
       size=8.2, color=RGBColor(0x99, 0x1B, 0x1B), spacing=1.04)
    rect(s, 6.72, 5.72, 5.98, 1.00, fill=PALE_GOLD, line=RGBColor(0xFD, 0xE6, 0x8A), radius=True)
    tx(s, 6.86, 5.81, 5.70, 0.84,
       "Barcode re-decode check fails on the rendered front: “did not decode to 000065412 (read ['<nothing>']).” "
       "The card is visually plausible but not scanner-verified at 300 DPI.",
       size=8.2, color=RGBColor(0x92, 0x40, 0x0E), spacing=1.04)
    caption(s, "Both card images are genuine IdCardService renders, not mock-ups. The failures are reproduced by the service's own checks.")
    notes(s, "This is the most uncomfortable slide, and it is deliberately specific. Two separate problems: the preview "
             "dialog throws before it can display the render, and the front card's bitmap barcode does not decode back "
             "to its payload. The second is a print-readiness issue: a card that cannot be scanned is not finished, even "
             "though the layout looks right.")


# --------------------------------------------------------------------------- #
# 26 reports
# --------------------------------------------------------------------------- #
def slide_reports(prs):
    s = new_slide(prs)
    header(s, 25, "03 · Walkthrough", "Reports and analytics",
           "Five report cards, each backed by a generator that writes a file and reports failure instead of silently doing nothing.")
    image_panel(s, SHOTS / "12_reports.png", M, 1.56, 8.20, 5.05, pad=0.03)
    section_tag(s, "Catalogue", 9.02, 1.62)
    bullets(s, 9.02, 1.90, 3.60, 3.55, [
        ("Certificate of Matriculation ", "→ A4 PDF / print."),
        ("Enrollment Summary ", "→ students for a chosen term."),
        ("Student Barcode Report ", "→ identity, payload, symbol."),
        ("Assessment Report ", "→ charges, schedule, payments."),
        ("Barcode Scan Log ", "→ CSV of history with resolved student."),
    ], size=9.4, gap=7)
    rect(s, 9.02, 5.52, 3.62, 1.16, fill=CREAM, line=BORDER, radius=True)
    tx(s, 9.18, 5.63, 3.30, 0.94,
       "Generation runs in a worker thread. A missing file or a write failure is shown as an error, not a success toast.",
       size=8.6, color=SLATE, spacing=1.06)
    caption(s, "Screenshots of the report cards; generated PDFs and CSV are in reports/ and were produced by ReportService.")
    notes(s, "The reports page is a thin UI over ReportService. The important engineering detail is that a build runs "
             "off the GUI thread and that a failure is reported plainly. The catalogue is also the best evidence that "
             "the document layer is not a mock-up.")


# --------------------------------------------------------------------------- #
# 27 database
# --------------------------------------------------------------------------- #
def slide_database(prs):
    s = new_slide(prs)
    header(s, 26, "04 · Data", "The live database at a glance",
           "MySQL 8.4.11, InnoDB, utf8mb4_unicode_ci. Nineteen tables, 230 columns and 33 foreign keys.")
    image_panel(s, CHARTS / "chart_db_inventory.png", M, 1.56, 7.40, 4.96, pad=0.06)
    rows = [
        ["Measure", "Value"],
        ["Tables", "19"],
        ["Columns", "230"],
        ["Foreign keys", "33"],
        ["Rows (all 19 tables)", "188"],
        ["Students", "3"],
        ["Enrollments / subject lines", "3 / 21"],
        ["Assessments / items", "3 / 33"],
        ["Scan records", "65"],
        ["Users / ID cards / models", "0 / 0 / 0"],
    ]
    table(s, 8.28, 1.62, 4.40, 4.02, rows, [2.50, 1.90], size=9.0, row_h=0.40,
          align=[PP_ALIGN.LEFT, PP_ALIGN.RIGHT])
    rect(s, 8.28, 5.78, 4.40, 0.92, fill=PALE_RED, line=RGBColor(0xFE, 0xCA, 0xCA), radius=True)
    tx(s, 8.44, 5.88, 4.08, 0.72,
       "Empty tables are real: no login users, no saved ID cards, no model registry rows, no dataset rows.",
       size=8.5, color=RGBColor(0x99, 0x1B, 0x1B), spacing=1.05)
    caption(s, "Row counts are live after removing the screenshot-inserted scan. Chart label is generated from the same query; the 188-row total includes reference data (assessment categories, courses, subjects, settings).")
    notes(s, "The schema is substantial for a desktop project, but the live data is a demonstration set. Reporting both "
             "is important: 19 tables and 33 FKs describe capability; 3 students and 65 scans describe the actual "
             "evidence base. The 188-row total counts all 19 tables, including 42 reference/settings rows that are not "
             "student records. Empty registry tables are why the Training Center shows zeros.")


# --------------------------------------------------------------------------- #
# 28 ERD
# --------------------------------------------------------------------------- #
def slide_erd(prs):
    s = new_slide(prs)
    header(s, 27, "04 · Data model", "Entity-relationship diagram",
           "Key fields shown for all 19 tables. PK/FK roles and the 1..N relationships are marked; 18 paths are drawn for legibility.")
    image_panel(s, DIAGRAMS / "WIT_Database_ERD.png", M, 1.46, 12.08, 5.30, pad=0.02)
    caption(s, "Verified with SHOW CREATE TABLE against the live schema. Every table uses InnoDB and utf8mb4_unicode_ci; "
               "barcode_scans is append-only and its student/barcode FKs are nullable so a deleted student does not erase history.")
    notes(s, "The ERD is dense on purpose: it is the reference diagram, not a teaching slide. The two junction tables "
             "are enrollment_subjects and assessment_items. The users table is the intended audit anchor, but there "
             "are no user rows yet, which is why created_by / updated_by are unused in the demo data.")


# --------------------------------------------------------------------------- #
# 29 data flow
# --------------------------------------------------------------------------- #
def slide_dataflow(prs):
    s = new_slide(prs)
    header(s, 28, "04 · Data flow", "Level 1 data flow diagram",
           "Four processes, four data stores and the external inputs/outputs that feed them.")
    image_panel(s, DIAGRAMS / "WIT_Data_Flow_Diagram.png", M, 1.46, 12.08, 5.30, pad=0.02)
    caption(s, "Process numbers are explanatory, not a formal DFD leveling. The data dictionary strip names the key columns "
               "and the uniqueness rules that make the scan lookup deterministic.")
    notes(s, "This diagram connects the UI screens to the tables: student management to D1, enrollment to D2, scanning to "
             "D3, and assessment/documents to D4. The camera and model store are inputs to the scan process, and the "
             "generated outputs are files, not database rows.")


# --------------------------------------------------------------------------- #
# 30 dataset inventory
# --------------------------------------------------------------------------- #
def slide_dataset(prs):
    s = new_slide(prs)
    header(s, 29, "04 · Dataset", "Eight dataset variants, one measured inventory",
           "A filesystem scan of datasets/ counted images, labels, boxes and negative samples. Nothing is estimated from a data.yaml comment.")
    image_panel(s, CHARTS / "chart_dataset_inventory.png", M, 1.54, 7.35, 4.55, pad=0.06)
    rows = [
        ["Variant", "Images", "Boxes"],
        ["barcode", "4", "2"],
        ["barcode_all_students", "8", "6"],
        ["barcode_aug", "44", "42"],
        ["barcode_aug_all", "108", "106"],
        ["barcode_detection", "136", "134"],
        ["barcode_detection_all", "197", "195"],
        ["barcode_distance", "828", "812"],
        ["barcode_distance_all", "1,226", "1,218"],
        ["Total", "2,551", "2,515"],
    ]
    table(s, 8.20, 1.58, 4.48, 4.62, rows, [2.10, 1.18, 1.20], size=8.5, row_h=0.415,
          align=[PP_ALIGN.LEFT, PP_ALIGN.RIGHT, PP_ALIGN.RIGHT])
    tx(s, 8.20, 6.33, 4.48, 0.34, "2.77 GB · 36 negative images · 43 distinct resolutions",
       size=8.6, color=DARK, bold=True)
    caption(s, "barcode_distance_all is the largest pool; barcode_distance is the variant named by the active model's data.yaml.")
    notes(s, "The eight variants are generations of the same problem, not eight independent datasets. The count that "
             "matters for the active model is barcode_distance: 828 images, 812 boxes and 16 negatives, all at one "
             "resolution. The larger distance_all pool adds a held-out test split that the active training did not use.")


# --------------------------------------------------------------------------- #
# 31 dataset quality
# --------------------------------------------------------------------------- #
def slide_dataset_quality(prs):
    s = new_slide(prs)
    header(s, 30, "04 · Dataset quality", "Labels are clean; the split strategy is not",
           "Zero missing label files and zero malformed boxes, but the core training variant has no test split and eight duplicate files.")
    image_panel(s, CHARTS / "chart_bbox_distribution.png", M, 1.56, 7.25, 3.30, pad=0.05)
    image_panel(s, SHOTS / "24_dataset_gallery.png", 8.02, 1.56, 4.65, 3.30, pad=0.04)
    rect(s, M, 5.08, 5.82, 1.64, fill=PALE_GREEN, line=RGBColor(0xBB, 0xF7, 0xD0), radius=True)
    tx(s, M + 0.16, 5.18, 5.50, 1.46,
       "Clean signals\n• 0 missing labels · 0 invalid boxes\n• 1 class, 2,515 boxes\n"
       "• 36 deliberate negatives teach 'no barcode here'\n• median box 50.4% × 17.4% of frame",
       size=8.8, color=RGBColor(0x16, 0x65, 0x34), spacing=1.12)
    rect(s, 5.96, 5.08, 6.70, 1.64, fill=PALE_RED, line=RGBColor(0xFE, 0xCA, 0xCA), radius=True)
    tx(s, 6.12, 5.18, 6.38, 1.46,
       "Risk signals\n• barcode_distance has 0 test images — selection used validation only\n"
       "• 8 duplicate files inside barcode_distance\n• 43 resolutions, so scale variance is high\n"
       "• 20 near-identical distance frames per source card inflate the count",
       size=8.8, color=RGBColor(0x99, 0x1B, 0x1B), spacing=1.12)
    caption(s, "Box geometry from all 2,515 labels. Gallery crops the annotated region only; no student data is shown.")
    notes(s, "The clean-label result is genuine: every image has a label file and every box is well formed. The red panel "
             "is the part a model card would normally omit. A validation-only selection is not a held-out test, and the "
             "distance dataset is built from many near-identical frames, so its image count overstates independent "
             "evidence.")


# --------------------------------------------------------------------------- #
# 32 training pipeline
# --------------------------------------------------------------------------- #
def slide_training_pipeline(prs):
    s = new_slide(prs)
    header(s, 31, "04 · Training", "Training pipeline and recorded configuration",
           "Six candidate runs were kept. The active checkpoint is the v7-distance run, trained at 640 px on the distance dataset.")
    image_panel(s, DIAGRAMS / "WIT_YOLO_Training_Pipeline.png", M, 1.48, 12.08, 4.78, pad=0.02)
    chips = [
        ("100 epochs requested", 0.62), ("64 recorded", 2.55), ("batch 16", 4.08),
        ("imgsz 640", 5.40), ("AdamW · lr0 0.002", 6.78), ("patience 30", 8.94),
        ("device CUDA:0", 10.40),
    ]
    for text, x in chips:
        chip(s, x, 6.43, 1.78, text, fill=CREAM, color=DARK, size=8.1)
    caption(s, "Configuration read from best.pt train_args and results.csv. The run stopped after 64 recorded epochs; "
               "the checkpoint is the best epoch (34), not the last.", y=6.82)
    notes(s, "The pipeline diagram shows the real build sequence: source cards and frames, annotation, augmentation, "
             "dataset variants, training, validation and promotion to active/best.pt. The active run's train_args are "
             "stored inside the checkpoint, which is how the 100-epoch request and 64-epoch outcome were verified.")


# --------------------------------------------------------------------------- #
# 33 training results
# --------------------------------------------------------------------------- #
def slide_training_results(prs):
    s = new_slide(prs)
    header(s, 32, "04 · Training", "Validation curves and the active model's scores",
           "The v7-distance run reaches mAP50 0.995 and mAP50-95 0.8075 at its best epoch, with precision 0.9975 and recall 0.9987.")
    image_panel(s, CHARTS / "chart_training_map.png", M, 1.54, 7.55, 2.70, pad=0.04)
    image_panel(s, CHARTS / "chart_training_losses.png", M, 4.32, 7.55, 2.34, pad=0.04)
    rect(s, 8.42, 1.54, 4.28, 4.66, fill=CREAM, line=BORDER, radius=True)
    tx(s, 8.64, 1.74, 3.84, 0.30, "Active checkpoint (v7)", size=12.0, color=DARK, bold=True)
    rows = [
        ["Metric", "Value"],
        ["Best epoch", "34 / 64"],
        ["Precision", "0.9975"],
        ["Recall", "0.9987"],
        ["mAP@0.50", "0.9950"],
        ["mAP@0.50:0.95", "0.8075"],
    ]
    table(s, 8.64, 2.14, 3.84, 2.34, rows, [1.70, 2.14], size=9.0, row_h=0.34,
          align=[PP_ALIGN.LEFT, PP_ALIGN.RIGHT])
    tx(s, 8.64, 4.68, 3.84, 1.32,
       "Losses fall smoothly through the run. The strict run is excluded from comparison: its log records only epochs 41–60 "
       "and duplicates v3's metrics, so it is not independent evidence.",
       size=8.4, color=SLATE, spacing=1.08)
    tx(s, 8.64, 5.88, 3.84, 0.26, "Source: results.csv + best.pt train_metrics", size=7.6, color=MUTED, italic=True)
    caption(s, "Curves are plotted directly from results.csv; no smoothing or interpolation. Validation uses the same label set for every run.")
    notes(s, "The active model's scores are high because the task is narrow: one class, a printed symbol, and a validation "
             "set drawn from the same generation process as training. The deck does not claim that 0.995 mAP generalises "
             "to every camera, distance or lighting condition. That is what the distance variants were meant to probe.")


# --------------------------------------------------------------------------- #
# 34 comparison and latency
# --------------------------------------------------------------------------- #
def slide_model_comparison(prs):
    s = new_slide(prs)
    header(s, 33, "04 · Model selection", "Six runs compared; GPU inference is 17× faster than CPU",
           "Best validation scores per run from results.csv, plus a 12-run latency measurement on a 1280×720 frame.")
    image_panel(s, CHARTS / "chart_model_comparison.png", M, 1.54, 7.45, 3.18, pad=0.05)
    image_panel(s, CHARTS / "chart_inference_latency.png", 8.22, 1.54, 4.48, 2.30, pad=0.05)
    section_tag(s, "How to read this", M, 5.00)
    bullets(s, M, 5.28, 7.40, 1.55, [
        ("v5-aug and v7-distance both reach mAP50 0.995; ", "v5 has the higher mAP50-95 (0.8736 vs 0.8075)."),
        ("v7 was promoted for the distance dataset, ", "not because it wins every metric."),
        ("v4-tame collapsed to 0.0585 mAP50; ", "it is kept as evidence of an unsuccessful augmentation strategy."),
    ], size=9.0, gap=4)
    rect(s, 8.22, 4.02, 4.48, 2.62, fill=CREAM, line=BORDER, radius=True)
    tx(s, 8.42, 4.16, 4.08, 0.30, "Measured latency", size=11.2, color=DARK, bold=True)
    tx(s, 8.42, 4.52, 4.08, 0.62, "17.22 ms GPU mean\n302.39 ms CPU mean", size=15.0, color=INK, bold=True,
       spacing=0.96)
    tx(s, 8.42, 5.22, 4.08, 1.28,
       "12 frames after warm-up; p95 18.93 ms GPU, 622.24 ms CPU. At the 12 FPS preview target (83 ms/frame), "
       "only the GPU has comfortable headroom.",
       size=8.6, color=SLATE, spacing=1.08)
    caption(s, "Latency includes detector pre/post-processing, not camera capture or decode. CPU variance was high (172–992 ms).")
    notes(s, "The model comparison is included because it shows the selection was made from evidence, and because it "
             "contains a failure (v4-tame) rather than only winners. The latency chart explains why the app requests "
             "CUDA and falls back to CPU: on CPU the pipeline is slower than the preview target, so the interface will "
             "feel choppy even though detection still works.")


# --------------------------------------------------------------------------- #
# 35 testing, defects
# --------------------------------------------------------------------------- #
def slide_testing(prs):
    s = new_slide(prs)
    header(s, 34, "05 · Verification", "Tests, visible defects and technical debt",
           "531 of 553 tests pass. The 22 failures are concentrated in one post-scan workflow file and are not hidden behind an aggregate percentage.")
    kpi(s, M, 1.58, 2.55, 1.16, "531", "Passed", "183.66 s runtime", fill=PALE_GREEN, accent=GREEN)
    kpi(s, 3.32, 1.58, 2.55, 1.16, "22", "Failed", "all in post-scan workflow", fill=PALE_RED, accent=RED)
    kpi(s, 6.02, 1.58, 2.55, 1.16, "553", "Collected", "pytest 9.1.1", fill=CREAM)
    image_panel(s, SHOTS / "01_dashboard.png", 8.80, 1.58, 3.90, 2.44, pad=0.03)
    tx(s, M, 2.95, 8.00, 0.28, "Reproduced product defects", size=10.2, color=DARK, bold=True)
    bullets(s, M, 3.25, 7.95, 1.62, [
        ("Dashboard table: ", "TypeError — set_cell() got an unexpected keyword argument 'family'."),
        ("ID-card preview: ", "UnboundLocalError — QImage is referenced before assignment."),
        ("Rendered card barcode: ", "re-decode returns nothing, so the service's own verification fails."),
    ], size=9.1, gap=4, bullet_color=RED)
    tx(s, M, 4.98, 8.00, 0.28, "Test failures and debt", size=10.2, color=DARK, bold=True)
    bullets(s, M, 5.28, 7.95, 1.58, [
        ("22/22 failures ", "are in tests/test_scan_post_scan_workflow.py (panel state, history insert, latch, async records)."),
        ("Training Center ", "shows 0 images while 2,551 files exist, because it reads dataset_images (0 rows)."),
        ("Unrouted generator; ", "stub logout; no auth; core dataset lacks a test split."),
    ], size=9.1, gap=4)
    tx(s, 8.80, 4.14, 3.90, 2.55,
       "The dashboard screenshot at right is the real failure state: the UI labels a Python TypeError as "
       "“Cannot reach the database”, which is itself a misleading error contract.\n\n"
       "None of the 531 passing tests exercises the ID-card decode-back check or the dashboard table.",
       size=8.6, color=SLATE, spacing=1.10)
    caption(s, "Command: venv/bin/python -m pytest tests/ -q. Full log: presentation/tools/pytest_output.txt.")
    notes(s, "This slide is the credibility check. The pass rate is 96.0%, but the absolute failure count matters more. The "
             "failures are not random: they cluster in the post-scan session contract, which is the same area where the "
             "scanner page has the most state. The dashboard screenshot shows both the broken table and the misleading "
             "database error label.")


# --------------------------------------------------------------------------- #
# 36 conclusion (must hit 35 total -> this is slide 35 if numbering adjusted)
# --------------------------------------------------------------------------- #
def slide_conclusion(prs, number):
    s = new_slide(prs)
    header(s, number, "05 · Roadmap", "What to fix first, and what the system already proves",
           "The core is demonstrable today. The remaining work is bounded, testable and mostly UI-level.")
    cols = [
        ("NOW · correctness", RED, [
            "Fix dashboard set_cell(family=...) call",
            "Fix ID-card preview QImage initialisation",
            "Make the rendered card barcode decode at 300 DPI",
            "Reconcile or quarantine the 22 post-scan tests",
        ]),
        ("NEXT · completeness", ORANGE, [
            "Route BarcodeGeneratorPage in the sidebar",
            "Replace logout stub with authentication",
            "Wire Training Center to the filesystem or populate dataset_images",
            "Add a real held-out test split before claiming generalisation",
        ]),
        ("LATER · operations", BLUE, [
            "Role-based access and audit user rows",
            "Packaged installer + migrations",
            "Camera/lighting field trial across distances",
            "Backup, retention and privacy review",
        ]),
    ]
    x = M
    for title, accent, items in cols:
        rect(s, x, 1.64, 3.90, 3.62, fill=SOFT, line=BORDER, radius=True)
        rect(s, x, 1.64, 3.90, 0.08, fill=accent)
        tx(s, x + 0.22, 1.86, 3.46, 0.30, title, size=10.8, color=INK, bold=True)
        bullets(s, x + 0.22, 2.24, 3.46, 2.86, items, size=8.9, gap=5, bullet_color=accent)
        x += 4.10
    rect(s, M, 5.48, 12.05, 1.20, fill=INK, radius=True)
    tx(s, M + 0.28, 5.64, 11.50, 0.90,
       "The defensible claim: a local, inspectable student-information system that turns a physical card into a verified "
       "database identity and then into enrollment, fees and documents — with its failures written down.",
       size=12.6, color=PAPER, bold=True, spacing=1.06)
    tx(s, M, 6.84, 8.90, 0.24,
       "Source reports: WIT_Project_Analysis_Report.md · WIT_Dataset_Analysis_Report.md · WIT_Presentation_References.md",
       size=7.8, color=MUTED, italic=True)
    notes(s, "Close on the distinction between what works and what is finished. The system proves the identity-to-record "
             "path and the document layer. It does not yet prove authentication, print-scannable ID cards or generalisation "
             "beyond the collected distance set. The roadmap is ordered by risk, not by novelty.")


# --------------------------------------------------------------------------- #
def main():
    prs = Presentation()
    prs.slide_width = Inches(W)
    prs.slide_height = Inches(H)

    slide_title(prs)
    slide_agenda(prs)
    slide_exec(prs)
    slide_problem(prs)
    slide_objectives(prs)
    slide_scope(prs)
    slide_context(prs)
    slide_stack(prs)
    slide_architecture(prs)
    slide_modules(prs)
    slide_dashboard(prs)
    slide_students(prs)
    slide_barcodes(prs)
    slide_scanner(prs)
    slide_pipeline(prs)
    slide_yolo(prs)
    slide_detection(prs)
    slide_decoding(prs)
    slide_outcomes(prs)
    slide_flowchart(prs)
    slide_enrollment(prs)
    slide_assessment(prs)
    slide_certificate(prs)
    slide_idcards(prs)
    slide_reports(prs)
    slide_database(prs)
    slide_erd(prs)
    slide_dataflow(prs)
    slide_dataset(prs)
    slide_dataset_quality(prs)
    slide_training_pipeline(prs)
    slide_training_results(prs)
    slide_model_comparison(prs)
    slide_testing(prs)
    slide_conclusion(prs, 35)

    # renumber the last slide's footer (testing is 35 above; conclusion must be 35)
    # The testing slide is actually 34: fix both footers deterministically.
    for idx, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip().endswith(f"/ {TOTAL}"):
                tf = shape.text_frame
                tf.paragraphs[0].runs[0].text = f"{idx:02d} / {TOTAL}"

    prs.save(OUT)
    print(f"[pptx] {OUT} · {len(prs.slides)} slides")


if __name__ == "__main__":
    main()
