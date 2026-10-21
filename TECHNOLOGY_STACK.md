# Technology Stack & Model Plan

**Project:** WIT Barcode Student Information System
**Last verified:** 2026-10-20 — every version below was read from the project's own
virtual environment (`venv/bin/pip list`) and confirmed by live import, not from a
requirements wish-list. The GPU was confirmed with `nvidia-smi`.

This document is the single reference list of **all technologies the system uses** and
**the model that is trained**. The technical presentation
(`presentation/WIT_Barcode_Student_Information_System_Technical_Presentation_Revised.pptx`,
slides 07–08 and 15–16, 32–34) summarises the same content.

---

## 1. Technology list

### 1.1 Runtime and platform

| # | Technology | Version | Role in the system |
| --- | --- | --- | --- |
| 1 | Python | 3.14.4 | Application interpreter (single-process desktop app) |
| 2 | NVIDIA RTX 2050 (4 GB) | CUDA | Training and inference device; CPU fallback when CUDA is absent |
| 3 | Linux workstation | — | Target deployment machine |

### 1.2 User interface

| # | Technology | Version | Role in the system |
| --- | --- | --- | --- |
| 4 | PySide6 (Qt 6) | 6.12.0 | Desktop GUI, ten routed pages, camera preview, dialogs |

### 1.3 Database

| # | Technology | Version | Role in the system |
| --- | --- | --- | --- |
| 5 | MySQL server | 8.4.11 | Local InnoDB server, `wit_barcode_system` database, 19 tables |
| 6 | mysql-connector-python | 26.7.0 | Official driver; connection pool of 10; raw SQL, no ORM |

### 1.4 Computer vision and machine learning

| # | Technology | Version | Role in the system |
| --- | --- | --- | --- |
| 7 | Ultralytics | 8.4.177 | YOLOv8n training and inference framework |
| 8 | PyTorch | 2.14.1 | Tensor runtime (CUDA / CPU) |
| 9 | torchvision | 0.29.1 | PyTorch vision utilities (Ultralytics dependency) |
| 10 | OpenCV | 5.0.0 | Frame, crop and preprocessing operations |
| 11 | NumPy | 2.5.4 | Array operations throughout the pipeline |
| 12 | Pillow | 12.3.0 | Image loading and card rendering support |
| 13 | imagehash | 4.3.2 | Duplicate/perceptual-hash checks on dataset images |

### 1.5 Barcode symbology

| # | Technology | Version | Role in the system |
| --- | --- | --- | --- |
| 14 | python-barcode | 0.16.1 | CODE39 symbol generation for cards and reports |
| 15 | pyzbar (ZBar) | 0.1.9 | CODE39 payload decoding — the only decoder trusted for a read |

> The card symbology is **CODE39**, verified by decoding the checked-in card assets
> (payloads `000065412`, `000065902`, `000065736`).

### 1.6 Documents and reporting

| # | Technology | Version | Role in the system |
| --- | --- | --- | --- |
| 16 | ReportLab | 5.0.1 | A4 PDF generation: Certificate of Matriculation, assessment and barcode reports |
| 17 | matplotlib | 3.11.2 | Chart rendering for analytics reports |

### 1.7 Configuration and utilities

| # | Technology | Version | Role in the system |
| --- | --- | --- | --- |
| 18 | python-dotenv | 1.2.4 | `.env` configuration (DB credentials, YOLO path, camera) |

### 1.8 Testing

| # | Technology | Version | Role in the system |
| --- | --- | --- | --- |
| 19 | pytest | 9.1.1 | Test runner — 553 tests collected, 531 passing |
| 20 | pytest-qt | 4.5.0 | Qt widget testing without hardware |
| 21 | pytest-mock | 3.16.0 | Mocking camera, database and model boundaries |

**Deliberately absent:** no web framework, no HTTP client, no cloud service, no ORM.
The application is one local process that owns the camera, runs the network locally,
talks to a local MySQL server and writes documents to disk.

---

## 2. The model to train

### 2.1 What is trained

| Property | Value |
| --- | --- |
| Architecture | **YOLOv8n** (nano, 8th-generation YOLO, anchor-free, decoupled head) |
| Base weights | `yolov8n.pt` — COCO-pretrained |
| Task | Object detection, **single class: `barcode`** |
| Input size | 640 × 640 (letterboxed) |
| Checkpoint size | ≈ 6.48 MB (`models_yolo/active/best.pt`) |
| Runtime confidence | 0.75 (code default 0.50) |
| Device | CUDA:0 (RTX 2050); automatic CPU fallback |

### 2.2 Why YOLOv8n (nano)

- **One symbol, one class** — the detection task is deliberately narrow, so a large
  model would add latency without adding capability.
- **Live-camera budget** — measured inference (12 runs, warm, 1280×720 frame):
  GPU mean **22.49 ms** (p95 25.0 ms) vs CPU mean **218.13 ms** (p95 453.5 ms).
  At the 12 FPS preview target (83 ms/frame) only the GPU has headroom; the nano
  model is what keeps that possible.
