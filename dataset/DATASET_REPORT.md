# DATASET_REPORT.md

**Project:** WIT Barcode Student Information System
**Generated:** 2026-10-15
**Generator:** `scripts/build_dataset.py`
**Output:** `datasets/barcode/` (separate from `assets/`, which is never written to)

Reproduce with:

```bash
source venv/bin/activate
python -m scripts.build_dataset --out datasets/barcode
```

The build is deterministic and idempotent: it derives every annotation from the
pixels of the source image, so re-running it produces byte-identical labels.

---

## 1. Source assets

Only these six files are permitted as source data. MD5 digests were taken before
any work began and again after the dataset was built and the model trained; they
are identical.

| Role | Path | MD5 | Size (px) | Dims |
|---|---|---|---|---|
| ID card | `assets/images/Id_1.png` | `15de778f2c61c9924b0ed0432d829853` | 585057 B | 590x1004 |
| ID card | `assets/images/Id_2.png` | `38e37019c04f9741e2e064e8262cc1e8` | 615253 B | 590x1004 |
| Barcode crop | `assets/barcodes/barcode_1.png` | `9adeb2f3248ca80744232465139e5bd9` | 11645 B | 562x202 |
| Barcode crop | `assets/barcodes/barcode_2.png` | `54f238f873fa4c697c4f2206b08717da` | 10399 B | 608x187 |
| Info panel | `assets/images/info/student_1_info.png` | `6f6160d4e5c72fbbdd18d34e7722823a` | 469028 B | 1414x2000 |
| Info panel | `assets/images/info/student_2_info.png` | `57c4262c9838999937d71d8e3ce2b834` | 530802 B | 1414x2000 |

**Path change during this work.** The two info panels were moved from
`assets/images/` into `assets/images/info/` partway through the session. The files
are byte-identical (MD5 unchanged). All code now resolves them through
`settings.info_image(...)`, which accepts either layout, so the build works on
both. Nothing was moved or renamed by this work.

## 2. Eligibility decision

Every image was opened, decoded across six preprocessing variants (colour,
greyscale, Otsu, adaptive, CLAHE, sharpen), and additionally inspected visually.

| Asset | Contains a real, decodable barcode? | Eligible for YOLO detection training? | Reason |
|---|---|---|---|
| `Id_1.png` | **Yes** - CODE39 `000065412` | **Yes** (positive) | Barcode visible at the card foot; decodes from the full image |
| `Id_2.png` | **Yes** - CODE39 `000065902` | **Yes** (positive) | Barcode visible at the card foot; decodes from the full image |
| `student_1_info.png` | **No** - zero symbols in 6 variants, confirmed visually | **No** - included as a **negative** | Certificate/assessment panel with no barcode whatsoever |
| `student_2_info.png` | **No** - zero symbols in 6 variants, confirmed visually | **No** - included as a **negative** | Certificate/assessment panel with no barcode whatsoever |
| `barcode_1.png` | **Yes** - CODE39 `000065902` | **No** - decode reference only | Tight crop of the *same physical card* as `Id_2.png`; training on it would leak the validation card |
| `barcode_2.png` | **Yes** - CODE39 `000065412` | **No** - decode reference only | Tight crop of the *same physical card* as `Id_1.png`; training on it would leak the training card |

Two findings that a filename-based reading would get wrong:

1. **`barcode_1.png` is a crop of `Id_2.png`, not `Id_1.png`.** Its payload is
   `000065902`, which is `Id_2.png`'s payload. The numeric suffixes do not
   correspond across the two folders.
2. **The info panels are negatives, not training positives.** No annotation was
   invented for them.

## 3. Annotation method

Annotations are **derived from a real decode of the image being annotated**, not
hand-typed and not guessed:

- `decode_symbols()` runs the project's `BarcodeDecoderService` variant ensemble
  over the image and collects every distinct payload with its pixel rectangle.
- `derive_box()` converts that rectangle to YOLO normalised centre/size form.
- A positive is only produced if **exactly one** non-degenerate symbol decodes.
  Zero symbols, or more than one, is recorded as a build problem and the image is
  not labelled positive.
