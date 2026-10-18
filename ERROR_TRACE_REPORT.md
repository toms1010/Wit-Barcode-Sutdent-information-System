# ERROR_TRACE_REPORT.md

**Project:** WIT Barcode Student Information System
**Session date:** 2026-10-15
**Scope:** inspection of the existing project and the six permitted assets,
recreation of the YOLO dataset from those assets only, retraining and evaluation
of the detector, hardening of the barcode decoder, and end-to-end testing of the
scan -> decode -> student-lookup -> dashboard path.

> This report **supersedes** the audit written at 2026-10-15 01:12. Several of
> that report's claims can no longer be verified because the model weights it
> described were removed from the working tree during this session (see
> **§4.4 Loss of the previously active model**). Nothing from that report is
> restated here as verified fact unless it was independently re-measured today.

Everything below is labelled **VERIFIED** (a command in this project produced it),
**FAIL**, **NOT TESTED**, or **UNRESOLVED**. No metric, decode or test result in
this document is estimated or reconstructed.

---

## 1. Summary

**VERIFIED** — The barcode pipeline works end to end on the real assets: YOLO
locates the symbol, the crop is decoded by zbar, the payload is looked up in
MySQL, and the correct student is displayed.

| Stage | Result |
|---|---|
| `barcode_1.png` direct decode | **PASS** - CODE39 `000065902` |
| `barcode_2.png` direct decode | **PASS** - CODE39 `000065412` |
| Barcode visible in `Id_1.png` | **PASS** - CODE39 `000065412` |
| Barcode visible in `Id_2.png` | **PASS** - CODE39 `000065902` |
| Barcode-free panels correctly left unlabelled | **PASS** |
| Dataset build from assets only, no synthesis | **PASS** |
| YOLO retrain, 60 epochs completed | **PASS** (completed, but see §5.3) |
| Detection -> crop-decode on all 4 barcode images | **PASS** |
| Verified lookup of `000065412` -> student | **PASS** - Javy Danielle N. Caporal |
| Unknown-barcode handling (`000065902`) | **PASS** - reports UNKNOWN, shows no student |
| Full pytest suite | **145 passed, 1 failed** |
| Scanner verification script | **17 PASS, 2 FAIL** |

**The two failures are real and are not papered over:**

1. The promoted model fires a **0.571-confidence false detection** on
   `student_1_info.png`, a document with no barcode. The safety-critical
   property still holds - it produces **no student** - but the box is wrong.
   Details and the recommended fix: **§7.1**.
2. Payload `000065902` has **no student record**, so the second card cannot be
   resolved to a student. This is an **unresolved data question, not a code
   bug**, and it is **not** something to guess around: **§4.3**.

**Headline limitation, stated up front:** this project contains **two**
independent physical barcodes and **two** barcode-free documents. Every number
below rests on those four files. That is enough to prove the pipeline works and
enough to catch a false-positive regression. It is **not** enough to claim the
model generalises to unseen students, other card designs, other lighting, angles,
distances, or a real webcam feed. No such claim is made anywhere in this report.

---

## 2. Environment

**VERIFIED** by direct interrogation of the interpreter.

| Item | Value |
|---|---|
| OS | Linux 7.0.0-38-generic x86_64 (Kubuntu) |
| Python | 3.14.4 (`venv/bin/python`) |
| Virtualenv | `venv/` reused throughout; **no system-wide installs were performed** |
| Ultralytics | 8.4.177 (framework confirmed: **Ultralytics YOLOv8**, not a custom one) |
| torch / torchvision | 2.14.1+cu130 / 0.29.1+cu130 |
| OpenCV | 5.0.0.93 |
| numpy | 2.5.4 |
| Decoder | pyzbar 0.1.9 against system `libzbar.so.0` |
| python-barcode | 0.16.1 (generation only; unused in this work) |
| PySide6 | 6.12.0 |
| mysql-connector-python | 26.7.0 |
| pytest | 9.1.1 (+ pytest-qt 4.5.0, pytest-mock 3.16.0) |
| Database | MySQL 8, schema `wit_barcode_system`, service `active`, `mysqld` pid 1918 |
| CPU | 12 cores; `torch.get_num_threads() = 8` |
| GPU | CUDA **available**, 1 device, **but `YOLO_DEVICE=cpu`** so it is unused |
| Cameras | `/dev/video0`, `/dev/video1` present |
| Disk | 222 G used / 438 G free |

Existing model as installed today: `models_yolo/active/best.pt`, YOLOv8n,
3 138 003 parameters, `names = {0: 'barcode'}`, MD5
`b4f807cf29eb0250a53f5e0b34d3bbc5`.

**Pretrained weights availability.** `yolov8n.pt` (COCO, MD5
`95a2449609c73cd69a072b09daaff0cc`) is **already present in the project root**,
so training started from it with **no download**. Network egress was confirmed
reachable (HTTP 200 to github.com/ultralytics/assets) but was **not used** for
weights or data.

---

## 3. Project inspection

Existing components that were reused rather than rewritten:

| Concern | Module |
|---|---|
| Model loading + inference | `services/yolo_detector.py` |
| Barcode decoding | `services/barcode_decoder.py` (pyzbar) |
| Scan state machine, region gate, verification | `services/scan_pipeline.py` |
| Scan-region geometry | `services/scan_geometry.py` |
| Overlay compositing | `services/scanner_overlay.py` |
| Student lookup | `services/student_service.py` -> `database/repositories/student_repository.py` |
| Scanner UI | `ui/scanner_page.py`, `ui/dialogs/scan_result_dialog.py` |
| Training driver | `scripts/train_yolo.py` |
| Config | `config/settings.py` + `.env` |

Lookup path: `get_student_by_barcode` -> `get_by_barcode_payload` ->
`SELECT * FROM students WHERE barcode_payload = %s` (exact match). The scanner
popup is opened only when `outcome.state == ScanState.VERIFIED and
outcome.verified_now` (`ui/scanner_page.py:504`), so a detection alone can never
display a student. That design was kept.

---

## 4. Asset inspection and decoding

### 4.1 The six assets - VERIFIED

| File | Dimensions | MD5 (before == after) |
|---|---|---|
| `assets/images/Id_1.png` | 590x1004 | `15de778f2c61c9924b0ed0432d829853` |
| `assets/images/Id_2.png` | 590x1004 | `38e37019c04f9741e2e064e8262cc1e8` |
| `assets/barcodes/barcode_1.png` | 562x202 | `9adeb2f3248ca80744232465139e5bd9` |
| `assets/barcodes/barcode_2.png` | 608x187 | `54f238f873fa4c697c4f2206b08717da` |
| `assets/images/info/student_1_info.png` | 1414x2000 | `6f6160d4e5c72fbbdd18d34e7722823a` |
| `assets/images/info/student_2_info.png` | 1414x2000 | `57c4262c9838999937d71d8e3ce2b834` |

Digests were captured before any change and re-checked after dataset build,
training, promotion and the full test run: **identical**. No asset was modified,
renamed, moved by this work, or deleted.

The two info panels were relocated into `assets/images/info/` by something other
than this work, partway through the session. They arrived **byte-identical**
(digests unchanged) and all code now resolves them through
`settings.info_image(...)`, which accepts either layout.

Each image was additionally inspected visually, not only by decode.

### 4.2 Decoding results - VERIFIED

Both reference barcodes were decoded **directly, with no YOLO and no
preprocessing ensemble** - they are tight crops, so running a detector first
would only add a way to fail.

| File | Symbology | Decoded value | zbar rect (left, top, w, h) |
|---|---|---|---|
| `barcode_1.png` | CODE39 | `000065902` | (18, 19, 528, 135) |
| `barcode_2.png` | CODE39 | `000065412` | (12, 6, 579, 124) |
| `Id_1.png` | CODE39 | `000065412` | (33, 792, 525, 112) |
| `Id_2.png` | CODE39 | `000065902` | (60, 792, 479, 122) |
| `student_1_info.png` | - | **none** (0 symbols across 6 variants) | - |
| `student_2_info.png` | - | **none** (0 symbols across 6 variants) | - |

Both cards are CODE39, not the CODE128 the project generates by default
(`BARCODE_PREFIX=WIT:STUDENT:`). This is a real mismatch between what the system
emits and what the physical cards carry, recorded in §8 (ERR-006).

