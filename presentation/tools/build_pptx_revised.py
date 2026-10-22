#!/usr/bin/env python3
"""Build the revised, fully editable WIT Barcode SIS technical deck.

Design goals (per the revision brief):
  * Projector-readable type: titles 34 pt, body 20-22 pt, diagram labels
    16-22 pt, tables 18-20 pt, footers 14 pt.
  * One main idea per slide; wording reduced, detail moved to speaker notes.
  * Diagrams (architecture, context, pipeline, flowchart, ERD, data flow,
    training) rebuilt as native PowerPoint shapes, connectors and text -
    individually selectable and editable.
  * Charts are native PowerPoint charts driven by the measured JSON/CSV
    evidence in presentation/charts and models_yolo/candidates.
  * Every slide carries a 100-180 word speaker note written as a script.

Every number comes from the repository: live database counts, results.csv,
evaluation reports, the pytest log, or captured application screenshots.
Nothing is invented.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from PIL import Image, ImageFont
from pptx import Presentation
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[2]
PRES = ROOT / "presentation"
SHOTS = PRES / "screenshots"
CHARTS = PRES / "charts"
CANDIDATES = ROOT / "models_yolo" / "candidates"
OUT = PRES / "WIT_Barcode_Student_Information_System_Technical_Presentation_Revised.pptx"

W, H = 13.333, 7.5
M = 0.62
CW = W - 2 * M                      # 12.09 in content width
TOP = 1.86                          # first usable content y
BOT = 6.96                          # last usable content y

INK = RGBColor(0x0F, 0x17, 0x2A)
SLATE = RGBColor(0x47, 0x55, 0x69)
MUTED = RGBColor(0x64, 0x74, 0x8B)
ORANGE = RGBColor(0xF9, 0x73, 0x16)
DARK = RGBColor(0xAD, 0x4D, 0x05)
GOLD = RGBColor(0xFF, 0xD2, 0x4D)
CREAM = RGBColor(0xFF, 0xF7, 0xED)
PAPER = RGBColor(0xFF, 0xFF, 0xFF)
BORDER = RGBColor(0xE2, 0xE8, 0xF0)
SOFT = RGBColor(0xF8, 0xFA, 0xFC)
GREEN = RGBColor(0x16, 0xA3, 0x4A)
PALE_GREEN = RGBColor(0xDC, 0xFC, 0xE7)
RED = RGBColor(0xDC, 0x26, 0x26)
PALE_RED = RGBColor(0xFE, 0xE2, 0xE2)
BLUE = RGBColor(0x25, 0x63, 0xEB)
PALE_BLUE = RGBColor(0xDB, 0xEA, 0xFE)
PALE_GOLD = RGBColor(0xFE, 0xF3, 0xC7)

FONT = "Arial"
TOTAL = 39

WARN: list[str] = []
_SLIDE_NO = 0

# --------------------------------------------------------------------------- #
# text metrics (Liberation Sans is metric-compatible with Arial)
# --------------------------------------------------------------------------- #
_FONT_FILES = {
    False: "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    True: "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
}
for _cand in (
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/LiberationSans-Regular.ttf",
):
    if not Path(_FONT_FILES[False]).exists() and Path(_cand).exists():
        _FONT_FILES[False] = _cand
        _FONT_FILES[True] = _cand.replace("Regular", "Bold")
_FCACHE: dict[tuple, ImageFont.FreeTypeFont] = {}


def _font(size: float, bold: bool) -> ImageFont.FreeTypeFont:
    key = (round(size * 4), bold)
    if key not in _FCACHE:
        _FCACHE[key] = ImageFont.truetype(_FONT_FILES[bold], int(round(size * 4)))
    return _FCACHE[key]


def text_w(text: str, size: float, bold: bool = False) -> float:
    """Width of *text* in inches at *size* pt."""
    return _font(size, bold).getlength(text) / 4 / 72


def wrapped_lines(text: str, size: float, width_in: float, bold: bool = False) -> int:
    words = str(text).split()
    if not words:
        return 1
    lines, cur = 1, ""
    for word in words:
        trial = (cur + " " + word).strip()
        if text_w(trial, size, bold) <= width_in or not cur:
            cur = trial
        else:
            lines += 1
            cur = word
    return lines


def block_h(text: str, size: float, width_in: float, bold: bool = False,
            spacing: float = 1.12) -> float:
    """Estimated rendered height, in inches."""
    return wrapped_lines(text, size, width_in, bold) * size * spacing / 72


# --------------------------------------------------------------------------- #
# low-level shape helpers
# --------------------------------------------------------------------------- #
def _name(shape, name: str):
    try:
        shape.name = name
    except Exception:
        pass
    return shape


def rect(slide, x, y, w, h, fill=None, line=None, radius=False, lw=1.0, name=""):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        shape.fill.background()
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(lw)
    if radius:
        try:
            shape.adjustments[0] = 0.10
        except Exception:
            pass
    shape.shadow.inherit = False
    shape.text_frame.word_wrap = True
    return _name(shape, name) if name else shape


def tx(slide, x, y, w, h, text, size=22, color=INK, bold=False, italic=False,
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.06, name="",
       warn=True):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    for i, line in enumerate(str(text).split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        r = p.add_run()
        r.text = line
        r.font.name = FONT
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.italic = italic
        r.font.color.rgb = color
    need = sum(block_h(ln, size, w, bold, spacing) for ln in str(text).split("\n"))
    if warn and need > h + 0.06:
        WARN.append(f"TEXT overflow {need:.2f}in > {h:.2f}in @({x:.2f},{y:.2f}) "
                    f"{size}pt :: {str(text)[:58]!r}")
    return _name(box, name) if name else box


def bullets(slide, x, y, w, h, items, size=22, color=SLATE, gap=11,
            bullet_color=ORANGE, lead_color=INK, spacing=1.08, name=""):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    used = 0.0
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(gap)
        p.line_spacing = spacing
        lead, rest = item if isinstance(item, tuple) else (None, item)
        b = p.add_run()
        b.text = "•  "
        b.font.name = FONT
        b.font.size = Pt(size)
        b.font.bold = True
        b.font.color.rgb = bullet_color
        full = (lead or "") + rest
        if lead:
            r1 = p.add_run()
            r1.text = lead
            r1.font.name = FONT
            r1.font.size = Pt(size)
            r1.font.bold = True
            r1.font.color.rgb = lead_color
        r = p.add_run()
        r.text = rest
        r.font.name = FONT
        r.font.size = Pt(size)
        r.font.color.rgb = color
        used += wrapped_lines("• " + full, size, w - 0.05) * size * spacing / 72
        used += gap / 72
    if used > h + 0.12:
        WARN.append(f"BULLET overflow {used:.2f}in > {h:.2f}in @({x:.2f},{y:.2f}) "
                    f"{size}pt :: {items[0]!r}")
    return _name(box, name) if name else box


def header(slide, kicker, title, subtitle=None):
    rect(slide, 0, 0, W, 0.08, fill=ORANGE, name="TopBand")
    rect(slide, M, 0.32, 0.06, 0.30, fill=ORANGE, name="KickerTick")
    tx(slide, M + 0.16, 0.30, 10.5, 0.32, kicker.upper(), size=14, color=DARK,
       bold=True, name="Kicker")
    tx(slide, M, 0.64, CW, 0.66, title, size=34, color=INK, bold=True,
       spacing=0.95, name="SlideTitle")
    if subtitle:
        tx(slide, M, 1.36, CW, 0.40, subtitle, size=16, color=SLATE,
           name="Subtitle")
    footer(slide)


def footer(slide):
    global _SLIDE_NO
    n = _SLIDE_NO
    rect(slide, M, 7.06, CW, 0.014, fill=BORDER, name="FooterRule")
    tx(slide, M, 7.13, 9.5, 0.26,
       "WIT Barcode Student Information System  ·  Technical Presentation  ·  October 2026",
       size=14, color=MUTED, name="FooterText", warn=False)
    tx(slide, W - M - 1.6, 7.13, 1.6, 0.26, f"{n:02d} / {TOTAL}", size=14,
       color=MUTED, align=PP_ALIGN.RIGHT, name="PageNumber", warn=False)


def section_tag(slide, text, x, y, w=None, color=DARK):
    if w is None:
        w = max(W - M - x, 1.0)
    tx(slide, x, y, w, 0.28, text.upper(), size=16, color=color, bold=True,
       name="SectionTag", warn=False)


def caption(slide, text, x=M, y=6.62, w=CW, color=MUTED, size=16):
    need = block_h(text, size, w) + 0.02
    y_eff = min(y, 7.02 - need)
    if y_eff < y - 0.01:
        pass  # caption raised to clear the footer rule
    tx(slide, x, y_eff, w, need, text, size=size, color=color, italic=True,
       name="Caption")


def kpi(slide, x, y, w, h, value, label, note="", accent=ORANGE, fill=CREAM,
        vsize=32, name=""):
    rect(slide, x, y, w, h, fill=fill, line=BORDER, radius=True, name=name or "KPI")
    rect(slide, x, y + 0.14, 0.07, h - 0.28, fill=accent, name="KPIAccent")
    tx(slide, x + 0.22, y + 0.14, w - 0.38, 0.52, value, size=vsize, color=INK,
       bold=True, spacing=0.9, name="KPIValue")
    tx(slide, x + 0.22, y + 0.68, w - 0.38, 0.28, label.upper(), size=15,
       color=DARK, bold=True, name="KPILabel", warn=False)
    if note:
        nh = h - 1.02
        if nh < 0.26:
            WARN.append(f"KPI tile too short for note @({x:.2f},{y:.2f}) h={h}")
        tx(slide, x + 0.22, y + 0.98, w - 0.38, max(nh, 0.26), note, size=15,
           color=SLATE, name="KPINote")


def chip(slide, x, y, w, text, fill=CREAM, color=DARK, size=17, h=0.40):
    rect(slide, x, y, w, h, fill=fill, line=BORDER, radius=True, name="Chip")
    tx(slide, x + 0.06, y + (h - size / 72 * 1.25) / 2, w - 0.12, size / 72 * 1.3,
       text, size=size, color=color, bold=True, align=PP_ALIGN.CENTER,
       name="ChipText", warn=False)


def image_panel(slide, path, x, y, w, h, pad=0.05, border=BORDER, fill=PAPER,
                name="Image"):
    rect(slide, x, y, w, h, fill=fill, line=border, name=name + "Frame")
    iw, ih = Image.open(path).size
    scale = min((w - 2 * pad) / iw, (h - 2 * pad) / ih)
    dw, dh = iw * scale, ih * scale
    pic = slide.shapes.add_picture(str(path), Inches(x + (w - dw) / 2),
                                   Inches(y + (h - dh) / 2), Inches(dw), Inches(dh))
    return _name(pic, name)


def table(slide, x, y, w, rows, col_widths, size=20, header_size=20,
          row_h=0.44, align=None, name="Table"):
    nrows, ncols = len(rows), len(rows[0])
    height = row_h * nrows
    shape = slide.shapes.add_table(nrows, ncols, Inches(x), Inches(y),
                                   Inches(w), Inches(height))
    tbl = shape.table
    _name(shape, name)
    for i, cw in enumerate(col_widths):
        tbl.columns[i].width = Inches(cw)
    for r in range(nrows):
        tbl.rows[r].height = Inches(row_h)
        for c in range(ncols):
            cell = tbl.cell(r, c)
            cell.margin_left = Inches(0.09)
            cell.margin_right = Inches(0.07)
            cell.margin_top = Inches(0.04)
            cell.margin_bottom = Inches(0.04)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            if r == 0:
                cell.fill.fore_color.rgb = INK
            else:
                cell.fill.fore_color.rgb = PAPER if r % 2 else SOFT
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = align[c] if align else PP_ALIGN.LEFT
            run = p.add_run()
            run.text = str(rows[r][c])
            run.font.name = FONT
            run.font.size = Pt(header_size if r == 0 else size)
            run.font.bold = r == 0
            run.font.color.rgb = PAPER if r == 0 else (INK if c == 0 else SLATE)
            lines = wrapped_lines(run.text, header_size if r == 0 else size,
                                  col_widths[c] - 0.18, r == 0)
            need = lines * (header_size if r == 0 else size) * 1.16 / 72 + 0.09
            if need > row_h + 0.03:
                WARN.append(f"TABLE row {r} col {c} needs {need:.2f}in > "
                            f"{row_h:.2f}in :: {run.text[:44]!r}")
    bottom = y + height
    if bottom > BOT + 0.04:
        WARN.append(f"TABLE bottom {bottom:.2f} exceeds {BOT} :: {rows[0]}")
    return tbl


def arrow(slide, x1, y1, x2, y2, color=SLATE, width=2.25, head=True, dash=False,
          name="Arrow"):
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1),
                                      Inches(y1), Inches(x2), Inches(y2))
    conn.line.color.rgb = color
    conn.line.width = Pt(width)
    conn.shadow.inherit = False
    ln = conn.line._get_or_add_ln()
    if dash:
        d = ln.makeelement(qn("a:prstDash"), {"val": "dash"})
        ln.append(d)
    if head:
        t = ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med",
                                             "len": "med"})
        ln.append(t)
    return _name(conn, name)


def node(slide, x, y, w, h, title, sub=None, fill=CREAM, line=BORDER,
         tcolor=INK, tsize=20, ssize=16, accent=None, name="Node", align=PP_ALIGN.CENTER):
    rect(slide, x, y, w, h, fill=fill, line=line, radius=True, name=name)
    if accent:
        rect(slide, x, y, w, 0.08, fill=accent, name=name + "Accent")
    top_pad = 0.10 if accent else 0.0
    if sub:
        sub_h = 0.32
        title_h = max(h - top_pad - sub_h - 0.14, 0.22)
        tx(slide, x + 0.09, y + top_pad + 0.06, w - 0.18, title_h, title, size=tsize,
           color=tcolor, bold=True, align=align, anchor=MSO_ANCHOR.BOTTOM,
           spacing=0.98, name=name + "Title", warn=False)
        tx(slide, x + 0.09, y + h - sub_h - 0.06, w - 0.18, sub_h, sub, size=ssize,
           color=SLATE, align=align, anchor=MSO_ANCHOR.TOP, name=name + "Sub",
           warn=False)
    else:
        tx(slide, x + 0.09, y + 0.04, w - 0.18, h - 0.08, title, size=tsize,
           color=tcolor, bold=True, align=align, anchor=MSO_ANCHOR.MIDDLE,
           spacing=0.98, name=name + "Title", warn=False)
    return None


def diamond(slide, cx, cy, w, h, text, fill=PALE_GOLD, line=RGBColor(0xFD, 0xE6, 0x8A),
            tsize=18, name="Decision"):
    sh = slide.shapes.add_shape(MSO_SHAPE.DIAMOND, Inches(cx - w / 2),
                                Inches(cy - h / 2), Inches(w), Inches(h))
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.color.rgb = line
    sh.line.width = Pt(1.2)
    sh.shadow.inherit = False
    tf = sh.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.06)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = text
    r.font.name = FONT
    r.font.size = Pt(tsize)
    r.font.bold = True
    r.font.color.rgb = INK
    return _name(sh, name)


def entity(slide, x, y, w, title, fields, accent=INK, name="Entity", fsize=17,
           tsize=20):
    """Editable ERD entity: header band + field list (PK/FK marked)."""
    fh = 0.325
    h = 0.50 + len(fields) * fh + 0.06
    rect(slide, x, y, w, h, fill=PAPER, line=RGBColor(0xCB, 0xD5, 0xE1), radius=True,
         name=name)
    rect(slide, x, y, w, 0.50, fill=accent, radius=True, name=name + "Header")
    tx(slide, x + 0.10, y + 0.11, w - 0.20, 0.32, title, size=tsize, color=PAPER,
       bold=True, name=name + "Name", warn=False)
    for i, (text, kind) in enumerate(fields):
        fy = y + 0.54 + i * fh
        bold = kind in ("pk", "fk")
        color = DARK if kind == "pk" else (BLUE if kind == "fk" else SLATE)
        tag = {"pk": "◆ ", "fk": "→ ", "ref": "⇢ ", "col": ""}[kind]
        label = tag + text
        fsz = fsize
        avail = w - 0.26
        while fsz > 12 and text_w(label, fsz, bold) > avail:
            fsz -= 0.5
        if text_w(label, fsz, bold) > avail:
            WARN.append(f"ENTITY field too wide @entity {name}: {label!r}")
        tx(slide, x + 0.12, fy, w - 0.22, fh - 0.02, label, size=fsz,
           color=color, bold=bold, name=f"{name}Field{i}", warn=False)
    return h


# --------------------------------------------------------------------------- #
# evidence (read from the repository at build time)
# --------------------------------------------------------------------------- #
def load_evidence() -> dict:
    ev: dict = {}
    ev["db"] = json.loads((CHARTS / "database_statistics.json").read_text())
    ev["latency"] = json.loads((CHARTS / "inference_latency.json").read_text())
    ev["runs"] = json.loads((CHARTS / "training_metrics.json").read_text())
    ev["dataset"] = json.loads((CHARTS / "dataset_statistics.json").read_text())
    v7 = sorted(CANDIDATES.glob("barcode_detector_v7_distance_*/results.csv"))[0]
    with v7.open() as fh:
        ev["v7"] = list(csv.DictReader(fh))
    ev["v7_dir"] = v7.parent
    return ev


def new_slide(prs):
    global _SLIDE_NO
    _SLIDE_NO += 1
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = PAPER
    return slide


NOTES_MIN, NOTES_MAX = 110, 180


def _sentences(par):
    """Split a paragraph into sentences, keeping terminal punctuation."""
    parts = re.split(r"(?<=[.!?])\s+", par.strip())
    return [p for p in parts if p]


def notes(slide, text):
    """Write speaker notes, trimmed to the NOTES_MIN..NOTES_MAX word band.

    Notes are written most-important-first, so if a block is over the ceiling the
    least-important trailing sentences of its final paragraph are dropped. This
    keeps the on-slide text untouched and guarantees a consistent notes length.
    """
    text = text.strip()
    if len(text.split()) > NOTES_MAX:
        blocks = [b.strip() for b in text.split("\n\n") if b.strip()]

        def total():
            return len(" ".join(blocks).split())

        while blocks and total() > NOTES_MAX:
            last = _sentences(blocks[-1])
            if len(last) > 1:
                last.pop()
                blocks[-1] = " ".join(last)
            else:
                blocks.pop()
        text = "\n\n".join(blocks)
    text = re.sub(r"[ \t]+", " ", text)
    n = len(text.split())
    if n < NOTES_MIN:
        WARN.append(f"NOTES too short after trim: {n} words")
    slide.notes_slide.notes_text_frame.text = text


# =========================================================================== #
# 01 title
# =========================================================================== #
def s01_title(prs, ev):
    s = new_slide(prs)
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = INK
    rect(s, 0, 0, 0.14, H, fill=ORANGE, name="Spine")
    rect(s, 0.80, 1.10, 1.70, 0.06, fill=GOLD, name="GoldRule")
    tx(s, 0.80, 1.28, 7.4, 0.34, "TECHNICAL SYSTEM PRESENTATION  ·  2026",
       size=16, color=GOLD, bold=True, name="Kicker", warn=False)
    tx(s, 0.80, 1.80, 7.5, 2.35, "WIT Barcode\nStudent Information\nSystem",
       size=44, color=PAPER, bold=True, spacing=0.92, name="DeckTitle")
    tx(s, 0.80, 4.35, 7.0, 1.15,
       "Computer-vision student identification, enrollment, assessment and "
       "documentation — a local desktop application.",
       size=20, color=RGBColor(0xCB, 0xD5, 0xE1), spacing=1.16, name="DeckSubtitle")
    rect(s, 0.80, 5.72, 6.2, 0.014, fill=RGBColor(0x33, 0x41, 0x55), name="Rule")
    tx(s, 0.80, 5.90, 7.0, 0.95,
       "Western Institute of Technology  ·  Computer Engineering\n"
       "Evidence from repository commit 521e66c  ·  October 2026",
       size=17, color=RGBColor(0x94, 0xA3, 0xB8), spacing=1.24, name="Byline")
    for label, x in (("PySide6 6.12", 0.80), ("MySQL 8.4", 2.90),
                     ("YOLOv8n", 4.66), ("CODE39", 6.30)):
        chip(s, x, 6.86, 1.90, label, fill=RGBColor(0x1E, 0x29, 0x3B),
             color=RGBColor(0xE2, 0xE8, 0xF0), size=16)
    rect(s, 8.46, 0.95, 4.44, 5.75, fill=RGBColor(0x1E, 0x29, 0x3B),
         line=RGBColor(0x33, 0x41, 0x55), name="CardPanel")
    image_panel(s, SHOTS / "16a_id_card_front.png", 8.68, 1.20, 4.00, 2.62,
                pad=0.02, name="IdCardFront")
    image_panel(s, SHOTS / "16b_id_card_back.png", 8.68, 4.00, 4.00, 2.62,
                pad=0.02, name="IdCardBack")
    tx(s, 8.68, 6.76, 4.00, 0.30, "CR80 card · 300 DPI render by the application",
       size=15, color=RGBColor(0x94, 0xA3, 0xB8), italic=True, name="CardCaption",
       warn=False)
    notes(s, """