- A negative is only produced if **zero** symbols decode from it. If a panel ever
  started decoding, the build would fail rather than poison training.

The full evidence for each box (decoded payload, symbology, pixel rect, normalised
rect, image size) is stored in `datasets/barcode/build_manifest.json`.

Zero-area rectangles returned by zbar on variants it cannot read are discarded
(`MIN_SYMBOL_SIDE_PX` in `services/barcode_decoder.py`); before that fix they could
have become annotations.

## 4. Resulting dataset

```
datasets/barcode/
├── data.yaml
├── build_manifest.json
├── images/train/card_A_Id_1.png              (positive)
├── images/train/negative_student_1_info.png  (negative, empty label)
├── images/val/card_B_Id_2.png                (positive)
├── images/val/negative_student_2_info.png    (negative, empty label)
├── labels/train/card_A_Id_1.txt              0 0.500847 0.844622 0.889831 0.111554
├── labels/train/negative_student_1_info.txt  (empty)
├── labels/val/card_B_Id_2.txt                0 0.507627 0.850100 0.811864 0.120518
└── labels/val/negative_student_2_info.txt    (empty)
```

`data.yaml`:

```yaml
path: /home/tommyp/Desktop/Desktop-app/WIT Barcode Student Information System/datasets/barcode
train: images/train
val: images/val
test: images/val
names:
  0: barcode
nc: 1
```

### Split rationale

| Split | Positive | Negative | Independent physical cards |
|---|---|---|---|
| train | 1 (`Id_1.png`) | 1 (`student_1_info.png`) | 1 |
| val | 1 (`Id_2.png`) | 1 (`student_2_info.png`) | 1 |

The split is assigned **per physical card, not per file**. `Id_1.png` is the only
card trained on and `Id_2.png` is the only card validated on, so no card is ever
both sides of the split. Exact-duplicate detection across the built dataset
reports `none`, and no image is copied or renamed to inflate the count.

### Annotation verification

`tests/test_dataset_build.py` re-derives each built label and checks it against
the live zbar extent, requiring **IoU >= 0.85**. Result: 22/22 tests pass, 2
positives verified.

## 5. Limitations - read this before trusting any metric

- **One positive image per split.** The training signal for the barcode *class*
  is a single photograph of a single card. That is not enough to learn a
  generalisable detector, and no metric computed on it should be quoted as
  evidence of accuracy.
- **The two cards are near-identical by design.** Same template, same orange
  background, same barcode position, same lighting setup. Validation therefore
  measures "does the model still fire on a slightly different card of the same
  design", not "does it generalise to unseen cards".
- **Two independent barcodes exist in the whole project.** Any accuracy figure
  rests on `000065412` and `000065902` plus two documents.
- **No negatives of the real failure kind.** The negatives are full-page
  documents. Real failures are usually a partial card at an angle, glare, or a
  phone screen - none of which are represented.
- **`test` points at `images/val`.** There is no third split, because there is no
  fifth image to build one from.

## 6. Next steps that would actually help

1. Photograph more cards (10+ different students) with the barcode in varied
   positions, angles and lighting. This is the single highest-value addition.
2. Capture real camera frames of the two known cards for negative/robustness
   testing, and keep several misaligned or unlit frames as negatives.
3. Only then re-measure. Metrics from the current dataset are not informative.

---

# Follow-up — 2026-10-15: augmented dataset and mandatory asset audit

## 7. Mandatory asset audit (every supplied file)

Every supplied image was opened, decoded across six preprocessing variants, and
inspected visually. No file is skipped, and every exclusion has a recorded
reason.