- **6.48 MB loads instantly** from disk at application startup; no serving
  infrastructure needed.

### 2.3 Training recipe (recorded configuration)

Read from the promoted checkpoint's `train_args` and the run's `results.csv`:

| Setting | Value |
| --- | --- |
| Optimizer | AdamW, initial learning rate 0.002 |
| Epochs | 100 requested → 64 recorded (early stopping) |
| Batch size | 16 |
| Early-stopping patience | 30 epochs |
| Seed | 0 (reproducible) |
| Augmentation | `tame` profile on tiny datasets — mosaic, mixup, shear, perspective and flips disabled because they destroy barcode geometry; HSV colour jitter kept at a reduced strength |
| Dataset (deployed model) | `datasets/barcode_distance` — 414 train / 414 val images |
| Promoted checkpoint | **best epoch 34**, not the last epoch |

### 2.4 How the dataset is built

- Annotations are **derived from real pyzbar decodes** of each image — never
  hand-drawn — so every box marks a symbol a decoder actually read.
- Splits are assigned **per physical card**, so no card (and no derivative of a card)
  appears on both sides of the boundary.
- Augmented variants are verified before being kept: a variant must still decode, must
  decode to its source image's own value, and its box must contain the symbol.
- Inventory (filesystem scan, see `presentation/charts/dataset_statistics.json`):
  **2,551 images · 2,515 barcode boxes · 36 negative images · 8 dataset variants**.

### 2.5 Promotion gate — the model is only replaced on evidence

`scripts/train_yolo.py` never promotes on training metrics alone. A candidate must:

1. produce **zero false positives** on barcode-free documents;
2. decode at least as many **held-out real card assets** as the active model;
3. detect at least as many held-out assets, with equal or higher mean confidence.

The active model is backed up before any replacement. A missing baseline **blocks**
promotion rather than permitting it.

### 2.6 How to train

```bash
source venv/bin/activate

# rebuild the dataset, validate it, train, evaluate — do not promote
python -m scripts.train_yolo --build-dataset --epochs 60

# promote the candidate only if it passes the gate
python -m scripts.train_yolo --epochs 60 --promote

# validate and report without training
python -m scripts.train_yolo --dry-run

# re-score an existing candidate against the gate
python -m scripts.train_yolo --evaluate-only models_yolo/candidates/<run>/weights/best.pt
```

The same trainer is reachable from the application's **Training Center** page.

### 2.7 The model is one stage of a three-stage pipeline

A detection is a rectangle, not an identity:

```text
YOLOv8n detect → ROI gate → crop → pyzbar decode → exact SQL lookup → student
```

Only a payload that matches a row in the `students` table is ever presented as a
student; two distinct payloads in one read are refused as `AMBIGUOUS`.

### 2.8 How training works in this project, end to end

Training is not a one-click "teach the camera" step. It is a five-stage pipeline
in which every stage is a separate script, and the model that ships is chosen by
measurement, not by whichever run finished last.

```text
assets/ (real card photos)
   │  ① build   scripts/build_dataset.py      — copy bytes, derive boxes from real pyzbar decodes
   ▼
datasets/<name>/  images/{train,val,test} + labels/*.txt + data.yaml
   │  ② validate scripts/train_yolo.py :: validate_dataset — one class "barcode", labels well-formed
   ▼
Ultralytics YOLO().train(...)                 — ③ fit on the RTX 2050 (CUDA)
   │      candidates/<run>/weights/best.pt
   ▼
scripts/train_yolo.py :: evaluate + should_promote   — ④ score on held-out cards & negatives
   │      pass → backup, then copy over models_yolo/active/best.pt
   ▼
runtime: YOLO detect → ROI gate → crop → pyzbar decode → SQL lookup
```

**① Dataset build — where the training images come from.**
`scripts/build_dataset.py` (plus its siblings `build_distance_dataset.py`,
`build_detection_dataset.py`, `prepare_dataset.py`, `augment_dataset.py`)
assembles the dataset from `config/student_assets.py`, the registry of real
card photos and barcode-free panels. Two rules are enforced in code, not just
documented:

- **Nothing is generated or downloaded.** Every pixel is a byte-for-byte copy of
  an existing asset; no external dataset is read, no synthetic barcode is drawn.
- **Every bounding box comes from a real pyzbar decode of that exact image.**
  Labels are never hand-drawn. An image that does not decode is never labelled
  positive, and a "negative" panel must decode *nothing*.

Splits are assigned **per physical card**, so card A trains, card B validates,
card C is test-only — a card can never appear on both sides of the boundary.
Augmented variants are kept only if they still decode, decode to their source's
own value, and still contain the symbol. `scripts/validate_dataset.py` /
`validate_dataset()` re-check the result (single class `0 = barcode`, every
image has a label file, every image is readable) before any GPU time is spent.