Good afternoon. This is a technical account of the system in this repository — not a
product brochure. Everything you will see is backed by the running application, the
live MySQL database, the training artefacts on disk, or captured interface screenshots.

The two cards on the right are genuine 300-DPI renders produced by the application's
own IdCardService from a real student record: an 85.6 by 54 millimetre CR80 card, front
and back. The four chips along the bottom are the runtimes that were actually verified
on this machine — PySide6 6.12, MySQL 8.4, a fine-tuned YOLOv8n detector, and CODE39
barcodes.

The system is a single-process Python desktop application: it owns the camera, runs the
neural network locally, talks to a local MySQL server and writes documents to disk. There
is no web server and no cloud component anywhere in the dependency set.

I will move from the problem, through the design and a live walkthrough, into the data
and the model, and I will finish with what is tested, what is broken, and what remains.
""")
    return s


# =========================================================================== #
# 02 agenda
# =========================================================================== #
def s02_agenda(prs, ev):
    s = new_slide(prs)
    header(s, "Navigation", "What this deck covers",
           "Five sections. Every number is traced to the repository, the live database, training artefacts or captured UI.")
    parts = [
        ("01", "Context & objectives", "Problem, goals, scope and what is deliberately not delivered."),
        ("02", "System design", "Context boundary, stack, layered architecture and module map."),
        ("03", "Product walkthrough", "Every working page, the scan pipeline and the documents it produces."),
        ("04", "Data & model", "Schema, ERD, data flow, the 2,551-image dataset, training and inference."),
        ("05", "Verification & roadmap", "Test results, documented defects, limitations and next steps."),
    ]
    y = TOP + 0.02
    for i, (num, title, desc) in enumerate(parts):
        rect(s, M, y, CW, 0.86, fill=CREAM if i % 2 == 0 else PAPER, line=BORDER,
             radius=True, name=f"AgendaRow{i}")
        tx(s, M + 0.24, y + 0.20, 0.75, 0.46, num, size=24, color=ORANGE, bold=True,
           name="AgendaNum", warn=False)
        tx(s, M + 1.10, y + 0.11, 4.6, 0.34, title, size=22, color=INK, bold=True,
           name="AgendaTitle", warn=False)
        tx(s, M + 1.10, y + 0.47, 10.6, 0.30, desc, size=17, color=SLATE,
           name="AgendaDesc", warn=False)
        y += 0.95
    tx(s, M, 6.74, CW, 0.30,
       "Evidence policy: no placeholder screenshots, no invented metrics, no hidden defects.",
       size=18, color=MUTED, italic=True, name="Policy", warn=False)
    notes(s, """
The deck moves in five steps: why the system exists, how it is built, what it actually
does on screen, what the data and the model evidence look like, and finally what is
verified and what is not.

The section that earns the most scrutiny is usually the walkthrough, because that is where
claims meet the interface. Every screenshot in that section was captured from the running
application against the live database — none are mock-ups or design renders, with one
explicit exception I will flag when we reach it.

Section four is the technical core: a nineteen-table MySQL schema, a rebuilt
entity-relationship diagram, and a YOLOv8n detector fine-tuned on a dataset we measured
file by file — two thousand five hundred and fifty-one images.

Section five is deliberately blunt. The test suite has real failures and the application
has three reproducible product defects. They are named on the slides where they matter,
not buried in an appendix.

If you only remember one policy from this deck: if a number appears on a slide, it was
measured from this repository.
""")
    return s


# =========================================================================== #
# 03 executive summary
# =========================================================================== #
def s03_exec(prs, ev):
    s = new_slide(prs)
    header(s, "01 · Executive summary", "A working desktop SIS with a real vision pipeline",
           "Eight operational modules, 19 database tables and a fine-tuned detector — plus a short, documented list of gaps.")
    tiles = [
        ("45,978", "Python LOC", "122 first-party files"),
        ("19", "Database tables", "230 columns · 33 FKs"),
        ("2,551", "Dataset images", "2,515 barcode boxes"),
        ("531", "Tests passing", "of 553 · 22 failures"),
    ]
    x = M
    for value, label, note in tiles:
        kpi(s, x, TOP, 2.92, 1.42, value, label, note, vsize=30)
        x += 3.06
    section_tag(s, "What is proven", M, 3.36)
    bullets(s, M, 3.68, 5.85, 2.95, [
        ("Identity: ", "YOLO → pyzbar → exact SQL match resolves the student."),
        ("Records: ", "students, enrollment, fees and scan history in MySQL."),
        ("Documents: ", "certificate, assessment and barcode reports, CSV log."),
        ("Honesty: ", "unknown, duplicate and ambiguous are separate states."),
    ], size=20, gap=9)
    section_tag(s, "What is not", M + 6.25, 3.36, color=RED)
    bullets(s, M + 6.25, 3.68, 5.85, 2.95, [
        ("No authentication: ", "logout is a stub; the app opens into the dashboard."),
        ("Generator unrouted: ", "the barcode generator page has no route."),
        ("Two UI defects: ", "dashboard table and ID-card preview both raise errors."),
        ("No test split: ", "the active model was selected on validation only."),
    ], size=20, gap=9, bullet_color=RED)
    tx(s, M, 6.66, CW, 0.34,
       "Bottom line: core identification and records work; the hardening list is finite.",
       size=17, color=DARK, bold=True, name="BottomLine")
    notes(s, """
This slide is the claim the rest of the deck has to support: a real, working local system,
not a mock-up — and an honest inventory of its gaps.

The four tiles are measured, not estimated. Forty-five thousand nine hundred and
seventy-eight lines of Python across a hundred and twenty-two first-party files. Nineteen
database tables carrying two hundred and thirty columns and thirty-three foreign keys.
Two thousand five hundred and fifty-one dataset images carrying two thousand five hundred
and fifteen barcode boxes. And a pytest run that collects five hundred and fifty-three
tests, passes five hundred and thirty-one and fails twenty-two.

On the proven side, the identity path is complete end to end: the detector localises the
symbol, the decoder turns it into a payload, and an exact database match resolves the
student. On the not-proven side: there is no login, the barcode generator page cannot be
reached from the interface, two dialogs raise exceptions, and the model was selected on
validation data with no held-out test split.

I put the limitations on the summary slide deliberately. You should not have to wait until
the last slide to discover what does not work.
""")
    return s


# =========================================================================== #
# 04 problem
# =========================================================================== #
def s04_problem(prs, ev):
    s = new_slide(prs)
    header(s, "01 · Problem", "Why a barcode-first student system",
           "Three operational frictions the project targets.")
    blocks = [
        ("01", "Identity checks are manual",
         "A registrar compares a printed name and reference against a card — slow at a counter, easy to misread.",
         "Target: decode the symbol, resolve one database row."),
        ("02", "Records live in separate places",
         "Details, subjects, fees and scan history sit in spreadsheets or paper forms, so one fact has many versions.",
         "Target: 19 related tables with keys and constraints."),
        ("03", "Documents are re-typed",
         "Certificates, statements and scan logs are assembled by hand for every request, repeating work and errors.",
         "Target: generate documents from the same records."),
    ]
    x = M
    for num, title, body, target in blocks:
        rect(s, x, TOP + 0.04, 3.90, 4.55, fill=SOFT, line=BORDER, radius=True,
             name=f"Problem{num}")
        tx(s, x + 0.26, TOP + 0.22, 0.7, 0.48, num, size=26, color=ORANGE, bold=True,
           warn=False)
        tx(s, x + 0.26, TOP + 0.78, 3.38, 0.92, title, size=23, color=INK, bold=True,
           spacing=0.98)
        tx(s, x + 0.26, TOP + 1.80, 3.38, 1.70, body, size=19, color=SLATE,
           spacing=1.14)
        rect(s, x + 0.26, TOP + 3.62, 3.36, 0.014, fill=BORDER, name="Rule")
        tx(s, x + 0.26, TOP + 3.78, 3.36, 0.70, target, size=18, color=DARK,
           bold=True, spacing=1.08)
        x += 4.10
    tx(s, M, 6.66, CW, 0.30,
       "Demonstration data: 3 students · 21 subject lines · 3 assessments · 65 scans.",
       size=18, color=MUTED, italic=True, name="Footnote")
    notes(s, """
The problem statement is operational rather than theoretical, and each of the three
frictions ends with the concrete design response that appears later in this deck.

First, identity checks. At a counter, a member of staff compares a printed name and a
reference number against the card in front of them. That is slow, and it fails quietly
when two cards look alike. The system's answer is to locate the barcode symbol with a
neural network, decode the payload with ZBar, and resolve exactly one database row —
presenting a student only when that row exists.

Second, fragmented records. When student details, subject lines, fees and scan history
live in separate spreadsheets or paper forms, a single fact develops several versions.
The answer here is a nineteen-table MySQL schema with foreign keys and uniqueness
constraints, so a payload can only belong to one student and an enrollment to one term.

Third, repetitive document assembly. Certificates, assessment statements and scan logs
are re-typed for every request. The system generates every document from the same tables
the interface reads.

The counts along the bottom come from the live database: three students, twenty-one
subject lines, three assessments and sixty-five scan records.
""")
    return s


# =========================================================================== #
# 05 objectives
# =========================================================================== #
def s05_objectives(prs, ev):
    s = new_slide(prs)
    header(s, "01 · Objectives", "Success criteria and where they stand",
           "Each objective is paired with the implementation that serves it and the evidence behind the status.")
    rows = [
        ["Objective", "Implementation and evidence", "Status"],
        ["Identify a student from a physical card",
         "YOLOv8n → ROI gate → pyzbar → exact SQL match · 3/3 real cards decoded", "Met"],
        ["Never present a decode as a student",
         "VERIFIED requires a matching students row · scan_pipeline.py state machine", "Met"],
        ["One source of truth for records",
         "19 tables, 33 foreign keys, UNIQUE constraints · verified against the live schema", "Met"],
        ["Produce documents from live data",
         "ReportLab COM, assessment and barcode reports, CSV scan log in reports/", "Met"],
        ["Make the pipeline testable offline",
         "No Qt or threads inside ScanPipeline · 553 tests collected, 22 failing", "Partial"],
        ["Protect access with authentication",
         "users table exists; no login flow · logout is a stub", "Not met"],
    ]
    tbl = table(s, M, TOP, CW, rows, [4.05, 6.44, 1.60], size=18, header_size=18,
                row_h=0.68,
                align=[PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.CENTER],
                name="ObjectivesTable")
    for r in range(1, len(rows)):
        cell = tbl.cell(r, 2)
        run = cell.text_frame.paragraphs[0].runs[0]
        status = rows[r][2]
        run.font.color.rgb = {"Met": GREEN, "Partial": DARK, "Not met": RED}[status]
        run.font.bold = True
        cell.fill.fore_color.rgb = {"Met": PALE_GREEN, "Partial": PALE_GOLD,
                                    "Not met": PALE_RED}[status]
    tx(s, M, 6.72, CW, 0.28,
       "“Met” = observed in the running application or queried from the live database.",
       size=18, color=MUTED, italic=True, name="Footnote")
    notes(s, """
The status column is intentionally blunt, and it is the pattern the whole deck follows:
every claim carries the evidence that could falsify it.

Three objectives are met on the identity side. A student can be identified from a physical
card — all three real cards in the repository decode and resolve. The system refuses to
present a bare decode as a student: the pipeline state machine only reaches VERIFIED when
an exact match exists in the students table. And the records are normalised — nineteen
tables, thirty-three foreign keys, uniqueness constraints on student identifiers and
barcode payloads, verified by querying SHOW CREATE TABLE against the live schema.

Two objectives are met on the output side. Every document is generated from live records
by ReportLab, and the whole application runs on one workstation with no server and no
network client anywhere in the dependency set.

Three are not fully met. The scan pipeline is testable without a camera — five hundred and
fifty-three tests collect — but the test suite currently has twenty-two failures. There is
a users table but no login flow, so nothing is protected. And although the barcode
generator page is fully implemented, no route in the sidebar reaches it.

The two "Not met" rows are developed again on the verification and roadmap slides.
""")
    return s


# =========================================================================== #
# 06 system context
# =========================================================================== #
def s06_context(prs, ev):
    s = new_slide(prs)
    header(s, "02 · System design", "System context — one workstation, no network tier",
           "A monolithic PySide6 desktop client that owns the camera, the model, the database connection and the filesystem.")
    node(s, M + 0.10, TOP + 1.62, 2.85, 1.45, "Operator",
         "Registrar staff · only human actor", fill=SOFT, accent=SLATE,
         tsize=22, name="Actor")
    arrow(s, M + 2.95, TOP + 2.34, M + 3.72, TOP + 2.34, name="ActorArrow")
    node(s, M + 3.72, TOP + 1.05, 4.30, 2.60, "WIT Barcode SIS",
         "PySide6 desktop app · single process", fill=CREAM, accent=ORANGE,
         tsize=26, ssize=18, name="System")
    tx(s, M + 3.86, TOP + 2.30, 4.02, 1.05,
       "UI pages  ·  scan pipeline\nYOLOv8n inference  ·  pyzbar decode\nrepositories  ·  report builders",
       size=17, color=SLATE, align=PP_ALIGN.CENTER, name="SystemDetail", warn=False)
    node(s, M + 3.72, TOP - 0.02, 4.30, 0.66, "USB camera  ·  1280 × 720 @ 30 fps",
         fill=PALE_BLUE, line=RGBColor(0x93, 0xC5, 0xFD), tsize=18, name="Camera")
    arrow(s, M + 5.87, TOP + 0.68, M + 5.87, TOP + 1.02, width=2.5, name="CamArrow")
    right = [
        ("MySQL 8.4 · localhost", "wit_barcode_system · 19 tables", PALE_GREEN,
         RGBColor(0xBB, 0xF7, 0xD0)),
        ("Model files on disk", "models_yolo/active/best.pt · 6.48 MB", CREAM, BORDER),
        ("Output files", "reports/ PDF & CSV · generated_id_cards/", PALE_GOLD,
         RGBColor(0xFD, 0xE6, 0x8A)),
    ]
    ry = TOP + 0.02
    for title, sub, fill, line in right:
        node(s, 9.30, ry, 3.42, 1.05, title, sub, fill=fill, line=line, tsize=19,
             ssize=16, align=PP_ALIGN.LEFT, name="External")
        arrow(s, 8.02, TOP + 2.34, 9.30, ry + 0.52, name="ExtArrow")
        ry += 1.33
    tx(s, 9.30, ry + 0.06, 3.42, 0.86,
       "No HTTP client, web framework or remote host appears in the dependency set.",
       size=17, color=MUTED, italic=True, name="BoundaryNote")
    caption(s, "Verified in ui/main_window.py routes, services/ and database/connection.py.",
            y=6.70)
    notes(s, """
This boundary diagram explains several later properties of the system, including some of
its limitations.

The application is a single process. The operator on the left is the only human actor:
there is no second role, no client-server split and no remote administration surface. The
camera is a local Video4Linux device captured at 1280 by 720 pixels, thirty frames a
second; if no camera is available the operator can upload a card image instead and the
same pipeline runs on the file.

On the right are the three external resources. MySQL 8.4 runs on the same workstation and
holds one database, wit_barcode_system, with nineteen tables. The detector is a file on
disk — models_yolo/active/best.pt, six and a half megabytes — loaded at startup rather
than served by anything. And every output, from the matriculation certificate to the CSV
scan log, is written to the local filesystem.

The consequence worth stating out loud: because there is no server, there is no session,
no role check and no network audit surface. That is a deliberate scope choice for a
registrar counter machine, and it is also the reason authentication is listed as not met.
""")
    return s


# 07 technology stack — application & data
# =========================================================================== #
def _tech_panel(s, x, y, w, title, accent, rows, fsize=17):
    """One grouped technology card: a heading plus 'name / role' rows."""
    pad = 0.16
    lh = 0.315
    h = 0.54 + len(rows) * lh + 0.10
    rect(s, x, y, w, h, fill=PAPER, line=BORDER, radius=True, name="TechPanel")
    rect(s, x, y, w, 0.085, fill=accent, name="TechAccent")
    tx(s, x + pad, y + 0.15, w - 2 * pad, 0.32, title, size=18, color=INK,
       bold=True, warn=False, name="TechTitle")
    ry = y + 0.55
    for name, role in rows:
        tx(s, x + pad, ry, w - 2 * pad, 0.26, name, size=fsize, color=DARK,
           bold=True, warn=False, name="TechName")
        tx(s, x + pad, ry + lh * 0.50, w - 2 * pad, 0.24, role, size=15,
           color=MUTED, warn=False, name="TechRole")
        ry += lh
    return h


def s07_stack(prs, ev):
    s = new_slide(prs)
    header(s, "02 · Technology 1/2", "Every runtime, library and tool — part 1",
           "Every version was read from the project virtual environment and confirmed by live import and nvidia-smi.")
    _tech_panel(s, M, TOP, 5.92, "Runtime & platform", ORANGE, [
        ("Python 3.14.4", "application interpreter — one local process"),
        ("NVIDIA RTX 2050 · CUDA", "training and inference device (4 GB)"),
        ("Linux workstation", "target deployment machine"),
        ("python-dotenv 1.2.4", ".env config: DB, YOLO path, camera"),
    ])
    _tech_panel(s, M + 6.17, TOP, 5.92, "User interface", BLUE, [
        ("PySide6 6.12.0 · Qt 6", "desktop GUI — ten routed pages"),
        ("QSS theme + WIT fonts", "styles/theme.qss, colors.py, typography.py"),
        ("Qt Multimedia / OpenCV", "live 1280 × 720 @ 30 fps preview"),
        ("Qt dialogs & widgets", "student, assessment, card, quick-search"),
    ])
    _tech_panel(s, M, TOP + 1.98, 5.92, "Database & storage", GREEN, [
        ("MySQL server 8.4.11", "local InnoDB, wit_barcode_system, 19 tables"),
        ("mysql-connector-python 26.7.0", "official driver; pool of 10; raw SQL"),
        ("No ORM", "schema from database/schema.py + migrations"),
        ("Filesystem", "reports/, generated_barcodes/, id cards"),
    ])
    _tech_panel(s, M + 6.17, TOP + 1.98, 5.92, "Documents & reporting", INK, [
        ("ReportLab 5.0.1", "A4 PDF — certificate, assessment, reports"),
        ("matplotlib 3.11.2", "chart rendering for analytics reports"),
        ("python-barcode 0.16.1", "CODE39 symbol generation for cards"),
        ("CSV export", "scan history and dataset logs"),
    ])
    caption(s, "Deliberately absent: no web framework, no HTTP client, no cloud service, "
               "no ORM — one workstation, one process.", y=6.66)
    notes(s, """