Decoding is variant-dependent, which is why the ensemble exists: on `Id_1.png`
the colour image, Otsu and adaptive variants decode, while plain greyscale and
CLAHE return nothing. Greyscale alone is not sufficient.

### 4.3 Student mapping - one verified, one UNRESOLVED

**Filename numbering does not correspond across folders. This was verified by
decoding, not assumed.**

- `barcode_1.png` decodes to `000065902`, which is the payload on **`Id_2.png`**.
  `barcode_1.png` is a crop of card 2.
- `barcode_2.png` decodes to `000065412`, which is the payload on **`Id_1.png`**.
  `barcode_2.png` is a crop of card 1.

| Payload | Card | Info panel | Database | Status |
|---|---|---|---|---|
| `000065412` | `Id_1.png` - Javy Danielle Caporal | `student_1_info.png` - "Javy Danielle N. Caporal", Reference Number `65412` | row present: ref `65412`, `barcode_payload = 000065412` | **VERIFIED** by three independent routes (decoded symbol, printed text on card, database record) plus photo/name agreement with the panel |
| `000065902` | `Id_2.png` - Ronna Mae D. Alemania | `student_2_info.png` - "Ronna Mae D. Alemania", Reference Number **`000001`** | **no record** | **UNRESOLVED** |

**The contradiction, stated precisely.** `Id_2.png` shows Ronna Mae D. Alemania
(photo and name agree with her panel) and its barcode encodes `000065902`, which
would correspond to reference `65902`. Her information panel states her reference
number is `000001`. The card and the panel **disagree**, and the database contains
no record for either payload.

**No student was created, and no payload was invented or reassigned.** The
pipeline correctly reports `UNKNOWN_BARCODE` for this card and displays no
student. Resolving this requires your confirmation:

> **Please confirm which is authoritative for Ronna Mae D. Alemania - reference
> `65902` (as encoded on her card `Id_2.png`) or `000001` (as printed on
> `student_2_info.png`)?** Once confirmed, the record can be added or corrected
> through the existing Students page; no schema change is needed.

### 4.4 Loss of the previously active model - UNRESOLVED

**This is the most serious incident of the session and it is reported in full.**

At 04:46 the previously active model was backed up as instructed:

- `models_yolo/active/best.pt` - MD5 `0545d90d24b86d3ff7a18aa6f717aadd`,
  YOLOv8n, class `{0: barcode}`
- copied to `models_yolo/backups/active_best_pre_retrain_20261015_044608.pt`
- and to `reports/backups/20261015_044608/` alongside a copy of `.env`

That model's measured behaviour on the assets, **captured at 04:48 before it
disappeared**, is recorded here because it is the baseline the new model must be
judged against:

| Asset | Detections | Max conf | Decoded from crop |
|---|---|---|---|
| `barcode_1.png` | 1 | 0.946 | `000065902` |
| `barcode_2.png` | 1 | 0.912 | `000065412` |
| `Id_1.png` | 1 | 0.772 | `000065412` |
| `Id_2.png` | 1 | 0.821 | `000065902` |
| `student_1_info.png` | **0** | - | - |
| `student_2_info.png` | **0** | - | - |

By 04:57 the following were **absent from the working tree**: `models_yolo/active/`
(including the backup), `models_yolo/backups/`, both earlier candidate runs, the
entire `reports/` directory (including the `.env` backup), and `datasets/`.

- I could not identify the cause. The scripts executed in this session contain no
  deletion or `rmtree` of those paths: `build_dataset.py` writes only under
  `--out`, and `train_yolo.py` writes only under `candidates/`, plus `backups/`
  and only when `--promote` is passed - which it was not, until §5.4.
- The original weights are **unrecoverable**. Nothing was restored from a backup,
  because no backup survived.
- Collateral damage: `results.csv` of the first training run was truncated to
  epochs 41-60, which is why that run reported **20 of 60** epochs completed
  despite requesting 60 (§5.2).

**Consequence:** there was no longer any trained model to compare against or to
fall back on, and the application had no model at the path it loads
(`models_yolo/active/best.pt`). A replacement was therefore installed (§5.4) -
chosen because it was the only functional option, and it is measurably worse than
the deleted model in two respects. That trade is stated plainly rather than
presented as an improvement.

---

## 5. Dataset and training

### 5.1 Dataset - VERIFIED

Built by `scripts/build_dataset.py` into `datasets/barcode/`, using **only** the
four files in `assets/images/`. Full detail in `dataset/DATASET_REPORT.md`.

| Split | Images | Positive | Negative | Independent cards |
|---|---|---|---|---|
| train | 2 | 1 (`Id_1.png`) | 1 (`student_1_info.png`) | 1 |
| val | 2 | 1 (`Id_2.png`) | 1 (`student_2_info.png`) | 1 |

- Single class `barcode` (id 0). `nc: 1`.
- **No synthetic barcode was generated.** `prepare_dataset.py` (the older script)
  renders 50 invented `WIT:STUDENT:*` Code128 barcodes; it is **not** part of this
  workflow and was not run.
- **No image was duplicated, renamed or reused** to inflate the split. Exact
  duplicate detection across the built dataset: `none`.
- Every box is the decoder's own rectangle for a symbol that actually decoded;
  evidence per box is in `datasets/barcode/build_manifest.json`.
- Labels re-verified against live zbar extents at **IoU >= 0.85** by the test
  suite (22/22 pass).

### 5.2 Training run v3 - completed, promoted

Command (reproducible; `seed=0`):

```bash
python -m scripts.train_yolo \
  --data datasets/barcode/data.yaml \
  --epochs 60 --imgsz 640 --batch 16 --device cpu \
  --name barcode_detector_v3
```

| Setting | Value |
|---|---|
| Base weights | `yolov8n.pt` (local, COCO pretrained, no download) |
| Epochs requested / completed | 60 / **60** (`completed: true`) |
| Wall time | 202.3 s |
| Device | CPU |
| Augmentation | Ultralytics defaults |
| Images / batch | 2 / 16 |

Checkpoint-reported validation metrics (on 1 positive + 1 negative):

| Metric | Value |
|---|---|
| mAP50 | 0.9950 |
| mAP50-95 | 0.9950 |
| Precision | 0.5983 |
| Recall | 1.0000 |

**These numbers must not be read as accuracy.** They come from a single
validation instance. mAP50 and mAP50-95 being *identical to four decimals* is
itself the signature of a trivially easy one-instance problem, not a strong
model. The held-out asset evaluation below is the number that matters.

### 5.3 Training run v4 (tame augmentation) - completed early, REJECTED

Run to test whether Ultralytics' default augmentation was overfitting a
2-image dataset. Hypothesis: mosaic/scale/perspective cut the only barcode into
quarters.

```bash
python -m scripts.train_yolo --data datasets/barcode/data.yaml \
  --epochs 60 --imgsz 640 --batch 16 --device cpu \
  --name barcode_detector_v4_tame --augmentation tame
```

**The hypothesis was wrong, and instructively so.**

| Metric | v3 (default aug) | v4 (tame aug) |
|---|---|---|
| Epochs completed | 60 / 60 | **30 / 60** (early stop, `patience=20`) |
| mAP50 | 0.9950 | **0.0188** |
| mAP50-95 | 0.9950 | **0.0149** |
| Precision | 0.5983 | **0.0175** |
| val/cls_loss | 4.80 | **1139.70** (diverged) |
| Max detections on one held-out image | 13 | **83** |
| Total detections, held-out assets | 27 | **111** |
| False positives on the barcode-free document | 1 | 1 |

**Conclusion (VERIFIED):** with one positive image, the default augmentation is
*load-bearing*, not harmful. Removing mosaic and the geometric jitter left the
model unable to learn at all. v4 is rejected. This is a property of a 2-image
dataset, not a fixable hyperparameter - which is itself the strongest evidence
that this dataset cannot support a trustworthy model.

### 5.4 Promotion decision - documented, forced, and not an improvement

The previously active model no longer existed (§4.4), so there was no baseline
to compare against.

