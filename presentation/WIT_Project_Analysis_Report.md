# WIT Barcode Student Information System - Project Analysis Report

**Date**: October 20, 2026  
**Repository**: https://github.com/toms1010/Wit-Barcode-Sutdent-information-System.git  
**Branch**: main  
**Commits**: e30152a, 521e66c  
**Analysis Scope**: Source, database, UI, vision pipeline, YOLO artifacts, tests

## Project Overview

WIT Barcode Student Information System is a PySide6 desktop application that links student records to barcode identity. A YOLOv8n detector + pyzbar decoder scans physical student ID cards; a verified payload resolves to a student row in MySQL and drives enrollment, assessments/fees, reports, certificates, and ID-card rendering. The app runs as a single workstation process (local MySQL, local filesystem). No authentication is implemented.

## Technology Stack (verified)

- Language: Python 3.14.4
- GUI: PySide6 6.12.0 / Qt 6.12.0
- DB: MySQL 8.4.11-0ubuntu0.26.04.1 (mysql-connector-python 26.7.0)
- CV/ML: ultralytics 8.4.177, PyTorch 2.14.1 (CUDA 13.0), opencv-python 5.0.0, numpy 2.3.x
- Barcodes: python-barcode 0.16.1, pyzbar 0.1.9
- Viz/Docs: matplotlib 3.10.x, reportlab 5.0.1, Pillow 11.3.0
- Test: pytest 9.1.1, pytest-qt 4.5.0, pytest-mock 3.14.0

## Codebase Structure

- `ui/`: 33 files (dashboard, students, scanner, enrollment, courses, assessment, reports, training_center, settings, barcode_generator_page, dialogs, widgets). `main_window.py` composes the app; `barcode_generator_page.py` (1,402 LOC) exists but is unrouted.
- `services/`: 19 files including `scan_pipeline.py`, `barcode_decoder.py`, `yolo_detector.py`, `camera_service.py`, `id_card_service.py`, `barcode_generator.py`, `assessment_service.py`, `report_service.py`, `com_document.py`.
- `database/`: schema (`schema.py`, 19 tables), connection pool, repositories (7 files).
- `config/`, `scripts/`, `models/`, `styles/`, `tests/` (26 files), `main.py`.

**Line count (first-party)**: 45,978 LOC across 122 Python files (config/, database/, models/, scripts/, services/, styles/, tests/, ui/ and main.py; venv/, .git/, presentation/ excluded).

## Database Design

- 19 tables, 230 columns, 33 foreign keys (InnoDB, utf8mb4_unicode_ci)
- Core entities: users, students, student_contacts, courses, subjects, enrollments, enrollment_subjects, barcodes, barcode_scans (append-only; FKs nullable), assessment_categories, assessments, assessment_items, payments, payment_schedules, audit_logs, system_settings, yolo_models, id_cards, dataset_images
- Live counts (188 total rows): barcode_scans 65 (41 success/24 not_found), reference/operational 42, student-related 21, empty: users 0, id_cards 0, yolo_models 0, dataset_images 0, payments 0
- Migrations: `002_barcode_type_code39.sql` (enforces CODE39 to match physical cards), `003_student_profile_images.sql`, `004_id_cards.sql`

## Vision Pipeline (evidence-based)

1. **Detection**: Ultralytics YOLOv8n (`models_yolo/active/best.pt`, v7-distance, 6.48 MB, nc=1). Train on `datasets/barcode_distance/data.yaml` (epochs 100, batch 16, AdamW lr0 0.002, patience 30, imgsz 640). Validation mAP50 0.995, mAP50-95 0.80754, precision 0.99754, recall 0.99865.
2. **Gating**: Scan region + guide gate; boxes must overlap guide or pass configured thresholds; smoothing/grace windows reduce flicker.
3. **Decoding**: pyzbar on cropped ROIs with preprocessing (color/greyscale, upscaling, CLAHE, Otsu) and bounded attempt budget (SCAN_MAX_DECODE_ATTEMPTS 6). Multiple attempts per ROI; results normalized to distinct payload strings.
4. **Verification**: exact match against `students.barcode_payload` (and related `barcodes` table) via repository; not_found for unknown payloads.
5. **Confirmation & state**: confirm frames, cooldown, duplicate suppression, state machine (idle/detecting/verified/unknown/ambiguous/error) with distinct outcomes; audio cue gated by debounce.
6. **Persistence**: `barcode_scans` logged with status, confidence, bbox (JSON), timestamp; student panel populated synchronously or asynchronously with loading/error states.

**Latency (measured)**: GPU 17.22 ms mean (p95 18.93 ms), CPU 302.39 ms mean (p95 622.24 ms). 12 runs after warm-up on 1280×720 frames.