Every version on this and the next slide was read from the project's own virtual
environment with pip list, then confirmed against live imports, and the GPU was confirmed
with nvidia-smi. Nothing here is copied from the requirements file, which only declares
minimum versions.

The runtime is Python 3.14.4, newer than most Qt tutorials assume, and the environment
imports and runs cleanly on it. The machine is a Linux workstation with an NVIDIA RTX 2050
carrying four gigabytes and CUDA support; the detector uses it when present and falls back
to CPU when it is not, which is why the latency slide reports both numbers.

The interface is PySide6 6.12 — the official Qt for Python binding — and this is a widgets
desktop application, not QML. The look and feel is not ad hoc: a QSS stylesheet in the
styles folder drives it, with colour and typography defined alongside it, and the camera
preview is a Qt multimedia surface fed at 1280 by 720 and thirty frames a second.

Storage is a local MySQL 8.4 server reached through the official connector at version
26.7, with a connection pool of ten and no ORM anywhere: the schema is applied as raw SQL
from database schema plus migrations, and read through repository classes.

Documents are produced by ReportLab — the Certificate of Matriculation, the assessment
statement and the reports — while matplotlib renders the charts those reports embed, and
python-barcode renders the CODE39 symbols the cards carry.
""")
    return s


# =========================================================================== #
# 08 technology stack — vision, ML & toolchain
# =========================================================================== #
def s08_stack2(prs, ev):
    s = new_slide(prs)
    header(s, "02 · Technology 2/2", "Every runtime, library and tool — part 2",
           "The computer-vision, machine-learning and engineering toolchain behind detection, decoding and the test suite.")
    _tech_panel(s, M, TOP, 5.92, "Computer vision & machine learning", ORANGE, [
        ("Ultralytics 8.4.177", "YOLOv8n training and inference framework"),
        ("PyTorch 2.14.1", "tensor runtime — CUDA / CPU"),
        ("torchvision 0.29.1", "vision utilities (Ultralytics dependency)"),
        ("OpenCV 5.0.0", "frame, crop and preprocessing operations"),
    ])
    _tech_panel(s, M + 6.17, TOP, 5.92, "Arrays, images & dataset tooling", BLUE, [
        ("NumPy 2.5.4", "array operations throughout the pipeline"),
        ("Pillow 12.3.0", "image loading and card rendering support"),
        ("imagehash 4.3.2", "perceptual-hash duplicate checks"),
        ("Generated datasets", "8 builds on disk · data.yaml variants"),
    ])
    _tech_panel(s, M, TOP + 1.98, 5.92, "Barcode symbology & decoding", GREEN, [
        ("pyzbar 0.1.9", "CODE39 decoding — the only trusted read"),
        ("ZBar C library (libzbar)", "system decoder that pyzbar wraps"),
        ("python-barcode 0.16.1", "renders the CODE39 symbols on cards"),
        ("CODE39 · 9-digit payload", "000065412, 000065902, 000065736"),
    ])
    _tech_panel(s, M + 6.17, TOP + 1.98, 5.92, "Testing & engineering toolchain", INK, [
        ("pytest 9.1.1", "553 tests collected · 531 passing"),
        ("pytest-qt 4.5.0", "Qt widget testing without hardware"),
        ("pytest-mock 3.16.0", "mock camera, database and model boundaries"),
        ("Python 3.14 · venv", "reproducible pinned environment"),
    ])
    caption(s, "21 verified third-party technologies plus the ZBar C library and the Qt "
               "stylesheet layer — the full list lives in TECHNOLOGY_STACK.md.", y=6.66)
    notes(s, """
The second half of the technology inventory is the part that does the actual work: vision,
learning, decoding and the engineering toolchain that keeps the claims honest.

The vision stack is the heaviest part of the dependency set. Ultralytics 8.4.177 hosts the
YOLOv8n model, sitting on PyTorch 2.14.1 — which is why this deck reports measured
inference latency rather than a guess — with torchvision providing the vision utilities
Ultralytics depends on and OpenCV handling frame, crop and preprocessing operations.

Around them sit the supporting libraries. NumPy carries array operations throughout the
pipeline. Pillow loads images and supports card rendering. imagehash performs perceptual
hash checks that catch duplicate dataset images, and the dataset builder produces eight
variants on disk, each with its own data yaml.

Decoding is a two-layer story. pyzbar 0.1.9 is the Python binding, and beneath it is the
ZBar C library — libzbar — which is the actual decoder. The system trusts a read only when
pyzbar returns a payload, which is why the card symbology is stated as CODE39 with the
three real nine-digit payloads shown here.

The engineering toolchain is what makes five hundred and fifty-three tests collectable
without a camera: pytest as the runner, pytest-qt to drive the widgets off-screen, and
pytest-mock to replace the camera, the database and the model at their boundaries — all
inside a pinned Python 3.14 virtual environment.
""")
    return s


# =========================================================================== #
# =========================================================================== #
# 09 model to train
# =========================================================================== #
def s08b_model_plan(prs, ev):
    s = new_slide(prs)
    header(s, "02 · Technology", "The one thing we train — a YOLOv8n detector",
           "Everything else is bought; the detector is fine-tuned here — with a recorded recipe and an evidence gate.")
    section_tag(s, "Training recipe — recorded in the checkpoint, not typed by hand", M, TOP)
    rows = [
        ["Setting", "Recorded value"],
        ["Base weights", "yolov8n.pt — COCO-pretrained"],
        ["Task · classes", "Detection · 1 class: barcode"],
        ["Input · batch", "640 × 640 · batch 16"],
        ["Optimizer", "AdamW · lr0 0.002"],
        ["Epochs", "100 requested · patience 30"],
        ["Seed · device", "0 · CUDA:0 (RTX 2050)"],
        ["Augmentation", "tame — geometry-safe profile"],
        ["Dataset", "barcode_distance · 414 / 414"],
        ["Promotion", "evidence gate · best epoch 34"],
    ]
    table(s, M, TOP + 0.34, 6.60, rows, [2.30, 4.30], size=18, header_size=18,
          row_h=0.42,
          align=[PP_ALIGN.LEFT, PP_ALIGN.LEFT], name="RecipeTable")
    px, pw = 7.47, 5.24
    rect(s, px, TOP, pw, 2.30, fill=CREAM, line=BORDER, radius=True, name="WhyPanel")
    tx(s, px + 0.22, TOP + 0.12, pw - 0.44, 0.32, "Why the nano model", size=20,
       color=DARK, bold=True, warn=False)
    bullets(s, px + 0.22, TOP + 0.52, pw - 0.44, 1.66, [
        "22.49 ms GPU mean (p95 25.0 ms).",
        "CPU fallback 218 ms \u2014 usable.",
        "6.48 MB loads instantly.",
        "Bigger model = latency, not skill.",
    ], size=17, gap=7)
    rect(s, px, TOP + 2.40, pw, 2.30, fill=PALE_GREEN, line=RGBColor(0xBB, 0xF7, 0xD0),
         radius=True, name="GatePanel")
    tx(s, px + 0.22, TOP + 2.52, pw - 0.44, 0.32, "Promotion gate", size=20,
       color=RGBColor(0x16, 0x65, 0x34), bold=True, warn=False)
    bullets(s, px + 0.22, TOP + 2.92, pw - 0.44, 1.66, [
        "No false positives on barcode-free docs.",
        "Held-out decode >= the active model.",
        "Active model backed up before replace.",
        "Missing baseline blocks promotion.",
    ], size=17, gap=7, bullet_color=GREEN, lead_color=RGBColor(0x16, 0x65, 0x34),
        color=RGBColor(0x14, 0x53, 0x2D))
    tx(s, M, 6.66, CW, 0.30,
       "Master list and recipe: TECHNOLOGY_STACK.md  \u00b7  measured evidence on slides 17 and 34\u201336.",
       size=16, color=MUTED, italic=True, name="DocRef", warn=False)
    notes(s, """
Every other technology on the previous slides was chosen off the shelf; this is the one
component the project trains itself, so it deserves its own slide.

The recipe on the left was read out of the promoted checkpoint and its results file, not
from memory: a COCO-pretrained yolov8-nano starting point, one class named barcode, an
input size of six hundred and forty pixels, batch sixteen, the AdamW optimiser at an
initial learning rate of two thousandths, seed zero, and the workstation's RTX 2050. The
augmentation profile is deliberately tame — mosaic, shear, perspective and flips are
disabled because they destroy barcode geometry on a small dataset.

Two panels on the right carry the decisions behind the recipe. The nano model exists
because of the measured latency: twenty-two and a half milliseconds on the GPU against
two hundred and eighteen on the CPU, against an eighty-three millisecond frame budget.

And promotion is gated: a candidate must show zero false positives on barcode-free
documents and match the active model on held-out cards, and a missing baseline blocks
promotion rather than permitting it. The full annotated list lives in
technology-stack-dot-m-d.
""")
    return s


# =========================================================================== #
# 08 architecture
# =========================================================================== #
def s08_architecture(prs, ev):
    s = new_slide(prs)
    header(s, "02 · Architecture", "Three tiers inside one process",
           "Presentation, application services and the local data tier — synchronous calls, not network messages.")
    band_y = [TOP + 0.00, TOP + 1.66, TOP + 3.32]
    band_h = 1.44
    labels = [
        ("Presentation — ui/", "Ten routed pages + dialogs", ORANGE, CREAM,
         ["Dashboard", "Students", "Scanner", "Enrollment", "Courses & subjects",
          "Assessment", "Certificate", "Reports", "Training", "Settings"]),
        ("Application services — services/", "Eighteen service modules", DARK, SOFT,
         ["ScanPipeline", "YOLODetector", "BarcodeDecoder", "CameraService",
          "StudentService", "AssessmentService", "ReportService", "IdCardService"]),
        ("Data — database/ · models/ · files", "Repositories, model weights, documents",
         INK, PALE_BLUE,
         ["StudentRepository", "ScanRepository", "AssessmentRepository",
          "Connection pool (10)", "MySQL wit_barcode_system", "best.pt weights",
          "reports/ · generated_id_cards/"]),
    ]
    for (title, sub, accent, fill, chips), y in zip(labels, band_y):
        rect(s, M, y, CW, band_h, fill=fill, line=BORDER, radius=True,
             name="Tier" + title[:6])
        rect(s, M, y, CW, 0.09, fill=accent, name="TierAccent")
        tx(s, M + 0.22, y + 0.16, 5.6, 0.32, title, size=21, color=INK, bold=True,
           name="TierTitle", warn=False)
        tx(s, M + 0.22, y + 0.52, 5.6, 0.28, sub, size=16, color=MUTED,
           name="TierSub", warn=False)
        cx = M + 0.22
        cw = (CW - 0.44 - (len(chips) - 1) * 0.11) / len(chips)
        for label in chips:
            chip(s, cx, y + 0.86, cw, label, fill=PAPER, color=SLATE, size=15, h=0.42)
            cx += cw + 0.11
    for y in (band_y[0] + band_h, band_y[1] + band_h):
        arrow(s, W / 2, y + 0.02, W / 2, y + 0.20, width=3, name="TierArrow")
    tx(s, M + 0.30, band_y[0] + band_h + 0.00, 3.2, 0.26, "signals · slots · calls",
       size=15, color=MUTED, italic=True, warn=False)
    tx(s, M + 0.30, band_y[1] + band_h + 0.00, 3.6, 0.26,
       "SQL through repositories · pooled connections", size=15, color=MUTED,
       italic=True, warn=False)
    caption(s, "Each tier is a Python package boundary, not a deployment boundary.", y=6.72)
    notes(s, """
This is the structural claim of the project: three tiers, one process, no hidden service
layer anywhere.

The top band is the presentation tier — ui/ — with ten routed pages plus the dialogs and
custom widgets. It owns nothing but state and layout. The middle band is the application
services tier — eighteen modules in services/. The important one for this presentation is
ScanPipeline, which coordinates detection, region gating, decoding and lookup. Note that
it contains no Qt code and no threads, which is exactly why five hundred tests can drive
it without a camera.

The bottom band is the data tier: four repository classes over a pooled MySQL connection,
the model weights on disk, and the document outputs on the filesystem.

Two arrows connect the bands, and they mean ordinary Python calls — Qt signal-slot
connections upwards, repository calls downwards. There is no queue, no RPC and no message
broker. That simplicity is why the system runs from a copied folder, and it is also why
thread discipline and error handling live in the page code — which is precisely where two
of the three known product defects are found.

The GPU note matters operationally: if CUDA is unavailable, the detector silently falls
back to CPU for the session, and the latency slide later shows what that costs.
""")
    return s


# =========================================================================== #
# 09 modules
# =========================================================================== #
def s09_modules(prs, ev):
    s = new_slide(prs)
    header(s, "02 · Codebase", "Application modules — what actually works",
           "Status was checked against the router and page classes, then confirmed in the running application.")
    rows = [
        ["Module", "State", "What is actually there"],
        ["Dashboard", "Working", "KPI cards, database health probe, global quick search"],
        ["Students", "Working", "Search, filters, pagination, create / edit / delete dialog"],
        ["Barcode Scanner", "Working", "Live camera, YOLO, decode, student panel, history"],
        ["Subject Enrollment", "Working", "Student selector, term tabs, subject lines and fees"],
        ["Course catalogue", "Working", "Read-only subjects and courses, UNIQUE codes"],
        ["Assessment & Fees", "Working", "Charges, schedules, balances, payment dialog"],
        ["Certificate of Matriculation", "Working", "Live preview, Save PDF, Print"],
        ["Reports & Analytics", "Working", "Five report cards, background generation"],
    ]
    tbl = table(s, M, TOP, CW, rows, [3.55, 1.55, 6.99], size=20, header_size=20,
                row_h=0.48,
                align=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.LEFT],
                name="ModulesTable")
    for r in range(1, len(rows)):
        run = tbl.cell(r, 1).text_frame.paragraphs[0].runs[0]
        run.font.color.rgb = GREEN
        run.font.bold = True
        tbl.cell(r, 1).fill.fore_color.rgb = PALE_GREEN
    tx(s, M, 5.94, CW, 0.32,
       "Not fully delivered — covered in section 05:", size=18, color=DARK,
       bold=True, warn=False)
    chip(s, M, 6.28, 3.85, "Partial: Training Center · Settings", fill=PALE_GOLD)
    chip(s, M + 4.02, 6.28, 3.98, "Unrouted: Barcode Generator page", fill=PALE_RED,
         color=RED)
    chip(s, M + 8.17, 6.28, 3.20, "Stub: logout / no login", fill=PALE_RED, color=RED)
    tx(s, M, 6.80, CW, 0.26,
       "122 first-party Python files · 45,978 lines (wc -l, venv and generated assets excluded)",
       size=15, color=MUTED, italic=True, warn=False)
    notes(s, """
This table is the ground truth for every module claim in the walkthrough that follows.
"Working" means the primary workflow was actually exercised to capture the screenshots —
not that the page merely imports.

Eight of the thirteen user-facing modules are working. The dashboard shows live KPI
counts and a real database health probe. The students page searches, filters and edits the
same rows the scanner resolves. The scanner page is the centre of the project and we will
look at it in detail. Enrollment, the read-only course catalogue, assessment and fees, the
Certificate of Matriculation and the reports page are all functional against the live
schema.

Three modules are partial, and the chips along the bottom name them precisely rather than
saying "needs work". The Training Center renders its configuration and registry UI but
reads the dataset_images database table, which is empty — so it shows zero while two and a
half thousand files sit on disk. The settings page persists its general tab but several
controls are inert. The ID-card preview dialog raises an exception even though the
rendering service itself is correct.

And one fully implemented page — the barcode generator, about fourteen hundred lines of
UI — has no route in the sidebar, so a user cannot reach it at all.

The line and file counts come from wc -l over the first-party packages only.
""")
    return s


# =========================================================================== #
# 10 dashboard
# =========================================================================== #
def s10_dashboard(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Walkthrough", "Dashboard and global navigation",
           "The landing page: live KPI counts, a database health probe and quick search from every page.")
    image_panel(s, SHOTS / "01b_dashboard_kpis.png", M, TOP, 7.60, 2.55, pad=0.04,
                name="DashboardKpis")
    image_panel(s, SHOTS / "17_global_search.png", 8.45, TOP, 4.27, 2.55, pad=0.04,
                name="GlobalSearch")
    tx(s, M, 4.50, 7.60, 0.28, "KPI STRIP · LIVE COUNTS", size=16, color=DARK,
       bold=True, warn=False)
    tx(s, 8.45, 4.50, 4.27, 0.28, "QUICK SEARCH · CTRL+K", size=16, color=DARK,
       bold=True, warn=False)
    bullets(s, M, 4.86, 7.60, 1.60, [
        ("3 students total, 3 active, 0 missing barcodes ", "— real rows, not placeholders."),
        ("Health probe: ", "“Database: Connected” is printed after a real query."),
    ], size=20, gap=8)
    bullets(s, 8.45, 4.86, 4.27, 1.60, [
        "Searches name, student ID and reference number from any page.",
        "Ten routed sidebar pages plus the logout stub.",
    ], size=20, gap=8)
    rect(s, M, 6.44, CW, 0.52, fill=PALE_RED, line=RGBColor(0xFE, 0xCA, 0xCA),
         radius=True, name="DefectNote")
    tx(s, M + 0.18, 6.53, CW - 0.36, 0.36,
       "Known defect: the student table below this strip raises a TypeError — the screenshot "
       "is cropped above the fault; slide 37 shows the error.",
       size=17, color=RGBColor(0x99, 0x1B, 0x1B), name="DefectText", warn=False)
    notes(s, """
The dashboard is the first thing an operator sees, and it is also where the first of the
three known product defects lives — so let me be precise about what works and what does
not.

The KPI strip is correct. It counts the same student rows the Students page uses: three
students, all active, all with a generated barcode. The health indicator in the top bar is
not decoration — it is printed after a real query against the database, so the operator
can tell a disconnected server from an empty one. On the right, the global quick-search
dialog is reachable with Control-K from any page and searches names, student IDs and
reference numbers in one query.

What does not work is the student table directly beneath the KPI strip. It raises a
Python TypeError — set_cell received an unexpected keyword argument called "family" —
and because the page reports every exception with the same message, the interface
labelled a programming error as "cannot reach the database". That is a misleading error
contract, and it is on the defect list for the roadmap.

