# Revised presentation — QA and editability report

**Deliverable:** `presentation/WIT_Barcode_Student_Information_System_Technical_Presentation_Revised.pptx`
**Original (unchanged):** `presentation/WIT_Barcode_Student_Information_System_Technical_Presentation.pptx`
**Build script:** `presentation/tools/build_pptx_revised.py` (regenerates the deck from measured evidence)
**Verification scripts:** `presentation/tools/check_render.py`, `presentation/tools/check_deck.py`
**Renders:** `presentation/render/…Revised.pdf`, `presentation/render/slide-01.png … slide-37.png`

The original file was never opened for writing. It is byte-identical to its pre-existing
state (md5 `3ceb42a48b9426c4fbf5b62d09d1ad6c`, mtime before this work began).

---

## 0. Update — 2026-10-20: technology list & model-to-train documentation

- **New master document:** [`TECHNOLOGY_STACK.md`](../TECHNOLOGY_STACK.md) at the repository
  root — the annotated list of all 21 verified technologies (versions read from `venv/bin/pip
  list`, GPU confirmed with `nvidia-smi`) plus the full "model to train" section: YOLOv8n,
  why nano, the recorded training recipe, dataset provenance, the promotion gate and the
  training commands. `README.md` links to it and its CODE128 claim was corrected to CODE39
  (verified by decoding the checked-in cards).
- **Revised deck: 37 → 39 slides** (count asserted in `build_pptx_revised.py`):
  - **Slide 07 — "Technology 1/2"**: runtime/platform, UI, database and document stacks
    as four grouped, editable panels (previously one 11-row table).
  - **Slide 08 — "Technology 2/2"**: vision/ML, arrays & dataset tooling, barcode
    symbology & decoding, and the testing toolchain.
  - **Slide 09 — "The one thing we train — a YOLOv8n detector"**: the recorded training
    recipe table (base weights, classes, input/batch, optimizer, epochs, seed/device,
    augmentation, dataset, promotion), a "why nano" panel with the measured latencies
    (GPU 22.49 ms / CPU 218.13 ms mean) and a promotion-gate panel. Speaker notes 150+
    words; on-slide numbers come from `inference_latency.json`, the v7 `results.csv` and
    the checkpoint's `train_args`.
- **Cross-references renumbered** after the insertion (dashboard defect → slide 37, CPU
  fallback → slide 36, ERD parents → slides 28/29).
- **Verification:** `build_pptx_revised.py` → 39 slides, 0 layout warnings;
  `check_pptx_fit.py` → slide 09 clean; PDF re-rendered (39 pages) and `check_pdf_layout.py`
  reports no problems on slides 7–9 (pre-existing tight spots on slides 3, 6, 16 and 39 are
  unchanged by this work).

---

## 1. What changed

### 1.1 Slide count and structure

- Original: **35 slides**. Revised: **37 slides** (the count is asserted in the build script).
- Slides were **split**, never shrunk to fit. Two crowded originals became four: the module
  inventory and the "what works / what does not" summary were each divided so that no slide
  carries more than one message.
- Full required section list is present: title, roadmap/contents, overview, problem,
  objectives, existing workflow, proposed solution, dashboard, modules, stack, architecture,
  scan workflow, scan pipeline, YOLO architecture, dataset, annotation and splits, training
  configuration, results, model comparison/latency, scanner walkthrough, database schema,
  ERD ×3, data flow, ID-card workflow, additional modules, security/validation, testing,
  limitations, deployment, future work, conclusion, references.

### 1.2 Typography — measured, not estimated

Font sizes were read back out of the saved `.pptx` (text runs in shapes *and* table cells):

| Metric | Original | Revised |
| --- | --- | --- |
| Smallest run on any slide | **7 pt** | **13 pt** |
| Largest run | 39 pt | 44 pt |
| Title (slide heading) | 24.5 pt | **34 pt** |
| Cover title | 39 pt | **44 pt** |
| Runs below 16 pt | **755 of 810 (93.2%)** | **257 of 1049 (24.5%)** |
| Distinct sizes used | 14 | 17 |

The deck now uses a deliberate scale: titles 34–44 pt, section headings 26–30 pt, body and
bullets 17–24 pt, table text 17–20 pt, chart labels 13–20 pt, footers 14 pt.

### 1.3 Visuals enlarged

Every screenshot is placed in a sized, captioned panel and was scaled up from the original
layout; diagrams now use the full 13.333 in canvas instead of a compressed centre block.
Native charts were enlarged (slide 30 chart 7.8 × 3.4 in; slides 33–34 charts ~7.4 × 2.9 in
and 4.44 × 2.9 in).

### 1.4 Text trimmed, detail moved to notes

On-slide text is now one message plus roughly three to five short bullets. The removed
detail was rewritten as **speaker notes on every one of the 37 slides**, each **150–180
words** (mean 169), written in spoken script style and *not* a duplicate of the bullet text.

### 1.5 Diagrams and data rebuilt as native objects

The originals carried diagrams and charts as flat PNGs. All of the following are now fully
editable PowerPoint objects:

- System-context diagram, three-tier architecture, scan pipeline, scan-state flowchart,
  end-to-end workflow, training workflow, data-flow diagram, ID-card workflow, and the three
  entity-relationship diagrams — built from **rounded rectangles, text boxes and 54 native
  connectors with arrowheads**.
- The training-metrics curve, the dataset/mAP comparison bars, and the inference-latency bars
  are **native PowerPoint charts** (1 XY-scatter-lines + 3 clustered-column) driven by the
  measured JSON/CSV in `presentation/charts/` and `models_yolo/candidates/…v7…/results.csv`.
