# WIT Barcode Student Information System — Quick Reference

**Version:** 1.0.0 | **Platform:** Ubuntu 26.04 · Python 3.14 · PySide6 6.12 · MySQL 8.4 · YOLOv8n

---

## What It Does

Desktop application for Western Institute of Technology that:
1. **Manages students** — register, edit, search, export
2. **Handles enrolment** — subjects, schedules, rooms, fees
3. **Generates Certificates of Matriculation** — PDF + print
4. **Tracks assessments & payments** — charges, discounts, instalments, balances
5. **Scans barcode ID cards** — YOLOv8n detection → CODE39 decode → exact DB match
6. **Trains the detector** — dataset import → training → promotion gate → model registry

---

## Screenshots (23)

| # | Page | File |
|---|------|------|
| 1 | Dashboard — overview & student list | `screenshots/01_dashboard.png` |
| 2 | Dashboard — student detail panel | `screenshots/02_dashboard_student_selected.png` |
| 3 | Students directory | `screenshots/03_students_directory.png` |
| 4 | Students — row selected | `screenshots/04_students_record_selected.png` |
| 5 | Add Student dialog | `screenshots/05_student_dialog_add.png` |
| 6 | Edit Student dialog | `screenshots/06_student_dialog_edit.png` |
| 7 | Enrolment — student picker | `screenshots/07_enrollment_select_student.png` |
| 8 | Enrolment — subjects enrolled | `screenshots/08_enrollment_subjects.png` |
| 9 | Add Subject dialog | `screenshots/09_add_subject_dialog.png` |
| 10 | Courses & Subjects catalogue | `screenshots/10_courses_subjects.png` |
| 11 | Certificate of Matriculation | `screenshots/11_certificate_of_matriculation.png` |
| 12 | Assessment & Fees | `screenshots/12_assessment_fees.png` |
| 13 | Record Payment dialog | `screenshots/13_record_payment_dialog.png` |
| 14 | Reports & Analytics | `screenshots/14_reports_analytics.png` |
| 15 | Enrollment Summary dialog | `screenshots/15_enrollment_summary_dialog.png` |
| 16 | Scanner — idle | `screenshots/16_scanner_idle.png` |
| 17 | Scanner — card verified | `screenshots/17_scanner_verified.png` |
| 18 | Scan Result panel | `screenshots/18_scan_result_dialog.png` |
| 19 | Scanner — live camera | `screenshots/19_scanner_camera_running.png` |
| 20 | Training Center — Dataset | `screenshots/20_training_center_dataset.png` |
| 21 | Training Center — Training | `screenshots/21_training_center_training.png` |
| 22 | Training Center — Models | `screenshots/22_training_center_models.png` |
| 23 | Settings | `screenshots/23_settings.png` |

---

## Key Screens at a Glance

### Dashboard
![Dashboard](screenshots/01_dashboard.png)
*Aggregate counts + searchable student table*

### Scanner — Verified Card
![Scanner Verified](screenshots/17_scanner_verified.png)
*Real card `Id_1.png` → payload `000065412` → Javy Danielle N. Caporal (WIT-2024-001)*

### Certificate of Matriculation
![Certificate](screenshots/11_certificate_of_matriculation.png)
*Student profile, enrolled courses, assessment breakdown, payment schedule, **₱489.51 discrepancy notice***

### Assessment & Fees
![Assessment](screenshots/12_assessment_fees.png)
*Tuition ₱13,340 · Misc ₱8,674.72 · Lab ₱5,618.59 · 4 instalments (all overdue)*

### Training Center
![Training](screenshots/21_training_center_training.png)
*640px, batch 16, AdamW lr 0.002, CUDA device 0*

---

## Run It

```bash
# 1. Activate venv
source venv/bin/activate

# 2. Start MySQL (if not running)
sudo systemctl start mysql

# 3. Run the app
python -m ui.main_window
```

**Default DB:** `wit_barcode_system` / user `wit_user` (configured in `.env`)

---

## Scanner Quick Test

1. Click **Barcode Scanner** in sidebar
2. Click **Upload Card Image**
3. Select `assets/images/Id_1.png`
4. Watch: YOLO detects → crops → decodes → matches DB → shows **Scan Result** panel

---

## Known Issues

| # | Issue | Location |
|---|-------|----------|
| 1 | Barcode Generator page crashes (`_on_item_changed` missing) | `ui/barcode_generator_page.py:536` |
| 2 | Dashboard shows "Oktubre" (Filipino month) | `ui/dashboard.py:847,858` |
| 3 | Installment total ₱28,122.82 ≠ Assessed ₱27,633.31 (gap ₱489.51) | All 3 students |
| 4 | No authentication / logout not implemented | `users` table empty |
| 5 | Model registry empty despite working `best.pt` on disk | `yolo_models` table |

---

## Full Documentation

See `Project_Documentation.docx` / `.md` / `.pdf` for:
- Complete IMRaD academic documentation
- 8 system diagrams (context, IPO, architecture, pipeline, ERD, use case, data flow, YOLO training)
- All 31 figures with description/functionality/process/output/contribution/objective
- 27 tables, APA references, QA checklist, defect log