- The training script's original promotion gate returned
  `promote: true, reason: "no active model to compare against"` - i.e. it treated
  the **absence of a baseline as permission to ship**. That is a dangerous
  default and it is now fixed (ERR-002). With the fix, v4 is correctly rejected.
- v3 was installed to `models_yolo/active/best.pt` (MD5
  `b4f807cf29eb0250a53f5e0b34d3bbc5`) **because the application had no model at
  all** and v3 was the only candidate that both detects and decodes the real
  assets. This was a restore-to-working-state decision, not a quality claim.

**Measured regressions of the installed model versus the deleted baseline:**

| Behaviour | Deleted baseline | Installed v3 |
|---|---|---|
| Detections per barcode image | **1** | 4 (`Id_1`), 2 (`Id_2`), 13 (`barcode_1`), 12 (`barcode_2`) |
| False positives on `student_1_info.png` | **0** | **1 at conf 0.571** |
| False positives on `student_2_info.png` | 0 | 0 at the app threshold (0.35); 1 at 0.299 when the evaluator used 0.25 |

Duplicate overlapping boxes do not break the pipeline in practice (the highest
confidence box decodes first and `verified_now` fires once), but they are wasted
work and they raise the risk of a fragment crop being decoded first.

**Recommended action (needs your approval, not applied unilaterally):** the four
true detections score 0.865-0.997 while the false one scores 0.571. Raising
`YOLO_CONFIDENCE_THRESHOLD` from `0.35` to `0.70` in `.env` would suppress every
false detection observed while retaining all four true ones. This was **not**
applied, because with only four positive observations the real-world confidence
distribution is unknown, and lowering sensitivity could cause genuine misses at
awkward angles. That is a judgement that should be made with more data, or
explicitly by you.

---

## 6. Test results

### 6.1 `python -m scripts/verify_scanner` - 17 PASS, 2 FAIL

Run against `models_yolo/active/best.pt` at the application's real thresholds
(`conf=0.35`, `iou=0.45`). Machine-readable output:
`reports/scanner_verification.json`.

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | `barcode_1.png` decodes directly | **PASS** | `000065902` CODE39 rect=[18, 19, 528, 135] |
| 2 | `barcode_2.png` decodes directly | **PASS** | `000065412` CODE39 rect=[12, 6, 579, 124] |
| 3 | `Id_1.png` contains a decodable barcode | **PASS** | `000065412` CODE39 rect=[33, 792, 525, 112] in 590x1004 |
| 4 | `Id_2.png` contains a decodable barcode | **PASS** | `000065902` CODE39 rect=[60, 792, 479, 122] in 590x1004 |
| 5 | Model loads | **PASS** | classes=['barcode'], conf=0.35, iou=0.45, cpu |
| 6 | `Id_1.png` detect -> crop-decode | **PASS** | 4 dets, maxconf 0.945, decoded `000065412` |
| 7 | `Id_2.png` detect -> crop-decode | **PASS** | 2 dets, maxconf 0.865, decoded `000065902` |
| 8 | `barcode_1.png` detect -> crop-decode | **PASS** | 13 dets, maxconf 0.997, decoded `000065902` |
| 9 | `barcode_2.png` detect -> crop-decode | **PASS** | 12 dets, maxconf 0.978, decoded `000065412` |
| 10 | `student_1_info.png` no false detection | **FAIL** | 1 detection at conf 0.571, box (88, 0, 1326, 1772) |
| 11 | `student_2_info.png` no false detection | **PASS** | 0 detections |
| 12 | Database reachable | **PASS** | `wit_barcode_system` |
| 13 | Known card -> VERIFIED with student | **PASS** | state=verified, `000065412`, Javy Danielle N. Caporal |
| 14 | Unrecorded card -> UNKNOWN_BARCODE, no student | **PASS** | state=unknown_barcode, `000065902`, student=None |
| 15 | `student_1_info.png` yields no student | **PASS** | state=barcode_detected, decoded=None, student=None (the false box does **not** produce a false student) |
| 16 | `student_2_info.png` yields no student | **PASS** | state=searching, decoded=None, student=None |
| 17 | Card outside the scan guide is not verified | **PASS** | state=barcode_detected, decoded=None |
| 18 | `000065412` resolves to a student | **PASS** | Javy Danielle N. Caporal (ref 65412) |
| 19 | `000065902` resolves to a student | **FAIL** | no database record; decoded from `Id_2.png` |

### 6.2 `python -m pytest tests/ -q` - 145 passed, 1 failed

| Stage | Result |
|---|---|
| Baseline before this work | 6 failed, 138 passed |
| After this work | **145 passed, 1 failed** |
| Remaining failure | `test_audit_regressions.py::TestBug005FalsePositives::test_barcode_free_document_yields_no_detection[student_1_info.png]` |

The remaining failure is **evidence, not debris**. It is a regression guard for
the exact bug class this candidate regressed on:

```
AssertionError: student_1_info.png contains no barcode but produced
[(0.5709069967269897, (88, 0, 1326, 1772))]
```

The guard was **left failing on purpose**. Deleting it, weakening its threshold,
or retuning `.env` purely to turn the suite green would hide a real defect behind
a cosmetic pass. See §7.1 for the decision that needs your input.

### 6.3 Dashboard integration - VERIFIED by inspection and pipeline tests

`ui/scanner_page.py` already implements the required states, and each was
exercised through the real `ScanPipeline` rather than a mock:

| Requirement | Implementation | Verified |
|---|---|---|
| Detection status | `ScanState` -> status label with per-state colour | yes |
| Bounding box drawn | `ui/scanner_page.py` draws `primary_candidate` | yes (candidate geometry asserted in tests) |
| Detection vs decode distinguished | `ScanOutcome.state` separate from `decoded_value`; `BARCODE_DETECTED` message is explicit when a decode fails | yes |
| Decoded value shown | `self.student_barcode_label.setText(f"Barcode: {...}")` | yes |
| Student info after verified lookup only | popup opened only on `VERIFIED and verified_now` (`ui/scanner_page.py:504`) | yes |
| Info image in popup | `scan_result_dialog.py` loads `student.profile_image_path` | yes (path corrected, see ERR-004) |
| Unknown barcode state | `UNKNOWN_BARCODE`, no student rendered | yes, test 14 |
| Decode failure state | `BARCODE_DETECTED` with "unable to decode" message | yes, tests 15/17 |
| Retry / scan another student | different payload verifies immediately; cooldown gates repeats of the same value | yes |
| Database failure state | `ScanState.ERROR`, distinct from unknown | covered by `tests/test_scan_pipeline.py` |
| Camera failure | `services/camera_service.py` raises; UI surfaces it | **NOT TESTED** this session (no physical camera exercised) |

No new buttons or decorative widgets were added.

### 6.4 Webcam scanning - NOT TESTED

`/dev/video0` and `/dev/video1` exist and the service enumerates them, but **no
live camera frame was processed in this session**. Every number in this report
comes from the six static assets, composited into a 1280x720 canvas by
`tests/scanner_fixtures.make_webcam_frame` (a real crop of a real card placed on a
plain background - no barcode is synthesised).

**Nothing here establishes real-world webcam accuracy.** Lighting, glare, motion
blur, card rotation, distance and background clutter are all unrepresented in the
evidence. Treat webcam behaviour as unproven.

### 6.5 Performance - VERIFIED (and a finding)

Steady state, 1280x720 frame, CPU, no other load:

| Operation | Median |
|---|---|
| YOLO detect | 218 ms |
| Full pipeline (detect -> gate -> decode ensemble -> lookup) | 522 ms |
| Throughput | **1.9 FPS** |

Configured `SCAN_TARGET_FPS` is **12**. The pipeline runs ~6x slower than target
on CPU. An RTX 2050 is present and `torch.cuda.is_available()` is `True`, but
`.env` sets `YOLO_DEVICE=cpu`. Switching to `device=0` is the obvious lever and
is **not** applied here, because it changes deployment behaviour and was not
requested. Note the duplicate boxes of §5.4 multiply decode work (13 crops x 6
preprocessing variants on one frame).

*(An earlier measurement in this session showed 0.13 FPS; that figure was taken
while a concurrent evaluation job was competing for the CPU and is superseded by
the clean measurement above.)*

---

## 7. Open issues requiring a decision