The screenshot is deliberately cropped above the fault so the working part can be seen
clearly. The failure itself is shown again in the verification section.
""")
    return s


# =========================================================================== #
# 11 students
# =========================================================================== #
def s11_students(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Walkthrough", "Student directory and record management",
           "Search, filter, page and edit the same student rows that the scanner resolves.")
    image_panel(s, SHOTS / "02_students_directory.png", M, TOP, 8.15, 4.75, pad=0.04,
                name="StudentsDirectory")
    image_panel(s, SHOTS / "03_students_search.png", 9.00, TOP, 3.72, 2.30, pad=0.04,
                name="StudentsSearch")
    tx(s, 9.00, 4.32, 3.72, 0.28, "FILTER EXAMPLE: “CAPORAL”", size=16, color=DARK,
       bold=True, warn=False)
    bullets(s, 9.00, 4.66, 3.72, 2.10, [
        "Name, ID or reference search.",
        "Status / program / year filters.",
        "Create, edit, delete — one dialog.",
        "Portraits from assets/profile/.",
    ], size=19, gap=7)
    caption(s, "Three real records — Computer Engineering, 3rd Year — from the live database.",
            y=6.70)
    notes(s, """
Student records are the join point of the entire system: enrollment, assessment, barcode
and ID cards all reference the students table by its primary key, so this page is the
administrative backbone the rest of the application leans on.

The directory shows the three demonstration records — all Computer Engineering students,
all in their third year, all active. Search covers names, the internal student ID and the
nine-digit reference number, and the filter chips narrow by status, programme and year
level. Pagination is set to twenty-five rows per page, which is more than the
demonstration data needs but is the production setting.

The dialog on the right handles create, edit and delete, and the same service behind it is
what the scan result dialog calls when it resolves a student — so a failure here would
propagate everywhere, which is why the post-scan workflow tests matter later in the deck.

One detail worth noting: the directory search is also used by the scanner's own lookup
path, and the portrait images are resolved from a local assets folder rather than stored
in the database — the database holds only the file path, which keeps the schema small but
means the assets folder must travel with the data.
""")
    return s


# =========================================================================== #
# 12 identity design
# =========================================================================== #
def s12_identity(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Identity design", "CODE39 payloads printed on the physical cards",
           "The decoder must match the cards that already exist, so the implementation follows the physical symbol.")
    cards = [
        ("Javy Danielle N. Caporal", "WIT-2024-001  ·  reference 65412",
         "payload 000065412", "barcode_2.png"),
        ("Ronna Mae D. Alemania", "WIT-2024-002  ·  reference 65902",
         "payload 000065902", "barcode_1.png"),
        ("Steven Anthony M. Oquiana", "WIT-2024-003  ·  reference 65736",
         "payload 000065736", "barcode_3.png"),
    ]
    y = TOP + 0.02
    for name, sid, payload, asset in cards:
        rect(s, M, y, 7.75, 1.50, fill=PAPER, line=BORDER, radius=True, name="CardRow")
        tx(s, M + 0.22, y + 0.16, 4.05, 0.34, name, size=21, color=INK, bold=True,
           warn=False)
        tx(s, M + 0.22, y + 0.56, 4.05, 0.30, sid, size=17, color=SLATE, warn=False)
        tx(s, M + 0.22, y + 0.94, 4.05, 0.30, payload, size=17, color=DARK, bold=True,
           warn=False)
        image_panel(s, ROOT / "assets" / "barcodes" / asset, M + 4.42, y + 0.24,
                    3.15, 1.02, pad=0.02, name="BarcodeImg")
        y += 1.62
    rect(s, 8.62, TOP + 0.02, 4.10, 4.72, fill=CREAM, line=BORDER, radius=True,
         name="WhyPanel")
    tx(s, 8.84, TOP + 0.20, 3.66, 0.34, "Why CODE39?", size=21, color=DARK, bold=True,
       warn=False)
    bullets(s, 8.84, TOP + 0.66, 3.66, 3.84, [
        "The physical cards already carry CODE39.",
        "Nine digits × module width 2 fit a CR80 card.",
        "barcode_payload is UNIQUE — the lookup key.",
        "README says CODE128; code and DB use CODE39.",
    ], size=19, gap=9)
    tx(s, M, 6.76, CW, 0.28,
       "The generator page is implemented but unrouted — cards are produced outside the app flow.",
       size=17, color=MUTED, italic=True, warn=False)
    notes(s, """
This slide fixes what "identity" means in this system, because everything downstream
depends on it.

Each student carries a nine-digit zero-padded reference as a CODE39 symbol. The three
payloads on the left are the real values in the database — 000065412, 000065902 and
000065736 — and the barcode images are the project's own generated assets. Note the file
naming trap: barcode-1 belongs to the second student and barcode-2 to the first. Nothing
in the application keys on a filename; it always keys on the decoded payload.

Why CODE39 rather than the more common CODE128? Because the cards already exist. The
physical specimens use CODE39, so the decoder has to match the artefact rather than the
documentation. Nine digits at module width two still fit comfortably on a CR80 card with a
two-millimetre quiet zone, and CODE39 needs no checksum for this length.

The payload is the key: students.barcode-payload carries a UNIQUE constraint, which is
what makes the scanner's lookup deterministic — one payload, one row, no ambiguity.

One documentation defect to flag honestly: the README still advertises CODE128 and a
WIT-colon-STUDENT-colon prefix. The code, the generator and the database all use CODE39
with a plain reference. That is a README problem, not a runtime problem.

The generator page that would print new symbols is fully implemented but has no sidebar
route, so today cards are produced outside the application flow.
""")
    return s


# =========================================================================== #
# 13 scanner experience
# =========================================================================== #
def s13_scanner(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Walkthrough", "The scanner experience",
           "Live camera and uploaded images share one pipeline; a verified read fills the student panel and appends history.")
    image_panel(s, SHOTS / "04_scanner_idle.png", M, TOP, 5.92, 3.60, pad=0.04,
                name="ScannerIdle")
    image_panel(s, SHOTS / "06_scanner_verified.png", 6.78, TOP, 5.94, 3.60, pad=0.04,
                name="ScannerVerified")
    tx(s, M, 5.56, 5.92, 0.30, "IDLE — OPERATOR AFFORDANCES", size=17, color=DARK,
       bold=True, warn=False)
    tx(s, 6.78, 5.56, 5.94, 0.30, "VERIFIED — 000065412 → JAVY D. CAPORAL", size=17,
       color=GREEN, bold=True, warn=False)
    bullets(s, M, 5.90, 5.92, 1.05, [
        "Camera, model status and live scan history.",
        "Upload Card Image fallback; Scan Next resets the latch.",
    ], size=18, gap=5)
    bullets(s, 6.78, 5.90, 5.94, 1.05, [
        "Panel, portrait and reference from one row.",
        "One history row, one tone, one dialog — fired once.",
    ], size=18, gap=5, bullet_color=GREEN)
    notes(s, """
The scanner is the centre of the project, so it is worth walking through both frames
slowly.

The idle frame on the left shows the operator affordances. At the top, a camera
selector — any Video4LInux device can be chosen — and next to it the model status
indicator, which reports which checkpoint is loaded. The large centre area is the live
preview with a yellow guide rectangle overlaid: that rectangle is a hint for the operator,
not the actual gate. The real acceptance region is a separate square covering eighty-eight
percent of the frame, and a detection must overlap it sufficiently before the pipeline
will even attempt a decode. On the right of that frame, the scan history table streams the
session's rows.

The verified frame on the right is the proof that all three stages agree on the same
person. The detector found the symbol, the decoder produced the payload 000065412, and the
database resolved that payload to Javy Danielle Caporal — portrait, reference and programme
all loaded from one row. A success tone plays and exactly one history row is inserted.

The "Scan Next Student" button clears the duplicate latch, which otherwise suppresses
repeated reads of the same card in consecutive frames.

Both frames were captured from the running application; the verified read came from feeding
the real card image through the pipeline, and the resulting database row was removed
afterwards so the counts you will see later are unaffected.
""")
    return s


# =========================================================================== #
# 14 scan pipeline
# =========================================================================== #
def s14_pipeline(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Scanner internals", "Detection, decoding and lookup are separate stages",
           "A YOLO hit identifies a region only — each later stage can fail without faking the next.")
    stages = [
        ("1 · Frame", "camera or\nuploaded image", PALE_BLUE,
         RGBColor(0x93, 0xC5, 0xFD)),
        ("2 · YOLOv8n", "detect barcode\nconf ≥ 0.75", CREAM, BORDER),
        ("3 · ROI gate", "overlap ≥ 0.65\nwith scan region", CREAM, BORDER),
        ("4 · Crop", "margin 0.20\nmin 80 × 24 px", CREAM, BORDER),
        ("5 · pyzbar", "decode ≤ 6\nattempts", CREAM, BORDER),
        ("6 · SQL lookup", "payload =\nbarcode_payload", CREAM, BORDER),
    ]
    bw, gap = 1.78, 0.26
    x = M
    for i, (title, sub, fill, line) in enumerate(stages):
        node(s, x, TOP + 0.10, bw, 1.62, title, sub, fill=fill, line=line, tsize=20,
             ssize=16, name=f"Stage{i+1}")
        if i:
            arrow(s, x - gap - 0.02, TOP + 0.91, x + 0.02, TOP + 0.91, width=2.5,
                  name="FlowArrow")
        x += bw + gap
    tx(s, M, TOP + 1.92, CW, 0.32, "Each stage has its own failure state — none of them "
       "collapses into “unknown student”:", size=19, color=INK, bold=True, warn=False)
    fails = [
        ("No symbol in frame", "keep scanning · no row written", PALE_RED, RED),
        ("Outside the gate", "rejected · rectangle is not centred", PALE_RED, RED),
        ("Crop unusable", "skipped · below 80 × 24 pixels", PALE_RED, RED),
        ("Decode failed", "UNKNOWN CROP · payload not read", PALE_RED, RED),
        ("No matching row", "NOT_FOUND · decoded value stored", PALE_RED, RED),
    ]
    fw = (CW - 4 * 0.22) / 5
    x = M
    for title, sub, fill, line in fails:
        node(s, x, TOP + 2.42, fw, 1.16, title, sub, fill=fill, line=line, tsize=18,
             ssize=15, tcolor=RGBColor(0x99, 0x1B, 0x1B), name="FailState")
        x += fw + 0.22
    rect(s, M, TOP + 3.62, CW, 1.00, fill=PALE_GREEN, line=RGBColor(0xBB, 0xF7, 0xD0),
         radius=True, name="VerifiedState")
    tx(s, M + 0.24, TOP + 3.76, 3.05, 0.72,
       "VERIFIED", size=26, color=RGBColor(0x16, 0x65, 0x34), bold=True, warn=False)
    tx(s, M + 3.30, TOP + 3.76, CW - 3.60, 0.74,
       "only when decode and exact SQL match agree — the student panel, one history row, "
       "the success tone and the result dialog all fire at this point and no earlier.",
       size=19, color=RGBColor(0x16, 0x65, 0x34), spacing=1.10, warn=False)
    caption(s, "Thresholds are the runtime values recorded in .env (services/scan_pipeline.py).",
            y=6.68)
    notes(s, """
This is the most important design decision in the scanner, and it is worth stating
plainly: detection, decoding and lookup are three separate stages, each of which can fail
independently.

Stage one acquires a frame — from the camera, or from an uploaded card image. Stage two
runs the YOLOv8n detector at a confidence threshold of 0.75, recorded in the environment
file. All that stage produces is a rectangle; a detection says nothing about the payload
and nothing about the student. Stage three is a region-of-interest gate: the detection must
overlap the operator's scan region by at least 0.65, measured against the detection box,
which is what stops a barcode glimpsed at the edge of the frame from being read. Stage four
crops the symbol with a twenty percent margin, rejecting crops smaller than eighty by
twenty-four pixels rather than feeding garbage to the decoder. Stage five is pyzbar with a
bounded budget of six image preparations. Stage six is an exact match on the unique
barcode-payload column.

The red row underneath shows the five distinct failure states. Because they are distinct,
the interface can honestly say "barcode found but not decodable" instead of collapsing
everything into "unknown student". Only the green state — decode and database agreement —
loads a student panel and plays the success tone.

This separation is also why the pipeline is testable without a camera.
""")
    return s


# =========================================================================== #
# 15 YOLO model
# =========================================================================== #
def s15_yolo(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Detection model", "YOLOv8n fine-tuned for one class: barcode",
           "A nano detector — 6.48 MB — keeps inference fast enough for a live camera preview.")
    chain = [
        ("Input", "640 × 640\nletterboxed", PALE_BLUE, RGBColor(0x93, 0xC5, 0xFD)),
        ("Backbone", "CBS · C2f · SPPF", CREAM, BORDER),
        ("Neck", "PAN-FPN fusion", CREAM, BORDER),
        ("Head", "decoupled · anchor-free", CREAM, BORDER),
        ("NMS", "IoU 0.45 → one box", CREAM, BORDER),
        ("Output", "box + confidence", PALE_GREEN, RGBColor(0xBB, 0xF7, 0xD0)),
    ]
    bw, gap = 1.849, 0.20
    x = M
    for i, (title, sub, fill, line) in enumerate(chain):
        node(s, x, TOP + 0.08, bw, 1.45, title, sub, fill=fill, line=line, tsize=20,
             ssize=15, name=f"Yolo{i}")
        if i:
            arrow(s, x - gap - 0.02, TOP + 0.80, x + 0.02, TOP + 0.80, width=2.5)
        x += bw + gap
    rect(s, M, TOP + 1.68, CW, 0.86, fill=PALE_GREEN, line=RGBColor(0xBB, 0xF7, 0xD0),
         radius=True, name="OutputBox")
    tx(s, M + 0.22, TOP + 1.82, CW - 0.44, 0.60,
       "A detection is a rectangle, not an identity  →  crop  →  decode  →  SQL lookup",
       size=20, color=RGBColor(0x16, 0x65, 0x34), bold=True, anchor=MSO_ANCHOR.MIDDLE,
       warn=False)
    section_tag(s, "Verified checkpoint properties", M, TOP + 2.64)
    rows = [
        ["Property", "Value"],
        ["Checkpoint", "best.pt (≈ 6.5 MB)"],
        ["Classes", "1 — “barcode”"],
        ["Confidence", "0.75 runtime (default 0.50)"],
        ["Device", "CUDA:0, CPU fallback"],
    ]
    table(s, M, TOP + 2.90, 5.95, rows, [2.20, 3.75], size=19, header_size=19,
          row_h=0.43, name="YoloTable")
    rect(s, 6.82, TOP + 2.90, 5.90, 2.20, fill=SOFT, line=BORDER, radius=True,
         name="ChoicePanel")
    tx(s, 7.04, TOP + 3.06, 5.46, 0.32, "Why a nano model?", size=20, color=DARK,
       bold=True, warn=False)
    bullets(s, 7.04, TOP + 3.46, 5.46, 1.56, [
        "One symbol, one class — narrow.",
        "6.5 MB loads instantly.",
        "CPU fallback works (slide 36).",
    ], size=18, gap=7)
    caption(s, "Architecture confirmed from the checkpoint metadata with Ultralytics 8.4.177.",
            y=6.70)
    notes(s, """
The detector is a YOLOv8n — the nano variant of the eighth-generation YOLO family —
fine-tuned on the project's own barcode dataset. It is loaded from models-yolo slash
active slash best-dot-pt, six and a half megabytes on disk.

Follow the chain left to right. The input is letterboxed to 640 by 640 pixels, the standard
YOLO input size. The backbone — a stack of convolution blocks, C2f modules and a spatial
pyramid pooling layer — extracts features at three scales. The neck, a PAN-FPN
arrangement, fuses those scales so that both distant and close barcodes are represented.
The head is decoupled and anchor-free, which means classification and box regression are
separate branches and the anchors are predicted directly rather than selected from a
predefined set. Non-maximum suppression at IoU 0.45 then collapses overlapping boxes into
one detection per symbol.

The output is a rectangle and a confidence — nothing more. Everything to the right of that
arrow on the previous slide — crop, decode, lookup — happens outside the network.

Why the nano model? The task is deliberately narrow: one printed class, controlled
acquisition, a fixed card format. A larger model would add latency without adding
capability here, and the 6.5 megabyte footprint loads instantly on the target workstation,
which has an RTX 2050 with CUDA available and a CPU fallback when it is not.

The runtime confidence of 0.75 was chosen after measurement; the code default is 0.50.
""")
    return s


# =========================================================================== #
# 16 detection evidence
# =========================================================================== #
def s16_detection(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Detection evidence", "The detector finds all three real cards",
           "Inference on the checked-in card images using the active checkpoint and the runtime threshold.")
    image_panel(s, SHOTS / "23_yolo_inference_boxes.png", M, TOP, 4.05, 4.40, pad=0.03,
                name="InferenceBoxes")
    rows = [
        ["Card", "Frame size", "Box (x, y, w, h)", "Confidence"],
        ["Id_1.png", "590 × 1004", "19, 789, 540, 111", "0.9545"],
        ["Id_2.png", "590 × 1004", "46, 784, 501, 130", "0.9480"],
        ["Id_3.png", "270 × 459", "13, 359, 239, 64", "0.9213"],
    ]
    table(s, 5.00, TOP + 0.04, 7.72, rows, [1.60, 1.80, 2.72, 1.60], size=20,
          header_size=20, row_h=0.52,
          align=[PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.LEFT, PP_ALIGN.CENTER],
          name="DetectionTable")
    section_tag(s, "What the numbers say", 5.00, TOP + 2.18)
    bullets(s, 5.00, TOP + 2.50, 7.72, 1.24, [
        "Every card clears 0.92 — margin over the 0.75 threshold.",
        "Id_3, the smallest specimen, still detects cleanly.",
        "A detection is a region: decode and SQL lookup still needed.",
    ], size=18, gap=8)
    kpi(s, 5.00, TOP + 3.80, 2.45, 1.30, "0.995", "mAP@0.50", "run v7-distance",
        fill=CREAM, vsize=24)
    kpi(s, 7.62, TOP + 3.80, 2.45, 1.30, "0.8075", "mAP@0.50:0.95", "best epoch 34",
        fill=CREAM, vsize=24)
    kpi(s, 10.24, TOP + 3.80, 2.48, 1.30, "0.9987", "Recall", "precision 0.9975",
        fill=PALE_GREEN, accent=GREEN, vsize=24)
    notes(s, """
This is not a synthetic demonstration. The three coordinates and confidences in the table
were printed by the active model itself when the three checked-in card assets were pushed
through services-slash-yolo-detector-dot-py at the runtime threshold of 0.75.

Read the confidence column first. Every card clears 0.92 — that is a margin of more than
twenty percent above the acceptance threshold, which matters operationally: it means
ordinary variation in exposure or distance will not drop a card below the cut-off. The
third card is the interesting case. It is the smallest specimen in the repository, only
270 by 459 pixels, and it still detects cleanly with a box 239 pixels wide. That is the
case that failed before the decoder was given its upscaling attempts, and it is why the
distance dataset exists.

The annotated image on the left is the model's own output — the bounding box drawn by
Ultralytics — scaled up for legibility on the projector.

Now, the discipline this slide is teaching: a detection is a rectangle and a confidence,
nothing more. It does not tell you which student holds the card, and it cannot be presented
as identification. Decode and an exact database lookup are still required before the
system will name anybody.

