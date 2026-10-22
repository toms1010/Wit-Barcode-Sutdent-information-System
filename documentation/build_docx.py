#!/usr/bin/env python3
"""
Build Project_Documentation.docx from the Markdown source and assets.

Produces an A4 document with:
- Times New Roman 12 pt, 1.5 line spacing, justified body
- 1-inch margins
- Numbered headings (Heading 1, 2, 3)
- Centered figures with captions ("Figure N. Title")
- Numbered tables with titles ("Table N. Title")
- Live TOC field (press F9 to update)
- Page numbers in footer
- Section breaks: landscape for tall diagrams (Figures 3, 4, 5, 7, 8)
- Fully editable text, tables, headings, captions
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.table import Table

# ─── Configuration ──────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
MD_PATH = ROOT / "documentation" / "Project_Documentation.md"
SCREENSHOTS_DIR = ROOT / "documentation" / "screenshots"
DIAGRAMS_DIR = ROOT / "documentation" / "System_Diagrams"
OUTPUT_PATH = ROOT / "documentation" / "Project_Documentation.docx"

# Page setup
PAGE_W = Cm(21.0)
PAGE_H = Cm(29.7)
MARGIN = Inches(1)

# Figures that need landscape orientation (wide diagrams)
LANDSCAPE_FIGURES = {2, 3, 4, 5, 7, 8}  # Figure numbers (1-based)

# Image display sizes (inches)
SCREENSHOT_MAX_W = Inches(6.0)
DIAGRAM_MAX_W = Inches(6.0)
DIAGRAM_LANDSCAPE_MAX_W = Inches(9.0)

# ─── State for section management ──────────────────────────────────────

class SectionManager:
    """Manages document sections with proper orientation handling."""
    
    def __init__(self, doc: Document):
        self.doc = doc
        self.current_orientation = WD_ORIENT.PORTRAIT
        self._setup_initial_section()
    
    def _setup_initial_section(self):
        """Configure the initial portrait section."""
        section = self.doc.sections[0]
        section.page_width = PAGE_W
        section.page_height = PAGE_H
        section.orientation = WD_ORIENT.PORTRAIT
        self._set_margins(section)
        self._add_page_numbers(section)
        self.current_orientation = WD_ORIENT.PORTRAIT
    
    def _set_margins(self, section):
        section.top_margin = MARGIN
        section.bottom_margin = MARGIN
        section.left_margin = MARGIN
        section.right_margin = MARGIN
    
    def _add_page_numbers(self, section):
        footer = section.footer
        footer.is_linked_to_previous = False
        p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.style = self.doc.styles['Normal']
        p.clear()
        run = p.add_run()
        fldChar1 = OxmlElement('w:fldChar')
        fldChar1.set(qn('w:fldCharType'), 'begin')
        run._r.append(fldChar1)
        run2 = p.add_run()
        instrText = OxmlElement('w:instrText')
        instrText.set(qn('xml:space'), 'preserve')
        instrText.text = ' PAGE '
        run2._r.append(instrText)
        run3 = p.add_run()
        fldChar2 = OxmlElement('w:fldChar')
        fldChar2.set(qn('w:fldCharType'), 'end')
        run3._r.append(fldChar2)
    
    def ensure_orientation(self, target_orientation: WD_ORIENT):
        """Switch to target orientation if different, using a NEXT PAGE section break."""
        if target_orientation == self.current_orientation:
            return
        
        # Set the current (previous) section's break type to nextPage
        # In OOXML, the section break type is stored in the PRECEDING section's sectPr
        prev_sectPr = self.doc.sections[-1]._sectPr
        type_elem = OxmlElement('w:type')
        type_elem.set(qn('w:val'), 'nextPage')
        prev_sectPr.append(type_elem)
        
        # Add new section with the target orientation
        section = self.doc.add_section(WD_SECTION.NEW_PAGE)
        section.orientation = target_orientation
        
        if target_orientation == WD_ORIENT.LANDSCAPE:
            section.page_width = PAGE_H
            section.page_height = PAGE_W
        else:
            section.page_width = PAGE_W
            section.page_height = PAGE_H
        
        self._set_margins(section)
        self._add_page_numbers(section)
        self.current_orientation = target_orientation
    
    def ensure_portrait(self):
        self.ensure_orientation(WD_ORIENT.PORTRAIT)
    
    def ensure_landscape(self):
        self.ensure_orientation(WD_ORIENT.LANDSCAPE)


# ─── Style helpers ──────────────────────────────────────────────────────

def setup_styles(doc: Document) -> None:
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(12)
    font.color.rgb = RGBColor(0, 0, 0)
    pf = style.paragraph_format
    pf.line_spacing = 1.5
    pf.space_after = Pt(6)
    pf.space_before = Pt(0)
    pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    for level, size, bold, italic, space_before, space_after in [
        (1, 16, True, False, 24, 12),
        (2, 14, True, False, 18, 8),
        (3, 12, True, True, 12, 6),
    ]:
        h = doc.styles[f'Heading {level}']
        h.font.name = 'Times New Roman'
        h.font.size = Pt(size)
        h.font.bold = bold
        h.font.italic = italic
        h.font.color.rgb = RGBColor(0, 0, 0)
        h.paragraph_format.space_before = Pt(space_before)
        h.paragraph_format.space_after = Pt(space_after)
        h.paragraph_format.keep_with_next = True

    # Caption style
    caption_style = doc.styles['Caption'] if 'Caption' in doc.styles else doc.styles.add_style('Caption', WD_STYLE_TYPE.PARAGRAPH)
    caption_style.font.name = 'Times New Roman'
    caption_style.font.size = Pt(11)
    caption_style.font.italic = True
    caption_style.font.color.rgb = RGBColor(80, 80, 80)
    caption_style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption_style.paragraph_format.space_before = Pt(4)
    caption_style.paragraph_format.space_after = Pt(12)
    caption_style.paragraph_format.line_spacing = 1.0

    # Table title style
    table_title = doc.styles['Table Title'] if 'Table Title' in doc.styles else doc.styles.add_style('Table Title', WD_STYLE_TYPE.PARAGRAPH)
    table_title.font.name = 'Times New Roman'
    table_title.font.size = Pt(11)
    table_title.font.bold = True
    table_title.font.color.rgb = RGBColor(0, 0, 0)
    table_title.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    table_title.paragraph_format.space_before = Pt(6)
    table_title.paragraph_format.space_after = Pt(4)

    # Table cell style
    cell_style = doc.styles['Table Cell'] if 'Table Cell' in doc.styles else doc.styles.add_style('Table Cell', WD_STYLE_TYPE.PARAGRAPH)
    cell_style.font.name = 'Times New Roman'
    cell_style.font.size = Pt(10)
    cell_style.paragraph_format.space_before = Pt(2)
    cell_style.paragraph_format.space_after = Pt(2)
    cell_style.paragraph_format.line_spacing = 1.15


def add_toc_field(doc: Document):
    """Insert a live Table of Contents field."""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = p.add_run()
    fldChar1 = OxmlElement('w:fldChar')
    fldChar1.set(qn('w:fldCharType'), 'begin')
    run._r.append(fldChar1)
    run2 = p.add_run()
    instrText = OxmlElement('w:instrText')
    instrText.set(qn('xml:space'), 'preserve')
    instrText.text = ' TOC \\o "1-3" \\h \\z \\u '
    run2._r.append(instrText)
    run3 = p.add_run()
    fldChar2 = OxmlElement('w:fldChar')
    fldChar2.set(qn('w:fldCharType'), 'end')
    run3._r.append(fldChar2)
    doc.add_page_break()


# ─── Content parsing ────────────────────────────────────────────────────

class MDParser:
    def __init__(self, md_path: Path):
        self.lines = md_path.read_text(encoding='utf-8').splitlines()
        self.i = 0
        self.blocks = []
        self.figure_counter = 0
        self.table_counter = 0
        self._parse()

    def _parse(self):
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if not line.strip():
                self.i += 1
                continue

            if line.startswith('# '):
                self.blocks.append(('h1', line[2:].strip()))
                self.i += 1
            elif line.startswith('## '):
                self.blocks.append(('h2', line[3:].strip()))
                self.i += 1
            elif line.startswith('### '):
                self.blocks.append(('h3', line[4:].strip()))
                self.i += 1
            elif line.strip().startswith('!['):
                match = re.match(r'!\[([^\]]*)\]\(([^)]+)\)', line.strip())
                if match:
                    caption, path = match.groups()
                    self.figure_counter += 1
                    self.blocks.append(('figure', self.figure_counter, caption.strip(), path.strip()))
                self.i += 1
            elif '|' in line and self.i + 1 < len(self.lines) and '|' in self.lines[self.i + 1] and '-' in self.lines[self.i + 1]:
                self.blocks.append(('table', self._parse_table()))
            else:
                para_lines = []
                while self.i < len(self.lines):
                    l = self.lines[self.i]
                    if not l.strip():
                        break
                    if l.startswith(('# ', '## ', '### ', '![')):
                        break
                    if '|' in l and self.i + 1 < len(self.lines) and '|' in self.lines[self.i + 1] and '-' in self.lines[self.i + 1]:
                        break
                    para_lines.append(l)
                    self.i += 1
                if para_lines:
                    text = ' '.join(para_lines)
                    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
                    text = re.sub(r'\*([^*]+)\*', r'\1', text)
                    text = re.sub(r'`([^`]+)`', r'\1', text)
                    self.blocks.append(('para', text.strip()))

    def _parse_table(self):
        rows = []
        header = [c.strip() for c in self.lines[self.i].split('|')[1:-1]]
        rows.append(header)
        self.i += 1
        self.i += 1  # skip separator
        while self.i < len(self.lines) and '|' in self.lines[self.i]:
            row = [c.strip() for c in self.lines[self.i].split('|')[1:-1]]
            if row:
                rows.append(row)
            self.i += 1
        self.table_counter += 1
        return {'num': self.table_counter, 'rows': rows}


# ─── Document building ──────────────────────────────────────────────────

def resolve_image_path(path: str) -> Path:
    return (ROOT / "documentation" / path).resolve()


def add_figure(doc: Document, sect_mgr: SectionManager, fig_num: int, caption: str, img_path: Path):
    is_landscape = fig_num in LANDSCAPE_FIGURES
    
    # Switch orientation if needed
    if is_landscape:
        sect_mgr.ensure_landscape()
    else:
        sect_mgr.ensure_portrait()
    
    # Calculate appropriate image size
    from PIL import Image
    im = Image.open(img_path)
    iw, ih = im.size
    aspect = iw / ih
    
    if is_landscape:
        # Landscape page usable area: ~9.69" x 6.27"
        max_w = Inches(9.0)
        max_h = Inches(5.8)
        # Scale to fit within both dimensions
        scale_w = max_w / iw * 72  # convert to points
        scale_h = max_h / ih * 72
        scale = min(scale_w, scale_h) / 72  # back to inches
        display_w = Inches(iw * scale)
    else:
        # Portrait page usable area: ~6.27" x 8.30"
        max_w = Inches(5.8)
        max_h = Inches(7.5)
        scale_w = max_w / iw * 72
        scale_h = max_h / ih * 72
        scale = min(scale_w, scale_h) / 72
        display_w = Inches(iw * scale)
    
    # Add the image
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    run.add_picture(str(img_path), width=display_w)

    # Add caption - avoid duplicate "Figure N." if caption already starts with it
    cap_p = doc.add_paragraph(style='Caption')
    cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if caption.startswith(f"Figure {fig_num}."):
        display_caption = caption
    elif caption.startswith("Figure "):
        display_caption = f"Figure {fig_num}. {caption.split('.', 1)[1].strip()}"
    else:
        display_caption = f"Figure {fig_num}. {caption}"
    cap_run = cap_p.add_run(display_caption)
    cap_run.font.italic = True


def add_table(doc: Document, table_data: dict):
    rows = table_data['rows']
    if not rows:
        return
    num_cols = len(rows[0])

    title_p = doc.add_paragraph(style='Table Title')
    title_run = title_p.add_run(f"Table {table_data['num']}. ")
    title_run.bold = True
    title_run2 = title_p.add_run(' '.join(rows[0][:3]) + ('...' if len(rows[0]) > 3 else ''))

    table = doc.add_table(rows=len(rows), cols=num_cols, style='Table Grid')
    table.autofit = True

    for i, row in enumerate(rows):
        for j, cell_text in enumerate(row):
            cell = table.cell(i, j)
            cell.text = ''
            p = cell.paragraphs[0]
            run = p.add_run(cell_text)
            run.font.name = 'Times New Roman'
            run.font.size = Pt(10)
            if i == 0:
                run.bold = True
                shading = OxmlElement('w:shd')
                shading.set(qn('w:fill'), 'EAF3FB')
                shading.set(qn('w:val'), 'clear')
                cell._tc.get_or_add_tcPr().append(shading)

    doc.add_paragraph()


def add_paragraph(doc: Document, text: str, style_name: str = 'Normal'):
    if not text.strip():
        return
    p = doc.add_paragraph(style=style_name)
    parts = re.split(r'(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)', text)
    for part in parts:
        if not part:
            continue
        run = p.add_run()
        if part.startswith('**') and part.endswith('**'):
            run.bold = True
            run.text = part[2:-2]
        elif part.startswith('*') and part.endswith('*'):
            run.italic = True
            run.text = part[1:-1]
        elif part.startswith('`') and part.endswith('`'):
            run.font.name = 'Consolas'
            run.font.size = Pt(10)
            run.text = part[1:-1]
        else:
            run.text = part
        run.font.name = 'Times New Roman'
        run.font.size = Pt(12)


# ─── Main build function ────────────────────────────────────────────────

def build_docx():
    doc = Document()
    setup_styles(doc)
    
    sect_mgr = SectionManager(doc)
    
    parser = MDParser(MD_PATH)
    
    # Add TOC after title page (first h1)
    toc_added = False
    fig_num = 0
    
    for block in parser.blocks:
        btype = block[0]
        
        if btype == 'h1':
            if toc_added:
                doc.add_page_break()
            else:
                # After the first h1 (title), add TOC
                pass
            doc.add_heading(block[1], level=1)
            if not toc_added:
                add_toc_field(doc)
                toc_added = True
        
        elif btype == 'h2':
            doc.add_heading(block[1], level=2)
        
        elif btype == 'h3':
            doc.add_heading(block[1], level=3)
        
        elif btype == 'figure':
            fig_num = block[1]
            caption = block[2]
            rel_path = block[3]
            img_path = resolve_image_path(rel_path)
            add_figure(doc, sect_mgr, fig_num, caption, img_path)
        
        elif btype == 'table':
            add_table(doc, block[1])
        
        elif btype == 'para':
            add_paragraph(doc, block[1])
    
    # Ensure document ends in portrait
    sect_mgr.ensure_portrait()
    
    doc.save(OUTPUT_PATH)
    print(f"Saved: {OUTPUT_PATH}")
    print(f"Figures processed: {fig_num}")
    print(f"Tables processed: {parser.table_counter}")
    print(f"Sections: {len(doc.sections)}")


if __name__ == '__main__':
    build_docx()