### 7.1 False detection on `student_1_info.png` (0.571) - needs your call

Options, with honest trade-offs:

| Option | Effect | Risk |
|---|---|---|
| A. Raise `YOLO_CONFIDENCE_THRESHOLD` to 0.70 | Suppresses the observed false detection; all 4 true detections (0.865-0.997) survive | Real cards at awkward angles may fall below 0.70 and be missed. Untestable on current data |
| B. Collect more negative samples (photos of documents, desks, hands) and retrain | Addresses the actual cause | Needs new photographs |
| C. Leave as is, failing test visible | Honest, no regression hidden | Cosmetic false boxes remain; ~1 failing test |

I did not apply A or B unilaterally. **Recommended: B, with A as an immediate
mitigation if the false box is visible in use.**

### 7.2 Ronna Mae D. Germany's reference number - needs your confirmation

See §4.3. Card says `65902` (encoded `000065902`), panel says `000001`. No record
exists. The scanner behaves correctly (reports unknown, shows nothing), but the
student cannot be displayed until this is settled.

### 7.3 Code39 vs Code128 payload convention - needs a decision

The physical cards are **CODE39** carrying the bare zero-padded reference
(`000065412`). The application's generator emits **CODE128** carrying
`WIT:STUDENT:65412`. These are different symbologies *and* different payload
formats. Anything printed by this application will not scan on the existing
readers, and vice versa. Not changed here because both conventions have users;
flagged for a decision.

---

## 8. Errors encountered, root causes and fixes

### ERR-001 - Dataset builder depended on deleted label files (High, **Fixed**)

`scripts/build_dataset.py` read ground-truth boxes from `dataset/labels/train/*.txt`.
That directory did not exist in the working tree, so the builder could not run and
six of its own tests failed at the start of this session.

```
FileNotFoundError: [Errno 2] No such file or directory:
  '.../dataset/labels/train/Id_1.txt'

AssertionError: source label vanished: '.../dataset/labels/train/Id_1.txt'
```

**Root cause.** Annotation boxes were read from a hand-written intermediate file
instead of being derived from the image they describe, so the build had a hard
dependency on a mutable artefact that was also the only record of where the
barcodes were.

**Fix.** Boxes are now derived at build time from a real decode of the image
itself (`decode_symbols` / `derive_box`), with the evidence recorded in
`build_manifest.json`. The builder refuses to label an image positive unless
exactly one non-degenerate symbol decodes, and refuses a negative that decodes.
The synthetic-render carry-over path was removed entirely.

### ERR-002 - Promotion gate shipped a regressed model when no baseline existed (High, **Fixed**)

```json
"promotion": {"promote": true, "reason": "no active model to compare against"}
```

**Root cause.** `should_promote()` short-circuited to `True` when there was no
active model, conflating "no evidence against" with "evidence for".

**Fix.** Absence of a baseline now **blocks** promotion and says so. A false
positive on a barcode-free document is an absolute veto, with or without a
baseline. Mean confidence on held-out assets is now also compared, so a
confidence regression is caught. Result: v4 is correctly rejected.

### ERR-003 - zbar's zero-area rectangles treated as detections (Medium, **Fixed**)

On `Id_1.png` the greyscale, sharpened and CLAHE variants returned
`Rect(left=558, top=790, width=0, height=0)` - decoder noise that flowed through
`decode()` as if it were a symbol, and could have become a training annotation.

**Fix.** `_decode_variant()` discards any symbol whose rect is smaller than
`MIN_SYMBOL_SIDE_PX` (2 px) on either axis, and logs the discard at debug level.
Also hardened: `decode()` now tries **colour first** and only then greyscale
(evidenced by `Id_1.png` decoding from BGR but not from grey), and non-UTF-8
payloads fall back to `latin-1` instead of raising; CLAHE was added to the
ensemble for dark-on-orange bars.

### ERR-004 - Asset relocation broke three hardcoded paths (Medium, **Fixed**)

The info panels moved to `assets/images/info/`, leaving stale paths that pointed
into a non-existent flat layout:

```
[ WARN:0] global loadsave.cpp:278 findDecoder imread('.../assets/images/student_1_info.png'):
  can't open/read file: check file path/integrity
AssertionError: assert None is not None
```

**Fix.** One canonical resolver, `settings.info_image(filename)`, which accepts
both `images/info/` and `images/`, is now used by `build_dataset.py`,
`train_yolo.py`, `initialize_database.py` (the seeded `profile_image_path`
values, which the scan popup renders) and the tests.

### ERR-005 - A test raced real inference time against a cooldown (Medium, **Fixed**)

```
AssertionError: repeated frames must not raise verified_now more than once;
got [True, False, False, False, False, False, False, False, True, False]
```

**Root cause.** `test_verified_now_is_raised_once_for_repeated_frames` used a real
clock against a 5 s cooldown while processing 10 frames at ~0.5-1 s each on CPU.
The cooldown legitimately expired mid-test. Re-raising after cooldown expiry is
**intentional, separately tested behaviour** (`test_cooldown_suppresses_repeat_
verifications`), so the pipeline was left alone.

**Fix.** The test now injects a frozen clock, so it measures the invariant it is
named for - one popup while the same card stays in frame - deterministically. The
underlying production caveat it exposed is real and is recorded in §6.5: at
1.9 FPS, 5 s of cooldown is only ~10 frames.

### ERR-006 - Generator symbology does not match the physical cards (Open, not fixed)

CODE128 + `WIT:STUDENT:` prefix generated vs CODE39 + bare zero-padded reference
on the cards. Reported as §7.3; no unilateral change.

### ERR-007 - First training run truncated, reporting a false epoch count (Medium, **Fixed by re-run**)

`evaluation_report.json` reported `epochs_completed: 20` with
`completed: false`, while `results.csv` contained epochs **41-60** - a file whose
first 40 rows had been lost when the containing run directory was deleted
mid-write (§4.4). The epoch-counting helper correctly refused to call it complete.

**Fix / handling.** The run was discarded rather than reported as successful, and
training was re-run to a genuine 60/60 (v3). For future runs, launch long jobs
detached so a client timeout cannot interrupt them:

```bash
setsid nohup venv/bin/python -m scripts.train_yolo ... > logs/train.log 2>&1 < /dev/null &
```

---

## 9. Reproduce everything

```bash
cd "WIT Barcode Student Information System"
source venv/bin/activate

# 0. confirm the six assets are intact (digests must match section 4.1)
md5sum assets/images/Id_1.png assets/images/Id_2.png \
       assets/barcodes/barcode_1.png assets/barcodes/barcode_2.png \
       assets/images/info/student_1_info.png assets/images/info/student_2_info.png

# 1. rebuild the dataset from assets only  (writes datasets/barcode/)
python -m scripts.build_dataset --out datasets/barcode

# 2. preflight: validate dataset + report the active model's baseline
python -m scripts.train_yolo --data datasets/barcode/data.yaml --dry-run

# 3. train a candidate (detached; writes models_yolo/candidates/<name>_<ts>/)
setsid nohup python -m scripts.train_yolo \
  --data datasets/barcode/data.yaml \
  --epochs 60 --imgsz 640 --batch 16 --device cpu \
  --name barcode_detector_v3 > logs/train_v3.log 2>&1 < /dev/null &

# 4. evaluate an existing candidate against the gate (no retraining)
python -m scripts.train_yolo --evaluate-only models_yolo/candidates/<run>/weights/best.pt

# 5. end-to-end verification with PASS/FAIL per check
python -m scripts.verify_scanner

# 6. full test suite
python -m pytest tests/ -q

# 7. run the application
python main.py
```

Dependencies are unchanged and already installed in `venv/`; nothing new was
required, so no `pip install` step is needed. Should you later need to reinstall:

```bash
# Ubuntu/Kubuntu system packages (needs sudo; run only if libzbar is missing)
sudo apt-get install -y libzbar0 zbar-tools

# Python packages into the project venv only - never system-wide
source venv/bin/activate
pip install -r requirements.txt
```

Running the GUI: `python main.py` from the project root. The database must be
running (`sudo systemctl start mysql`). The scanner page requires a camera; the
rest of the application does not.

---

## 10. Modified files