The three tiles underneath are validation metrics from the training run, which we will
look at properly in the model section.
""")
    return s


# =========================================================================== #
# 17 decoding
# =========================================================================== #
def s17_decoding(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Decoding", "From pixels to a payload — bounded attempts",
           "The gate decides which detections reach pyzbar; the decoder tries a short ordered list and records what worked.")
    image_panel(s, SHOTS / "05_scanner_detection.png", M, TOP, 6.05, 3.95, pad=0.04,
                name="DetectionShot")
    tx(s, M, 5.98, 6.05, 0.30, "LIVE DETECTION — BOX, GATE AND DECODED VALUE", size=16,
       color=DARK, bold=True, warn=False)
    section_tag(s, "Gate — which crops are attempted", 7.00, TOP + 0.02)
    bullets(s, 7.00, TOP + 0.36, 5.72, 1.90, [
        "Overlap ≥ 0.65 with the scan region.",
        "Crop margin 0.20; minimum 80 × 24 px.",
        "De-duplication at IoU 0.70.",
    ], size=19, gap=8)
    section_tag(s, "Decode — bounded attempts", 7.00, TOP + 2.28)
    bullets(s, 7.00, TOP + 2.62, 5.72, 1.90, [
        "At most 6 ordered attempts per crop.",
        "Colour, greyscale, CLAHE, Otsu, 2× and 3× upscale.",
        "Two distinct payloads → AMBIGUOUS, never a guess.",
    ], size=19, gap=8)
    rect(s, M, 6.36, CW, 0.52, fill=PALE_GOLD, line=RGBColor(0xFD, 0xE6, 0x8A),
         radius=True, name="UpscaleNote")
    tx(s, M + 0.20, 6.44, CW - 0.40, 0.36,
       "Why upscaling exists: the smallest card supplies only ~1.85 px per narrow CODE39 "
       "element at native size.",
       size=17, color=RGBColor(0x92, 0x40, 0x0E), name="UpscaleText", warn=False)
    notes(s, """
Two mechanisms deserve detail here, because they are what make difficult reads succeed
without letting the pipeline run away.

The gate comes first. A detection must overlap the operator's scan region by at least
0.65, measured as intersection over the detection box — that is what stops a barcode
glimpsed at the edge of the preview from being decoded at all. Accepted detections are
expanded by a twenty percent margin before cropping, and anything smaller than eighty by
twenty-four pixels is skipped outright instead of being handed to the decoder as noise.
Candidates are de-duplicated at IoU 0.70 so that one symbol appearing in consecutive
frames is not decoded over and over.

The decoder itself is deliberately bounded: at most six ordered attempts per crop — the
original colour image, greyscale, CLAHE contrast enhancement, Otsu thresholding, and two
upscale variants at two and three times. Six attempts, not an unbounded search over every
filter combination, which keeps a frame from stalling on a hopeless crop.

Two safety properties follow. First, every payload is kept, not just the first: if two
distinct values are read from the same crop the read is marked AMBIGUOUS and the system
refuses to choose. Second, the method and the attempt count travel with the result, so the
interface can say how hard a read was rather than pretending every decode is equally
clean.

The upscaling exists because the smallest card provides only about 1.85 pixels per narrow
CODE39 element — below what ZBar reliably needs.
""")
    return s


# =========================================================================== #
# 18 scan outcomes
# =========================================================================== #
def s18_outcomes(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Outcomes", "Verified, unknown and ambiguous are different states",
           "The interface never collapses a failed lookup into a success — and the database proves it.")
    image_panel(s, SHOTS / "06_scanner_verified.png", M, TOP, 3.92, 2.60, pad=0.04,
                name="OutcomeVerified")
    image_panel(s, SHOTS / "08_scanner_unknown.png", 4.72, TOP, 3.92, 2.60, pad=0.04,
                name="OutcomeUnknown")
    image_panel(s, SHOTS / "07_scan_result_dialog.png", 9.42, TOP, 3.30, 2.60, pad=0.04,
                name="OutcomeDialog")
    tx(s, M, 4.56, 3.92, 0.30, "VERIFIED — student resolved", size=17, color=GREEN,
       bold=True, warn=False)
    tx(s, 4.72, 4.56, 3.92, 0.30, "NOT_FOUND — decoded, no row", size=17, color=RED,
       bold=True, warn=False)
    tx(s, 9.42, 4.56, 3.30, 0.30, "RESULT DIALOG", size=17, color=DARK, bold=True,
       warn=False)
    bullets(s, M, 4.90, 3.92, 1.30, [
        "Panel, portrait and reference loaded.",
        "One history row, one success tone.",
    ], size=18, gap=5, bullet_color=GREEN)
    bullets(s, 4.72, 4.90, 3.92, 1.30, [
        "Decoded value is shown, student is not.",
        "not_found row written; no success tone.",
    ], size=18, gap=5, bullet_color=RED)
    kpi(s, 9.42, 4.90, 1.55, 1.24, "65", "Scans on file", fill=CREAM, vsize=26)
    kpi(s, 11.10, 4.90, 1.62, 1.24, "41 / 24", "Success / not found", fill=PALE_GREEN,
        accent=GREEN, vsize=22)
    tx(s, M, 6.42, CW, 0.50,
       "The 24 not-found rows are deliberate test reads against unknown payloads, not silent "
       "failures — a database error is a third, separate ERROR state.",
       size=18, color=MUTED, italic=True, warn=False)
    notes(s, """
The unknown state is the slide that proves the system does not treat a decode as identity,
so it is worth dwelling on.

On the left, the verified path: the payload matched a row, and the student panel, portrait
and reference all loaded from the database. In the middle, the not-found path. A symbol was
decoded successfully — the interface shows the decoded value — but no student row carries
that payload, so no panel appears, no success tone plays, and a not-found row is written to
the scan history with the value it read. A member of staff can therefore see every attempt
against an unrecognised card, which is exactly what you want at a counter.

There are two further states that are not pictured. AMBIGUOUS fires when two distinct
payloads are read from one crop — the system refuses to choose between them. And ERROR is
reserved for a genuine database failure, which is reported as itself rather than being
mislabeled as an unknown student.

The counts come from the live scan-history table: sixty-five records, forty-one successes
and twenty-four not-found. The not-found rows were created deliberately during testing with
unknown payloads — they are evidence of the state machine working, not silent failures.

The dialog on the right is the structured result view: the same outcome, presented as a
popup with the scan details, separate from the persistent student panel.
""")
    return s


# =========================================================================== #
# 19 end-to-end workflow
# =========================================================================== #
def s19_workflow(prs, ev):
    s = new_slide(prs)
    header(s, "03 · End-to-end flow", "The complete scan workflow — all branches",
           "Every branch below maps to a real state in scan_pipeline.py or scan_session.py.")
    main_x = M + 0.30
    mw = 3.10
    cx = main_x + mw / 2
    node(s, main_x, TOP + 0.02, mw, 0.74, "Scan Next Student · camera frame",
         None, fill=SOFT, line=BORDER, tsize=18, name="Step0")
    decisions = [
        ("Symbol in the\nscan region?", 3.02),
        ("Payload decoded\n(≤ 6 attempts)?", 4.14),
        ("Matches a\nstudents row?", 5.26),
    ]
    node(s, main_x, TOP + 4.04, mw, 0.80, "VERIFIED",
         None, fill=PALE_GREEN, line=RGBColor(0xBB, 0xF7, 0xD0), tsize=22,
         tcolor=RGBColor(0x16, 0x65, 0x34), name="Verified")
    tx(s, main_x, TOP + 4.56, mw, 0.28, "panel · history row · tone", size=15,
       color=RGBColor(0x16, 0x65, 0x34), align=PP_ALIGN.CENTER, warn=False)
    for i, (text, cy) in enumerate(decisions):
        diamond(s, cx, cy, 3.10, 0.96, text, tsize=17, name=f"Decision{i}")
    arrow(s, cx, TOP + 0.78, cx, 2.54, width=2.5, name="Arrow0")
    arrow(s, cx, 3.50, cx, 3.66, width=2.5, name="Arrow1")
    arrow(s, cx, 4.62, cx, 5.02, width=2.5, name="Arrow2")
    tx(s, cx + 0.06, TOP + 2.86, 0.7, 0.26, "yes", size=15, color=GREEN, bold=True,
       warn=False)
    tx(s, cx + 0.06, TOP + 3.98, 0.7, 0.26, "yes", size=15, color=GREEN, bold=True,
       warn=False)
    branches = [
        ("No symbol — keep scanning", "no row written", 3.02),
        ("UNKNOWN CROP — decode failed", "logged for review", 4.14),
        ("NOT_FOUND — value stored", "decoded payload saved", 5.26),
    ]
    for text, sub, cy in branches:
        node(s, 4.20, cy - 0.44, 3.45, 0.88, text, sub, fill=PALE_RED,
             line=RGBColor(0xFE, 0xCA, 0xCA), tsize=16, ssize=14,
             tcolor=RGBColor(0x99, 0x1B, 0x1B), name="Branch")
        arrow(s, 4.02, cy, 4.20, cy, color=RED, width=2, name="BranchArrow")
        tx(s, 3.94, cy - 0.26, 0.7, 0.26, "no", size=15, color=RED, bold=True,
           warn=False)
    node(s, 8.05, 4.82, 4.60, 0.88, "DUPLICATE — frame ignored",
         "content-aware latch, not a timer", fill=PALE_GOLD,
         line=RGBColor(0xFD, 0xE6, 0x8A), tsize=17, ssize=14, name="Duplicate")
    arrow(s, 4.02, 5.26, 8.05, 5.26, color=GREEN, width=2.5, name="VerifiedArrow")
    tx(s, 8.10, 4.30, 4.55, 0.46,
       "Duplicate check runs inside the verified branch: the same payload is ignored, a "
       "different card is accepted immediately.",
       size=15, color=SLATE, spacing=1.06, warn=False)
    caption(s, "Camera, gate, decode, unknown payload, duplicate frame and database outage "
               "each have a distinct path.", y=6.70)
    notes(s, """
This flowchart is the best single answer to "what happens when it goes wrong", and every
branch on it is implemented code, not a hypothetical.

The main path runs down the left in green-labelled steps. An operator starts a scan, or a
camera frame arrives. The detector must find a symbol inside the scan region — if nothing
is found the frame is simply dropped and no row is written, which keeps the history table
free of noise. If a symbol is found but the decoder cannot produce a payload within its six
attempts, the read is marked UNKNOWN CROP and logged for review rather than guessed at.

If a payload is produced, it is checked against the unique barcode-payload column. No
match means a NOT_FOUND record containing the decoded value — the operator sees what was
read, and the database keeps the evidence.

The last decision is the duplicate latch. If the payload equals the last verified card, the
frame is ignored. Importantly, that latch is content-aware — it compares payloads — not a
blind timer, so two different students scanned in quick succession are both accepted, while
the same card held in front of the camera produces exactly one history row.

Only the full green path — decode plus database agreement plus a fresh payload — fires the
student panel, the success tone and the history insert.

A database outage during any of this is a separate ERROR state that is reported as itself.
""")
    return s


# =========================================================================== #
# 20 enrollment & catalogue
# =========================================================================== #
def s20_enrollment(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Walkthrough", "Subject enrollment and the course catalogue",
           "One student is selected once; each term becomes a tab of subject lines, schedules and fees.")
    image_panel(s, SHOTS / "09_enrollment.png", M, TOP, 8.15, 4.62, pad=0.04,
                name="EnrollmentShot")
    kpi(s, 9.00, TOP, 3.72, 1.30, "3", "Enrollments", "one per term per student",
        fill=CREAM, vsize=26)
    kpi(s, 9.00, TOP + 1.38, 3.72, 1.30, "21", "Subject lines", "7 subjects × terms",
        fill=CREAM, vsize=26)
    bullets(s, 9.00, TOP + 2.72, 3.72, 2.10, [
        "Schedules, rooms and fees per line.",
        "Status and fees are stored, not computed.",
        "Catalogue read-only: 1 course, 7 subjects.",
    ], size=19, gap=8)
    caption(s, "All seven catalogue subjects are attached to the live demonstration enrollments.",
            y=6.66)
    notes(s, """
Enrollment is where the barcode identity becomes an academic record, so this page is the
bridge between the scanner and the registrar's actual work.

An operator selects a student once, and every enrollment for that student appears as a tab
— one tab per academic term. The live demonstration data holds three enrollments and
twenty-one subject lines in total. The record on screen shows Steven Oquiana's 2024-2025
first-semester enrollment: seven subjects, twenty credits, each line carrying its section,
room, scheduled days, time, period and fees.

Two engineering details are worth stating. First, status and per-subject fees are stored
columns — enrollment-subjects holds the fee amounts and the line status — rather than
values computed on the fly in the widget, so the same numbers appear in reports and
documents. Second, adding an enrollment or a subject line goes through a modal dialog
backed by the same student-service and repository code the table reads from, which keeps
one write path.

The catalogue behind it — one Computer Engineering course and seven subjects, from CPE 311
to MATH E4 — is exposed read-only on this screen so an operator can confirm what exists
before attaching a subject line. Subject codes carry a unique constraint, and the foreign
keys mean an enrollment line can never point at a subject that does not exist.

The unique constraint on enrollment — student, academic year and semester together — is
what prevents duplicate term records.
""")
    return s


# =========================================================================== #
# 21 assessment
# =========================================================================== #
def s21_assessment(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Walkthrough", "Assessment, fees and balances",
           "Charges, discounts, payments and installment schedules are separate tables joined by one assessment per term.")
    image_panel(s, SHOTS / "11_assessment_fees.png", M, TOP, 8.15, 4.62, pad=0.04,
                name="AssessmentShot")
    kpi(s, 9.00, TOP, 3.72, 1.30, "PHP 82,899.93", "Assessed · 3 students",
        "33 assessment items", fill=CREAM, vsize=22)
    kpi(s, 9.00, TOP + 1.38, 3.72, 1.30, "PHP 0.00", "Payments recorded",
        "12 schedules, all pending", fill=PALE_RED, accent=RED, vsize=22)
    bullets(s, 9.00, TOP + 2.72, 3.72, 2.10, [
        "Tuition, lab and misc. fees as line items.",
        "Outstanding balance persisted per assessment.",
        "Payment dialog exists; demo data has none.",
    ], size=19, gap=8)
    caption(s, "Visible student: Steven A. M. Oquiana (PHP 27,633.31); totals cover all three.",
            y=6.66)
    notes(s, """
The financial module is the most normalised part of the schema, and the slide is careful
to distinguish what the code can do from what the demonstration data contains.

An assessment belongs to one student and one enrollment term. Its charges are separate
line items in the assessment-items table, each referencing one of twenty predefined
assessment categories — tuition, laboratory, miscellaneous and similar — and each carrying
its own verification flag. Discounts and payments are modelled the same way: payments
reference the assessment, and an installment schedule holds due dates, amounts due and
status.

The live totals are PHP 82,899.93 assessed across the three students, made up of thirty-
three items, with twelve payment schedules all currently pending.

Now the honest part: zero payments have been recorded. The payment dialog exists and the
write path is implemented, but nobody has entered a payment against the demonstration data,
so the paid column is a true zero rather than a bug. I would rather show you an honest zero
than a fabricated transaction.

The outstanding balance is persisted on the assessment row rather than recomputed from the
items each time, which keeps the number stable as line items are verified, and it is what
the reports and the certificate read.
""")
    return s


# =========================================================================== #
# 22 certificate
# =========================================================================== #
def s22_certificate(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Walkthrough", "Certificate of Matriculation",
           "Rendered from the same enrollment and subject rows the page displays — saved as A4 PDF or printed.")
    image_panel(s, SHOTS / "15_certificate_com.png", M, TOP, 8.05, 4.62, pad=0.04,
                name="CertificateShot")
    section_tag(s, "Document facts", 8.92, TOP + 0.02)
    bullets(s, 8.92, TOP + 0.36, 3.80, 3.10, [
        ("Live record: ", "Steven A. M. Oquiana, ref. 65736."),
        ("7 subjects · 20 credits ", "with schedules and fees."),
        ("Actions: ", "Save PDF · Print · open record."),
        ("Off the GUI thread, ", "with errors surfaced."),
    ], size=19, gap=8)
    rect(s, 8.92, TOP + 3.72, 3.80, 0.98, fill=PALE_GREEN,
         line=RGBColor(0xBB, 0xF7, 0xD0), radius=True, name="TruthNote")
    tx(s, 9.08, TOP + 3.84, 3.48, 0.76,
       "Not a separate data-entry form: the certificate reads enrollment records by "
       "student ID.",
       size=17, color=RGBColor(0x16, 0x65, 0x34), spacing=1.08, warn=False)
    caption(s, "Screenshot shows the live in-app preview, built from the student's records.",
            y=6.70)
    notes(s, """
The Certificate of Matriculation is the clearest example of the project's "one source of
truth" goal, so it is worth showing what actually happens when it is generated.

The page on screen is a live preview rendered inside the application — not a PDF opened in
a viewer. It shows Steven Oquiana's record: reference 65736, seven enrolled subjects,
twenty credits, and for each subject the code, the schedule, the room, the period and the
fees. Every one of those values comes from the enrollment and enrollment-subjects tables
through the same service calls the enrollment page uses. There is no separate data-entry
form for the certificate, which means the document cannot disagree with the record.

Two actions are available: Save PDF and Print. Both are built by ReportLab as an A4
document, and both run in a worker thread so the interface does not freeze while the
document is laid out — if the write fails, the error is surfaced to the operator instead
of being swallowed.

There is a saved artefact in the repository that proves the PDF path runs end to end:
reports/certificate-65412-20261017-112221.pdf, produced by this same service for a
different student. That file is evidence, not a mock-up.

If you are evaluating what "implemented" means in this project, this page is a good
benchmark: live preview, real data, threaded generation, and a file on disk as proof.
""")
    return s


# =========================================================================== #
# 23 ID cards
# =========================================================================== #
def s23_idcards(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Walkthrough", "ID card service — render works, checks do not",
           "Physically correct CR80 cards at 150 / 300 / 600 DPI, with two reproducible failures named below.")
    image_panel(s, SHOTS / "16a_id_card_front.png", M, TOP, 5.92, 3.30, pad=0.03,
                name="IdFront")
    image_panel(s, SHOTS / "16b_id_card_back.png", 6.80, TOP, 5.92, 3.30, pad=0.03,
                name="IdBack")
    tx(s, M, 5.30, 5.92, 0.30, "FRONT · 1011 × 638 px at 300 DPI", size=17, color=DARK,
       bold=True, warn=False)
    tx(s, 6.80, 5.30, 5.92, 0.30, "BACK · CR80 85.6 × 54 mm", size=17, color=DARK,
       bold=True, warn=False)
    rect(s, M, 5.66, 5.92, 1.24, fill=PALE_RED, line=RGBColor(0xFE, 0xCA, 0xCA),
         radius=True, name="Defect1")
    tx(s, M + 0.16, 5.76, 5.60, 1.04,
       "Defect 1 — preview dialog\nUnboundLocalError: “QImage” is referenced before "
       "assignment. The dialog cannot display the render.",
       size=17, color=RGBColor(0x99, 0x1B, 0x1B), spacing=1.06, warn=False)
    rect(s, 6.80, 5.66, 5.92, 1.24, fill=PALE_GOLD, line=RGBColor(0xFD, 0xE6, 0x8A),
         radius=True, name="Defect2")
    tx(s, 6.96, 5.76, 5.60, 1.04,
       "Defect 2 — re-decode check\nThe rendered front barcode does not decode back to "
       "000065412. Visually plausible, not scanner-verified.",
       size=17, color=RGBColor(0x92, 0x40, 0x0E), spacing=1.06, warn=False)
    notes(s, """
This is the most uncomfortable slide in the deck, and it is deliberately specific rather
than vague.

What works: the rendering service. Both images are genuine outputs of IdCardService, not
mock-ups. They are physically correct CR80 cards — 85.6 by 54 millimetres — rendered at
300 dots per inch, which gives 1011 by 638 pixels for the front. The layout, the portrait,
the institution branding and the barcode symbol are all composed by the service from a
real student record, and it supports 150, 300 and 600 DPI output for print workflows.

