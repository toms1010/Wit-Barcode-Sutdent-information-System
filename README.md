# WIT Barcode Student Information System

A complete Barcode Student Information and Subject Enrollment System for Western Institute of Technology (WIT).

## Features

- **Student Management**: Register, edit, view, and search student records
- **Barcode Generation**: Generate CODE39 barcodes for students
- **Barcode Scanning**: Webcam-based barcode scanning with YOLO detection
- **Subject Enrollment**: Manage student enrollments and subjects
- **Certificate of Matriculation**: Generate printable certificates
- **Assessment & Fees**: Track tuition, fees, discounts, payments, and balances
- **Reports & Analytics**: Generate PDF reports
- **Training Center**: Manage YOLO dataset and model training
- **Modern UI**: Clean, professional light theme with WIT branding

## Technology Stack

The complete, version-verified list of every technology the system uses — plus the
model that is trained and its full training recipe — is maintained in
[`TECHNOLOGY_STACK.md`](TECHNOLOGY_STACK.md). Summary:

- **Runtime**: Python 3.14.4 on a Linux workstation with an NVIDIA RTX 2050 (CUDA)
- **GUI**: PySide6 6.12 (Qt 6) — desktop widgets app, no web framework
- **Database**: MySQL 8.4 via mysql-connector-python (raw SQL, no ORM)
- **Computer Vision / ML**: Ultralytics 8.4 (YOLOv8n), PyTorch 2.14, OpenCV 5.0
- **Barcode**: python-barcode 0.16 (CODE39 generation), pyzbar 0.1.9 (ZBar decoding)
- **Documents**: ReportLab 5.0 (A4 PDF), matplotlib (charts)
- **Configuration**: python-dotenv
- **Testing**: pytest, pytest-qt, pytest-mock

## Model to Train

A **YOLOv8n (nano)** detector, fine-tuned from COCO-pretrained `yolov8n.pt` for a
single class — `barcode`:

- **Why nano**: one narrow class; 6.48 MB checkpoint; measured inference 22.49 ms
  mean on the RTX 2050 (CPU fallback 218 ms) — the only configuration with headroom
  for a 12 FPS live camera preview.
- **Recipe**: 640 px input, batch 16, AdamW (lr0 0.002), 100 epochs requested with
  patience 30, seed 0, `tame` augmentation profile on small datasets.
- **Dataset**: `datasets/barcode_distance` (414 train / 414 val); annotations derived
  from real pyzbar decodes; splits assigned per physical card to prevent leakage.
- **Promotion**: a candidate only replaces `models_yolo/active/best.pt` if it passes
  the evidence gate in `scripts/train_yolo.py` (zero false positives on barcode-free
  documents, held-out decode rate ≥ the active model). The promoted checkpoint is
  best-epoch 34 of the v7-distance run, not the last epoch.

Full details, commands and verification sources:
[`TECHNOLOGY_STACK.md`](TECHNOLOGY_STACK.md) §2.

## Installation

### Prerequisites

- Python 3.11+
- MySQL 8+ server
- ZBar library (for pyzbar)

#### Ubuntu/Debian
```bash
sudo apt-get install mysql-server zbar-tools libzbar0
```

#### macOS
```bash
brew install mysql zbar
```

#### Windows
- Install MySQL from mysql.com
- Install ZBar from https://sourceforge.net/projects/zbar/

### Setup

1. Clone the repository:
```bash
cd "WIT Barcode Student Information System"
```

2. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate  # Windows
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure database:
```bash
cp .env.example .env
# Edit .env with your database credentials
```

5. Initialize database:
```bash
python scripts/initialize_database.py
```

6. Run the application:
```bash
python main.py
```

## Project Structure

```
wit_barcode_system/
├── main.py                 # Entry point
├── requirements.txt        # Python dependencies
├── .env.example           # Environment configuration template
├── config/                # Configuration modules
├── database/              # Database connection, schema, repositories
├── models/                # Data models
├── services/              # Business logic services
├── ui/                    # User interface
│   ├── main_window.py     # Main application window
│   ├── dashboard.py       # Dashboard page
│   ├── students_page.py   # Student management
│   ├── scanner_page.py    # Barcode scanner
│   ├── barcode_generator_page.py
│   ├── enrollment_page.py # Subject enrollment
│   ├── assessment_page.py # Fees & payments
│   ├── reports_page.py    # Reports
│   ├── training_center.py # YOLO training
│   └── settings_page.py   # Settings
├── styles/                # Theming (colors, typography, QSS)
├── assets/                # Images, fonts, icons
├── dataset/               # YOLO dataset
├── models_yolo/           # YOLO models
├── generated_barcodes/    # Generated barcode images
├── reports/               # Generated PDF reports
└── scripts/               # Utility scripts
```