| File | Change |
|---|---|
| `scripts/build_dataset.py` | Rewritten. Derives annotations from real decodes instead of reading a deleted label directory; removed the synthetic-render carry-over; enforces the six-asset allowlist, single `barcode` class, negative verification, and source-digest immutability checks. |
| `scripts/train_yolo.py` | Added `--augmentation {default,tame}` and `--patience`; fixed the dangerous `should_promote` default (ERR-002); added false-positive veto and mean-confidence comparison; added `max_detections_on_one_image` / `total_detections` to the evaluation summary so duplicate boxes are visible; made the panel path relocatable. |
| `scripts/verify_scanner.py` | **New.** End-to-end PASS/FAIL verification over the six assets, keeping detection, decoding and lookup as separate reported stages; writes `reports/scanner_verification.json`. |
| `services/barcode_decoder.py` | Discard zbar's zero-area rectangles; colour-before-greyscale decode; `latin-1` fallback; added CLAHE to the preprocessing ensemble; extracted `_decode_variant()` with per-variant logging. |
| `config/settings.py` | Added `INFO_IMAGES_DIR` and the `info_image()` resolver. |
| `scripts/initialize_database.py` | Seeded `profile_image_path` values updated to the `assets/images/info/` layout. |
| `tests/test_dataset_build.py` | Adapted to the corrected builder contract. Kept every guard (no card leakage, negatives present and genuinely barcode-free, IoU >= 0.85 annotation check, sources untouched) and added two new ones: only permitted images are copied, and no source was modified. |
| `tests/test_audit_regressions.py` | Panel paths via `settings.info_image()`. One guard deliberately left failing as evidence (§6.2). |
| `tests/test_scanner_worker.py` | Frozen clock in the duplicate-suppression test (ERR-005). |
| `datasets/barcode/**` | **New**, generated dataset (`data.yaml`, 4 images, 4 labels, `build_manifest.json`). |
| `dataset/DATASET_REPORT.md` | **New.** Dataset provenance, eligibility, annotation method, limitations. |
| `ERROR_TRACE_REPORT.md` | This document, replacing the 01:12 audit. |
| `models_yolo/active/best.pt` | Replaced (§5.4). Original `0545d90d...` unrecoverable. |
| `models_yolo/candidates/barcode_detector_v3_*/` | **New.** Promoted candidate: weights, `results.csv`, `args.yaml`, curves, `evaluation_report.json`. |
| `models_yolo/candidates/barcode_detector_v4_tame_*/` | **New.** Rejected candidate, retained as evidence. |
| `models_yolo/backups/partial20ep_best.pt` | Retained partial run from the truncated first attempt. |

**Untouched:** all six source assets, `.env`, `requirements.txt`, `database/schema.py`,
`models/*`, and the existing UI structure.

---

## 11. Recommended next steps, in priority order

1. **Answer the two questions in §7.2 and §7.3.** Both block correct behaviour and
   neither can be settled by inference from the files.
2. **Collect more data.** 10+ photographed cards with varied angle, distance and
   lighting, plus negative photographs of documents, hands and desks. Until then,
   no metric from this dataset means anything and the model should be treated as
   experimental.
3. **Restore a known-good baseline.** The deleted model was measurably better
   (one tight box per image, zero false positives). If a copy exists in a backup
   or snapshot outside this working tree, restoring it and re-running
   `--evaluate-only` against v3 would settle the comparison properly.
4. **Decide on the confidence threshold** (§7.1) with more real frames in hand.
5. **Re-test with a physical camera.** Section 6.4 is honest about being untested;
   a single live session would close the largest gap in this report.
6. **Consider `device=0`** to use the RTX 2050 and close the 1.9 vs 12 FPS gap.
7. **Investigate what removed the generated directories.** Until the cause is
   known, any training output can vanish mid-run, as it already did once.

---

# Follow-up session — 2026-10-15 (later)

This section is **appended**, not a replacement. Sections 1-11 above are the
earlier audit and are left exactly as written, including the incident in §4.4
where the previously active model disappeared. What follows records what changed
after that, and what is still open.

Everything here was produced by commands in this project directory on
2026-10-15. Results are labelled **VERIFIED**, **FAIL**, **NOT TESTED** or
**UNRESOLVED**.

## F1. Cause of the earlier file loss — UNRESOLVED (external)

Searched for anything that could have removed `models_yolo/active/`,
`models_yolo/backups/`, the earlier candidate runs, `reports/` and `datasets/`:

| Checked | Result |
|---|---|
| User crontab | `no crontab for tommyp` |
| systemd timers | only stock distro timers (sysstat, apt, man-db, fwhupd...) |
| Cleanup scripts in `/etc/cron*`, `/etc/systemd/system/*.service`, `/usr/local/bin` | none referencing this project |
| `.vscode/settings.json` | animation preferences only; no file exclusions or watchers |
| Scripts in this project | `build_dataset.py` writes only under `--out`; `train_yolo.py` writes only under `candidates/`, plus `backups/` and only with `--promote`. No `rmtree` of any of the lost paths. |

No in-project or OS-level mechanism accounts for it. The same window also
*relocated* the two info panels into `assets/images/info/` — a move no script here
performs. **Conclusion: an external process with write access to the workspace is
the only consistent explanation, and I cannot identify it from inside the
project.** What I can do is stop depending on the workspace for backups, which is
F2.

## F2. Persistent backups — VERIFIED

Backups now live **outside** the working tree, so a repeat of F1 cannot take them:

```
/home/tommyp/wit-barcode-backups/20261015_062754/
├── models/      active model + every candidate's best.pt and last.pt
├── config/      .env, .env.example, requirements.txt, and the changed sources
├── datasets/    baseline and augmented labels + data.yaml + manifests
├── reports/     every JSON report and this document
├── logs/        every training and test log
└── SHA256SUMS.txt   98 files, re-verified: 98 OK, 0 mismatches
```

Every backed-up model was compared against its source with `md5sum`: **0
mismatches**. The previously active model (v3) was additionally copied to
`models/active_before_v5_promotion.pt` *before* the promotion.

## F3. False positive on `student_1_info.png` — RESOLVED IN THE MODEL

The earlier audit could not fix this and deliberately left the guard test
failing. It is now fixed, and fixed in the **model**, not by tuning.

**Threshold tuning was tried first and does not work.** `scripts/threshold_sweep.py`:

| Threshold | Active model (v3): detected / decoded | False detections |
|---|---|---|
| 0.35 | 4/4 / 4/4 | **1** (conf 0.571) |
| 0.50 | 4/4 / 4/4 | **1** |
| 0.60 | 4/4 / 4/4 | 0 |
| 0.70 | 4/4 / 4/4 | 0 |
| 0.80 | 4/4 / 4/4 | 0 |
| 0.90 | 3/4 / 3/4 | 0 — **rejected, loses genuine detections** |

The catch: that table scores the six static assets. On a *composited scanning
frame* — what the scanner actually receives — the genuine barcode in card
`Id_1.png` is detected at only **0.388** confidence, *below* the 0.571 false
detection. The two classes overlap, so **no threshold separates them.** Raising
the threshold to 0.70 removed the false detection and simultaneously broke
`TestBug002CropTruncation::test_pipeline_recovers_the_payload_for_every_card_asset`.
I reverted that change; `YOLO_CONFIDENCE_THRESHOLD` stays at **0.35**, and the
`.env` comment now records why.

The real fix was the augmented training (F4). Measured on the same six assets and
the same four composited scanning offsets:

| | v3 (previous active) | v5 (augmented, now active) |
|---|---|---|
| False detections on the two documents | 1 @ 0.571 | **0** |
| Detections on `barcode_1.png` / `barcode_2.png` | 13 / 12 | **2 / 2** |
| `Id_1.png` on a scanning frame | 0.388 (fragile) | **0.994** |
| `Id_2.png` on a scanning frame | 0.727 | **0.997** |
| All 4 barcode assets detected **and** decoded | yes | **yes** |

`test_barcode_free_document_yields_no_detection[student_1_info.png]` now
**passes** because the model produces no detection — not because the assertion
was weakened or the threshold raised.

## F4. Training and model comparison — VERIFIED