What does not work, and there are two separate problems. First, the preview dialog that
should display these renders raises an UnboundLocalError — the name QImage is referenced
before it is assigned — so a user reaching the preview from the interface sees a crash
instead of their card. That is a straightforward initialisation bug in the dialog, not in
the service.

Second, and more interesting: when the service's own verification pass re-decodes the
barcode printed on the rendered front, it reads nothing back — the expected payload
000065412 does not come out of the bitmap. The card looks right but is not scanner-ready
at 300 DPI. That is a print-readiness issue, and it matters because a card that cannot be
scanned is not a finished card, no matter how correct the layout looks.

Both are on the NOW column of the roadmap.
""")
    return s


# =========================================================================== #
# 24 reports
# =========================================================================== #
def s24_reports(prs, ev):
    s = new_slide(prs)
    header(s, "03 · Walkthrough", "Reports and analytics",
           "Five report cards, each backed by a generator that writes a real file and reports failure.")
    image_panel(s, SHOTS / "12_reports.png", M, TOP, 7.60, 4.55, pad=0.04,
                name="ReportsShot")
    section_tag(s, "Catalogue", 8.42, TOP + 0.02)
    bullets(s, 8.42, TOP + 0.36, 4.30, 2.70, [
        "Certificate → A4 PDF / print.",
        "Enrollment summary by term.",
        "Barcode report: payload + symbol.",
        "Assessment report: charges + schedule.",
        "Scan log → CSV of full history.",
    ], size=19, gap=8)
    rect(s, 8.42, TOP + 3.20, 4.30, 1.30, fill=CREAM, line=BORDER, radius=True,
         name="ThreadNote")
    tx(s, 8.58, TOP + 3.34, 3.98, 1.02,
       "Generation runs in a worker thread. A failed write is shown as an error, not a "
       "success message.",
       size=17, color=SLATE, spacing=1.06, warn=False)
    caption(s, "PDF and CSV artefacts in reports/ were produced by ReportService itself.",
            y=6.68)
    notes(s, """
The reports page is a thin interface over one service class, and the engineering detail
worth remembering is how it behaves under failure.

Five reports are available. The Certificate of Matriculation produces an A4 PDF or prints
directly. The Enrollment Summary lists the students enrolled in a chosen academic term. The
Student Barcode Report pairs each student's identity with their barcode payload and the
rendered symbol. The Assessment Report breaks down charges, the installment schedule and
payments. And the Barcode Scan Log exports the entire scan history as a CSV file, including
the resolved student where one exists.

Every one of these writes a real file through ReportLab or the CSV writer — the artefacts
in the reports folder were produced by this service, not assembled for the presentation.

Two behaviours are deliberate. Generation runs in a worker thread, so building a
twenty-page assessment report does not freeze the interface. And a failure — a missing
asset, a permissions problem, a failed write — is surfaced to the operator as an error
rather than being reported as a success with no file behind it. That is a small thing, but
it is the difference between a report system people trust and one they re-check by hand.

The scan-log CSV is also the bridge between this application and any later analysis: it is
the raw evidence of every detection, decode and lookup the system has performed.
""")
    return s


# =========================================================================== #
# 25 database at a glance
# =========================================================================== #
def s25_database(prs, ev):
    s = new_slide(prs)
    header(s, "04 · Data", "The live database at a glance",
           "MySQL 8.4, InnoDB, utf8mb4 — nineteen tables, and a demonstration dataset that is honestly small.")
    tiles = [
        ("19", "Tables", ORANGE, CREAM),
        ("230", "Columns", ORANGE, CREAM),
        ("33", "Foreign keys", ORANGE, CREAM),
        ("188", "Rows total", ORANGE, CREAM),
    ]
    x = M
    for value, label, accent, fill in tiles:
        kpi(s, x, TOP, 2.92, 1.10, value, label, fill=fill, accent=accent, vsize=28)
        x += 3.06
    rows = [
        ["Table group", "Rows", "Table group", "Rows"],
        ["students", "3", "assessments / items", "3 / 33"],
        ["barcodes", "3", "payments / schedules", "0 / 12"],
        ["barcode_scans", "65", "courses / subjects", "1 / 7"],
        ["enrollments / lines", "3 / 21", "categories / settings", "20 / 11"],
    ]
    tbl = table(s, M, TOP + 1.30, 8.15, rows, [2.75, 0.95, 2.90, 1.55], size=19,
                header_size=19, row_h=0.52,
                align=[PP_ALIGN.LEFT, PP_ALIGN.RIGHT, PP_ALIGN.LEFT, PP_ALIGN.RIGHT],
                name="DbTable")
    chip(s, M, TOP + 3.60, 8.15,
         "Empty tables: users · id_cards · yolo_models · dataset_images  —  0 rows each",
         fill=PALE_RED, color=RGBColor(0x99, 0x1B, 0x1B), size=17, h=0.48)
    rect(s, 9.00, TOP + 1.30, 3.72, 3.20, fill=PALE_RED,
         line=RGBColor(0xFE, 0xCA, 0xCA), radius=True, name="EmptyWarning")
    tx(s, 9.20, TOP + 1.44, 3.32, 0.34, "Empty tables are real", size=20,
       color=RGBColor(0x99, 0x1B, 0x1B), bold=True, warn=False)
    bullets(s, 9.20, TOP + 1.84, 3.32, 2.50, [
        "No users — so no authentication.",
        "No ID cards, no model registry rows.",
        "dataset_images empty → Training Center zeros.",
        "188 rows include reference data, not just students.",
    ], size=17, gap=6, bullet_color=RED,
        lead_color=RGBColor(0x99, 0x1B, 0x1B), color=RGBColor(0x7F, 0x1D, 0x1D))
    tx(s, M, TOP + 4.70, CW, 0.30,
       "19 tables and 33 FKs describe capability; 3 students and 65 scans describe the evidence.",
       size=18, color=MUTED, italic=True, name="Footnote", warn=False)
    notes(s, """
The schema is substantial for a desktop project, but the live data is a demonstration set,
and reporting both facts together is the honest thing to do.

Nineteen tables carry two hundred and thirty columns and thirty-three foreign keys, all
InnoDB with utf8mb4 unicode collation. The row counts come from querying the live database
directly. Three students, each with a barcode, and sixty-five scan records. Three
enrollments expanding to twenty-one subject lines. Three assessments made of thirty-three
charge items, with twelve installment schedules and — as I mentioned on the assessment
slide — zero payments.

The right-hand panel is the part a promotional deck would leave out. Four table groups are
empty, and each emptiness has a consequence you can see in the interface. The users table
is empty, which is why there is no login. The ID-cards table is empty because the preview
dialog crashes before anything can be saved. The YOLO-models registry is empty, so the
Training Center has no run history to display. And dataset-images is empty, which is why
the Training Center reports zero images while two and a half thousand files sit on disk.

Also note that the 188-row total includes reference data — twenty assessment categories,
eleven settings rows, courses and subjects — not only student records.

Capability and evidence are different things, and this deck keeps them apart.
""")
    return s


# =========================================================================== #
# 26 ERD — identity & scans
# =========================================================================== #
def s26_erd_core(prs, ev):
    s = new_slide(prs)
    header(s, "04 · Data model 1/3", "ERD — identity, barcodes and scan history",
           "Every table below is InnoDB; PK, FK and UNIQUE constraints are what make the scan lookup deterministic.")
    uh = entity(s, M, TOP + 0.04, 3.05, "users", [
        ("id  PK", "pk"), ("username  UQ", "col"), ("role  ENUM", "col"),
        ("password_hash", "col"), ("is_active", "col")], accent=INK, name="Users")
    sh = entity(s, M + 3.55, TOP + 0.04, 3.90, "students", [
        ("id  PK", "pk"), ("student_id  UQ · reference  UQ", "col"),
        ("first / middle / last", "col"),
        ("program · year · section", "col"),
        ("barcode_payload  UQ", "col"),
        ("created_by / updated_by  FK", "fk")],
        accent=DARK, name="Students")
    ch = entity(s, M + 7.95, TOP + 0.04, 3.30, "student_contacts", [
        ("id  PK", "pk"), ("student_id  FK → students", "fk"),
        ("contact_type  ENUM", "col"), ("contact_value · is_primary", "col")],
        accent=INK, name="Contacts")
    bh = entity(s, M + 3.55, TOP + 2.60, 3.90, "barcodes", [
        ("id  PK", "pk"), ("student_id  FK → students", "fk"),
        ("barcode_value  UQ", "col"), ("barcode_type  ENUM (CODE39)", "col"),
        ("is_active · regenerated_count", "col")], accent=INK, name="Barcodes")
    sc_h = entity(s, M + 7.95, TOP + 2.60, 3.30, "barcode_scans", [
        ("id  PK", "pk"), ("barcode_id  FK (nullable)", "fk"),
        ("student_id  FK (nullable)", "fk"),
        ("scanned_value · scan_status  ENUM", "col"),
        ("confidence_score · detection_bbox", "col")], accent=INK, name="Scans")
    arrow(s, M + 3.05, TOP + 0.60, M + 3.55, TOP + 0.60, width=2.5, name="U2S")
    arrow(s, M + 7.45, TOP + 0.60, M + 7.95, TOP + 0.60, width=2.5, name="S2C")
    arrow(s, M + 5.50, TOP + 1.98, M + 5.50, TOP + 2.60, width=2.5, name="S2B")
    arrow(s, M + 7.45, TOP + 3.20, M + 7.95, TOP + 3.20, width=2.5, name="B2C")
    arrow(s, M + 5.50, TOP + 1.40, M + 11.30, TOP + 2.90, width=1.5, head=True,
          dash=True, name="S2Scan")
    rect(s, M, TOP + 2.60, 3.05, 1.42, fill=SOFT, line=BORDER, radius=True,
         name="CardLegend")
    tx(s, M + 0.16, TOP + 2.72, 2.73, 0.30, "Cardinality", size=17, color=DARK,
       bold=True, warn=False)
    tx(s, M + 0.16, TOP + 3.06, 2.73, 0.84,
       "1..N   one student →\nmany barcodes / scans\n(nullable FKs on scans)",
       size=15, color=SLATE, spacing=1.10, warn=False)
    rect(s, M, TOP + 4.16, 3.05, 1.38, fill=SOFT, line=BORDER, radius=True,
         name="AuditNote")
    tx(s, M + 0.14, TOP + 4.26, 2.77, 1.20,
       "users is the intended audit anchor — but it has 0 rows, so created_by stays NULL.",
       size=16, color=SLATE, spacing=1.06, warn=False)
    caption(s, "barcode_scans is append-only; its student and barcode FKs are SET NULL so "
               "deleting a student never erases scan history.", x=M + 3.55, w=CW - 3.55,
            y=6.62, size=16)
    notes(s, """
This is the first of three entity-relationship slides, and it covers the identity core —
the tables the scanner depends on.

At the centre is students. Its primary key is referenced by everything else, and three
constraints there are worth naming: student-id and reference-number are both unique, and
barcode-payload is unique as well. That last constraint is what makes the scanner's final
step deterministic — one payload can only ever resolve to one row, so there is no
ambiguity to disambiguate in application code.

To the left, users. It is the intended audit anchor: students carry created-by and
updated-by foreign keys pointing at it. Because the users table is empty in the
demonstration database, those columns are NULL — the mechanism exists, the rows do not.

To the right, student-contacts is a simple one-to-many child table for phone, email and
guardian details.

Below, barcodes is also a child of students, one row per printed symbol, with a unique
barcode-value, a symbology type that includes CODE39, and regeneration counters.

And barcode-scans is the append-only history. Note both of its foreign keys — to barcodes
and to students — are nullable and set-null on delete. That is deliberate: if a student
record is ever removed, their scan history must survive as evidence, with the raw
scanned-value intact even though the link is gone.

The confidence score and the detection bounding box are stored as JSON on each scan row,
which is what makes later accuracy analysis possible.
""")
    return s


# =========================================================================== #
# 27 ERD — academics
# =========================================================================== #
def s27_erd_academics(prs, ev):
    s = new_slide(prs)
    header(s, "04 · Data model 2/3", "ERD — courses, subjects and enrollment",
           "Two junction tables carry the many-to-many relationships and hold the per-line detail.")
    node(s, M, TOP + 0.10, 2.55, 0.80, "students", "from slide 28", fill=SOFT,
         line=MUTED, tsize=20, ssize=14, name="GhostStudents")
    co = entity(s, M + 3.30, TOP + 0.04, 3.30, "courses", [
        ("id  PK", "pk"), ("course_code  UQ", "col"), ("course_name · department", "col"),
        ("credits · is_active", "col")], accent=INK, name="Courses")
    su = entity(s, M + 3.30, TOP + 2.20, 3.30, "subjects", [
        ("id  PK", "pk"), ("subject_code  UQ", "col"),
        ("course_id  FK → courses", "fk"), ("lecture_hours · lab_hours", "col")],
        accent=INK, name="Subjects")
    en = entity(s, M + 7.30, TOP + 0.04, 3.55, "enrollments", [
        ("id  PK", "pk"), ("student_id  FK → students", "fk"),
        ("academic_year · semester  ENUM", "col"),
        ("status  ENUM · total_credits", "col"),
        ("UNIQUE (student, year, semester)", "col")], accent=DARK, name="Enrollments")
    el = entity(s, M + 7.30, TOP + 2.40, 3.55, "enrollment_subjects", [
        ("id  PK", "pk"), ("enrollment_id  FK → enrollments", "fk"),
        ("subject_id  FK → subjects", "fk"),
        ("section · room · schedule", "col"), ("grade · status · lab_fee", "col")],
        accent=DARK, name="EnrSubjects")
    arrow(s, M + 2.55, TOP + 0.50, M + 7.30, TOP + 0.50, width=2.5, name="S2E")
    arrow(s, M + 6.60, TOP + 0.90, M + 6.60, TOP + 2.20, width=2.5, name="C2S")
    arrow(s, M + 9.07, TOP + 1.90, M + 9.07, TOP + 2.40, width=2.5, name="E2ES")
    arrow(s, M + 6.60, TOP + 3.10, M + 7.30, TOP + 3.30, width=2.5, name="S2ES")
    rect(s, M, TOP + 3.20, 2.55, 1.40, fill=SOFT, line=BORDER, radius=True,
         name="CardLegend")
    tx(s, M + 0.16, TOP + 3.32, 2.23, 0.30, "Cardinality", size=17, color=DARK,
       bold=True, warn=False)
    tx(s, M + 0.16, TOP + 3.66, 2.23, 0.84,
       "1..N   one course →\nmany subjects; one\nenrollment → many lines",
       size=15, color=SLATE, spacing=1.10, warn=False)
    tx(s, M, TOP + 1.30, 2.55, 1.60,
       "Live data\n1 course\n7 subjects\n3 enrollments\n21 subject lines",
       size=17, color=SLATE, spacing=1.20, warn=False)
    caption(s, "Subject deletion is RESTRICTed so historical enrollment lines stay intact.",
            y=6.62, size=16)
    notes(s, """
The second ERD slide covers the academic core, and its story is junction tables.

Courses and subjects form a simple parent-child pair: one Computer Engineering course owns
seven subjects, each with its own code, credit value and lecture or laboratory hours. The
subject-code column carries a unique constraint, so "CPE 311" can mean only one thing.

An enrollment belongs to exactly one student and one academic term, and it carries a
unique constraint on the triple student, academic year and semester — that is what stops
the same student being enrolled twice in the same term.

The interesting table is enrollment-subjects, the junction between an enrollment and the
subjects it contains. It is not a bare link table: it carries the section, the room, the
scheduled days and time, the period, the fees, the grade and the line status. That is why
the certificate can be generated straight from these rows — everything the document shows
about a subject lives here. Its unique constraint on enrollment and subject prevents the
same subject appearing twice in one term.

Note the foreign-key policies. Deleting a subject is RESTRICTed while enrollment lines
reference it, which protects historical records. CASCADE from students exists but is
effectively unused, because the application deactivates students rather than deleting
them — enrollment history is the point of the system.

The demonstration data is small and I will say it plainly: one course, seven subjects,
three enrollments and twenty-one subject lines.
""")
    return s


# =========================================================================== #
# 28 ERD — finance
# =========================================================================== #
def s28_erd_finance(prs, ev):
    s = new_slide(prs)
    header(s, "04 · Data model 3/3", "ERD — assessments, payments and schedules",
           "Fees are modelled as verified line items, not as a single number on the enrollment.")
    node(s, M, TOP + 0.10, 2.55, 0.80, "students", "from slide 28", fill=SOFT,
         line=MUTED, tsize=20, ssize=14, name="GhostStudents")
    node(s, M, TOP + 1.30, 2.55, 0.80, "enrollments", "from slide 29", fill=SOFT,
         line=MUTED, tsize=20, ssize=14, name="GhostEnroll")
    cat = entity(s, M + 3.30, TOP + 0.04, 3.05, "assess_categories", [
        ("id  PK", "pk"), ("category_code  UQ", "col"),
        ("category_name", "col"), ("is_fee · is_discount", "col")],
        accent=INK, name="Categories")
    asm = entity(s, M + 7.05, TOP + 0.04, 3.60, "assessments", [
        ("id  PK", "pk"), ("student_id  FK → students", "fk"),
        ("enrollment_id  FK → enrollments", "fk"),
        ("academic_year · semester", "col"),
        ("status  ENUM · total_assessed", "col"),
        ("outstanding_balance", "col")], accent=DARK, name="Assessments")
    it = entity(s, M + 3.30, TOP + 2.30, 3.05, "assessment_items", [
        ("id  PK", "pk"), ("assessment_id  FK", "fk"),
        ("category_id  FK → categories", "fk"),
        ("amount · is_verified", "col")], accent=INK, name="Items")
    pay = entity(s, M + 7.05, TOP + 2.76, 3.20, "payments", [
        ("id  PK", "pk"), ("assessment_id  FK", "fk"),
        ("amount · method  ENUM", "col"),
        ("is_voided · voided_by / at", "col")], accent=INK, name="Payments",
        fsize=16)
    sch = entity(s, M + 10.38, TOP + 2.76, 1.71, "schedules", [
        ("id  PK", "pk"), ("assess. FK", "fk"),
        ("due_date", "col"), ("amount_due", "col"), ("status  ENUM", "col")],
        accent=INK, name="Schedules", fsize=15, tsize=17)
    arrow(s, M + 2.55, TOP + 0.50, M + 7.05, TOP + 0.50, width=2.5, name="S2A")
    arrow(s, M + 2.55, TOP + 1.70, M + 7.05, TOP + 1.10, width=2.5, name="E2A")
    arrow(s, M + 6.35, TOP + 1.00, M + 7.05, TOP + 1.50, width=2, name="C2I")
    arrow(s, M + 6.35, TOP + 3.10, M + 7.05, TOP + 1.60, width=2, dash=True, name="C2I2")
    arrow(s, M + 8.85, TOP + 2.06, M + 8.85, TOP + 2.30, width=2.5, name="A2I")
    arrow(s, M + 10.65, TOP + 1.50, M + 10.65, TOP + 2.76, width=2.5, name="A2P")
    arrow(s, M + 10.25, TOP + 3.50, M + 10.38, TOP + 3.50, width=2.5, name="A2S")
    rect(s, M, TOP + 2.30, 2.55, 1.32, fill=SOFT, line=BORDER, radius=True,
         name="CardLegend")
    tx(s, M + 0.16, TOP + 2.42, 2.23, 0.30, "Cardinality", size=17, color=DARK,
       bold=True, warn=False)
    tx(s, M + 0.16, TOP + 2.74, 2.23, 0.82,
       "1..N   one assessment →\nmany items, payments\nand schedules",
       size=15, color=SLATE, spacing=1.10, warn=False)
    rect(s, M, TOP + 3.72, 2.55, 0.92, fill=SOFT, line=BORDER, radius=True,
         name="LiveData")
    tx(s, M + 0.16, TOP + 3.82, 2.23, 0.74,
       "Live rows: 20 categories,\n3 assessments, 33 items,\n0 payments, 12 schedules",
       size=14, color=SLATE, spacing=1.06, warn=False)
    caption(s, "Payments can be voided with a reason and a user — the ledger is correctional.",
            y=6.62, size=16)
    notes(s, """
