# WIT Barcode Student Information System - Presentation References

**Date**: October 20, 2026  
**Purpose**: Citations/traceable references for claims made in the technical presentation

## Runtime & Framework

- Python 3.14.4 — OS environment
- PySide6 6.12.0, Qt 6.12.0 — GUI framework
- mysql-connector-python 26.7.0 — MySQL connectivity
- ultralytics 8.4.177, PyTorch 2.14.1 (CUDA 13.0) — YOLOv8n training/inference
- opencv-python 5.0.0 — image processing
- pyzbar 0.1.9, python-barcode 0.16.1 — barcode decode/generate
- reportlab 5.0.1 — PDF generation
- matplotlib 3.10.x, numpy 2.3.x, Pillow 11.3.0 — charts/visualization
- pytest 9.1.1, pytest-qt 4.5.0, pytest-mock 3.14.0 — testing

## Model Artifacts (verified)

- Active checkpoint: `models_yolo/active/best.pt` (v7-distance)
  - Size: 6,484,970 bytes (6.48 MB)
  - Epoch: best at 34/64 (recorded), nc=1, names={0: 'barcode'}, imgsz=640
  - Train args: epochs=100, batch=16, optimizer=AdamW, lr0=0.002, patience=30, device=0, data=`datasets/barcode_distance/data.yaml`
  - Validation (from checkpoint): precision 0.99754, recall 0.99865, mAP50 0.995, mAP50-95 0.80754, fitness 0.80754
- Candidate results: `models_yolo/candidates/*/results.csv`, `args.yaml` (6 runs: strict, v3, v4-tame, v5-aug, v6-full, v7-distance)
- Training metrics summary (measured): `presentation/charts/training_metrics.json`

## Inference Latency (measured)

- GPU (RTX 2050, CUDA): 12 runs after warm-up — mean 17.22 ms, median 17.63 ms, p95 18.93 ms, samples 15.26–19.70 ms
- CPU: 12 runs — mean 302.39 ms, median 233.40 ms, p95 622.24 ms, samples 172.68–991.65 ms
- Source: `presentation/charts/inference_latency.json`, `scripts/benchmark_devices.py` (referenced)

## Database (live)

- MySQL 8.4.11-0ubuntu0.26.04.1; InnoDB; utf8mb4_unicode_ci
- Tables: 19; Columns: 230; Foreign keys: 33 (live constraint count); Total rows: 188 (all 19 tables)
- Key row counts: barcode_scans 65 (success 41, not_found 24; dates 2026-10-15,17,18,19), students 3, enrollments 3, assessment_items 33, enrollment_subjects 21, reference tables include 20 assessment_categories, 12 payment_schedules, 11 system_settings
- Schema DDL: `database/schema.py`; Migrations: `database/migrations/*.sql`

## Dataset

- Measured inventory: `presentation/charts/dataset_statistics.json` (2,551 images, 2,515 boxes, 2.77 GB, 43 resolutions, 8 duplicates, 36 negatives)
- Gallery: `presentation/screenshots/24_dataset_gallery.png`

## Tests & Evidence

- Pytest: 531 passed, 22 failed, 553 collected, 183.66s (full log `presentation/tools/pytest_output.txt`)
- Failures concentrated in `tests/test_scan_post_scan_workflow.py` (22/22)
- Screenshots: `presentation/screenshots/*.png` (27 files, including 09_enrollment, 11_assessment_fees recaptured)
- Charts: `presentation/charts/*.png` (13 images), JSON summaries
- Diagrams: `presentation/diagrams/*.png` (9 images)
- Layout checks: PDF bbox via pdftotext (no line/word overlap issues reported), PPTX text-fit estimator passed (0 issues)

## Code References (key evidence)

- Scan pipeline, gating, confirmation: `services/scan_pipeline.py`, `config/settings.py` (SCAN_* constants)
- Barcode decode (pyzbar + preprocessing, attempts): `services/barcode_decoder.py`
- Detection (YOLO): `services/yolo_detector.py`, `services/camera_service.py`
- Barcode generation/ID card rules (CODE39, quiet zone, DPI): `services/barcode_generator.py`, `services/id_card_service.py`
- Schema and live state: `database/schema.py`, `database/connection.py`
- UI state: `ui/dashboard.py`, `ui/scanner_page.py`, `ui/main_window.py`, `ui/barcode_generator_page.py`

## Notes on Discrepancies (documented truthfully)

- README states CODE128 + `WIT:STUDENT:` prefix; implementation uses CODE39 with zero-padded 9-digit payload (matches physical cards and `students.barcode_payload`).
- Dashboard reports "Cannot reach the database" on a Python TypeError (`set_cell()` got unexpected keyword argument 'family') — misleading error contract (documented in slide 34).
- ID-card preview raises `UnboundLocalError` (QImage referenced before assignment); ID card rendering service exists and produces valid CR80 images, but the preview dialog is broken.
- Rendered card barcode re-decode verification fails for the rendered bitmap (documented defect).
- BarcodeGeneratorPage is fully implemented but unrouted (no sidebar entry); logout is a stub; no authentication.
- Training Center reads `dataset_images` (empty) while 2,551 files exist on disk.

## Presentation Artifacts

- PPTX: `presentation/WIT_Barcode_Student_Information_System_Technical_Presentation.pptx` (35 slides, 16:9)
- PDF: `presentation/render/WIT_Barcode_Student_Information_System_Technical_Presentation.pdf`
- Diagrams: `presentation/WIT_System_Architecture.png`, `WIT_System_Flowchart.png`, `WIT_Database_ERD.png`, `WIT_Data_Flow_Diagram.png`, `WIT_YOLO_Training_Pipeline.png`
- Reports: `WIT_Project_Analysis_Report.md`, `WIT_Dataset_Analysis_Report.md`, `WIT_Presentation_References.md`
- Render pages: `presentation/render/page-01.png` through `page-35.png`