| Source image | Inspected | YOLO training status | Other purpose |
|---|---|---|---|
| `barcode_1.png` | Yes — 562x202, CODE39 decodes to `000065902`, no detection needed | **Excluded**: a tight crop of the *same physical card* as `Id_2.png`; training on it would leak the validation card | Barcode decoding test reference; full-card localisation test |
| `barcode_2.png` | Yes — 608x187, CODE39 decodes to `000065412` | **Excluded**: tight crop of the same physical card as `Id_1.png` | Barcode decoding test reference; full-card localisation test |
| `Id_1.png` | Yes — 590x1004, CODE39 `000065412` at rect (33, 792, 525, 112) | **Included (train)**, box derived from the decode | ID localisation; pipeline verification |
| `Id_2.png` | Yes — 590x1004, CODE39 `000065902` at rect (60, 792, 479, 122) | **Included (val)**, box derived from the decode | ID localisation; pipeline verification |
| `student_1_info.png` | Yes — 1414x2000, **zero** symbols in 6 variants, visually confirmed to contain no barcode | **Included as a NEGATIVE** (empty label). No barcode exists, so there is nothing to annotate | Student profile display; negative detection sample |
| `student_2_info.png` | Yes — 1414x2000, **zero** symbols in 6 variants, visually confirmed | **Included as a NEGATIVE** (empty label) | Student profile display; negative detection sample |

MD5 digests are identical to the start of the project; the six originals are
unmodified.

**Note on filenames:** `barcode_1.png` is a crop of **`Id_2.png`** and
`barcode_2.png` of **`Id_1.png`**, established by comparing decoded payloads, not
by name. This is now a regression test.

## 8. Augmented dataset (`datasets/barcode_aug/`)

Generated by `scripts/augment_dataset.py` from the two real cards. **No barcode
content is synthesised** — every variant is the same photograph under different
exposure, contrast, angle, scale or noise.

| | Baseline | Augmented |
|---|---|---|
| **Unique original images with a barcode** | **2** | **2** |
| Generated images | 4 | 44 |
| train | 1 positive + 1 negative | 21 positive + 1 negative |
| val | 1 positive + 1 negative | 21 positive + 1 negative |
| Exact duplicates | none | none |

**44 images is not 44 samples.** They are derivatives of two photographs and
must never be counted as independent real-world examples or used to claim a
generalisation improvement.

### Variations applied

Brightness (0.60x, 0.75x, 1.30x, 1.55x), contrast (0.55x, 1.45x), gamma (0.75,
1.30), directional shading, blur (sigma 1.0), noise (sigma 10, 20), JPEG q40,
rotation (+/-4 degrees), scale (0.65x, 0.80x, 1.25x), translation, and mild
perspective (5%).

### Annotation correctness

Each box is produced by applying the **same geometry** that was applied to the
pixels: the operator's matrix is recovered by probing the transform it actually
produced, and the four box *corners* are transformed (not just the centre, which
would leave the box no longer covering the symbol) and clipped to the canvas.

Every generated variant is then verified before it is kept. A variant is
discarded unless it:

1. still decodes,
2. decodes to the **original's** value (a different value is a false read, never
   a pass), and
3. has a box that contains the symbol actually found in the variant.

The acceptance rule is *containment plus tightness*, not raw IoU: zbar reports a
tighter rect for a tilted symbol than any detector will draw, so IoU alone
rejects correct annotations (measured 0.59 for a 4-degree rotation that
contained the symbol perfectly). A box may be at most 2.5x the symbol's area.

**Three variants per card were discarded for failing that check** — this is the
gate working, not a bug:

| Discarded | Why |
|---|---|
| `rot_cw_08` / `rot_ccw_08` | axis-aligned box grows to 3.70x the symbol area — too loose to teach |
| `perspective_05` | box does not contain the symbol (coverage 0.96) |

### Leakage control

Every variant of `Id_1.png` is in train and every variant of `Id_2.png` is in
val, keyed by source image rather than by file. No card, and no derivative of a
card, appears on both sides of the boundary.

## 9. What the augmented data actually bought

Measured, not assumed: on the same six assets and the same four composited
scanning offsets, the augmented model (v5) reports **zero** detections on both
barcode-free documents where the previous model fired one at 0.571, and emits 2
boxes per barcode crop where the previous model emitted 13. Robustness across
27 transformations improved from 137 to 145 passing rows.

It did **not** fix everything: heavily blurred, heavily tilted, far and
perspective-distorted variants still fail to decode, and a document darkened to
25% exposure still draws a weak box (0.459). No false student is produced in any
of those cases, because a false identity needs a successful decode *and* a
database match.