The third ERD slide is the financial model, and the design principle here is that a fee is
never a single number — it is always a verified line item.

An assessment belongs to one student and one enrollment term, and it persists its own
totals: total assessed, total discounts, total payments and the outstanding balance. That
last column is stored rather than recomputed, so the number a report shows today matches
the number it showed when the statement was issued.

Each charge is a row in assessment-items, referencing one of twenty predefined categories —
tuition, laboratory, miscellaneous and the rest — through a foreign key. Items carry a
verified flag and a verifying user, which is how the "is this fee actually chargeable?"
question is answered in the data rather than in somebody's spreadsheet.

Payments reference the assessment, not the individual item, and they support voiding: a
payment can be marked void with a reason and the user who voided it, which makes the ledger
correctional rather than destructive. Nothing is ever hard-deleted. And an installment
schedule — down payment, midterm, prefinal, final or a custom date — tracks amounts due,
amounts paid and a status from pending through overdue to waived.

The live demonstration data holds three assessments made of thirty-three items, twelve
schedules, and zero payments — a true zero, as I said earlier.
""")
    return s


# =========================================================================== #
# 29 data flow
# =========================================================================== #
def s29_dataflow(prs, ev):
    s = new_slide(prs)
    header(s, "04 · Data flow", "Level-1 data flow: four processes, four data stores",
           "How camera frames and operator actions become rows, and how rows become documents.")
    node(s, M, TOP + 0.06, 2.30, 0.85, "Operator", None, fill=SOFT, line=SLATE,
         tsize=20, name="DFActor")
    node(s, M, TOP + 1.24, 2.30, 0.85, "USB camera", "1280 × 720 frames", fill=PALE_BLUE,
         line=RGBColor(0x93, 0xC5, 0xFD), tsize=18, ssize=13, name="DFCamera")
    procs = [
        ("P1 · Manage records", "students · contacts", TOP + 0.06),
        ("P2 · Enroll subjects", "enrollments · lines", TOP + 1.24),
        ("P3 · Scan & identify", "detect · decode · lookup", TOP + 2.42),
        ("P4 · Assess & document", "fees · certificates · CSV", TOP + 3.60),
    ]
    for title, sub, y in procs:
        node(s, 4.10, y, 3.30, 0.85, title, sub, fill=CREAM, accent=ORANGE, tsize=18,
             ssize=14, align=PP_ALIGN.LEFT, name="DFProc")
    stores = [
        ("D1 · students", TOP + 0.06), ("D2 · enrollments", TOP + 1.24),
        ("D3 · barcode_scans", TOP + 2.42), ("D4 · assessments", TOP + 3.60),
    ]
    for title, y in stores:
        node(s, 8.70, y, 2.75, 0.85, title, "rows written here", fill=PALE_GREEN,
             line=RGBColor(0xBB, 0xF7, 0xD0), tsize=17, ssize=13, align=PP_ALIGN.LEFT,
             name="DFStore")
    node(s, 11.80, TOP + 3.60, 0.92, 0.85, "PDF\nCSV\nCR80", None, fill=PALE_GOLD,
         line=RGBColor(0xFD, 0xE6, 0x8A), tsize=14, name="DFOut")
    arrow(s, M + 2.30, TOP + 0.48, 4.10, TOP + 0.48, width=2, name="DFA1")
    arrow(s, M + 2.30, TOP + 1.66, 4.10, TOP + 1.66, width=2, name="DFA2")
    arrow(s, M + 2.30, TOP + 2.84, 4.10, TOP + 2.84, width=2, name="DFA3")
    arrow(s, M + 2.30, TOP + 0.70, 4.10, TOP + 3.90, width=2, dash=True, name="DFA4")
    for y in (TOP + 0.48, TOP + 1.66, TOP + 2.84, TOP + 4.02):
        arrow(s, 7.40, y, 8.70, y, width=2, name="DFStoD")
    arrow(s, 11.45, TOP + 4.02, 11.80, TOP + 4.02, width=2, name="DFOutArrow")
    tx(s, 7.50, TOP + 2.06, 1.16, 0.50, "read\nstudents", size=14, color=MUTED,
       align=PP_ALIGN.CENTER, warn=False)
    caption(s, "P3 reads students and always writes scan history; generated outputs are files, "
               "not rows.", y=6.60, size=16)
    notes(s, """
This diagram connects the interface screens you have seen to the tables behind them.

Four processes sit in the middle. P1 manages records — creating and editing students and
their contact details — and writes to the students store. P2 handles subject enrollment
and writes enrollments and their subject lines. P3 is the scan process: frames arrive from
the camera, the pipeline runs, and every attempt is appended to the scan history whatever
its outcome. P4 handles assessment and document generation — fees flow into the
assessments store, and documents flow out to the right as files.

Two rules on this diagram are worth pointing at. First, P3 both reads and writes. It reads
students to resolve a decoded payload, and it writes barcode-scans unconditionally —
successes, not-found reads and errors all become rows. A scan history that only recorded
successes would be useless for evaluation.

Second, generated outputs are files, not rows. The Certificate of Matriculation, the
assessment PDF and the CSV scan log land on the filesystem, referencing database content
rather than duplicating it. That keeps the database as the single source of truth while
still producing the paperwork a registrar needs.

The dashed line from the operator to P4 exists because document requests come from a person
choosing a student and a report — there is no scheduled or automatic generation anywhere in
the system today.
""")
    return s


# =========================================================================== #
# 30 dataset inventory
# =========================================================================== #
def s30_dataset(prs, ev):
    s = new_slide(prs)
    header(s, "04 · Dataset", "Eight dataset variants, one measured inventory",
           "A filesystem scan of datasets/ counted images, labels, boxes and negatives — nothing estimated.")
    ds = ev["dataset"]["datasets"]
    order = ["barcode", "barcode_all_students", "barcode_aug", "barcode_aug_all",
             "barcode_detection", "barcode_detection_all", "barcode_distance",
             "barcode_distance_all"]
    chart_data = CategoryChartData()
    chart_data.categories = [n.replace("barcode", "bc").replace("_", " ")
                             for n in order]
    chart_data.add_series("Images", tuple(ds[n]["images"] for n in order))
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(M), Inches(TOP),
                            Inches(7.30), Inches(4.30), chart_data)
    _name(gf, "DatasetChart")
    chart = gf.chart
    chart.has_legend = False
    chart.has_title = False
    chart.font.size = Pt(15)
    chart.font.name = FONT
    plot = chart.plots[0]
    plot.gap_width = 60
    plot.has_data_labels = True
    plot.data_labels.font.size = Pt(14)
    plot.data_labels.font.bold = True
    plot.data_labels.font.color.rgb = DARK
    ser = plot.series[0]
    ser.format.fill.solid()
    ser.format.fill.fore_color.rgb = ORANGE
    chart.value_axis.has_major_gridlines = True
    chart.value_axis.tick_labels.font.size = Pt(15)
    chart.category_axis.tick_labels.font.size = Pt(14)
    rows = [
        ["Variant", "Images", "Boxes"],
    ]
    for n in order:
        rows.append([n, f"{ds[n]['images']:,}", f"{ds[n]['annotations']:,}"])
    rows.append(["Total", "2,551", "2,515"])
    tbl = table(s, 8.10, TOP, 4.62, rows, [2.62, 1.00, 1.00], size=17,
                header_size=17, row_h=0.40,
                align=[PP_ALIGN.LEFT, PP_ALIGN.RIGHT, PP_ALIGN.RIGHT],
                name="DatasetTable")
    for c in range(3):
        cell = tbl.cell(len(rows) - 1, c)
        cell.fill.fore_color.rgb = PALE_GOLD
        cell.text_frame.paragraphs[0].runs[0].font.bold = True
        cell.text_frame.paragraphs[0].runs[0].font.color.rgb = DARK
    tx(s, 8.10, TOP + 4.44, 4.62, 0.66,
       "2.77 GB  ·  36 negative images\n43 distinct resolutions  ·  8 duplicate files",
       size=17, color=DARK, bold=True, spacing=1.16, warn=False)
    caption(s, "barcode_distance is the variant named by the active model's data.yaml.",
            y=6.66, size=16)
    notes(s, """
These counts come from walking the datasets directory on disk and counting files and label
boxes directly — not from reading the comment in a YAML file, which is how dataset
inventories usually go stale.

Eight variants exist, and they are generations of one problem rather than eight independent
datasets. The original four-image build grew to eight images once all three student cards
were included, then to forty-four and a hundred-and-eight through augmentation, then to
the detection sets that split by physical card so that no card appears in both training and
validation. The two distance variants at the right are the current generation: eight
hundred and twenty-eight and one thousand two hundred and twenty-six images, captured at
1280 by 720 to introduce real camera distance and scale.

In total: two thousand five hundred and fifty-one images carrying two thousand five hundred
and fifteen barcode boxes, two point seven seven gigabytes, and thirty-six deliberate
negative images — frames with no barcode at all, which teach the detector to stay quiet.

Two caveats are worth flagging now because they return on the next slide. Forty-three
distinct resolutions means high scale variance, and eight duplicate files were found inside
the distance pool.

The variant that matters for the deployed model is barcode-distance: four hundred and
fourteen training and four hundred and fourteen validation images. The larger distance-all
pool has a held-out test split, but the active model was not trained or selected with it.
""")
    return s


# =========================================================================== #
# 31 dataset quality
# =========================================================================== #
def s31_dataset_quality(prs, ev):
    s = new_slide(prs)
    header(s, "04 · Dataset quality", "Labels are clean; the split strategy is not",
           "Zero missing labels and zero malformed boxes — but no held-out test set for the active model.")
    image_panel(s, SHOTS / "24_dataset_gallery.png", 7.55, TOP, 5.17, 3.05, pad=0.04,
                name="GalleryShot")
    tx(s, 7.55, 4.98, 5.17, 0.28, "ANNOTATED SAMPLES · NO STUDENT DATA SHOWN", size=15,
       color=DARK, bold=True, warn=False)
    rect(s, M, TOP, 6.55, 1.95, fill=PALE_GREEN, line=RGBColor(0xBB, 0xF7, 0xD0),
         radius=True, name="CleanPanel")
    tx(s, M + 0.22, TOP + 0.12, 6.1, 0.34, "Clean signals", size=21,
       color=RGBColor(0x16, 0x65, 0x34), bold=True, warn=False)
    bullets(s, M + 0.22, TOP + 0.52, 6.1, 1.34, [
        "0 missing labels · 0 invalid boxes.",
        "1 class · 2,515 boxes · 36 negatives.",
        "Median box: 50.4% × 17.4% of frame.",
    ], size=18, gap=7, bullet_color=GREEN, lead_color=RGBColor(0x16, 0x65, 0x34),
        color=RGBColor(0x14, 0x53, 0x2D))
    rect(s, M, TOP + 2.09, 6.55, 2.45, fill=PALE_RED, line=RGBColor(0xFE, 0xCA, 0xCA),
         radius=True, name="RiskPanel")
    tx(s, M + 0.22, TOP + 2.21, 6.1, 0.34, "Risk signals", size=21,
       color=RGBColor(0x99, 0x1B, 0x1B), bold=True, warn=False)
    bullets(s, M + 0.22, TOP + 2.61, 6.1, 1.84, [
        "barcode_distance has 0 test images — selection used validation only.",
        "8 duplicate files inside the distance pool.",
        "~20 near-identical frames per card inflate counts.",
        "43 resolutions → high scale variance.",
    ], size=18, gap=7, bullet_color=RED, lead_color=RGBColor(0x99, 0x1B, 0x1B),
        color=RGBColor(0x7F, 0x1D, 0x1D))
    tx(s, 7.55, 5.34, 5.17, 1.10,
       "Labels were derived from real pyzbar decodes — every box marks a symbol a decoder "
       "actually read, so the ground truth is verified, not hand-drawn.",
       size=17, color=SLATE, spacing=1.10, warn=False)
    caption(s, "Box geometry measured from all 2,515 label files.", y=6.70, size=16)
    notes(s, """
Every model claim rests on its dataset, so this slide reports both the good news and the
part a model card would normally omit.

The good news is genuine. Across all two thousand five hundred and fifty-one images there
are no missing label files and no malformed boxes — every image that should have an
annotation has one, and every box is well formed. There is exactly one class. The median
bounding box covers about half the frame horizontally and seventeen percent vertically,
which matches a card held at arm's length. And thirty-six images deliberately contain no
barcode at all, so the detector is explicitly taught what "nothing here" looks like.

Crucially, the labels were derived from real pyzbar decodes, not drawn by hand: every box
marks a symbol a decoder actually read from that image. The ground truth is therefore
verified rather than asserted.

Now the honest part. The distance variant the active model was trained on has no test
split — model selection was performed on validation data only, so the headline metrics are
optimistic by construction. Eight duplicate files sit inside that pool, which is small but
sloppy. Forty-three distinct resolutions mean high scale variance. And the distance
capture produced roughly twenty near-identical frames per source card, so the image count
overstates the amount of independent evidence.

These are the reasons the roadmap asks for a real held-out test split before any
generalisation claim is made.
""")
    return s


# =========================================================================== #
# 32 training pipeline
# =========================================================================== #
def s32_training_pipeline(prs, ev):
    s = new_slide(prs)
    header(s, "04 · Training", "Training pipeline and recorded configuration",
           "Six candidate runs were kept; the active checkpoint is the v7-distance run.")
    stages = [
        ("Source captures", "3 ID cards ·\nbarcode assets", PALE_BLUE,
         RGBColor(0x93, 0xC5, 0xFD)),
        ("Annotation", "boxes from real\npyzbar decodes", CREAM, BORDER),
        ("Augmentation", "scale · distance\n36 negatives", CREAM, BORDER),
        ("Dataset variants", "8 builds on\ndisk · data.yaml", CREAM, BORDER),
        ("Train yolov8n", "640 px · batch 16\nCUDA:0", CREAM, BORDER),
        ("Validate + promote", "best epoch →\nactive/best.pt", PALE_GREEN,
         RGBColor(0xBB, 0xF7, 0xD0)),
    ]
    bw, gap = 1.849, 0.20
    x = M
    for i, (title, sub, fill, line) in enumerate(stages):
        node(s, x, TOP + 0.10, bw, 1.55, title, sub, fill=fill, line=line, tsize=18,
             ssize=15, name=f"Train{i}")
        if i:
            arrow(s, x - gap - 0.02, TOP + 0.88, x + 0.02, TOP + 0.88, width=2.5)
        x += bw + gap
    section_tag(s, "Recorded configuration — read from best.pt train_args and results.csv",
                M, TOP + 1.92, w=CW)
    cfg = [
        ("yolov8n.pt", "base weights"), ("100 → 64", "epochs requested / recorded"),
        ("imgsz 640", "input size"), ("batch 16", "batch size"),
        ("AdamW · lr0 0.002", "optimizer"), ("patience 30", "early stopping"),
        ("seed 0", "reproducibility"), ("CUDA:0 (RTX 2050)", "device"),
    ]
    cx = M
    cw = (CW - 3 * 0.18) / 4
    for i, (value, label) in enumerate(cfg):
        col, row = i % 4, i // 4
        x = M + col * (cw + 0.18)
        y = TOP + 2.28 + row * 1.08
        rect(s, x, y, cw, 0.96, fill=CREAM, line=BORDER, radius=True, name="CfgTile")
        tx(s, x + 0.14, y + 0.11, cw - 0.28, 0.42, value, size=20, color=INK,
           bold=True, warn=False)
        tx(s, x + 0.14, y + 0.57, cw - 0.28, 0.30, label.upper(), size=14, color=DARK,
           bold=True, warn=False)
    tx(s, M, TOP + 4.56, CW, 0.62,
       "Stopped after 64 of 100 requested epochs; the promoted checkpoint is best-epoch 34, "
       "not the last.\nCLI: python -m scripts.train_yolo --epochs N [--promote]",
       size=17, color=SLATE, spacing=1.16, warn=False)
    notes(s, """
This is the real build sequence, and every configuration value here was read out of the
checkpoint itself — the Ultralytics trainer stores its full arguments inside best-dot-pt —
and cross-checked against the results dot CSV the run wrote.

The sequence starts from physical captures: three ID cards and their barcode assets. Boxes
were derived from real pyzbar decodes rather than drawn by hand, which means every
annotation corresponds to a symbol a decoder actually read. Augmentation then varies scale
and simulated distance, and adds thirty-six negative images. Eight dataset variants were
built and kept on disk, each with its own YAML file naming the splits.

Training itself used the COCO-pretrained yolov8-nano checkpoint as a starting point, at an
input size of six hundred and forty pixels, batch size sixteen, the AdamW optimiser with an
initial learning rate of 0.002, patience of thirty epochs for early stopping, and seed zero.
It ran on the workstation's RTX 2050 under CUDA.

Two numbers deserve emphasis. One hundred epochs were requested and sixty-four were
recorded — early stopping or interruption ended the run. And the promoted file is the best
epoch, number thirty-four, not the final one. Promotion is decided by validation mAP, so
what sits in models-yolo slash active is genuinely the strongest checkpoint of the run.

The same trainer is reachable from the application's Training Center or from the command
line with the flags shown.
""")
    return s


# =========================================================================== #
# 33 training results
# =========================================================================== #
def s33_training_results(prs, ev):
    s = new_slide(prs)
    header(s, "04 · Training results", "Validation curves and the active model's scores",
           "Plotted directly from results.csv — no smoothing, no interpolation.")
    rows_v7 = ev["v7"]
    xy = XyChartData()
    s50 = xy.add_series("mAP@0.50")
    s95 = xy.add_series("mAP@0.50:0.95")
    for r in rows_v7:
        ep = int(r["epoch"])
        s50.add_data_point(ep, float(r["metrics/mAP50(B)"]))
        s95.add_data_point(ep, float(r["metrics/mAP50-95(B)"]))
    gf = s.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER_LINES, Inches(M), Inches(TOP),
                            Inches(7.85), Inches(4.55), xy)
    _name(gf, "MapChart")
    chart = gf.chart
    chart.has_title = False
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.include_in_layout = False
    chart.legend.font.size = Pt(16)
    chart.font.size = Pt(15)
    chart.font.name = FONT
    colors = [ORANGE, BLUE]
    for series, col in zip(chart.plots[0].series, colors):
        series.format.line.color.rgb = col
        series.format.line.width = Pt(2.5)
    chart.value_axis.minimum_scale = 0.0
    chart.value_axis.maximum_scale = 1.0
    chart.value_axis.tick_labels.font.size = Pt(15)
    chart.category_axis.tick_labels.font.size = Pt(15)
    tx(s, M, TOP + 4.62, 7.85, 0.30, "Epoch →", size=15, color=MUTED, align=PP_ALIGN.CENTER,
       warn=False)
    rect(s, 8.72, TOP, 4.00, 4.92, fill=CREAM, line=BORDER, radius=True,
         name="MetricsPanel")
    tx(s, 8.94, TOP + 0.16, 3.56, 0.34, "Active checkpoint (v7)", size=21, color=DARK,
       bold=True, warn=False)
    mrows = [
        ["Metric", "Value"],
        ["Best epoch", "34 / 64"],
        ["Precision", "0.9975"],
        ["Recall", "0.9987"],
        ["mAP@0.50", "0.9950"],
        ["mAP@0.50:0.95", "0.8075"],
    ]
    table(s, 8.94, TOP + 0.60, 3.56, mrows, [1.86, 1.70], size=19, header_size=19,
          row_h=0.47, align=[PP_ALIGN.LEFT, PP_ALIGN.RIGHT], name="MetricsTable")
    tx(s, 8.94, TOP + 3.60, 3.56, 1.20,
       "Losses fall smoothly. The strict run is excluded from comparison — its log records "
       "only epochs 41–60 and duplicates v3's metrics.",
       size=16, color=SLATE, spacing=1.08, warn=False)
    caption(s, "Source: models_yolo/candidates/…v7…/results.csv · barcode_distance validation split.",
            y=6.66, size=16)
    notes(s, """