**②/③ Training — the actual "teaching" of the image model.**
The one training call in the project is `YOLO(...).train(...)` from
**Ultralytics 8.4.177**, running on **PyTorch 2.14.1** with **CUDA** on the
RTX 2050 (CPU fallback if CUDA is absent). It is a **fine-tune**, not training
from scratch: the run starts from the COCO-pretrained `yolov8n.pt` and updates it
on the barcode dataset (640 px, batch 16, AdamW lr 0.002, seed 0, early-stopping
patience). Augmentation is supplied as an explicit `tame` profile — mosaic,
mixup, shear, perspective, flips and erasing all set to 0 because they destroy a
barcode's bar geometry; only reduced HSV colour jitter is kept. Writes are done
by Ultralytics to `models_yolo/candidates/<run>/` (`results.csv`, `weights/best.pt`).

**④ Evaluation and promotion — evidence before deployment.**
`evaluate()` scores both the candidate and the currently active model with the
same instrument: held-out real cards (val + test splits), barcode-free negative
panels, decode-from-crop and decode-from-scan-region counts, mean confidence,
and a full `detect → gate → decode → SQL` run over all three cards
(`pipeline_verification`). `should_promote()` then compares candidate against
active and refuses the swap if the candidate fires on a barcode-free document,
decodes fewer held-out cards, detects fewer, or has lower mean confidence. The
active model is backed up first, and a missing baseline **blocks** promotion.
`--promote` must be passed explicitly; nothing is ever promoted automatically.

**⑤ Serving.** Only `models_yolo/active/best.pt` is ever loaded by the scanner
(`services/yolo_detector.py`); candidates are inert files until the gate passes.

**Two entry points to the same trainer:**

| Entry point | What it is |
| --- | --- |
| `python -m scripts.train_yolo …` | CLI: `--build-dataset`, `--epochs`, `--promote`, `--dry-run`, `--evaluate-only` (see §2.6) |
| **Training Center** page (`ui/training_center.py`) | Qt GUI: a `TrainingWorker(QThread)` runs the same Ultralytics `YOLO("yolov8n.pt").train(...)` without freezing the UI; runs and their statuses (training / candidate / active) are recorded in MySQL through `YOLOModelRepository` |

### 2.9 Which technology trains what — role map

| Stage | Technology | What it does there |
| --- | --- | --- |
| Reading training images | OpenCV 5.0 (`cv2.imread`), NumPy 2.5.4, Pillow 12.3.0 | Load, crop, letterbox frames into arrays |
| Producing ground-truth boxes | **pyzbar / ZBar 0.1.9** (via `BarcodeDecoderService`) | The decoder itself draws the annotation rectangles — labels are machine-derived, never hand-drawn |
| Hand-written augmentation | OpenCV + NumPy (`scripts/augment_dataset.py`) | Blur, brightness, rotation, resize and similar geometry-preserving variants, each verified by re-decoding |
| Duplicate detection | imagehash 4.3.2 | Perceptual-hash check so near-identical images do not leak across splits |
| **Model fitting** | **Ultralytics 8.4.177 (YOLOv8n)** on **PyTorch 2.14.1 / CUDA** | The only thing that actually optimises weights — forward/backward passes, AdamW, early stopping |
| Hardware for training | NVIDIA RTX 2050 (4 GB), CUDA | 22.49 ms mean inference vs 218.13 ms on CPU |
| Metric logging | Ultralytics `results.csv`, matplotlib 3.11.2 | Per-epoch losses/mAP; charts in `presentation/charts/chart_training_*.png` |
| Barcode *generation* (symbols to scan) | python-barcode 0.16.1 (`scripts/generate_barcodes.py`) | Renders CODE39 images used as assets/reference — **not** used to train; the detector learns from real card photos |
| Evaluation / gate | pyzbar + Ultralytics + `services/*` | Decode-rate and false-positive scoring used by `should_promote()` |
| Orchestrating training | PySide6 `QThread` (`ui/training_center.py`) | Runs training off the GUI thread |
| Tracking runs | MySQL 8.4 via `YOLOModelRepository` | Stores each run's status: training / candidate / active |
| Testing the training code | pytest 9.1.1 | Dataset, gate and pipeline logic are unit-tested |

> **Note:** barcode *detection* (finding the symbol in a camera frame) is ML and
> is trained as described above. Barcode *decoding* (turning the pixels into
> `000065412`) is **not** trained at all — it is deterministic ZBar/pyzbar
> image-processing. Likewise CODE39 *generation* is deterministic
> python-barcode. Only the detector has weights.

---

## 3. Where to verify every claim

| Claim | Source |
| --- | --- |
| Library versions | `venv/bin/pip list`, live imports |
| GPU / latency | `nvidia-smi`, `presentation/charts/inference_latency.json` |
| Training configuration | `models_yolo/candidates/…v7…/results.csv`, checkpoint `train_args` |
| Dataset inventory | `presentation/charts/dataset_statistics.json`, `dataset/DATASET_REPORT.md` |
| Promotion gate | `scripts/train_yolo.py` (`should_promote`) |
| Dataset build rules | `scripts/build_dataset.py`, `config/student_assets.py` |
| Training entry points | `scripts/train_yolo.py` (`main`), `ui/training_center.py` (`TrainingWorker`) |
| Presentation slides | `presentation/tools/build_pptx_revised.py` |