---

# Follow-up 3 — 2026-10-15: all four source images in one dataset

## 10. Asset audit (all four training images + the two panels)

| Source image | Inspected | YOLO training status | Other purpose |
|---|---|---|---|
| `Id_1.png` | Yes — 590x1004, CODE39 `000065412` at rect (33, 792, 525, 112) | **Included (train)**, card_A | ID localisation; pipeline verification |
| `Id_2.png` | Yes — 590x1004, CODE39 `000065902` at rect (60, 792, 479, 122) | **Included (val)**, card_B | ID localisation; pipeline verification |
| `barcode_2.png` | Yes — 608x187, CODE39 `000065412` | **Included (train)**, card_A — it is a crop of `Id_1` | Decoding reference |
| `barcode_1.png` | Yes — 562x202, CODE39 `000065902` | **Included (val)**, card_B — it is a crop of `Id_2` | Decoding reference |
| `student_1_info.png` | Yes — 1414x2000, zero symbols in 6 variants | **Negative** (empty label); no barcode exists | Profile display |
| `student_2_info.png` | Yes — 1414x2000, zero symbols in 6 variants | **Negative** (empty label); no barcode exists | Profile display |

All four previously-excluded barcode crops are now training images. They were
excluded earlier only because the dataset used two source images; with all four
available, grouping them by their **physical card** keeps the split honest:

```
card_A  payload 000065412  ->  Id_1.png + barcode_2.png  ->  train
card_B  payload 000065902  ->  Id_2.png + barcode_1.png  ->  val
```

Verified by decoded payload, never by filename. MD5s are unchanged from the start
of the project.

## 11. `datasets/barcode_detection/`

Built by `scripts/build_detection_dataset.py`, seed `20261015`.

| | |
|---|---|
| Unique source images with a barcode | **4** (2 physical cards) |
| Generated images | **136** |
| train | 67 (66 positive + 1 negative) |
| val | 69 (68 positive + 1 negative) |
| Exact duplicates | none |
| Discarded variants | 6 of 140 |
| Synthetic barcode content | **none** |

### Conditions simulated (33 per source)

*Exposure and lighting:* dim (0.55x), dimmer (0.30x), bright (1.45x), hot
(1.90x), gamma down/up, contrast low/high, soft and hard shadow, soft glare.
*Optics:* soft and hard blur, light and heavy noise, JPEG q35.
*Realistic combinations:* dark+noisy, bright+glare, indoor (dim, shadowed,
blurred, noisy) — because a real camera does not underexpose *or* add noise, it
does both.
*Geometry:* ±4°, ±8°, **±14°** rotation, soft and strong shear, 4% and 9%
perspective, 0.70x and 1.30x scale, left and up shifts, edge and corner
occlusion.

### Bounding-box handling under rotation

A rotated barcode's axis-aligned box grows with the angle; by 8° it is ~3.7x the
symbol's area, too loose to train on. Two policies, recorded per variant in
`build_manifest.json`:

1. **Propagate** the decoded box through the same transform applied to the
   pixels, accept if it contains the symbol and is within 2.6x its area;
2. **Re-measure** otherwise — decode the variant (allowing small deskew) and
   take the symbol's own extent, verified to contain the symbol.

This is what allows ±14° rotations to be kept where ±8° was previously
discarded. Every variant must also still decode **to its source image's own
value**; six were discarded for failing that (strong shear, an occluding band
across the bars, and shifts that push the symbol out of a tight crop).

### Honest limits

- **136 images from 4 photographs, 2 of which are crops of the other 2.**
  Independent real-world samples: **two**.
- The model can learn the WIT card template, its orange background and its fixed
  barcode position. Nothing here demonstrates generalisation to another
  institution, another card design, or a different print process.
- Validation mAP50 fell from 0.995 (21 near-identical variants) to 0.908 (68
  variants spanning ±14°, shear, occlusion and glare). That is a **harder, more
  honest** validation set, not a regression.
- Tilt is trained to ±14° but independently verified only to about ±11°.