These curves are the raw training history of the active model, plotted straight from the
results dot CSV the Ultralytics trainer wrote — every one of the sixty-four recorded
epochs, with no smoothing and no interpolation applied.

Two series are shown. The orange line is mAP at an IoU threshold of 0.50: the loose
criterion that asks only whether the box overlaps the truth reasonably. It climbs quickly
and settles just under 1.0. The blue line is the stricter mAP over thresholds from 0.50 to
0.95, which also demands precise localisation. It peaks at 0.8075 and then oscillates —
that oscillation is normal near the end of a run and is exactly why patience-based
selection of the best epoch beats taking the last one.

The table on the right summarises the promoted checkpoint: best epoch thirty-four of
sixty-four, precision 0.9975, recall 0.9987, mAP at 0.50 of 0.995 and the strict mAP of
0.8075.

Before anyone over-trusts those numbers, two qualifications. First, the validation split
comes from the same capture process as training, so these figures measure consistency
within our own data, not performance on arbitrary cameras or lighting. Second, the earlier
"strict" run is deliberately excluded from comparison — its log only covers epochs forty-
one to sixty and its metrics duplicate the v3 run, so it is not independent evidence.

The honest summary: excellent performance on the collected distribution, generalisation
still unproven.
""")
    return s


# =========================================================================== #
# 34 comparison & latency
# =========================================================================== #
def s34_comparison(prs, ev):
    s = new_slide(prs)
    header(s, "04 · Model selection", "Six runs compared — GPU inference ~10× faster",
           "Best validation scores per run from results.csv, plus a 12-run latency measurement each.")
    runs = ev["runs"]
    keys = ["barcode_detector_strict_20261015_045020",
            "barcode_detector_v3_20261015_045947",
            "barcode_detector_v4_tame_20261015_050513",
            "barcode_detector_v5_aug_20261015_060903",
            "barcode_detector_v6_full_20261015_090208",
            "barcode_detector_v7_distance_20261015_20261015_221339"]
    labels = ["strict", "v3", "v4-tame", "v5-aug", "v6-full", "v7-dist ★"]
    cd = CategoryChartData()
    cd.categories = labels
    cd.add_series("best mAP@0.50", tuple(runs[k]["best_map50"] for k in keys))
    cd.add_series("best mAP@0.50:0.95", tuple(runs[k]["best_map50_95"] for k in keys))
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(M), Inches(TOP),
                            Inches(7.35), Inches(2.90), cd)
    _name(gf, "RunChart")
    chart = gf.chart
    chart.has_title = False
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.include_in_layout = False
    chart.legend.font.size = Pt(15)
    chart.font.size = Pt(14)
    chart.font.name = FONT
    chart.value_axis.maximum_scale = 1.0
    chart.value_axis.minimum_scale = 0.0
    chart.value_axis.tick_labels.font.size = Pt(13)
    chart.category_axis.tick_labels.font.size = Pt(14)
    for series, col in zip(chart.plots[0].series, (ORANGE, BLUE)):
        series.format.fill.solid()
        series.format.fill.fore_color.rgb = col
    chart.plots[0].gap_width = 70
    lat = ev["latency"]
    lcd = CategoryChartData()
    lcd.categories = ["RTX 2050 (CUDA)", "CPU"]
    lcd.add_series("mean ms", (lat["0"]["mean_ms"], lat["cpu"]["mean_ms"]))
    gf2 = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(8.28),
                             Inches(TOP), Inches(4.44), Inches(2.90), lcd)
    _name(gf2, "LatencyChart")
    c2 = gf2.chart
    c2.has_title = False
    c2.has_legend = False
    c2.font.size = Pt(14)
    c2.font.name = FONT
    p2 = c2.plots[0]
    p2.gap_width = 110
    p2.has_data_labels = True
    p2.data_labels.font.size = Pt(14)
    p2.data_labels.font.bold = True
    p2.data_labels.number_format = '0" ms"'
    p2.data_labels.number_format_is_linked = False
    p2.series[0].format.fill.solid()
    p2.series[0].format.fill.fore_color.rgb = GREEN
    c2.value_axis.tick_labels.font.size = Pt(13)
    c2.category_axis.tick_labels.font.size = Pt(14)
    section_tag(s, "How to read this", M, TOP + 3.00)
    bullets(s, M, TOP + 3.32, 7.35, 1.55, [
        "v5-aug and v7-dist both reach mAP@0.50 = 0.995.",
        "v5 leads strict mAP: 0.8736 vs 0.8075.",
        "v4-tame collapsed to 0.0585 — a failed strategy.",
    ], size=18, gap=7)
    rect(s, 8.28, TOP + 3.00, 4.44, 2.10, fill=CREAM, line=BORDER, radius=True,
         name="LatencyPanel")
    tx(s, 8.48, TOP + 3.12, 4.04, 0.30, "Measured latency · 12 runs", size=19,
       color=DARK, bold=True, warn=False)
    tx(s, 8.48, TOP + 3.46, 4.04, 0.70, "GPU mean 22.49 ms  (p95 25.0 ms)\n"
       "CPU mean 218.13 ms  (p95 453.5 ms)", size=16, color=INK, bold=True,
       spacing=1.16, warn=False)
    tx(s, 8.48, TOP + 4.20, 4.04, 0.80,
       "At the 12 FPS target (83 ms/frame) only the GPU has headroom; CPU works but "
       "feels choppy.",
       size=16, color=SLATE, spacing=1.06, warn=False)
    caption(s, "Latency covers detector pre/post-processing only; CPU varied 125–569 ms.",
            y=6.70, size=16)
    notes(s, """
This slide exists to show that model selection was made from evidence — and because the
comparison contains a failure, not only winners.

Six training runs are plotted. The strict and v3 runs both reached 0.995 mAP, but as I
mentioned, the strict log is not independent evidence. The v4-tame run is the interesting
one: it collapsed to 0.0585 mAP, and it is kept in the repository and on this chart as a
record of an augmentation strategy that did not work. That is what an honest experimental
record looks like. The v5-aug run has the best strict localisation score at 0.8736, and
v6-full sits lower at 0.8021. The deployed v7-distance run reaches 0.995 on the loose
criterion and 0.8075 on the strict one.

So why was v7 promoted rather than v5? Because of its training data: v7 was trained on the
distance dataset, which is the configuration that addresses the real operational question
of cards read from different distances. It was promoted for coverage, not for winning a
 leaderboard.

The chart on the right is a straight measurement: twelve inference runs per device after
warm-up on a 1280 by 720 frame. The RTX 2050 averages 22.49 milliseconds with a 95th
percentile of 25 milliseconds. The CPU averages 218 milliseconds with a 95th percentile of
453, and it varies widely between 125 and 569. Against an 83-millisecond frame budget for a
twelve-frame-per-second preview, only the GPU has real headroom — which is why the
application requests CUDA and simply degrades to a choppier preview when it is absent.
""")
    return s


# =========================================================================== #
# 35 testing & defects
# =========================================================================== #
def s35_testing(prs, ev):
    s = new_slide(prs)
    header(s, "05 · Verification", "Tests, reproduced defects and technical debt",
           "531 of 553 tests pass — the 22 failures are concentrated in one file, not scattered.")
    kpi(s, M, TOP, 2.55, 1.32, "531", "Passed", "183.66 s runtime", fill=PALE_GREEN,
        accent=GREEN, vsize=28)
    kpi(s, 3.32, TOP, 2.55, 1.32, "22", "Failed", "all post-scan workflow", fill=PALE_RED,
        accent=RED, vsize=28)
    kpi(s, 6.02, TOP, 2.55, 1.32, "553", "Collected", "pytest 9.1.1 · pytest-qt",
        fill=CREAM, vsize=28)
    kpi(s, 8.72, TOP, 4.00, 1.32, "96.0%", "Pass rate", "absolute failures matter more",
        fill=CREAM, vsize=28)
    section_tag(s, "Reproduced product defects", M, TOP + 1.52, color=RED)
    bullets(s, M, TOP + 1.86, 6.00, 1.90, [
        ("Dashboard table: ", "TypeError — bad 'family' keyword."),
        ("ID-card preview: ", "UnboundLocalError — 'QImage'."),
        ("Rendered card: ", "re-decode fails at 300 DPI."),
    ], size=18, gap=7, bullet_color=RED, lead_color=RGBColor(0x99, 0x1B, 0x1B))
    section_tag(s, "Test failures and debt", 6.90, TOP + 1.52)
    bullets(s, 6.90, TOP + 1.86, 5.82, 1.90, [
        ("All 22 failures ", "sit in one workflow test file."),
        ("Training Center ", "reads dataset_images (0 rows)."),
        ("Unrouted generator; ", "stub logout; no test split."),
    ], size=18, gap=7)
    rect(s, M, TOP + 3.86, CW, 1.16, fill=PALE_RED, line=RGBColor(0xFE, 0xCA, 0xCA),
         radius=True, name="MislabelPanel")
    tx(s, M + 0.22, TOP + 3.98, CW - 0.44, 0.96,
       "Also a defect: the dashboard reports that TypeError as “Cannot reach the "
       "database”. And no passing test touches either broken component — coverage "
       "would not have caught this.  ·  pytest tests/ -q",
       size=17, color=RGBColor(0x99, 0x1B, 0x1B), spacing=1.10, warn=False)
    notes(s, """
This is the credibility slide, so I will not round anything off.

The suite collects five hundred and fifty-three tests under pytest with the Qt and mocking
plugins, and the recorded full run takes about three minutes. Five hundred and thirty-one
pass. Twenty-two fail, and a pass rate of ninety-six percent sounds reassuring until you
look at where the failures are: all twenty-two live in a single file, the post-scan
workflow tests. They cover exactly the stateful area you would most want covered — the
student panel contract after a scan, history insertion, the duplicate latch, and records
arriving asynchronously after a panel has closed. Failures clustered there are more
concerning than scattered ones, because that is where the scanner page's complexity
concentrates.

Three product defects are reproducible on demand and named on the slides where they appear:
the dashboard table raising a TypeError, the ID-card preview dialog crashing on an
uninitialised QImage, and the rendered card barcode failing to decode back to its payload.

One more defect worth calling out separately: the dashboard labels that internal TypeError
to the user as "cannot reach the database". A misleading error contract erodes trust faster
than the bug itself.

And the gap behind all of this: none of the five hundred and thirty-one passing tests
touches the two components that are broken. Coverage percentage would not have caught
this — targeted tests would.
""")
    return s


# =========================================================================== #
# 36 roadmap
# =========================================================================== #
def s36_roadmap(prs, ev):
    s = new_slide(prs)
    header(s, "05 · Roadmap", "What to fix first — what already works",
           "The core is demonstrable today; the remaining work is bounded, testable and mostly UI-level.")
    cols = [
        ("NOW · correctness", RED, PALE_RED, [
            "Fix dashboard set_cell(family=…) call",
            "Fix ID-card preview QImage init",
            "Make rendered card decode at 300 DPI",
            "Fix or quarantine the 22 failing tests",
        ]),
        ("NEXT · completeness", ORANGE, PALE_GOLD, [
            "Route BarcodeGeneratorPage in the sidebar",
            "Replace the logout stub with authentication",
            "Point Training Center at the files on disk",
            "Add a held-out test split",
        ]),
        ("LATER · operations", BLUE, PALE_BLUE, [
            "Role-based access and audit-user rows",
            "Packaged installer + schema migrations",
            "Camera and lighting field trial",
            "Backup, retention and privacy review",
        ]),
    ]
    x = M
    for title, accent, fill, items in cols:
        rect(s, x, TOP + 0.02, 3.90, 3.74, fill=fill, line=BORDER, radius=True,
             name="RoadCol")
        rect(s, x, TOP + 0.02, 3.90, 0.09, fill=accent, name="RoadAccent")
        tx(s, x + 0.24, TOP + 0.24, 3.42, 0.34, title, size=20, color=INK, bold=True,
           warn=False)
        bullets(s, x + 0.24, TOP + 0.68, 3.42, 2.96, items, size=17, gap=8,
                bullet_color=accent)
        x += 4.10
    rect(s, M, TOP + 3.88, CW, 1.30, fill=INK, radius=True, name="Claim")
    tx(s, M + 0.30, TOP + 4.04, CW - 0.60, 1.00,
       "The defensible claim: a local, inspectable student-information system that turns a "
       "physical card into a verified database identity — and then into enrollment, fees "
       "and documents — with its failures written down.",
       size=22, color=PAPER, bold=True, spacing=1.10, anchor=MSO_ANCHOR.MIDDLE,
       warn=False)
    notes(s, """
I will close on the distinction between what works and what is finished, because they are
not the same thing.

The NOW column is correctness, ordered by risk. Three of the four items are the defects we
have already seen: the dashboard table call, the ID-card preview initialisation, and the
rendered barcode that will not decode back — that last one matters most, because a card
that cannot be scanned is not a finished card. The fourth item is the twenty-two post-scan
test failures: fix them or quarantine them explicitly, but do not leave them ambiguous.

The NEXT column is completeness. Routing the barcode generator page is a small change with
real user value — fourteen hundred lines of working UI are currently unreachable.
Authentication exists as a schema but not as a flow. The Training Center should either read
the two and a half thousand files on disk or populate the table it actually queries. And
before any generalisation claim, the dataset needs a genuine held-out test split.

The LATER column is operational: roles, audit rows, packaging, a field trial across
distances and lighting, and the boring-but-essential backup and privacy work.

The claim at the bottom is what I am prepared to defend. This system turns a physical card
into a verified database identity, and that identity into enrollment records, fees and
printed documents — entirely locally, entirely inspectable, with its failures written down
rather than hidden. Thank you — I am happy to take questions, and the repository is open
for anyone who wants to check any number on these slides.
""")
    return s


# =========================================================================== #
# 37 references
# =========================================================================== #
def s37_references(prs, ev):
    s = new_slide(prs)
    header(s, "Appendix", "References and evidence sources",
           "Everything cited in this deck, and where to verify it.")
    left = [
        ("Project repository", "github.com/toms1010/Wit-Barcode-SIS"),
        ("Application source", "main.py · ui/ · services/ · database/"),
        ("Database schema", "database/schema.py + database/migrations/*.sql"),
        ("Model checkpoint", "models_yolo/active/best.pt · …v7…/results.csv"),
        ("Dataset", "datasets/*/data.yaml · DATASET_REPORT.md"),
    ]
    right = [
        ("Ultralytics YOLOv8", "docs.ultralytics.com — models/yolov8 (accessed Oct 2026)"),
        ("ZBar / pyzbar", "pypi.org/project/pyzbar 0.1.9 — decoding library"),
        ("PySide6 6.12", "doc.qt.io/qtforpython — desktop UI toolkit"),
        ("MySQL 8.4", "dev.mysql.com/doc/refman/8.4/en — InnoDB, utf8mb4"),
        ("ReportLab 5.0.1", "docs.reportlab.com — A4 PDF generation"),
    ]
    section_tag(s, "Project evidence", M, TOP + 0.02)
    y = TOP + 0.42
    for label, value in left:
        tx(s, M, y, 5.85, 0.30, label, size=19, color=INK, bold=True, warn=False)
        tx(s, M, y + 0.32, 5.85, 0.56, value, size=16, color=SLATE, spacing=1.04,
           warn=False)
        y += 1.02
    section_tag(s, "Technology references", 6.90, TOP + 0.02)
    y = TOP + 0.42
    for label, value in right:
        tx(s, 6.90, y, 5.82, 0.30, label, size=19, color=INK, bold=True, warn=False)
        tx(s, 6.90, y + 0.32, 5.82, 0.56, value, size=16, color=SLATE, spacing=1.04,
           warn=False)
        y += 1.02
    tx(s, M, 6.50, CW, 0.42,
       "Internal evidence: reports/*.md · presentation/WIT_Project_Analysis_Report.md · "
       "WIT_Dataset_Analysis_Report.md",
       size=16, color=MUTED, italic=True, warn=False)
    notes(s, """
This appendix slide is here so that any claim in the deck can be checked afterwards.

On the left is the project evidence. The repository itself, whose commit this deck was
built against. The application source, arranged in the package structure you saw on the
architecture slide. The database schema is applied from a single SQL string in
database-schema dot py, with three migration files beside it — so the nineteen tables can
be read without running anything. The model checkpoint and its training CSV are checked
into models-yolo, which is how every metric on the results slides could be verified
offline. And the dataset carries its own report describing provenance: which captures
produced which images, and how the splits were made by physical card to avoid leakage.

On the right are the technology references: the Ultralytics documentation for the YOLOv8
family, the pyzbar wrapper around the ZBar decoding library, the Qt for Python
documentation for PySide6, the MySQL reference manual for the InnoDB behaviour our
foreign-key policies rely on, and ReportLab for the document layer.

Finally, three internal analysis reports sit in the presentation folder and cover the
project audit, the dataset analysis and the full reference list with measured values —
they are the source of most of the numbers on these slides.

I would rather you left with the habit of checking a number than with the impression of
one. Thank you.
""")
    return s


# =========================================================================== #
def main():
    ev = load_evidence()
    prs = Presentation()
    prs.slide_width = Inches(W)
    prs.slide_height = Inches(H)

    builders = [
        s01_title, s02_agenda, s03_exec, s04_problem, s05_objectives, s06_context,
        s07_stack, s08_stack2, s08b_model_plan, s08_architecture, s09_modules, s10_dashboard,
        s11_students,
        s12_identity, s13_scanner, s14_pipeline, s15_yolo, s16_detection,
        s17_decoding, s18_outcomes, s19_workflow, s20_enrollment, s21_assessment,
        s22_certificate, s23_idcards, s24_reports, s25_database, s26_erd_core,
        s27_erd_academics, s28_erd_finance, s29_dataflow, s30_dataset,
        s31_dataset_quality, s32_training_pipeline, s33_training_results,
        s34_comparison, s35_testing, s36_roadmap, s37_references,
    ]
    for fn in builders:
        fn(prs, ev)

    n = len(prs.slides)
    assert n == TOTAL, f"slide count {n} != TOTAL {TOTAL}"

    # verify notes on every slide
    missing = [i for i, sl in enumerate(prs.slides, 1)
               if not (sl.has_notes_slide and sl.notes_slide.notes_text_frame.text.strip())]
    if missing:
        WARN.append(f"Slides missing notes: {missing}")

    prs.save(OUT)
    print(f"[pptx] saved {OUT}")
    print(f"[pptx] {n} slides")
    if WARN:
        print(f"[warn] {len(WARN)} layout warnings:")
        for w in WARN:
            print("   -", w)
    else:
        print("[warn] none")


if __name__ == "__main__":
    main()