| Candidate | Data | Epochs | False positives | Max boxes on one image | Decoded from crop | Outcome |
|---|---|---|---|---|---|---|
| v3 | baseline (2 originals) | 60/60 | 1 | 13 | 2 | superseded |
| v4 (`tame` augmentation) | baseline | 30/60 (early stop) | 1 | 83 | 1 | **rejected** — mAP50 collapsed to 0.019 |
| **v5** | **augmented (44 from 2 originals)** | **60/60** | **0** | **2** | **2** | **promoted** |

v5 validation metrics: mAP50 0.995, mAP50-95 0.874, precision 0.997, recall 1.0.
**These are measured on 21 variants of a single unseen card.** They show the model
generalises across exposure and angle *of that card*. They say nothing about
unseen cards, and must not be quoted as if they did.

Promotion was gated: the previous active model was backed up first, the gate
compares false positives, held-out detection, crop decode and mean confidence,
and a candidate with any false positive on a barcode-free document is vetoed
outright. Rejected candidates and their reports are retained on disk.

One gate bug was found and fixed first: `evaluate_asset` decoded from the *raw*
detection box, so it scored a code path production never runs and rejected a
model for producing a tight box. It now uses the production crop margin.

## F5. Augmented dataset — VERIFIED, with the caveat stated

`scripts/augment_dataset.py` derives variants from the two real cards only. No
barcode content is generated.

| | Baseline | Augmented |
|---|---|---|
| Unique original images with a barcode | 2 | **2** |
| Generated images | 4 | 44 |
| train / val | 1 pos + 1 neg each | 21 pos + 1 neg each |

Every variant is verified before being kept: it must still decode, it must decode
to the *original's* value, and its transformed box must contain the symbol. Three
variants per card were **discarded** for failing that check (±8° rotation, whose
axis-aligned box grows to 3.7x the symbol area, and perspective, which misses the
symbol entirely). Leakage is prevented by grouping: every variant of `Id_1` is in
train, every variant of `Id_2` is in val.

**44 images is not 44 samples.** They are derivatives of two photographs.

## F6. Decoding robustness — VERIFIED, with measured limits

`scripts/test_barcode_decoder.py` runs all six assets through 27 in-memory
transformations. Before/after summary is in `reports/robustness_matrix.json`.
Every condition that failed did so with the *reason* recorded:

- **Exposure, contrast, gamma, shadow, noise, JPEG: all decode.** CLAHE rescues
  bright frames; Otsu rescues noisy ones; most need nothing beyond the original.
- **Tilt: readable to about ±11°.** Fails at ±12°+. At ±13° and 90° the barcode
  *still decodes* while YOLO no longer detects it.
- **Blur: fails at sigma 3.0** (sigma 2.0 fails for `Id_2`).
- **Distance: fails at 35% scale** — too few bar modules.
- **Perspective 8%: decode fails on three of the four assets.**

A whole-frame decode fallback was added for the case where YOLO is blind but the
barcode is still readable. Measured: it recovers a sideways (90°) card that
previously reported `searching`, and across 8 document variants — including the
dimmed ones that made the old model fire at 0.97 — it produced **no** false
decode. It is gated on a sustained no-detection period and rate limited, so it
cannot dominate the frame budget.

**Rotations inside the decoder were measured and found useless**: zbar already
deskews to ±11° and reads 90°/180° presentations natively, and adding discrete
rotations recovered nothing at 20-60°. The capability exists but is off by
default (`SCAN_DECODE_ROTATIONS=False`) rather than paid for per frame.

## F7. Performance — VERIFIED, CPU vs CUDA

`scripts/benchmark_devices.py`, same model and frames, 12 iterations after
3 warm-up runs:

| Stage | CPU | CUDA (RTX 2050) |
|---|---|---|
| model load | 0.067 s | 0.045 s |
| detect (median) | 119.8 ms | **12.2 ms** |
| decode (median) | 37.3 ms | 37.0 ms |
| full pipeline (median) | 116.4 ms | **13.4 ms** |
| pipeline throughput | 8.59 fps | **74.46 fps** |
| overlay render | 16.7 ms | 20.2 ms |

**9.82x faster on detection with identical detections and confidences** on both
devices. CUDA: compute capability 8.6, 3773 MB, runtime 13.0.

`YOLO_DEVICE` was therefore switched from `cpu` to `0`. Before that,
`YOLODetectorService.detect` was given a **permanent CPU fallback**: if inference
on the GPU raises, the detector logs it, switches to CPU and keeps scanning
rather than returning nothing. A regression test covers this.

This also resolves an earlier finding: the pipeline was measured at 522 ms per
frame before this session and is now **116 ms on CPU** — a 4.5x improvement from
decoder early exit, replacing a 140 ms denoise filter that never once produced a
winning decode, and suppressing duplicate crops.

**NOT TESTED:** live webcam capture. Both `/dev/video0` and `/dev/video1` were
enumerated and index 0 opened, but it delivered no frames (0.0 ms reads, no frame
size). Nothing here establishes real-world camera accuracy, lighting response or
motion performance.

## F8. Student lookup — one verified, one still UNRESOLVED

`scripts/audit_ids.py` inventories all six assets with SHA-256, decodes each,
detects each, and queries the database with a **parameterised, read-only** query.

| Asset | Value | Status |
|---|---|---|
| `Id_1.png`, `barcode_2.png` | `000065412` | **VERIFIED** — Javy Danielle N. Caporal, ref 65412 |
| `Id_2.png`, `barcode_1.png` | `000065902` | **STUDENT_RECORD_MISSING** |
| `student_1_info.png`, `student_2_info.png` | none | NO_BARCODE_IN_IMAGE |

The `000065902` conflict is unchanged and still needs an administrator: the card
`Id_2.png` encodes reference `65902` and shows Ronna Mae D. Alemania, while her
panel prints reference `000001`. **No record was created, altered or merged.**
`reports/ADMIN_ACTIONS_REQUIRED.md` states the question. The scanner reports
`UNKNOWN_BARCODE` and displays no student — correct, and the one remaining
`FAIL` in the verification run.

Note the payload-to-filename trap is confirmed by test: `barcode_1.png` is a
crop of **`Id_2.png`**, not `Id_1.png`.

## F9. Test results — VERIFIED

| Suite | Result |
|---|---|
| `pytest tests/ -q` | **179 passed, 0 failed** (146 before, +33 new regression tests) |
| `python -m scripts.verify_scanner` | **18 PASS, 1 FAIL** (the unresolved mapping) |

New regression tests in `tests/test_audit_followup.py` cover: the six asset
digests, barcode-to-card identity, panels decoding nothing, decode ordering and
early exit, attempt caps, degenerate-rect rejection, rotation canvas growth, the
±9° readable limit, the >20° failure, duplicate-candidate suppression, legacy
decoder compatibility, smoother jitter/grace/no-box/non-mutation, per-state
overlay hints, reduced motion, the audit's name comparison, and the CPU fallback.

## F10. Still open

1. **`000065902` unresolved** — an administrator must confirm Ronna Mae
   Germany's reference number. Blocking for that student.
2. **Code39 vs Code128** — the cards are CODE39 with a bare zero-padded
   reference; `services/barcode_generator.py` emits CODE128 with a
   `WIT:STUDENT:` prefix. Anything this application prints will not scan on the
   existing readers.
3. **No real camera test.** Everything is static-image evidence.
4. **Two physical cards.** No claim of generalisation is possible.
5. **External deletion (F1) unexplained.** Mitigated by out-of-tree backups, not
   solved.
6. **Augmentation is photometric-plus-mild-geometry only.** ±8° rotation and
   perspective variants were rejected by the annotation gate; a model trained to
   handle them would need those boxes derived from the symbol rather than
   propagated from the original.

## F11. Reproducing this follow-up

```bash
cd "WIT Barcode Student Information System"; source venv/bin/activate

python -m scripts.build_dataset --out datasets/barcode      # baseline dataset
python -m scripts.augment_dataset --out datasets/barcode_aug # derivatives
python -m scripts.threshold_sweep                            # threshold evidence
python -m scripts.benchmark_devices --frames 12              # CPU vs CUDA
python -m scripts.audit_ids                                  # verification matrix
python -m scripts.verify_scanner                             # end-to-end PASS/FAIL
python -m scripts.test_barcode_decoder                       # robustness matrix
python -m pytest tests/ -q                                   # full suite

# or all of it, with per-stage exit codes:
python -m scripts.run_pipeline
```

