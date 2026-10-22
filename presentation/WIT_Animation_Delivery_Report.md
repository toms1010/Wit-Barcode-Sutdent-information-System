# Presentation Animations & Transitions — Design & Delivery Report

**Deck:** `presentation/WIT_Barcode_Student_Information_System_Technical_Presentation_Revised.pptx`
**Slides:** 39 · **Transitions:** 39/39 · **Animation effects:** 1,016 · **Audience clicks:** ≤ 4 per slide
**Tool:** `presentation/tools/add_animations.py` (post-processes the deck produced by `build_pptx_revised.py`)

---

## 1. Why a post-processing stage

`python-pptx` 1.0.2 exposes **no API** for transitions or animations. Rather than fake
them with static slides, `add_animations.py` writes the real DrawingML OOXML directly
into each slide part:

| XML written | Purpose |
|---|---|
| `<p:transition>` | Slide transitions (fade / cover / push) with explicit durations |
| `<p:timing>` → `<p:tnLst>` → `<p:seq>` → `<p:cTn nodeType="mainSeq">` | The animation sequence |
| `<p:cTn nodeType="clickEffect">` | One audience click ("beat") |
| `<p:animEffect>` / `<p:set>` / `<p:animScale>` | Fade, Appear and Zoom behaviours |
| `<p:spTgt spid="…">` | Binds each effect to a real shape id |
| `<p:bldLst>` / `<p:bldP build="p">` | Paragraph-level bullet build |

These are the same elements Microsoft PowerPoint itself writes, so they play in
Slide Show and remain fully editable in the **Animation Pane**. Nothing is flattened.

---

## 2. Transitions

| Slide | Section | Effect | Duration |
|---|---|---|---|
| 1 | Cover | Fade | 600 ms |
| 3 | 01 · Executive summary | Cover | 600 ms |
| 6 | 02 · System design | Push | 600 ms |
| 12 | 03 · Product walkthrough | Cover | 600 ms |
| 27 | 04 · Data & model | Push | 600 ms |
| 37 | 05 · Verification & roadmap | Cover | 600 ms |
| all others | continuous content | Fade | 500 ms |

Durations are held in the 0.5–0.6 s band, so movement never outlasts the speaker.

---

## 3. Animation roles

Each shape is classified by its deck name (and, for the anonymous bullet boxes, by
paragraph count), then given one consistent effect:

| Role | Effect | Duration | Applies to |
|---|---|---|---|
| `title` | **Fade** | 500 ms | `SlideTitle`, `Kicker`, `DeckTitle` |
| `subtitle` | **Fade** | 500 ms | `Subtitle`, `Caption`, `SectionTag`, panel sub-captions |
| `bullet` | **Appear**, one paragraph at a time | 300 ms | body bullet lists |
| `node` | **Wipe** (left → right) | 450 ms | architecture tiers, flow nodes, arrows, ERD cards, training stages |
| `metric` | **Zoom** (fade + scale) | 450 ms | KPI values, config tiles |
| `image` | **Zoom** (fade + scale) | 450 ms | screenshots, chart frames |
| `chip` | **Appear** | 300 ms | layer chips, agenda numbers |
| `fade` | **Fade** | 500 ms | remainder |

Effects never obscure content: every reveal is an *entrance*, text stays full-opacity
once shown, and no exit, bounce, spin or flash effect is used anywhere.

### Sequencing

A **beat** is one audience click. Shapes inside a beat start together with a 120 ms
stagger, so a group arrives as a readable cascade instead of a simultaneous pop.
Reveal order follows the reading order — title → subtitle → bullets → nodes → chips →
metrics → images. Each slide is capped at **4 beats** (`MAX_BEATS`), which keeps a
long slide to a handful of clicks rather than dozens.

---

## 4. Verification performed

| Check | Result |
|---|---|
| Slide parts well-formed | 39 / 39 |
| Slides with a transition | 39 / 39 |
| Slides with a timing tree | 39 / 39 |
| Animation targets resolving to a real shape | 1,016 / 1,016 — **0 dangling ids** |
| Empty click beats | **0** |
| Max click groups on any slide | **4** |
| Paragraph-level bullet builds | 44 |
| Zip integrity (`unzip -t`) | no errors |
| Static render after animation (39 pages) | 0 out-of-bounds words · 0 words < 14 pt · 0 overlaps |
| Speaker notes in range | 39 / 39 slides (100–190 words) |
| Full-slide images (flattening) | none |

Editability census of the delivered deck: **1,123 text boxes · 376 auto-shapes ·
54 connectors · 8 native tables · 4 native charts · 23 pictures.**

---

## 5. Regenerating

```bash
cd "WIT Barcode Student Information System"

# 1. build the deck
./venv/bin/python presentation/tools/build_pptx_revised.py

# 2. add transitions + animations  (always run AFTER the build)
./venv/bin/python presentation/tools/add_animations.py
```

`add_animations.py` is **idempotent**: it strips any pre-existing `<p:transition>` and
`<p:timing>` before writing, so it can be re-run safely. It must run *after*
`build_pptx_revised.py`, because rebuilding the deck replaces the file and discards
the animations.

---

## 6. Adjusting in Microsoft PowerPoint

Open the deck and use the native panes — everything is editable:

* **Animation Pane** — `Animations ▸ Animation Pane`. Reorder, retime or change any
  effect. Each beat maps to one click; effects inside a beat are grouped.
* **Timing** — each effect is an *On Click* entrance. To make a beat automatic after
  the previous one, select it in the Animation Pane and set **Start: After Previous**.
* **Durations** — edit the timing constants at the top of `add_animations.py`
  (`FADE_DUR`, `APPEAR_DUR`, `WIPE_DUR`, `ZOOM_DUR`, `STAGGER`) and re-run step 2.
* **Beats per slide** — change `MAX_BEATS` (lower = fewer clicks, higher = finer
  reveals).
* **Transitions** — `Transitions ▸ Effect Options`. Section effects are listed in the
  `SECTIONS` dict.
* **Morph** — this deck uses Fade/Cover/Push. If you want true **Morph** between
  related slides, select the slide(s) in PowerPoint and choose
  `Transitions ▸ Morph`; Morph cannot be authored reliably outside PowerPoint because
  it depends on matching shape identities across slides.
* **Reduced motion** — to disable animation for a screen-reader or low-motion
  audience, select all slides (`Ctrl+A` in the slide sorter) and set
  `Transitions ▸ None`, and clear effects via the Animation Pane.

---

## 7. Known limitations

1. **Morph transitions are not applied.** PowerPoint's Morph is a match-and-interpolate
   effect evaluated at playback; it is not reliably expressible as authored OOXML, so
   it is called out here with instructions instead of being faked.
2. **Effects use the classic filter names** (`fade`, `wipe(left)`) rather than the
   newer Office 365 effect set (`p14:` extensions). These are universally supported and
   render identically in PowerPoint, LibreOffice and Keynote.
3. **Verification is structural, not visual.** Every effect was validated by
   re-parsing the OOXML and resolving targets against real shape ids, plus a full
   static re-render. The *motion* itself was not watched frame-by-frame — that final
   look is a human check in Slide Show mode.
4. **The original deck was not modified.** It was already at a different content
   revision before this work (34 slides, mtime 19:12); nothing in this task writes to
   it. A safety copy is at `/tmp/opencode/ORIGINAL_backup_2202.pptx`.