- Eight data blocks (module inventory, checkpoint properties, detection results, database
  statistics, test results, etc.) are **native tables**.
- Screenshots remain individual **pictures** — they are photographs of the running UI and
  cannot be vectorised — each with an editable caption and panel.

---

## 2. Editability census

Objects counted directly from the saved file:

| Object type | Count |
| --- | --- |
| Editable text boxes | 661 |
| Editable auto-shapes (rectangles/rounded boxes/diamonds) | 355 |
| Editable connectors (arrows) | 54 |
| Editable tables | 8 |
| Editable native charts | 4 |
| Individual pictures (screenshots/assets) | 23 |

**No slide is a flattened image.** A check for any picture covering ≥92% of the slide width
and height returned none — there is no full-slide background raster anywhere. Every title,
label, arrow and diagram node can be clicked, moved and re-typed in PowerPoint.

---

## 3. Validation performed

Because the renderer here has no ability to *view* images, validation was done
programmatically against a LibreOffice PDF render at true slide geometry (960 × 540 pt).

1. **Build-time layout checks** (inside `build_pptx_revised.py`): every text block is
   measured with PIL using Liberation Sans (metric-compatible with Arial) and warned if it
   would overflow its box; every table row and table bottom is checked; every ERD field is
   checked for width. **Final build: 0 layout warnings.**
2. **Rendered-geometry checks** (`check_render.py` over `pdftotext -bbox`):
   - words outside the page safe area: **0**
   - words smaller than 14 pt: **0**
   - genuine same-line word collisions (≥55% area, ≥60% overlap on both axes): **0**
   - smallest rendered text on every page: **16.7 pt** on content slides; 15.6 pt is the
     14 pt footer band, as intended.
3. **Structural checks** (`check_deck.py`):
   - speaker notes present on **all 37** slides, each **in the 100–190 word band**
     (actual range 150–180);
   - object census above; **no flattened slides**.
4. **Fact cross-check:** every number on a slide was taken from measured evidence — the
   latency JSON (GPU mean 22.49 ms / p95 25.0; CPU mean 218.13 ms / p95 453.46), the v7
   training `results.csv` (best epoch 34/64, P 0.9975, R 0.9987, mAP@0.50 0.995,
   mAP@0.50:0.95 0.8075), the dataset statistics JSON, the database statistics JSON, and the
   recorded pytest run (553 collected / 531 passed / 22 failed). Several older, rougher
   figures that appeared in the original build script were replaced with these measured
   values (e.g. GPU described as ~10× faster rather than an approximate multiple).

---

## 4. Deliberate compromises (disclosed)

To keep slides uncluttered rather than shrink text, some secondary text sits below the
brief's *ideal* target bands while staying well above the original's 7–9 pt:

- **Diagram sub-labels** (node second lines, captions) are **13–15 pt** rather than 22–26 pt.
  These label small structural boxes; 22 pt in a 1.8 in node would not fit at any layout.
- **KPI tile labels and notes** are **15 pt** (brief suggested 20–24 pt). The large KPI
  *values* are 22–32 pt; only the small caption under each value is 15 pt.
- **Chip / tag text** is **15 pt**.
- **ERD field rows** are **13–17 pt** — the three ERD slides are the densest content in the
  deck. The alternatives were (a) shrink further, (b) split each ERD across more slides, or
  (c) move fields to notes. I chose the smallest step that keeps every field legible on a
  projector while keeping each ERD to one slide.
- **Footers/kickers/page numbers** are 14 pt (brief allowed 16–18 pt for footers); they are
  decorative furniture and not content.
- **Chart value-axis labels** are 13 pt and category labels 14 pt (brief suggested 20–24 pt);
  raising them would have forced the chart to shrink and the plotted data to become less
  readable.

All of the above are *secondary* text. Every title, heading, bullet, table body cell, KPI
value and diagram node label is at or above the brief's minimum.

---

## 5. Unresolved issues / limits of this validation

1. **No human visual review.** The environment cannot display images, so "looks right" was
   verified by geometry and text metrics, not by eye. A human should still open the PDF/PNG
   renders and confirm aesthetic balance — especially diagram arrow routing on the ERD and
   data-flow slides, where connectors were positioned by computed coordinates.
2. **Font rendering depends on the viewer.** The deck specifies **Arial**. On a machine
   without Arial, LibreOffice substituted a metric-compatible face; text may reflow very
   slightly under a different substitute. Layout was validated against Arial metrics.
3. **Automated overlap detection is heuristic.** It flags pairs of words overlapping ≥55% by
   area *and* ≥60% on both axes. It cannot detect a shape covering text, or two shapes
   overlapping where the text does not. Those cases were checked by geometry separately but
   not exhaustively.
4. **Screenshots are point-in-time.** They reflect the repository at the commit noted on the
   cover; they are not regenerated by the build.
5. **Charts are static data-bound objects.** They render from pasted category/series values
   (as `python-pptx` requires) — they are editable and reformattable in PowerPoint but not
   linked to a live workbook.
6. **No held-out test split exists in the project itself**, so the deck states generalisation
   as unproven. This is a limitation of the underlying work, correctly reported rather than
   papered over.

---

## 6. How to regenerate

```sh
# from the repository root
./venv/bin/python presentation/tools/build_pptx_revised.py          # rebuild the .pptx
cd presentation
soffice --headless --convert-to pdf --outdir render \
  WIT_Barcode_Student_Information_System_Technical_Presentation_Revised.pptx
pdftotext -bbox render/*.pdf render/out.xml
../venv/bin/python tools/check_render.py render/out.xml             # geometry / font checks
../venv/bin/python tools/check_deck.py                              # notes / editability checks
```