## F12. Robustness re-measured on the promoted model (2026-10-15)

`scripts/test_barcode_decoder.py` re-run against the active (v5) model,
6 assets x 27 transformations = 162 rows.

| Model | PASS | FAIL |
|---|---|---|
| v3 (previous active) | 137 | 25 |
| **v5 (active)** | **145** | **17** |

Where the remaining 17 failures actually are — this matters, because it changes
who is at fault:

| Failure class | Count | What it means |
|---|---|---|
| Detected but **not decoded** | 15 | YOLO still finds the barcode (0.46-0.92 confidence); zbar cannot read the degraded pixels. A **decoder** limit, not a detector limit. |
| False detection on a darkened document | 2 | `student_1/2_info.png` at 25% exposure, confidence **0.459 / 0.481** |

**The dim-document weakness is reduced, not eliminated.** On the documents as
supplied the active model fires **zero** detections, which is what fixed F3. But
darkening them to a quarter of their brightness still produces a weak false
detection. v3 produced the same effect far more strongly (0.94-0.97). So the
honest statement is: the false positive is fixed *for the supplied conditions*,
and a heavily under-exposed document can still draw a box.

**In every one of the 17 failures `decoded_value` is `None`.** A false student
still requires a successful decode *and* a database match, so no false identity
can be displayed. That is the property that actually matters for safety, and it
holds in all 162 rows.

Conditions that now pass which previously failed include blur and several
rotation cases. The conditions that fail are unchanged in character: heavy blur
(sigma 3.0), tilt beyond ±11°, distance at 35% scale, and 8% perspective. None
of these is claimed to be supported.

---

# Follow-up 2 — 2026-10-15: the `000065902` mapping is resolved

Section F10 item 1 is now closed. The administrator confirmed that the value
encoded on the card is authoritative.

## F13. Resolution — VERIFIED

**Question asked:** which reference number is authoritative for Ronna Mae D.
Alemania — `65902` (encoded by the barcode on her card) or `000001` (printed on
her information panel)?

**Answer given:** `65902`, the card's value.

**Why that is the consistent choice.** This project's own convention is that a
payload is the zero-padded 9-digit reference number — the verified student is
stored exactly that way (reference `65412` -> payload `000065412`). Under that
convention `000065902` means reference `65902`, in the same `65xxx` series as
the verified card. The `000001` on the panel has the shape of a placeholder, and
the project's seed script already recorded the contradiction in its own comment
(`# From barcode_1.png and Id_2.png (doesn't match ref 000001)`).

### What was written

`scripts/resolve_student_mapping.py` performs the change. It is deliberately
hard to run by accident: it prints the live evidence, refuses to write without
`--confirm`, and refuses any reference number that is not one of the two that
appear in evidence.

```bash
python -m scripts.resolve_student_mapping                                    # evidence only
python -m scripts.resolve_student_mapping --apply --reference 65902 --confirm --dry-run
python -m scripts.resolve_student_mapping --apply --reference 65902 --confirm
```

A pre-change dump of `students`, `barcodes`, `audit_logs` and `student_contacts`
was taken to the persistent backup first. The change:

| Table | Action |
|---|---|
| `students` | **INSERT** id=2 — `WIT-2024-002`, ref `65902`, `barcode_payload 000065902` |
| `barcodes` | **INSERT** — payload `000065902`, symbology **CODE39** (the one the card actually uses), image path the card |
| `audit_logs` | **INSERT** — action `resolve_barcode_mapping`, with old and new values |

Schema unchanged; every other field was transcribed from the project's existing
seed data for this student. **No value was invented** — only
`reference_number` and `barcode_payload` were ever in dispute, and only
`reference_number` was changed by the decision.

### Verification — measured, not assumed

The resolver re-ran the real scan pipeline against `Id_2.png` after writing:

```
state    verified
decoded  000065902
student  Ronna Mae D. Alemania
PASS: the card now resolves to a verified student.
```

### Results after the change

| Check | Before | After |
|---|---|---|
| `verify_scanner.py` | 18 PASS / **1 FAIL** | **21 PASS / 0 FAIL** |
| Audit: missing student records | 1 | **0** |
| Audit: mismatches | 0 | 0 |
| `pytest tests/ -q` | 179 passed | **182 passed, 0 failed** |
| `Id_2.png` end to end | `unknown_barcode`, no student | **`verified`, Ronna Mae D. Alemania** |

Both supplied cards now resolve to their own student, and a regression test
(`TestResolvedStudentMapping`) asserts that `Id_1` shows Caporal, `Id_2` shows
Alemania, and that they are never cross-matched — so the two cards cannot be
swapped without a test failing.

### One discrepancy that remains, permanently

`student_2_info.png` still **prints `000001`**. A database edit cannot change a
printed document. The audit therefore reports these two images as
`PANEL_DISCREPANCY` rather than `VERIFIED`:

| Asset | Value | Status |
|---|---|---|
| `Id_1.png`, `barcode_2.png` | `000065412` | `VERIFIED` |
| `Id_2.png`, `barcode_1.png` | `000065902` | `PANEL_DISCREPANCY` — mapping verified; the printed panel disagrees |
| `student_1_info.png`, `student_2_info.png` | none | `NO_BARCODE_IN_IMAGE` |

`PANEL_DISCREPANCY` is a new status, distinct from a mapping failure: the
barcode-to-student link is sound, and only the paper is wrong. If the registrar
issues a corrected panel, this status clears on its own.

### Other open items unchanged

Items 2-6 of F10 stand: the Code39/Code128 generator mismatch, no live webcam
test, only two physical cards, the unexplained external deletion, and
augmentation coverage for large rotations.

## F14. Commands added in this follow-up

```bash
python -m scripts.resolve_student_mapping      # show the conflict, change nothing
python -m scripts.resolve_student_mapping --apply --reference 65902 --confirm
python -m scripts.audit_ids                    # regenerate the verification matrix
python -m scripts.verify_scanner               # end-to-end PASS/FAIL
```

---

# Follow-up 3 — 2026-10-15: all four source images, and a real webcam

Sections 1-11 and F1-F14 above are preserved as written.

## F15. The webcam was never broken — my probe was

Follow-up F7 reported camera capture as **NOT TESTED**, and `probe_camera`
returned `read_ms 0.0` with `frame_size None`. That was read as "camera opens but
delivers no frames".

**It was wrong, and the cause was in my own benchmark.** The probe called
`get_latest_frame_with_id()` immediately after `start()`. The capture thread has
to start and the UVC device has to negotiate its format, which on this machine
takes ~670 ms. Reading in the first 100 ms therefore returns `None` on hardware
that is delivering 10 fps.

Measured, on the real device:

```
/dev/video0  "ACER HD User Facing" (built-in laptop webcam)
requested   1280 x 720 @ 30 fps
negotiated  1280 x 720 @ 10.5 fps     <- the device, not the request, sets this
capture     10.5 fps, 206 frames in 3 s
fetch       median 0.8 ms
```

`probe_camera` now waits 1.5 s before measuring and reports capture rate and
consumer fetch latency **separately** — conflating them is what produced the
misleading 0.0 ms. Two cameras enumerate (`[0, 1]`); index 1 is the second
stream node of the same UVC device, not a second camera.

**Correction to F7:** webcam capture is VERIFIED working. What remains untested
is *detection of a card on the live feed*, because no card was in front of the
lens during any run I could perform (see F19).

## F16. All four source images are now training and evaluation images

Earlier work trained on `Id_1`/`Id_2` only and used the two crops purely as
decode references. Both are now in the dataset, and the split is still leakage
free because it is keyed on the **physical card**, verified by decoded payload:

| Card | Payload | Files | Split |
|---|---|---|---|
| card_A | `000065412` | `Id_1.png` + `barcode_2.png` | **train** |
| card_B | `000065902` | `Id_2.png` + `barcode_1.png` | **val** |

`barcode_1` carries `000065902`, which is `Id_2`'s payload — so `barcode_1` is
card B and lands in validation with it. No card, and no crop of a card, is ever
on both sides of the split.

