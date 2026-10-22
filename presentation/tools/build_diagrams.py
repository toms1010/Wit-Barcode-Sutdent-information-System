#!/usr/bin/env python3
"""Generate all system diagrams from the verified implementation."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "presentation" / "tools"))
sys.path.insert(0, str(PROJECT))

from diagram_lib import (   # noqa: E402
    Canvas, INK, SLATE, MUTED, ORANGE, DARK, GOLD, CREAM, PAPER, BORDER,
    GREEN, RED, BLUE, PALE_GREEN, PALE_RED, PALE_BLUE, PALE_GOLD,
)

OUT = PROJECT / "presentation" / "diagrams"
OUT.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
def system_context():
    c = Canvas()
    c.title("System Context — WIT Barcode Student Information System",
            "External entities and the single desktop application boundary (verified from source)")

    c.box(4, 24, 17, 10, "Operator",
          "Registrar / authorised staff.\nNo sign-in is implemented;\nthe actor defaults to user id 1.",
          fill=PALE_GOLD, edge="#D97706", title_size=12, body_size=8.2)

    c.box(33, 15, 32, 28, "WIT Barcode Student\nInformation System",
          "PySide6 desktop application\n\n• Student & academic records\n• YOLO + pyzbar barcode scanning\n"
          "• ID card / COM / report generation\n• Local MySQL persistence",
          fill=CREAM, edge=ORANGE, title_size=14, body_size=9.0, line=2.2)

    c.box(78, 42, 19, 9, "MySQL 8.4.11", "wit_barcode_system\n19 tables · InnoDB · utf8mb4",
          fill=PALE_BLUE, edge=BLUE, title_size=10, body_size=7.8)
    c.box(78, 28, 19, 9, "Camera", "OpenCV / V4L2 capture\n1280×720 default",
          fill=PALE_BLUE, edge=BLUE, title_size=10, body_size=7.8)
    c.box(78, 14, 19, 9, "Filesystem", "model · datasets · generated\ncards · reports",
          fill=PALE_BLUE, edge=BLUE, title_size=10, body_size=7.8)
    c.box(78, 4, 19, 7, "Printer / PDF", "QPrinter · QPdfWriter\nReportLab",
          fill=PALE_BLUE, edge=BLUE, title_size=9.4, body_size=7.4)

    c.arrow(21.5, 29, 32.5, 29, "records, scans,\nreports", lw=1.8,
            label_offset=(0, 2.0), label_size=7.2)
    c.arrow(65.5, 34, 77.5, 45, "parameterised SQL\n(mysql-connector 26.7)", lw=1.5,
            label_offset=(0, 1.8), label_size=7.0)
    c.arrow(77.5, 33, 65.5, 31, "frames", lw=1.5, label_offset=(0, 1.3), label_size=7.0)
    c.arrow(65.5, 25, 77.5, 20, "model, dataset,\ncards, reports", lw=1.5,
            label_offset=(0, 1.6), label_size=7.0)
    c.arrow(65.5, 20, 77.5, 8, "print / export", lw=1.5,
            label_offset=(-2, 1.2), label_size=7.0)

    c.text(4, 15, "Scope note", size=9, weight="bold", color=DARK)
    c.text(4, 13.4,
           "The application is a single-process desktop client. It has no web server, no remote API and no\n"
           "authentication layer: it connects directly to a local MySQL instance and reads local files. The\n"
           "database is the only shared component; every other relationship is local to the workstation.",
           size=7.6, color=SLATE)
    c.save(OUT / "WIT_System_Context.png")


# --------------------------------------------------------------------------- #
def architecture():
    c = Canvas()
    c.title("Overall System Architecture",
            "Verified components, runtimes and data paths — no web/API tier exists in this implementation")

    # Operator
    c.box(1.2, 27, 9.5, 8, "Operator", "registrar\nstaff", fill=PALE_GOLD,
          edge="#D97706", title_size=8.8, body_size=7.0)

    # Presentation tier
    c.band(12.5, 9.5, 21.5, 39, "PRESENTATION · PySide6 6.12 (Qt 6)", fill="#FFF7ED", edge="#FDBA74")
    c.box(14.0, 41.5, 18.5, 5.2, "MainWindow · router · sidebar", "", title_size=8.6)
    c.box(14.0, 35.2, 8.7, 5.2, "Dashboard", "", title_size=8.2)
    c.box(23.8, 35.2, 8.7, 5.2, "Students", "", title_size=8.2)
    c.box(14.0, 29.0, 8.7, 5.2, "Scanner", "", title_size=8.2)
    c.box(23.8, 29.0, 8.7, 5.2, "Enrollment", "", title_size=8.2)
    c.box(14.0, 22.8, 8.7, 5.2, "Assessment", "", title_size=8.2)
    c.box(23.8, 22.8, 8.7, 5.2, "Reports", "", title_size=8.2)
    c.box(14.0, 16.6, 8.7, 5.2, "Certificate", "", title_size=8.2)
    c.box(23.8, 16.6, 8.7, 5.2, "Training", "", title_size=8.2)
    c.box(14.0, 11.0, 18.5, 4.2, "Courses · Settings · dialogs", "", title_size=8.2)

    # Services tier
    c.band(36.5, 9.5, 27, 39, "APPLICATION SERVICES · Python 3.14.4", fill="#F8FAFC", edge="#CBD5E1")
    c.box(38.0, 42.5, 11.4, 5.4, "StudentService", "CRUD + lookup", title_size=8.2, body_size=6.9)
    c.box(50.4, 42.5, 11.6, 5.4, "ScanPipeline", "detect→gate→decode→verify", title_size=8.2, body_size=6.7)
    c.box(38.0, 35.6, 11.4, 5.4, "YOLODetectorService", "Ultralytics 8.4.177", title_size=8.0, body_size=6.7)
    c.box(50.4, 35.6, 11.6, 5.4, "BarcodeDecoder", "pyzbar 0.1.9", title_size=8.2, body_size=6.7)
    c.box(38.0, 28.7, 11.4, 5.4, "IdCardService", "CR80 · QPainter · PDF", title_size=8.2, body_size=6.7)
    c.box(50.4, 28.7, 11.6, 5.4, "AssessmentService", "fees · schedules", title_size=8.2, body_size=6.7)
    c.box(38.0, 21.8, 11.4, 5.4, "ReportService", "A4 PDF / CSV", title_size=8.2, body_size=6.7)
    c.box(50.4, 21.8, 11.6, 5.4, "ScanSession", "latch + debounce", title_size=8.2, body_size=6.7)
    c.box(38.0, 13.0, 24.0, 7.2, "Support services",
          "camera capture · scan geometry · overlay renderer · detection smoother\n"
          "audio cue · COM summary/document/print · identifiers · formatting",
          title_size=8.2, body_size=6.8)

    # Data tier
    c.band(65.5, 9.5, 33, 39, "DATA TIER · local workstation", fill="#F8FAFC", edge="#CBD5E1")
    c.box(67.0, 40.0, 30.0, 9.2, "MySQL 8.4.11 · InnoDB · utf8mb4",
          "wit_barcode_system — 19 tables, 230 columns, 33 foreign keys\n"
          "pool: MySQLConnectionPool size 10 · dict cursor · explicit commit",
          fill=PALE_BLUE, edge=BLUE, title_size=9.0, body_size=7.1)
    c.box(67.0, 29.5, 14.4, 8.6, "Model store", "models_yolo/active/best.pt\nYOLOv8n · 6.48 MB",
          fill=PALE_GREEN, edge=GREEN, title_size=8.4, body_size=7.0)
    c.box(82.6, 29.5, 14.4, 8.6, "Dataset store", "datasets/ — 2,551 images\n2.77 GB · 8 variants",
          fill=PALE_GREEN, edge=GREEN, title_size=8.4, body_size=7.0)
    c.box(67.0, 19.0, 14.4, 8.4, "Generated cards", "generated_id_cards/\ngenerated_barcodes/",
          fill=PALE_GREEN, edge=GREEN, title_size=8.4, body_size=7.0)
    c.box(82.6, 19.0, 14.4, 8.4, "Reports & assets", "reports/ · assets/\nCOM, assessment, CSV",
          fill=PALE_GREEN, edge=GREEN, title_size=8.4, body_size=7.0)
    c.box(67.0, 11.0, 30.0, 6.2, "Local filesystem only — no cloud, no network service",
          "", fill="#F1F5F9", edge=BORDER, title_size=8.0)

    # arrows
    c.arrow(10.9, 31, 13.8, 32.5, "UI events", lw=1.6, label_size=6.4,
            label_offset=(-0.4, 0), label_rot=90)
    c.arrow(34.2, 31, 36.4, 31, "service calls (Qt signals)", lw=2.0, label_size=6.2,
            label_offset=(0, 0), label_rot=90)
    c.arrow(63.8, 31, 66.9, 31, "SQL / rows", lw=2.0, label_size=6.4,
            label_offset=(-1.2, 0), label_rot=90)

    # external hardware + files
    c.box(1.2, 12.5, 9.5, 8, "Camera", "OpenCV 5.0.0\nV4L2", fill=PALE_BLUE, edge=BLUE,
          title_size=8.4, body_size=6.7)
    c.arrow(10.9, 16.5, 13.8, 18.5, "frames", lw=1.4, label_size=6.4,
            label_offset=(-0.45, 0), label_rot=90)

    c.box(1.2, 2.0, 9.5, 6.5, "GPU", "RTX 2050\nCUDA → CPU fallback", fill=PALE_BLUE, edge=BLUE,
          title_size=8.0, body_size=6.5)
    c.elbow([(10.9, 6.0), (44.0, 6.0), (44.0, 9.2)], "CUDA inference",
            lw=1.2, label_offset=(0, 1.0), label_size=6.2)

    c.text(12.2, 1.2,
           "Solid arrows: synchronous calls / data.\nBlue = infrastructure.  Green = on-disk artefacts.",
           size=7.0, color=MUTED, va="bottom")
    c.save(OUT / "WIT_System_Architecture.png")


# --------------------------------------------------------------------------- #
def flowchart():
    c = Canvas()
    c.title("Complete System Flowchart — Student Identification")

    def step(x, y, w, h, n, title, body=""):
        c.box(x, y, w, h, f"{n}. {title}", body, fill=PAPER, edge=BORDER,
              title_size=7.9, body_size=6.8, align="left")

    # --- column 1 ---------------------------------------------------------
    c.pill(4, 48.0, 18, 3.4, "START", fill=ORANGE, size=8.4)
    step(3, 40.5, 20, 5.6, 1, "Launch main.py", "QApplication + theme + MainWindow\n(no login screen)")
    step(3, 33.0, 20, 5.6, 2, "Open Barcode Scanner", "sidebar route → ScannerPage")
    c.diamond(13, 25.5, 15, 7.6, "Camera\navailable?")
    step(3, 14.0, 20, 6.2, 3, "Upload a card image", "or use the live camera feed\n(OpenCV 1280×720)")
    c.box(3, 5.0, 20, 6.2, "ERROR state", "camera error is shown in header,\nviewport and footer; upload stays available",
          fill=PALE_RED, edge=RED, title_size=7.8, body_size=6.6)
    c.arrow(13, 48.0, 13, 46.3)
    c.arrow(13, 40.5, 13, 38.8)
    c.arrow(13, 33.0, 13, 29.4)
    c.arrow(13, 21.7, 13, 20.4, "yes", label_size=6.6, label_offset=(1.4, 0))
    c.elbow([(5.5, 25.5), (1.0, 25.5), (1.0, 8.2), (2.8, 8.2)], "no", label_size=6.4,
            label_offset=(1.0, 5))
    c.arrow(23.0, 17.0, 26.5, 17.0, "")

    # --- column 2 ---------------------------------------------------------
    step(28, 41.0, 22, 5.4, 4, "YOLO inference", "Ultralytics 8.4.177 · YOLOv8n\n1 class: barcode · conf 0.75 · iou 0.45")
    c.diamond(39, 33.2, 18, 8.0, "Barcode box\ninside ROI gate?")
    step(28, 23.0, 22, 5.4, 5, "Crop region", "expand by 0.30 margin\nmin 80×24 px")
    step(28, 16.0, 22, 5.0, 6, "Decode with pyzbar", "ordered preprocessing attempts\n(≤6 configured)")
    c.diamond(39, 9.0, 18, 7.4, "Payload\ndecoded?")
    c.arrow(39, 41.0, 39, 37.3)
    c.arrow(39, 29.2, 39, 26.5, "yes", label_size=6.6, label_offset=(1.2, 0))
    c.arrow(39, 23.0, 39, 21.2)
    c.arrow(39, 16.0, 39, 12.8)
    c.elbow([(47.5, 33.2), (52.0, 33.2), (52.0, 49.0), (28.0, 49.0), (28.0, 44.0)],
            "no →\nrescan", label_size=6.4, label_offset=(0, 1.0))
    c.elbow([(47.5, 9.0), (53.0, 9.0), (53.0, 18.5), (50.2, 18.5)], "no →\nretry",
            label_size=6.4, label_offset=(0, 0.9))

    # --- column 3 ---------------------------------------------------------
    c.diamond(64, 48.5, 17, 7.6, "Two different\npayloads?")
    c.diamond(64, 39.0, 17, 7.6, "Duplicate of\nlast scan?")
    step(54, 29.5, 20, 6.0, 7, "Database lookup", "SELECT * FROM students\nWHERE barcode_payload = %s")
    c.diamond(64, 21.0, 17, 7.6, "Student\nfound?")
    step(54, 11.0, 20, 6.4, 8, "Display + confirm", "student panel, success tone,\nCOM / finance result modal")
    step(54, 3.2, 20, 5.6, 9, "Record scan", "INSERT INTO barcode_scans\nthen finish")
    c.arrow(53.0, 9.0, 54.0, 46.0, "yes", lw=1.4, label_size=6.4, label_offset=(-3, 8), rad=-0.25)
    c.arrow(64, 44.7, 64, 42.8)
    c.arrow(64, 35.2, 64, 33.0, "no", label_size=6.4, label_offset=(1.2, 0))
    c.arrow(64, 29.5, 64, 24.8)
    c.arrow(64, 17.2, 64, 14.2, "yes", label_size=6.4, label_offset=(1.2, 0))
    c.arrow(64, 11.0, 64, 8.8)
    c.pill(54, 0.4, 20, 2.2, "FINISH", fill=GREEN, size=7.8)

    # --- failure column ---------------------------------------------------
    c.box(79, 45.5, 19, 6.2, "AMBIGUOUS", "two payloads in one frame are refused;\noperator must present one card",
          fill=PALE_RED, edge=RED, title_size=7.8, body_size=6.5)
    c.box(79, 36.5, 19, 6.2, "DUPLICATE LATCH", "one card = one scan; card must leave frame\nor Scan Next Student must reset",
          fill=PALE_GOLD, edge="#D97706", title_size=7.8, body_size=6.5)
    c.box(79, 27.5, 19, 6.2, "NOT FOUND", "not_found row written to history;\nno student panel, no success tone",
          fill=PALE_RED, edge=RED, title_size=7.8, body_size=6.5)
    c.box(79, 18.5, 19, 6.2, "DATABASE UNAVAILABLE", "ScanState.ERROR — never reported as\nan unknown barcode; retry reset available",
          fill=PALE_RED, edge=RED, title_size=7.6, body_size=6.5)
    c.box(79, 9.5, 19, 6.2, "RE-SCAN", "duplicate suppressed, then session returns\nto Ready for the next card",
          fill="#F1F5F9", edge=BORDER, title_size=7.8, body_size=6.5)
    c.arrow(72.5, 48.5, 78.8, 48.5, "yes", lw=1.2, label_size=6.2, label_offset=(0, 1.0))
    c.arrow(72.5, 39.0, 78.8, 39.0, "yes", lw=1.2, label_size=6.2, label_offset=(0, 1.0))
    c.arrow(72.5, 21.0, 78.8, 21.0, "no", lw=1.2, label_size=6.2, label_offset=(0, 1.0))
    c.arrow(72.7, 30.5, 78.8, 30.5, "DB error", lw=1.2, label_size=6.2, label_offset=(0, 1.0))
    c.save(OUT / "WIT_System_Flowchart.png")


# --------------------------------------------------------------------------- #
def barcode_stages():
    c = Canvas()
    c.title("Detection, Decoding and Lookup are Three Separate Stages",
            "A YOLO hit identifies a region; it says nothing about the payload or the student")
    stages = [
        ("1 · YOLO DETECTION", "Ultralytics YOLOv8n\n1 class: barcode\nconf ≥ 0.75 · iou 0.45", ORANGE, "locates a region"),
        ("2 · ROI GATE + CROP", "overlap ≥ 0.65 with scan region\ncrop expanded by 0.30 margin", DARK, "selects pixels"),
        ("3 · BARCODE DECODE", "pyzbar · CODE39\n≤6 preprocessing attempts", ORANGE, "reads the payload"),
        ("4 · VALIDATE", "9-digit payload\nambiguity / duplicate checks", DARK, "accepts one value"),
        ("5 · DATABASE LOOKUP", "students.barcode_payload = %s\nexact string match", ORANGE, "resolves a person"),
        ("6 · PRESENT + RECORD", "student panel, tone, modal\nINSERT barcode_scans", GREEN, "confirms and logs"),
    ]
    x = 1.5
    w = 15.0
    for i, (title, body, colour, tag) in enumerate(stages):
        fill = {ORANGE: "#FFF7ED", DARK: "#FFF7ED", GREEN: PALE_GREEN}[colour]
        c.box(x, 27.5, w, 15.0, title, body, fill=fill, edge=colour,
              title_size=7.7, body_size=6.9, line=1.5)
        c.pill(x + 1.2, 23.5, w - 2.4, 2.6, tag, fill=colour, size=6.8)
        if i < len(stages) - 1:
            c.arrow(x + w + 0.2, 35.0, x + w + 1.9, 35.0, lw=1.8)
        x += w + 2.1

    c.text(1.5, 20.7, "Why each stage can fail independently", size=10, weight="bold", color=DARK)
    notes = [
        ("Detection succeeds, decode fails", "Blur, glare, low resolution or an undersampled symbol can defeat zbar even when the box is correct."),
        ("Decode succeeds, lookup fails", "A valid CODE39 payload that is not present in students.barcode_payload resolves to no person."),
        ("Lookup is never attempted", "A frame with two different payloads is refused as AMBIGUOUS instead of choosing one."),
        ("Database is unavailable", "The pipeline raises ScanState.ERROR; it must not be shown as an unknown barcode."),
    ]
    for i, (head, body) in enumerate(notes):
        x = 1.5 + (i % 2) * 49.0
        y = 14.5 - (i // 2) * 5.6
        c.box(x, y, 47.0, 4.6, head, body, fill="#F8FAFC", edge=BORDER,
              title_size=8.2, body_size=7.0, align="left")

    c.text(1.5, 2.2,
           "Verified in source: services/scan_pipeline.py (ScanState), services/yolo_detector.py, "
           "services/barcode_decoder.py, database/repositories/student_repository.py.",
           size=6.8, color=MUTED)
    c.save(OUT / "WIT_Barcode_Pipeline.png")


# --------------------------------------------------------------------------- #
def yolo_architecture():
    c = Canvas()
    c.title("YOLOv8n Architecture — Conceptual View of the Trained Detector",
            "Variant verified from the checkpoint: depth 0.33 / width 0.25, 1 class, 3.2 M parameters (nano)")

    blocks = [
        ("INPUT", "640 × 640 × 3\nBGR frame → RGB,\nletterboxed", PALE_BLUE, BLUE),
        ("BACKBONE", "CBS stem\nC2f × N\nSPPF", "#FFF7ED", ORANGE),
        ("NECK", "PAN-FPN\ntop-down + bottom-up\nfeature fusion", PALE_GOLD, "#D97706"),
        ("HEAD", "decoupled head\nbox + class branches\nanchor-free", PALE_GREEN, GREEN),
        ("OUTPUT", "1 class: barcode\nboxes + confidence\nNMS (IoU 0.45)", "#F1F5F9", BORDER),
    ]
    x = 1.5
    for i, (title, body, fill, edge) in enumerate(blocks):
        c.box(x, 31.0, 17.0, 12.0, title, body, fill=fill, edge=edge,
              title_size=9.2, body_size=7.4, line=1.6)
        if i < len(blocks) - 1:
            c.arrow(x + 17.2, 37.0, x + 19.4, 37.0, lw=1.9)
        x += 19.6

    c.text(1.5, 26.0, "What the checkpoint proves", size=9.5, weight="bold", color=DARK)
    c.text(1.5, 24.4,
           "• The model YAML records C2f blocks, an SPPF module and a Detect head — the YOLOv8 detection family.\n"
           "• depth_multiple 0.33 and width_multiple 0.25 identify the nano variant.\n"
           "• names = {0: 'barcode'} and nc = 1 identify a single-class custom detector.\n"
           "• train_args.model = yolov8n.pt shows COCO-pretrained weights were fine-tuned, not trained from scratch.",
           size=7.6, color=SLATE)

    c.box(1.5, 10.0, 29.0, 10.0, "Backbone — feature extraction",
          "Repeated CBS (Conv + BatchNorm + SiLU) and C2f blocks reduce spatial resolution while increasing "
          "channel depth; SPPF pools at multiple kernel sizes to widen the receptive field.",
          fill="#FFF7ED", edge=ORANGE, title_size=8.6, body_size=7.0, align="left")
    c.box(32.5, 10.0, 29.0, 10.0, "Neck — multi-scale fusion",
          "PAN-FPN passes semantic information down and localisation detail up, so small barcode symbols "
          "survive at the stride-8/16/32 feature maps.",
          fill=PALE_GOLD, edge="#D97706", title_size=8.6, body_size=7.0, align="left")
    c.box(63.5, 10.0, 35.0, 10.0, "Head + post-processing",
          "The decoupled head predicts box regression and class probability separately. Predictions below the "
          "confidence threshold are dropped, then non-maximum suppression at IoU 0.45 keeps one box per symbol.",
          fill=PALE_GREEN, edge=GREEN, title_size=8.6, body_size=7.0, align="left")

    c.text(1.5, 5.2,
           "Conceptual illustration. A layer-by-layer architecture graph was not produced from the checkpoint; "
           "the variant facts above were read from model.yaml and train_args.",
           size=6.8, color=MUTED, style="italic")
    c.save(OUT / "WIT_YOLO_Architecture.png")


# --------------------------------------------------------------------------- #
def training_pipeline():
    c = Canvas()
    c.title("YOLO Training Workflow — Completed Stages and Gaps",
            "Stages are evidence-based: logs, checkpoints and results.csv in models_yolo/candidates/")

    stages = [
        ("1 · COLLECT", "3 physical ID cards\n2 barcode-free panels\n(1 more card added later)", "done"),
        ("2 · CLEAN", "corrupt / duplicate checks\nsource assets audited", "done"),
        ("3 · ANNOTATE", "YOLO txt, 1 class\nbarcode bounding boxes", "done"),
        ("4 · VALIDATE", "scripts/validate_dataset.py\nbounds, classes, leakage", "done"),
        ("5 · SPLIT", "by physical card:\nA train · B val · C test", "done"),
        ("6 · BUILD / AUGMENT", "8 dataset variants\n2,551 images · 2,515 boxes", "done"),
        ("7 · CONFIGURE", "yolov8n.pt · 640 px\nbatch 16 · AdamW lr0 0.002", "done"),
        ("8 · TRAIN", "6 recorded runs\nv7: 64 of 100 epochs", "partial"),
        ("9 · EVALUATE", "results.csv, confusion\nmatrices, PR curves", "done"),
        ("10 · PROMOTE", "promotion gate exists;\nv7 install not logged", "partial"),
        ("11 · INTEGRATE", "models_yolo/active/best.pt\nloaded by scanner", "done"),
        ("12 · FIELD TEST", "real cards A/B verified;\ncard C is the hard case", "partial"),
    ]
    colours = {"done": (PALE_GREEN, GREEN), "partial": (PALE_GOLD, "#D97706")}
    x, y = 1.5, 40.0
    w, h = 15.0, 9.6
    for i, (title, body, status) in enumerate(stages):
        fill, edge = colours[status]
        c.box(x, y, w, h, title, body, fill=fill, edge=edge, title_size=7.6,
              body_size=6.7, line=1.3)
        if i < len(stages) - 1:
            if (i + 1) % 6 == 0:
                pass
            else:
                c.arrow(x + w + 0.2, y + h / 2, x + w + 1.9, y + h / 2, lw=1.5)
        x += w + 2.1
        if (i + 1) % 6 == 0:
            x = 1.5
            y = 25.0
    # wrap arrow
    c.elbow([(94.0, 40.0), (97.0, 40.0), (97.0, 49.0), (1.5, 49.0), (1.5, 45.0)],
            "", lw=1.2, color="#94A3B8")

    c.legend([("completed with artefact", GREEN, "box"), ("partial / unverified", "#D97706", "box")],
             x=1.5, y=18.0, size=7.2, gap=30)

    c.box(1.5, 4.5, 47.0, 11.0, "Verified gaps",
          "• v7 requested 100 epochs and stopped at 64; its own report says promote:false because mean "
          "confidence was lower than the active model.\n"
          "• active/best.pt is byte-identical to v7/best.pt, but no promotion log line records the copy.\n"
          "• strict run's results.csv begins at epoch 41 and duplicates v3's epoch-60 metrics.\n"
          "• In-app Training Center: dataset import, annotation import and model test are stubs.",
          fill=PALE_RED, edge=RED, title_size=8.4, body_size=6.9, align="left")
    c.box(51.0, 4.5, 47.5, 11.0, "What is reproducible",
          "• Every checkpoint reports ultralytics 8.4.177 and license AGPL-3.0.\n"
          "• All six runs used yolov8n.pt, imgsz 640, batch 16, seed 0.\n"
          "• v5, v6 and v7 used GPU device '0'; strict/v3/v4 ran on CPU.\n"
          "• Metrics below are read from results.csv — nothing is interpolated.",
          fill=PALE_GREEN, edge=GREEN, title_size=8.4, body_size=6.9, align="left")
    c.save(OUT / "WIT_YOLO_Training_Pipeline.png")


# --------------------------------------------------------------------------- #
def data_flow():
    c = Canvas()
    c.title("Level 1 Data Flow Diagram",
            "Processes, data stores and external entities verified from the repository layer")

    c.box(1.2, 27.0, 12.0, 8.0, "Operator", "registrar /\nstaff", fill=PALE_GOLD,
          edge="#D97706", title_size=9.0, body_size=7.2)
    c.box(1.2, 18.0, 12.0, 6.0, "Camera", "OpenCV frame source", fill=PALE_BLUE, edge=BLUE,
          title_size=8.2, body_size=6.8)
    c.box(1.2, 9.0, 12.0, 6.0, "Model store", "best.pt · YOLOv8n", fill=PALE_GREEN, edge=GREEN,
          title_size=8.2, body_size=6.8)

    procs = [
        ("1.0 Manage student records", 43.0),
        ("2.0 Manage enrollment", 33.0),
        ("3.0 Detect, decode & verify", 23.0),
        ("4.0 Assess fees / generate docs", 13.0),
    ]
    for title, y in procs:
        c.box(20.0, y, 22.0, 6.0, title, "", fill="#FFF7ED", edge=ORANGE,
              title_size=8.2, line=1.5)

    stores = [
        ("D1 · students\n+ student_contacts", 43.0),
        ("D2 · courses · subjects\nenrollments · enrollment_subjects", 33.0),
        ("D3 · barcodes\nbarcode_scans", 23.0),
        ("D4 · assessments · items\npayments · schedules", 13.0),
    ]
    for title, y in stores:
        c.box(50.0, y, 24.0, 6.0, title, "", fill=PALE_BLUE, edge=BLUE,
              title_size=7.9, line=1.2)
        # open-ended data store glyph
        c.ax.plot([50.0, 50.0], [y, y + 6.0], color=BLUE, lw=3)

    c.box(80.0, 17.0, 18.0, 7.0, "Generated outputs", "ID cards · COM · reports\nPDF / CSV / print",
          fill=PALE_GREEN, edge=GREEN, title_size=8.0, body_size=6.8)

    for _, y in procs:
        c.elbow([(13.2, 31.0), (16.5, 31.0), (16.5, y + 3.0), (19.8, y + 3.0)], "", lw=1.2, color="#94A3B8")

    for (title, y), (store, sy) in zip(procs, stores):
        c.arrow(42.2, y + 3.0, 49.8, sy + 3.0, "SQL", lw=1.4, label_size=6.4,
                label_offset=(0, 1.0))

    c.arrow(13.2, 20.0, 19.8, 24.5, "frames", lw=1.3, label_size=6.4, label_offset=(0, 1.0))
    c.arrow(13.2, 11.0, 19.8, 23.2, "weights", lw=1.3, label_size=6.4, label_offset=(-1.6, 0.4))
    c.elbow([(42.2, 16.5), (45.0, 16.5), (45.0, 21.2), (78.0, 21.2), (78.0, 19.0), (79.8, 19.0)],
            "PDF / CSV", label_size=6.4, label_offset=(0, 0.9))

    c.text(1.2, 8.2, "Data dictionary (selected)", size=9, weight="bold", color=DARK)
    c.text(1.2, 6.6,
           "D1 students(barcode_payload UNIQUE, student_id, reference_number) · D3 barcodes(barcode_value UNIQUE, type) / "
           "barcode_scans(scanned_value, student_id, scan_status, confidence_score, detection_bbox)\n"
           "D2 enrollments UNIQUE(student_id, academic_year, semester) · enrollment_subjects UNIQUE(enrollment_id, subject_id) · "
           "D4 assessments(1..N items, payments, schedules)",
           size=7.0, color=SLATE)
    c.save(OUT / "WIT_Data_Flow_Diagram.png")


# --------------------------------------------------------------------------- #
TABLES = {
    # name: (column, row, fields, height_boost)
    "users": ("c1", 0, ["PK id", "username UQ", "email UQ", "password_hash", "role ENUM", "is_active"], 0),
    "students": ("c1", 1, ["PK id", "student_id UQ", "reference_number UQ", "first/middle/last_name",
                            "program", "year_level ENUM", "enrollment_status", "barcode_payload UQ",
                            "FK created_by → users.id", "FK updated_by → users.id", "… 21 columns"], 2.6),
    "student_contacts": ("c1", 2, ["PK id", "FK student_id → students.id", "contact_type ENUM", "contact_value", "is_primary"], 0),
    "courses": ("c2", 0, ["PK id", "course_code UQ", "course_name", "department", "credits"], 0),
    "subjects": ("c2", 1, ["PK id", "subject_code UQ", "subject_name", "FK course_id → courses.id", "credits", "lecture/lab_hours"], 0),
    "enrollments": ("c2", 2, ["PK id", "FK student_id → students.id", "academic_year", "semester ENUM",
                              "status ENUM", "UNIQUE(student, year, sem)"], 0.8),
    "enrollment_subjects": ("c2", 3, ["PK id", "FK enrollment_id → enrollments.id", "FK subject_id → subjects.id",
                                      "section · schedule · room", "grade · status", "UNIQUE(enrollment, subject)"], 0.6),
    "barcodes": ("c3", 0, ["PK id", "FK student_id → students.id", "barcode_value UQ", "barcode_type ENUM", "is_active", "regenerated_count"], 0),
    "barcode_scans": ("c3", 1, ["PK id", "FK barcode_id → barcodes.id", "FK student_id → students.id", "FK scanned_by → users.id",
                                "scanned_value", "scan_status ENUM", "confidence_score", "detection_bbox JSON", "scanned_at"], 1.2),
    "id_cards": ("c3", 2, ["PK id", "FK student_id → students.id", "template_key", "status ENUM", "PDF / image paths",
                           "UNIQUE(student, template)"], 0.6),
    "audit_logs": ("c3", 3, ["PK id (BIGINT)", "FK user_id → users.id", "action", "table_name · record_id", "old/new_values JSON", "created_at"], 0.4),
    "assessment_categories": ("c4", 0, ["PK id", "category_code UQ", "category_name", "is_fee / is_discount", "display_order"], 0),
    "assessments": ("c4", 1, ["PK id", "FK student_id → students.id", "FK enrollment_id → enrollments.id", "academic_year · semester",
                              "status ENUM", "total_assessed", "total_payments", "outstanding_balance", "… 16 columns"], 2.2),
    "assessment_items": ("c4", 2, ["PK id", "FK assessment_id → assessments.id", "FK category_id → categories.id",
                                   "amount", "is_verified · verified_by"], 0.4),
    "payments": ("c4", 3, ["PK id", "FK assessment_id → assessments.id", "payment_date · amount", "payment_method ENUM",
                           "is_voided · voided_by"], 0.4),
    "payment_schedules": ("c5", 0, ["PK id", "FK assessment_id → assessments.id", "schedule_type ENUM", "due_date", "amount_due/paid", "status ENUM"], 0.2),
    "yolo_models": ("c5", 1, ["PK id", "model_name · version", "file_path", "classes JSON", "metrics JSON", "status ENUM"], 0.2),
    "dataset_images": ("c5", 2, ["PK id", "file_path UQ", "width · height · size", "split ENUM", "has_annotations", "is_validated"], 0.2),
    "system_settings": ("c5", 3, ["PK id", "setting_key UQ", "setting_value", "setting_type ENUM", "is_public"], 0.2),
}
COL_X = {"c1": 1.2, "c2": 20.6, "c3": 40.0, "c4": 59.4, "c5": 78.8}
ROW_Y = [41.0, 30.4, 19.8, 9.2]


def erd():
    c = Canvas(width=16, height=9)
    # different coordinate height for 16:9 in inches 16x9 -> still 56.25 y units
    c.title("Entity-Relationship Diagram — 19 Tables, 230 Columns, 33 Foreign Keys",
            "Live MySQL 8.4.11 schema verified with SHOW CREATE TABLE; key fields shown, PK/FK marked", size=15)

    positions = {}
    for name, (col, row, fields, boost) in TABLES.items():
        x = COL_X[col]
        y = ROW_Y[row] - boost
        w = 18.0
        h = 8.6 + boost
        fill = PAPER
        if name in ("students", "barcodes", "barcode_scans"):
            fill = "#FFF7ED"
        body = "\n".join(fields)
        c.box(x, y, w, h, name, body, fill=fill, edge=BORDER, title_size=7.0,
              body_size=5.35, align="left", line=0.8, radius=0.35)
        positions[name] = (x, y, w, h)

    # relationships drawn behind (approximate edges, labels for key cardinalities)
    def edge(a, b, side_a, side_b, label=None, colour="#94A3B8"):
        ax, ay, aw, ah = positions[a]
        bx, by, bw, bh = positions[b]
        pts = {
            "top": (ax + aw / 2, ay + ah), "bottom": (ax + aw / 2, ay),
            "left": (ax, ay + ah / 2), "right": (ax + aw, ay + ah / 2),
        }
        ptb = {
            "top": (bx + bw / 2, by + bh), "bottom": (bx + bw / 2, by),
            "left": (bx, by + bh / 2), "right": (bx + bw, by + bh / 2),
        }
        x1, y1 = pts[side_a]
        x2, y2 = ptb[side_b]
        c.ax.add_patch(__import__("matplotlib").patches.FancyArrowPatch(
            (x1, y1), (x2, y2), arrowstyle="-", linewidth=0.8, color=colour,
            zorder=0.5, shrinkA=0, shrinkB=0,
            connectionstyle="arc3,rad=0.0"))
        if label:
            c.ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.3, label, fontsize=5.0,
                      color="#64748B", ha="center", va="bottom", zorder=6,
                      bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.95))

    # Cardinality is uniform (1..N) and carried by the legend; repeating it on
    # every line produced illegible stacks where several FKs converge.
    edge("users", "students", "bottom", "top")
    edge("students", "student_contacts", "bottom", "top")
    edge("courses", "subjects", "bottom", "top")
    edge("enrollments", "enrollment_subjects", "bottom", "top")
    edge("subjects", "enrollment_subjects", "right", "left")
    edge("students", "enrollments", "right", "left")
    edge("students", "barcodes", "right", "left")
    edge("students", "barcode_scans", "right", "left")
    edge("barcodes", "barcode_scans", "bottom", "top")
    edge("students", "id_cards", "right", "left")
    edge("users", "audit_logs", "right", "left")
    edge("students", "assessments", "right", "left")
    edge("enrollments", "assessments", "right", "left")
    edge("assessments", "assessment_items", "bottom", "top")
    edge("assessment_categories", "assessment_items", "bottom", "top")
    edge("assessments", "payments", "bottom", "top")
    edge("assessments", "payment_schedules", "right", "left")
    edge("users", "yolo_models", "right", "left")

    c.legend([("PK", ORANGE, "box"), ("FK", BLUE, "box"), ("1..N one-to-many", "#94A3B8", "line")],
             x=1.2, y=4.2, size=6.4, gap=13)
    c.text(1.2, 3.0,
           "Junction tables: enrollment_subjects (enrollments ↔ subjects) and assessment_items (assessments ↔ categories, carrying amount/verification). "
           "barcode_scans is an append-only history: deleting a student or barcode sets its FK to NULL so the scan survives.",
           size=6.2, color=SLATE)
    c.text(1.2, 1.9,
           "All 19 tables use InnoDB and utf8mb4_unicode_ci. 33 foreign keys total; 18 relationship paths are drawn for legibility, "
           "principally the optional users.created_by/updated_by audit links.",
           size=6.0, color=MUTED, style="italic")
    c.save(OUT / "WIT_Database_ERD.png", dpi=200)


# --------------------------------------------------------------------------- #
def deployment():
    c = Canvas()
    c.title("Deployment and Execution View",
            "Single-workstation desktop deployment — verified from main.py, .env and installed runtimes")

    c.band(2.0, 8.0, 62.0, 40.0, "WORKSTATION · Linux · no server process, no network listener", fill="#F8FAFC", edge="#CBD5E1")
    c.box(4.0, 36.0, 26.0, 9.0, "Python 3.14.4 virtualenv",
          "venv/ · pip requirements.txt\nPySide6 6.12 · ultralytics 8.4.177 · torch 2.14.1",
          fill=PALE_BLUE, edge=BLUE, title_size=8.6, body_size=7.0, align="left")
    c.box(32.0, 36.0, 29.0, 9.0, "Application process — main.py",
          "QApplication → apply_theme() → MainWindow\n10 routed pages created lazily",
          fill="#FFF7ED", edge=ORANGE, title_size=8.6, body_size=7.0, align="left")
    c.box(4.0, 24.0, 26.0, 9.0, "MySQL 8.4.11 server",
          "localhost:3306 · database wit_barcode_system\nuser wit_user · least-privilege grant on one schema",
          fill=PALE_GREEN, edge=GREEN, title_size=8.6, body_size=7.0, align="left")
    c.box(32.0, 24.0, 29.0, 9.0, "Connector pool",
          "mysql-connector-python 26.7.0\nMySQLConnectionPool(pool_size=10) · explicit commit/rollback",
          fill=PALE_GREEN, edge=GREEN, title_size=8.6, body_size=7.0, align="left")
    c.box(4.0, 12.0, 26.0, 9.0, "Model + dataset",
          "models_yolo/active/best.pt (6.48 MB)\ndatasets/ 2.77 GB · 2,551 images",
          fill=PALE_GOLD, edge="#D97706", title_size=8.6, body_size=7.0, align="left")
    c.box(32.0, 12.0, 29.0, 9.0, "Local artefact directories",
          "generated_barcodes/ · generated_id_cards/\nreports/ · logs/app.log · assets/",
          fill=PALE_GOLD, edge="#D97706", title_size=8.6, body_size=7.0, align="left")

    c.box(68.0, 40.0, 29.0, 8.0, "Camera", "USB / integrated · /dev/video0\nOpenCV 5.0.0 · 1280×720 @ 30 fps",
          fill=PALE_BLUE, edge=BLUE, title_size=9.0, body_size=7.0)
    c.box(68.0, 28.5, 29.0, 8.0, "GPU (optional)", "NVIDIA RTX 2050 · CUDA 12.x\n17.2 ms / frame measured; CPU fallback 302 ms",
          fill=PALE_BLUE, edge=BLUE, title_size=9.0, body_size=7.0)
    c.box(68.0, 17.0, 29.0, 8.0, "Printer / PDF", "System print dialog · QPdfWriter\nReportLab A4 reports",
          fill=PALE_BLUE, edge=BLUE, title_size=9.0, body_size=7.0)

    c.arrow(64.2, 40.0, 67.8, 43.0, "frames", lw=1.4, label_size=6.6, label_offset=(0, 1.0))
    c.arrow(64.2, 32.0, 67.8, 32.5, "CUDA", lw=1.4, label_size=6.6, label_offset=(0, 1.0))
    c.arrow(64.2, 20.0, 67.8, 21.0, "print/export", lw=1.4, label_size=6.6, label_offset=(0, 1.0))

    c.text(2.0, 5.2,
           "Start-up:  python main.py   (README).  Database initialisation:  python scripts/initialize_database.py.  "
           "There is no service unit, container, web server or remote API in the repository.",
           size=7.0, color=MUTED)
    c.save(OUT / "WIT_Deployment_Diagram.png")


if __name__ == "__main__":
    system_context()
    architecture()
    flowchart()
    barcode_stages()
    yolo_architecture()
    training_pipeline()
    data_flow()
    erd()
    deployment()
    print("done")