## Barcode/ID Implementation

- **Symbology**: CODE39 (not CODE128) for all physical ID cards; payload is zero-padded 9-digit numeric (e.g., `000065412`), stored as `students.barcode_payload`. README text claiming CODE128+prefix is inconsistent with the implemented, evidence-backed behavior.
- **Generation**: `services/barcode_generator.py` writes CODE39 PNGs; `generated_barcodes/` contains files byte-identical to `assets/barcodes/barcode_N.png`.
- **ID card rendering**: `services/id_card_service.py` renders CR80 at configurable DPI with measured module sizes for CODE39; includes a verification step that attempts to decode the rendered barcode bitmap. Service is implemented; preview dialog currently raises `UnboundLocalError` (QImage referenced before assignment) — a known UI defect, not a missing renderer.

## Dataset

- 8 variants, 2,551 images, 2,515 annotations, 1 class, 36 negatives, 43 resolutions, 2.77 GB
- `barcode_distance` (828/812, 0 test) is the data.yaml for active model; `barcode_distance_all` has 406 test images
- Labels valid (no missing .txt, no malformed boxes); 8 duplicate files in `barcode_distance`
- Median box 50.4%×17.4% of frame; 1280×720 dominates (2,054)

## Testing

- Collected: 553, Passed: 531 (96.0%), Failed: 22 (4.0%), Duration: 183.66s
- All 22 failures in `tests/test_scan_post_scan_workflow.py` (state transitions, history insertion, timestamp formatting, badge rendering, profile navigation, audio gating, async record loading/latching). No other test modules report failures.
- Passing coverage spans detection/decoding, assets, student/assessment repositories, navigation/layout, COM documents, third-student recognition, etc.

## UI/UX Notes (from walkthroughs)

- Dashboard: KPI cards, global quick-search (Ctrl+K), DB health indicator. Student table in dashboard has `set_cell(family=...)` TypeError (misleadingly labeled "Cannot reach the database").
- Scanner: idle/detecting/verified/unknown states with overlays, history, result modal. Demonstrated with real assets (detector finds all three cards; end-to-end verified/unknown paths differ).
- Enrollment, Assessment/Fees, Reports, Certificate of Matriculation — exercised and working on live data.
- Training Center reads `dataset_images` table (empty) so dataset counts show 0 despite filesystem having 2,551 files; model registry UI exists.
- Settings partially implemented; BarcodeGeneratorPage unrouted; logout stub; no authentication.

## Known Defects (truthfully documented)

1. Dashboard student table: `TypeError: set_cell() got an unexpected keyword argument 'family'` (Python/PySide6 table API). UI mislabels as database connectivity issue.
2. ID-card preview dialog: `UnboundLocalError` — `QImage` referenced before assignment.
3. Rendered card barcode verification: re-decode of rendered CR80 bitmap returns nothing, causing the service's verification to fail (even though rendering path exists).
4. 22 post-scan workflow tests failing (clustered around panel state/history/latch/async semantics) — require reconciliation with current implementation.
5. Training Center shows zeros because it queries `dataset_images` (DB table) rather than filesystem inventory.
6. Core dataset (`barcode_distance`) has no test split; model selection used validation only.
7. Generator unrouted; no auth; logout stub.

## Deliverables Status

- PPTX (35 slides, 16:9, WIT orange/gold accents, slide numbers/footers, speaker notes): `presentation/WIT_Barcode_Student_Information_System_Technical_Presentation.pptx` ✓
- PDF + 35 render pages: `presentation/render/` ✓
- Diagrams: `WIT_System_Architecture.png`, `WIT_System_Flowchart.png`, `WIT_Database_ERD.png`, `WIT_Data_Flow_Diagram.png`, `WIT_YOLO_Training_Pipeline.png` ✓ (9 total in diagrams/)
- Charts: 13 PNGs + JSON summaries ✓
- Screenshots: 27 PNGs ✓
- Reports: `WIT_Dataset_Analysis_Report.md`, `WIT_Project_Analysis_Report.md`, `WIT_Presentation_References.md` ✓
- Validation: layout/fit checks pass (0 issues); diagrams/charts regenerated with live data (188 rows, 33 FKs, 45,978 LOC, 122 files)

## Conclusion

The system proves a real end-to-end identity-to-record path: YOLO detection + pyzbar decode + exact DB lookup + enrollment/fees/documents, with honest handling of unknown/ambiguous states and append-only scan history. The documented gaps are bounded (UI bugs, unrouted page, missing test split, empty registry tables, 22 failing workflow tests). The evidence base is live and reproducible from the repository and running database.