## Configuration

Edit `.env` file for configuration:

```env
# Database
DB_HOST=localhost
DB_PORT=3306
DB_NAME=wit_barcode_system
DB_USER=root
DB_PASSWORD=your_password

# YOLO
YOLO_MODEL_PATH=models_yolo/active/best.pt
YOLO_CONFIDENCE_THRESHOLD=0.5
YOLO_DEVICE=cpu

# Camera
DEFAULT_CAMERA_INDEX=0
CAMERA_WIDTH=1280
CAMERA_HEIGHT=720

# Barcode
BARCODE_PREFIX=WIT:STUDENT:
BARCODE_OUTPUT_DIR=generated_barcodes
```

## Fonts

Place font files in:
- `assets/fonts/open-sauce/` - Open Sauce font family
- `assets/fonts/agrandir/` - Agrandir font family

The application will use system fonts as fallback if custom fonts are not available.

## YOLO Dataset

Prepare your dataset in `dataset/`:
```
dataset/
├── data.yaml
├── images/
│   ├── train/
│   ├── val/
│   └── test/
└── labels/
    ├── train/
    ├── val/
    └── test/
```

Run validation:
```bash
python scripts/validate_dataset.py
```

Train model:
```bash
python scripts/train_yolo.py
```

## Usage

### Adding Students
1. Navigate to **Students** page
2. Click **Add Student**
3. Fill in details and save

### Generating Barcodes
1. Go to **Barcode Generator**
2. Select students (or click **Generate All Missing**)
3. Barcodes are saved to `generated_barcodes/`

### Scanning Barcodes
1. Go to **Barcode Scanner**
2. Select camera and click **Start Scanner**
3. Point barcode at camera
4. On a verified scan the student's portrait, reference, program and year level
   appear, a history row is appended to Scan History, a success tone plays, and
   the structured result modal opens with the COM and Financial tabs.

A barcode is only reported as a success once it has been decoded **and** the
student row has been confirmed in the database. A card held in frame produces one
scan: it must leave the frame, or **Scan Next Student** must start a new session,
before it can be recorded again.

Student portraits live in `assets/profile/`. The identifier-to-portrait mapping,
how it was verified, and the missing-asset behaviour are documented in
[`reports/PROFILE_IMAGE_MAPPING.md`](reports/PROFILE_IMAGE_MAPPING.md).
Outstanding data problems (the printed reference on `student_2_info.png`, the
third student's missing information panel, and the PHP 489.51 gap between the
printed assessment and the installment schedule) are listed in
[`reports/ADMIN_ACTIONS_REQUIRED.md`](reports/ADMIN_ACTIONS_REQUIRED.md).

### Students on file

| Barcode payload | Card | Reference | Student | Portrait |
|---|---|---|---|---|
| `000065412` | `Id_1.png` / `barcode_2.png` | `65412` | Javy Danielle N. Caporal | `javy_profile.png` |
| `000065902` | `Id_2.png` / `barcode_1.png` | `65902` | Ronna Mae D. Alemania | `ronna_profile.png` |
| `000065736` | `Id_3.png` / `barcode_3.png` | `65736` | Steven Anthony M. Oquiana | `Steven_profile.png` |

The payloads were decoded from the assets; the `barcode_N.png` filenames do
**not** line up with the `Id_N.png` cards, so nothing in the application keys on
a filename. In fact `assets/barcodes/barcode_N.png` are the *generated* symbols,
byte-identical to the matching files in `generated_barcodes/`.

`Id_3.png` is a smaller card than the other two (270x459, with a 204 px-wide
symbol -- about 1.85 px per narrow CODE39 element), which is why the decoder
interpolates undersampled crops before it filters them; see
[`reports/STUDENT_3_INTEGRATION.md`](reports/STUDENT_3_INTEGRATION.md) and, for
the measurements behind the current settings,
[`reports/CARD_C_RECOGNITION_REPORT.md`](reports/CARD_C_RECOGNITION_REPORT.md).

A frame that yields **two different payloads** in one read is refused
(`ScanState.AMBIGUOUS`) rather than resolved: both values map to real students,
so choosing one would be a coin toss between two people. The operator is asked to
present a single card.

### Managing Enrollment
1. Go to **Subject Enrollment**
2. Select a student
3. Create enrollment and add subjects

### Assessments & Payments
1. Go to **Assessment & Fees**
2. Select a student
3. Create assessment and record payments

## Development

### Running Tests
```bash
pytest tests/
```

### Code Style
- Follow PEP 8
- Use type hints
- Document public APIs

## License

Proprietary - Western Institute of Technology

## Support

For issues and feature requests, contact the WIT IT Department.# Wit-Barcode-Sutdent-information-System