### Annotation under rotation — the fix that unblocked medium and large tilts

Previously any rotation past ~4 degrees was discarded, because propagating the
box through the rotation grows the axis-aligned extent to ~3.7x the symbol's
area, which is too loose to train on. `scripts/build_detection_dataset.py`
applies two policies in order and records which was used in the manifest:

1. **Propagate** the decoded box through the transform actually applied to the
   pixels, and keep it if it contains the symbol and is tight enough.
2. **Re-measure** when it is not: decode the variant (allowing small deskew
   attempts) and take the symbol's own extent. This is a measurement of the
   variant, verified to contain the symbol before being written.

Result: **±14 degree rotations are now included**, where ±8 was previously
discarded. Only 6 of 140 variants were discarded, all genuinely unreadable
(strong shear, an edge occluding the bars, and horizontal shifts on the tight
crops which push the symbol out of frame).

| Dataset | Sources | Images | train | val |
|---|---|---|---|---|
| `datasets/barcode/` (previous) | 2 | 44 | 22 | 22 |
| `datasets/barcode_detection/` (new) | **4** | **136** | 67 (66 pos + 1 neg) | 69 (68 pos + 1 neg) |

**136 images is not 136 samples.** They are derivatives of four photographs, two
of which are crops of the other two.

## F17. Candidate v6 — trained, compared, promoted

```
python -m scripts.build_detection_dataset --out datasets/barcode_detection
python -m scripts.train_yolo --data datasets/barcode_detection/data.yaml \
    --epochs 60 --imgsz 640 --batch 16 --device 0 --name barcode_detector_v6_full
```

| Setting | Value |
|---|---|
| Base weights | `yolov8n.pt` (local) |
| Epochs requested / completed | 60 / **60** |
| Wall time | 215.0 s |
| Device | **CUDA** (`device=0`) |
| Validation set | 68 variants + 1 negative, all from card B — never trained on |

Validation metrics: mAP50 **0.908**, mAP50-95 **0.802**, precision **0.950**,
recall **0.838**.

**That mAP is lower than the previous model's 0.995, and it is not a
regression.** The previous validation set was 21 near-identical variants of one
card; this one spans 33 conditions including ±14 degree rotations, shear,
occlusion and glare. A harder set scoring lower is the expected direction, and
0.995 on near-duplicates was never a meaningful number. Recall of 0.838 on 68
variants is likewise far more informative than 1.0 on 21.

### Measured on the four originals and the two panels, same threshold (0.35)

| Asset | v5 boxes / conf | **v6 boxes / conf** | Decoded |
|---|---|---|---|
| `Id_1.png` | 1 / 0.868 | **1 / 0.968** | `000065412` |
| `Id_2.png` | 1 / 0.885 | **1 / 0.930** | `000065902` |
| `barcode_1.png` | 2 / 0.780 | **1 / 0.993** | `000065902` |
| `barcode_2.png` | 2 / 0.854 | **1 / 0.991** | `000065412` |
| `student_1_info.png` | 0 detections | **0 detections** | - |
| `student_2_info.png` | 0 detections | **0 detections** | - |

Recall 4/4, false positives 0, false negatives 0, wrong values 0 — for both
models. v6 additionally produces a **single** box per image instead of two, and
every confidence is higher.

### Promotion was earned, not assumed

1. The gate compared v6 against the live model on held-out real assets, and
   vetoes any candidate with a false positive on a barcode-free document.
2. The full regression suite was run against the **candidate, before installing
   it**, via `YOLO_MODEL_PATH=<candidate> pytest tests/` — **223 passed**.
3. Only then was it promoted. The previous model is preserved at
   `models_yolo/backups/best_20261015_091348.pt`.

One existing test had to change, and the reason matters: `test_raw_yolo_crop_
loses_the_barcode_on_some_assets` asserted that **at least one** asset's raw
YOLO crop *must fail* to decode, i.e. it required the old defect to still
exist. v6's tighter boxes mean every crop now decodes, so the test was asserting
a defect rather than a safety property. It was rewritten to assert the property
that actually matters — that the pipeline recovers the payload regardless — and
to report the defect state as a diagnostic. No assertion was weakened.

## F18. Performance — measured on the laptop, both devices

`scripts/benchmark_devices.py --frames 15 --warmup 4`, same model, same frames:

| Stage | CPU | CUDA (RTX 2050) |
|---|---|---|
| model load | 0.332 s | 0.117 s |
| detect (median) | 216.0 ms | **18.8 ms** |
| decode (median) | 28.7 ms | 28.3 ms |
| full pipeline (median) | 227.0 ms | **18.6 ms** |
| overlay render | 24.8 ms | 23.9 ms |
| pipeline throughput | 4.41 fps | **53.79 fps** |

**11.49x on detection, with identical detections and confidences on both
devices.** Decoding is CPU-bound (zbar) and barely moves, which is why the
pipeline gain is smaller than the detect gain.

**No FPS target is claimed for live scanning**, because the camera is the
binding constraint: it negotiates **10.5 fps** regardless of the 30 fps
requested. The model could go far faster than the lens can feed it.

## F19. Live camera run — what was and was not proven

`scripts/live_camera_check.py` runs the production path on real frames. Real
hardware, 20 s:

| Measurement | Value |
|---|---|
| Frames delivered by the camera | 1280x720, 10.5 fps |
| Frames processed by the scanner | 126 in 20.1 s (**6.3 fps** end to end) |
| Capture fetch | median 0.8 ms |
| Scan pipeline latency | median **27.9 ms** |
| Barcode detections | **0** |
| States | `searching` on all 126 frames |

**0 detections because no card was in front of the lens.** The script reports
this as `NO BARCODE SEEN` and states explicitly that it is **not a pass**.
Detection on a live feed is only proven when a target is actually in frame, and
I cannot place a card in front of a laptop webcam from here.

To close it, run this while holding an ID card inside the yellow guide:

```bash
venv/bin/python -m scripts.live_camera_check --seconds 60 --save-frames reports/live_frames
```

It prints, per run, frames processed, latency percentiles, how many frames
contained a detection, every decoded payload, and every verified student, and
writes `reports/live_camera_check.json`. It exits non-zero unless a real card
was decoded **and** matched to a verified student.

## F20. Tests

| Suite | Result |
|---|---|
| `pytest tests/ -q` | **223 passed, 0 failed** |
| of which, against the candidate *before* promotion | 223 passed |
| `scripts/verify_scanner.py` | **21 PASS / 0 FAIL** |
| `scripts/live_camera_check.py` | ran on real hardware; **NO BARCODE SEEN** (no card presented) |

New file `tests/test_detection_and_tracking.py` covers all four source images
explicitly:

- the four source digests are unchanged;
- each of the four decodes **directly** to its expected value and symbology;
- YOLO localises the barcode on each of the four, at **IoU >= 0.85** against the
  symbol zbar actually reads;
- **the box is the barcode, not the card**: on both cards it covers < 30% of the
  image and starts below 55% of the height, so it cannot be sitting on the photo
  or the name; and on the tight crops it covers > 20%, so it is not a fragment;
- neither information panel produces a detection;
- the detection crop decodes to the same value as the direct read;
- tracking: monotonic box motion under card movement, no inheritance of position
  by an unrelated distant detection, box dropped after the grace period, and
  exactly one verification raised for a card held in frame;
- camera: invalid index fails cleanly, `stop()` is idempotent and leaves no
  capture thread alive, restart works, and a real camera delivers advancing
  frame ids (marked `slow`, skipped without hardware);
- identity: an empty lookup shows nobody, and the two cards can never be
  cross-matched.

## F21. Still open

1. **Live detection on the webcam is unverified** — needs a physical card (F19).
2. **Code39 vs Code128** — the generator still emits CODE128 with a
   `WIT:STUDENT:` prefix while the cards are CODE39 with a bare reference.
3. **Four images, two physical cards.** Overfitting risk is real: v6 could be
   learning the WIT card template. Nothing here establishes generalisation to
   other institutions or card designs.
4. **External file deletion (F1)** still unexplained.
5. Rotation is now trained to ±14 degrees but *verified* only to about ±11; a
   tilted card between those limits is a known gap.
