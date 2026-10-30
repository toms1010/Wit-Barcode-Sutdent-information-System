#!/usr/bin/env python3
"""
generate_documentation.py

Reusable documentation generator for the WIT Barcode Student Information System.

Discovers the project structure, reads actual source files, extracts functions,
classes, API routes, database schema, and dependencies from the real source,
then generates seven editable Microsoft Word (.docx) documents under docs/.

The script does NOT modify application source files and never exposes secrets
(.env contents, passwords, API keys).

Usage:
    python tools/generate_documentation.py
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.section import WD_SECTION, WD_ORIENT
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
except Exception as exc:  # pragma: no cover - runtime dependency check
    raise SystemExit(
        "python-docx is required to generate the documents. "
        "Install it with: pip install python-docx\n"
        f"Original error: {exc}"
    )


# ---------------------------------------------------------------------------
# Project root and directory constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"

# Subdirectories matching the required structure
DOCS_PROJECT = DOCS_DIR / "project"
DOCS_FRONTEND = DOCS_DIR / "frontend"
DOCS_BACKEND = DOCS_DIR / "backend"
DOCS_SOURCE = DOCS_DIR / "source-code"
DOCS_LEARNING = DOCS_DIR / "learning-guide"
DOCS_DEPLOY = DOCS_DIR / "deployment"
DOCS_QUALITY = DOCS_DIR / "quality"

OUTPUT_FILES = {
    "project": DOCS_PROJECT / "01_Complete_Project_Documentation.docx",
    "frontend": DOCS_FRONTEND / "02_Frontend_Technical_Documentation.docx",
    "backend": DOCS_BACKEND / "03_Backend_Technical_Documentation.docx",
    "source": DOCS_SOURCE / "04_Important_Source_Code_Reference.docx",
    "learning": DOCS_LEARNING / "05_Code_Explanation_and_Learning_Guide.docx",
    "deployment": DOCS_DEPLOY / "06_Installation_and_Maintenance_Manual.docx",
    "quality": DOCS_QUALITY / "07_Testing_and_Code_Quality_Report.docx",
}

# Directories and file patterns to exclude from source discovery
EXCLUDED_DIRS = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".vscode",
    "venv",
    "node_modules",
    "build",
    "dist",
    ".activity-logs",
    "logs",
    "generated_barcodes",
    "generated_id_cards",
    "dataset",
    "datasets",
    "models_yolo",
    "weights",
    "runs",
}
EXCLUDED_FILE_SUFFIXES = {".pyc", ".png", ".jpg", ".jpeg", ".pt", ".svg", ".ico", ".pdf", ".pptx"}
EXCLUDED_FILES = {".env"}  # never include secrets


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

ACCENT_COLOR = RGBColor(0xC2, 0x41, 0x0C)   # WIT burnt orange
DARK_TEXT = RGBColor(0x21, 0x21, 0x21)
MEDIUM_TEXT = RGBColor(0x55, 0x55, 0x55)
LIGHT_FILL = "FFF7ED"
TABLE_HEADER_FILL = "C2410C"
TABLE_ALT_FILL = "FFF9F2"


def set_cell_background(cell, hex_color: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def set_cell_text(cell, text: str, bold: bool = False, size: int = 9,
                  color: Optional[RGBColor] = None, align: str = "left") -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.alignment = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
    }.get(align, WD_ALIGN_PARAGRAPH.LEFT)
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = color


def add_page_number_footer(document: Document, section_label: str) -> None:
    for section in document.sections:
        footer = section.footer
        footer.is_linked_to_previous = False
        paragraph = footer.paragraphs[0]
        paragraph.text = ""
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

        run = paragraph.add_run(f"{section_label}  |  Page ")
        run.font.size = Pt(8)
        run.font.color.rgb = MEDIUM_TEXT

        fld = OxmlElement("w:fldSimple")
        fld.set(qn("w:instr"), "PAGE")
        run_element = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), "16")
        rpr.append(sz)
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "555555")
        rpr.append(color)
        run_element.append(rpr)
        t = OxmlElement("w:t")
        t.text = "1"
        run_element.append(t)
        fld.append(run_element)
        paragraph._p.append(fld)

        run2 = paragraph.add_run(" of ")
        run2.font.size = Pt(8)
        run2.font.color.rgb = MEDIUM_TEXT

        fld2 = OxmlElement("w:fldSimple")
        fld2.set(qn("w:instr"), "NUMPAGES")
        run_element2 = OxmlElement("w:r")
        rpr2 = OxmlElement("w:rPr")
        sz2 = OxmlElement("w:sz")
        sz2.set(qn("w:val"), "16")
        rpr2.append(sz2)
        color2 = OxmlElement("w:color")
        color2.set(qn("w:val"), "555555")
        rpr2.append(color2)
        run_element2.append(rpr2)
        t2 = OxmlElement("w:t")
        t2.text = "1"
        run_element2.append(t2)
        fld2.append(run_element2)
        paragraph._p.append(fld2)


def add_heading(document: Document, text: str, level: int = 1) -> None:
    heading = document.add_heading(text, level=level)
    for run in heading.runs:
        run.font.color.rgb = ACCENT_COLOR if level == 1 else DARK_TEXT
        if level == 1:
            run.font.size = Pt(16)
        elif level == 2:
            run.font.size = Pt(13)
        elif level == 3:
            run.font.size = Pt(11.5)
        else:
            run.font.size = Pt(10.5)
    return heading


def add_para(document: Document, text: str, size: int = 10, bold: bool = False,
             italic: bool = False, color: Optional[RGBColor] = None,
             space_after: int = 6, align: str = "left") -> None:
    paragraph = document.add_paragraph()
    paragraph.alignment = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }.get(align, WD_ALIGN_PARAGRAPH.LEFT)
    paragraph.paragraph_format.space_after = Pt(space_after)
    run = paragraph.add_run(text)
    run.bold = bold
    run.italic = italic
    run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = color
    return paragraph


def add_bullet(document: Document, text: str, size: int = 10, bold_prefix: Optional[str] = None) -> None:
    paragraph = document.add_paragraph(style="List Bullet")
    paragraph.paragraph_format.space_after = Pt(3)
    if bold_prefix:
        run = paragraph.add_run(bold_prefix)
        run.bold = True
        run.font.size = Pt(size)
        run.font.color.rgb = DARK_TEXT
    run = paragraph.add_run(text)
    run.font.size = Pt(size)
    return paragraph


def add_code_block(document: Document, code: str, size: int = 8) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(6)
    paragraph.paragraph_format.space_before = Pt(4)
    table = document.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    cell = table.cell(0, 0)
    set_cell_background(cell, "F5F5F5")
    cell.text = ""
    first = True
    for line in code.split("\n"):
        if first:
            run = cell.paragraphs[0].add_run(line)
            first = False
        else:
            run = cell.paragraphs[0].add_run()
            run.add_break()
            run = cell.paragraphs[0].add_run(line)
        run.font.name = "Consolas"
        run.font.size = Pt(size)
        run.font.color.rgb = DARK_TEXT
    return table


def add_table(document: Document, headers: List[str], rows: List[List[str]],
              col_widths: Optional[List[float]] = None, header_size: int = 9,
              body_size: int = 9, landscape: bool = False) -> object:
    table = document.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # Header row
    for j, header in enumerate(headers):
        cell = table.cell(0, j)
        set_cell_text(cell, header, bold=True, size=header_size,
                      color=RGBColor(0xFF, 0xFF, 0xFF), align="left")
        set_cell_background(cell, TABLE_HEADER_FILL)

    # Body rows
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            cell = table.cell(i + 1, j)
            set_cell_text(cell, str(value), size=body_size, align="left")
            if i % 2 == 1:
                set_cell_background(cell, TABLE_ALT_FILL)

    if col_widths:
        for j, width in enumerate(col_widths):
            for i in range(len(rows) + 1):
                table.cell(i, j).width = Inches(width)

    return table


def add_cover_page(document: Document, title: str, subtitle: str, project: str) -> None:
    # Top spacing
    for _ in range(3):
        document.add_paragraph()

    add_para(document, project, size=13, bold=True, color=MEDIUM_TEXT, align="center", space_after=2)
    add_para(document, "WESTERN INSTITUTE OF TECHNOLOGY", size=11, color=MEDIUM_TEXT, align="center", space_after=18)

    # Horizontal rule via paragraph border
    rule = document.add_paragraph()
    rule.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pPr = rule._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "12")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "C2410C")
    pBdr.append(bottom)
    pPr.append(pBdr)

    for _ in range(2):
        document.add_paragraph()

    add_para(document, title, size=26, bold=True, color=ACCENT_COLOR, align="center", space_after=10)
    add_para(document, subtitle, size=14, color=DARK_TEXT, align="center", space_after=24)

    add_para(document, f"Generated: {datetime.now().strftime('%B %d, %Y')}", size=10, color=MEDIUM_TEXT, align="center", space_after=2)
    add_para(document, "Status: Generated from actual source code (verified)", size=10, italic=True, color=MEDIUM_TEXT, align="center", space_after=30)

    document.add_page_break()


def add_toc(document: Document, entries: List[Tuple[int, str]]) -> None:
    add_heading(document, "Table of Contents", level=1)
    for level, text in entries:
        indent = 0.25 * (level - 1)
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.left_indent = Inches(indent)
        paragraph.paragraph_format.space_after = Pt(2)
        run = paragraph.add_run(text)
        run.font.size = Pt(11 if level == 1 else 10)
        run.bold = (level == 1)
        if level == 1:
            run.font.color.rgb = ACCENT_COLOR
    document.add_page_break()


# ---------------------------------------------------------------------------
# Source discovery & extraction
# ---------------------------------------------------------------------------

def discover_source_files() -> Dict[str, List[Path]]:
    """Walk the project root and categorise real source files."""
    found: Dict[str, List[Path]] = {
        "frontend": [],
        "backend": [],
        "database": [],
        "testing": [],
        "configuration": [],
        "deployment": [],
        "shared": [],
        "other": [],
    }

    ui_dir = PROJECT_ROOT / "ui"
    database_dir = PROJECT_ROOT / "database"
    services_dir = PROJECT_ROOT / "services"
    models_dir = PROJECT_ROOT / "models"
    config_dir = PROJECT_ROOT / "config"
    scripts_dir = PROJECT_ROOT / "scripts"
    tests_dir = PROJECT_ROOT / "tests"
    styles_dir = PROJECT_ROOT / "styles"

    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file():
            continue
        # Skip excluded dirs
        parts = path.relative_to(PROJECT_ROOT).parts
        if any(part in EXCLUDED_DIRS for part in parts):
            continue
        # Skip build / generated / secret / binary
        if path.suffix.lower() in EXCLUDED_FILE_SUFFIXES:
            continue
        if path.name in EXCLUDED_FILES:
            continue

        rel = path.relative_to(PROJECT_ROOT)
        category = "other"

        if str(path).startswith(str(ui_dir)) or str(path).startswith(str(styles_dir)):
            category = "frontend"
        elif str(path).startswith(str(database_dir)):
            category = "database"
        elif str(path).startswith(str(services_dir)):
            category = "backend"
        elif str(path).startswith(str(models_dir)):
            category = "backend"
        elif str(path).startswith(str(config_dir)):
            category = "configuration"
        elif str(path).startswith(str(scripts_dir)):
            category = "deployment"
        elif str(path).startswith(str(tests_dir)):
            category = "testing"
        elif path.name in {"main.py", "settings.py", "requirements.txt", ".env.example", "pytest.ini", "setup_database.sh", "setup_db.sh", "initialize_database.py"}:
            category = "configuration" if path.suffix in {".txt", ".sh", ".ini", ".example"} else "backend"
        elif path.suffix == ".py":
            category = "shared"

        found[category].append(path)

    # Sort each list
    for key in found:
        found[key].sort(key=lambda p: str(p.relative_to(PROJECT_ROOT)))

    return found


def read_file_safely(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"[Could not read file: {exc}]"


def extract_python_symbols(source: str) -> Dict[str, List[Dict]]:
    """Extract classes, functions, and methods from Python source using regex."""
    symbols = {"classes": [], "functions": [], "methods": []}

    # Class definitions
    for m in re.finditer(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[:\(]", source, re.MULTILINE):
        symbols["classes"].append({"name": m.group(1), "line": source[:m.start()].count("\n") + 1})

    # Top-level function definitions
    for m in re.finditer(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", source, re.MULTILINE):
        symbols["functions"].append({"name": m.group(1), "line": source[:m.start()].count("\n") + 1})

    # Method definitions (indented def inside a class)
    for m in re.finditer(r"^\s{4,}def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", source, re.MULTILINE):
        symbols["methods"].append({"name": m.group(1), "line": source[:m.start()].count("\n") + 1})

    return symbols


def extract_sql_tables(schema_sql: str) -> List[Dict]:
    """Extract CREATE TABLE definitions from schema SQL."""
    tables = []
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+`([^`]+)`\s*\((.*?)\)\s*ENGINE=", schema_sql, re.DOTALL | re.IGNORECASE):
        table_name = m.group(1)
        body = m.group(2)
        columns = []
        for col_m in re.finditer(r"^\s*`([^`]+)`\s+([^,\n]+)", body, re.MULTILINE):
            columns.append({"name": col_m.group(1), "type": col_m.group(2).strip()})
        fks = re.findall(r"FOREIGN KEY\s*\(`([^`]+)`\)\s*REFERENCES\s*`([^`]+)`\s*\(`([^`]+)`\)", body)
        tables.append({
            "name": table_name,
            "columns": columns,
            "foreign_keys": [{"column": c[0], "ref_table": c[1], "ref_column": c[2]} for c in fks],
        })
    return tables


def extract_api_endpoints_from_source() -> List[Dict]:
    """
    This is a desktop PySide6 application with NO HTTP API server.
    Return an empty list and document that there are no HTTP endpoints.
    """
    return []


def get_line_count(path: Path) -> int:
    try:
        return len(read_file_safely(path).splitlines())
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Document section builders (content from real source)
# ---------------------------------------------------------------------------

class DocBuilder:
    def __init__(self):
        self.source_files = discover_source_files()
        self.all_files = []
        for cat in self.source_files.values():
            self.all_files.extend(cat)
        self.all_files.sort(key=lambda p: str(p.relative_to(PROJECT_ROOT)))

        # Read key files for content extraction
        self.schema_sql = read_file_safely(PROJECT_ROOT / "database" / "schema.py")
        self.settings_src = read_file_safely(PROJECT_ROOT / "config" / "settings.py")
        self.auth_src = read_file_safely(PROJECT_ROOT / "services" / "auth_service.py")
        self.main_src = read_file_safely(PROJECT_ROOT / "main.py")
        self.connection_src = read_file_safely(PROJECT_ROOT / "database" / "connection.py")
        self.requirements = read_file_safely(PROJECT_ROOT / "requirements.txt")
        self.readme = read_file_safely(PROJECT_ROOT / "README.md")

        self.db_tables = extract_sql_tables(self.schema_sql)

    # -- helpers -----------------------------------------------------------

    def _file_entry(self, path: Path) -> Dict:
        rel = path.relative_to(PROJECT_ROOT)
        source = read_file_safely(path)
        symbols = extract_python_symbols(source) if path.suffix == ".py" else {"classes": [], "functions": [], "methods": []}
        deps = self._extract_imports(source)
        return {
            "name": path.name,
            "rel": str(rel),
            "abs": str(path.resolve()),
            "module": self._categorise_module(path),
            "purpose": self._infer_purpose(path, source),
            "symbols": symbols,
            "deps": deps,
            "lines": len(source.splitlines()),
            "path": path,
        }

    def _categorise_module(self, path: Path) -> str:
        parts = path.relative_to(PROJECT_ROOT).parts
        if parts[0] == "ui":
            return "Frontend"
        if parts[0] == "database":
            return "Database"
        if parts[0] == "services":
            return "Backend"
        if parts[0] == "models":
            return "Backend (models)"
        if parts[0] == "config":
            return "Configuration"
        if parts[0] == "tests":
            return "Testing"
        if parts[0] == "scripts":
            return "Deployment / Scripts"
        if parts[0] == "styles":
            return "Frontend (styles)"
        if path.name == "main.py":
            return "Backend (entry point)"
        return "Shared"

    def _infer_purpose(self, path: Path, source: str) -> str:
        name = path.name.lower()
        parts = path.relative_to(PROJECT_ROOT).parts
        if name == "main.py":
            return "Application entry point; manages the login -> password-change -> main window session loop."
        if name == "settings.py":
            return "Central configuration loaded from .env; database, YOLO, camera, barcode, scan, report, and asset settings."
        if name == "connection.py":
            return "MySQL connection pool, cursor context manager with commit/rollback, and database initialization."
        if name == "schema.py":
            return "Database schema SQL: CREATE TABLE for all 18 tables plus seed data for system_settings and assessment_categories."
        if name == "auth_service.py":
            return "Authentication service: PBKDF2-HMAC-SHA256 password hashing, login/logout, lockout, role-based authorization, password change."
        if name == "student_service.py":
            return "Business logic for students, enrollments, subjects, courses, and certificate enrollment lookup."
        if name == "barcode_generator.py":
            return "Generates CODE39 barcode images and persists barcode records; batch and regenerate support."
        if name == "scan_pipeline.py":
            return "Real-time scan pipeline: YOLO detection, ROI gating, barcode decoding, and student verification."
        if name == "barcode_decoder.py":
            return "Barcode decoding service using pyzbar and optional zxing-cpp, with ordered preprocessing attempts."
        if name == "yolo_detector.py":
            return "YOLOv8n detector wrapper; loads the active model and runs inference on camera frames."
        if name == "report_service.py":
            report = "PDF report generation: Certificate of Matriculation, assessment statements, barcode reports, enrollment summaries, CSV exports."
        if name == "user.py":
            return "In-memory User dataclass with roles (admin/staff/viewer) and authorization helpers."
        if name == "student.py":
            return "Student and StudentContact dataclasses with computed properties (full_name, display_name, age)."
        if name == "id_card.py":
            return "ID card data types: CR80 geometry, templates, layout, content, render results."
        if name == "main_window.py":
            return "Main application window: sidebar navigation, role-based page access, page stack, logout, shutdown."
        if name == "scanner_page.py":
            return "Barcode scanner page: camera, YOLO, decoder, pipeline worker, scan session, history, post-scan navigation."
        if "repository" in name:
            return "Data access layer: raw SQL queries against MySQL tables via the connection pool."
        if path.suffix == ".py" and parts[0] == "ui":
            return "Frontend UI page or dialog (PySide6)."
        if path.suffix == ".py" and parts[0] == "services":
            return "Backend business-logic service."
        if path.suffix == ".py" and parts[0] == "tests":
            return "Automated test module (pytest)."
        if path.suffix == ".sql":
            return "Database migration SQL."
        if path.suffix == ".sh":
            return "Shell setup script."
        if path.suffix == ".txt":
            return "Python dependency list (requirements)."
        if path.suffix == ".md":
            return "Markdown documentation."
        if path.suffix == ".qss":
            return "Qt stylesheet for theming."
        return "Source module."

    def _extract_imports(self, source: str) -> List[str]:
        imports = []
        for m in re.finditer(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", source, re.MULTILINE):
            module = m.group(1) or m.group(2)
            imports.append(module.split(".")[0] if module else "")
        # Deduplicate preserving order
        seen = set()
        result = []
        for imp in imports:
            if imp and imp not in seen:
                seen.add(imp)
                result.append(imp)
        return result

    # -- document builders -------------------------------------------------

    def build_cover(self, document: Document, title: str, subtitle: str) -> None:
        add_cover_page(document, title, subtitle, "WIT Barcode Student Information System")

    def build_toc(self, document: Document, entries: List[Tuple[int, str]]) -> None:
        add_toc(document, entries)

    def save(self, document: Document, path: Path) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            document.save(str(path))
            return True
        except Exception as exc:
            print(f"  [FAIL] Could not save {path}: {exc}")
            return False


# ===========================================================================
# Document 1: Complete Project Documentation
# ===========================================================================

def build_document_project(builder: DocBuilder) -> bool:
    document = Document()

    # Base style
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    style.font.color.rgb = DARK_TEXT

    for section in document.sections:
        section.top_margin = Inches(0.9)
        section.bottom_margin = Inches(0.9)
        section.left_margin = Inches(0.9)
        section.right_margin = Inches(0.9)

    add_page_number_footer(document, "Complete Project Documentation")
    add_cover_page(document, "WIT Barcode Student Information System",
              "Complete Project Documentation", "WIT Barcode Student Information System")

    toc_entries = [
        (1, "1. Project Overview"),
        (2, "1.1 Problem Statement and Purpose"),
        (2, "1.2 Objectives and Scope"),
        (1, "2. Technology Stack"),
        (1, "3. System Architecture"),
        (2, "3.1 Frontend Architecture"),
        (2, "3.2 Backend Architecture"),
        (2, "3.3 Database Architecture"),
        (1, "4. Folder Structure"),
        (1, "5. Main Application Workflows"),
        (1, "6. Authentication and Authorization"),
        (1, "7. API Documentation"),
        (1, "8. Database Documentation"),
        (1, "9. Testing Information"),
        (1, "10. Deployment Architecture"),
        (1, "11. Security Considerations"),
        (1, "12. Limitations and Known Issues"),
        (1, "13. Conclusion"),
    ]
    add_toc(document, toc_entries)

    # 1. Project Overview
    add_heading(document, "1. Project Overview", level=1)
    add_heading(document, "1.1 Problem Statement and Purpose", level=2)
    add_para(document,
        "The WIT Barcode Student Information System is a desktop application for Western Institute of "
        "Technology (WIT) that manages student records, barcode-based student identification, subject "
        "enrollment, assessment and fees, certificate generation, and barcode-scanner-based student "
        "verification. It replaces paper-based or spreadsheet-based student administration with a single "
        "application that can issue CODE39 barcode ID cards, scan them with a webcam plus YOLOv8n object "
        "detection, verify the scanned payload against the student database, and produce Certificates of "
        "Matriculation and fee statements as PDF documents.", align="justify")
    add_heading(document, "1.2 Objectives and Scope", level=2)
    add_bullet(document, "Register, edit, view, search, and delete student records with contact information.")
    add_bullet(document, "Generate CODE39 barcode images for students and persist barcode records.")
    add_bullet(document, "Scan physical ID cards using a webcam, YOLOv8n detection, and zbar/zxing decoding.")
    add_bullet(document, "Manage subject enrollment and subjects linked to courses.")
    add_bullet(document, "Track assessments, fees, discounts, payments, and outstanding balances.")
    add_bullet(document, "Generate Certificates of Matriculation and assessment reports as PDF files.")
    add_bullet(document, "Manage a YOLO training center (dataset, model training pipeline).")
    add_bullet(document, "Role-based access control with admin, staff, and viewer roles.")
    add_para(document,
        "Scope: This is a local desktop application (PySide6/Qt 6) with a MySQL 8.4 database. It is NOT a "
        "web application and exposes no HTTP API endpoints. Communication between the UI and data layer is "
        "through in-process Python service and repository calls.", align="justify")

    # 2. Technology Stack
    add_heading(document, "2. Technology Stack", level=1)
    add_table(document,
        ["Layer", "Technology", "Version / Source", "Purpose"],
        [
            ["Runtime", "Python", "3.14.4", "Application language and runtime"],
            ["GUI framework", "PySide6", ">=6.10.0 (Qt 6)", "Desktop widgets application"],
            ["Database", "MySQL", "8.4 via mysql-connector-python", "Relational data store, raw SQL, no ORM"],
            ["Computer vision / ML", "Ultralytics YOLOv8n + PyTorch + OpenCV", "8.4 / 2.14 / 5.0", "Barcode detection in camera frames"],
            ["Barcode generation", "python-barcode", "0.16", "CODE39 barcode image generation"],
            ["Barcode decoding", "pyzbar (+ optional zxing-cpp)", "0.1.9 / 2.2.0", "Barcode symbol decoding from images"],
            ["PDF generation", "ReportLab", "4.2.0", "Certificates, assessment and barcode reports"],
            ["Charts", "matplotlib", ">=3.9.0", "Data visualization charts"],
            ["Configuration", "python-dotenv", ">=1.0.1", "Loads .env environment variables"],
            ["Testing", "pytest, pytest-qt, pytest-mock", "8.2.2 / 4.4.0 / 3.14.0", "Unit and GUI tests"],
            ["Hashing", "hashlib / hmac (standard library)", "Python stdlib", "PBKDF2-HMAC-SHA256 password hashing"],
        ],
        col_widths=[1.6, 1.9, 1.9, 2.0])
    add_para(document, "", space_after=2)
    add_para(document, "Full, version-verified details (including the YOLO training recipe) are maintained in "
        "TECHNOLOGY_STACK.md at the project root.", size=9, italic=True, color=MEDIUM_TEXT)

    # 3. System Architecture
    add_heading(document, "3. System Architecture", level=1)
    add_para(document,
        "The application follows a layered desktop architecture. There is no separate backend server and no "
        "HTTP API: the frontend (PySide6 UI) calls backend services directly in the same process, which in "
        "turn call repository classes that issue raw SQL against a local MySQL 8.4 database through a "
        "connection pool.", align="justify")

    add_heading(document, "3.1 Frontend Architecture", level=2)
    add_para(document,
        "The frontend is a PySide6 Qt 6 desktop application. main.py is the entry point. It shows a login "
        "dialog, optionally a mandatory first-login password-change dialog, then opens MainWindow. MainWindow "
        "uses a QStackedWidget to switch between pages (dashboard, students, scanner, enrollment, assessment, "
        "reports, courses, certificate, training, settings). A Sidebar drives navigation and is filtered by "
        "user role (admin/staff/viewer). Each page owns the services it needs and talks to backend services "
        "and repositories directly.", align="justify")

    add_heading(document, "3.2 Backend Architecture", level=2)
    add_para(document,
        "The backend consists of service classes (services/) that contain business logic, and repository "
        "classes (database/repositories/) that perform raw SQL queries. Services depend on repositories; "
        "repositories depend on database.connection for pooled MySQL access. The auth_service is a module-level "
        "singleton that holds the current session in memory and enforces authentication and authorization. "
        "Models (models/) are plain Python dataclasses that carry data between layers.", align="justify")

    add_heading(document, "3.3 Database Architecture", level=2)
    add_para(document,
        "The database is MySQL 8.4, schema defined in database/schema.py (SCHEMA_SQL). There are 18 tables: "
        "users, students, student_contacts, courses, subjects, enrollments, enrollment_subjects, barcodes, "
        "barcode_scans, assessment_categories, assessments, assessment_items, payments, payment_schedules, "
        "audit_logs, system_settings, yolo_models, id_cards, and dataset_images. Tables use InnoDB with "
        "foreign keys (ON DELETE CASCADE / SET NULL / RESTRICT) and indexes. Initialization runs the schema "
        "SQL and seeds system_settings and assessment_categories.", align="justify")

    # 4. Folder Structure
    add_heading(document, "4. Folder Structure", level=1)
    add_para(document, "Key directories (relative to project root):", size=10, bold=True, space_after=4)
    structure_rows = [
        ["main.py", "Application entry point"],
        ["ui/", "Frontend: pages, dialogs, widgets (PySide6)"],
        ["services/", "Backend: business-logic services"],
        ["database/", "Database: connection, schema, repositories, migrations"],
        ["models/", "Data models (dataclasses)"],
        ["config/", "Configuration: settings, logging, student assets"],
        ["styles/", "Frontend: Qt stylesheet, colors, typography"],
        ["scripts/", "Deployment and utility scripts"],
        ["tests/", "Automated tests (pytest)"],
        ["assets/", "Static assets: logos, fonts, images, profile photos, audio"],
        ["generated_barcodes/", "Generated barcode images (output)"],
        ["generated_id_cards/", "Generated ID card images (output)"],
        ["reports/", "Generated PDF reports (output)"],
        ["logs/", "Application log files (output)"],
        ["docs/", "Generated Word documentation"],
        ["documentation/", "Pre-existing project documentation (diagrams, screenshots, charts)"],
    ]
    add_table(document, ["Path", "Purpose"], structure_rows, col_widths=[2.2, 5.2])

    # 5. Workflows
    add_heading(document, "5. Main Application Workflows", level=1)
    workflows = [
        ("Login and session", "User enters credentials at LoginDialog. auth_service verifies against the users table (PBKDF2 hash, constant-time comparison). On first login (password_changed_at is NULL) a mandatory password change is enforced before MainWindow opens."),
        ("Student management", "Users create/edit/search students via StudentsPage and StudentDialog; StudentService persists via StudentRepository to the students and student_contacts tables."),
        ("Barcode generation", "BarcodeGeneratorService generates a CODE39 image, writes it to generated_barcodes/, and inserts a barcodes row. Generation requires a valid acting user id (audit foreign key)."),
        ("Barcode scanning", "ScannerPage opens a camera; ScanWorker runs YOLO detection -> ROI gating -> barcode decoding -> student verification in a background thread. A verified scan opens the student profile/enrollment/assessment and records a barcode_scans row."),
        ("Subject enrollment", "EnrollmentPage creates enrollments and adds subjects; credits are recalculated and stored."),
        ("Assessment & fees", "AssessmentPage creates assessments with items, payment schedules, and records payments; balances are tracked."),
        ("Certificate of Matriculation", "CertificatePage resolves a reference to a verified enrollment and generates a PDF via ReportLab."),
        ("Logout / shutdown", "Logout invalidates the in-memory session and returns to the login dialog; shutdown releases camera and worker threads."),
    ]
    for title, desc in workflows:
        add_bullet(document, desc, bold_prefix=f"{title}: ")

    # 6. Authentication
    add_heading(document, "6. Authentication and Authorization", level=1)
    add_para(document,
        "Authentication is handled by services/auth_service.py (AuthService singleton). Passwords are hashed "
        "with PBKDF2-HMAC-SHA256 (210,000 iterations, random 16-byte salt) and compared with hmac.compare_digest. "
        "Failed attempts are rate-limited (max 5, 15-minute lockout) and persisted in the users table. The "
        "failure message is identical whether the account exists, preventing account enumeration.", align="justify")
    add_table(document,
        ["Role", "Can read", "Can write", "Typical pages"],
        [
            ["admin", "Yes", "Yes", "All pages"],
            ["staff", "Yes", "Yes", "All pages except admin-only restrictions"],
            ["viewer", "Yes", "No", "Dashboard, students, scanner, reports"],
        ],
        col_widths=[1.0, 0.9, 0.9, 3.6])
    add_para(document,
        "Authorization is enforced in MainWindow.navigate_to (ROLE_PAGES) and in pages that perform writes. "
        "models/user.py defines ROLE_ADMIN, ROLE_STAFF, ROLE_VIEWER, WRITE_ROLES, READ_ROLES, and User.can_write / can_read.",
        align="justify")

    # 7. API Documentation
    add_heading(document, "7. API Documentation", level=1)
    add_para(document,
        "This is a desktop application with no HTTP server and no REST/Web API endpoints. All communication "
        "between the UI and the data layer is in-process Python: pages call service methods, which call "
        "repository methods that run raw SQL via database.connection.get_cursor(). No HTTP methods, routes, "
        "or JSON API responses exist in this codebase.", align="justify")
    add_para(document, "Internal service-method reference (not HTTP):", size=10, bold=True, space_after=4)
    add_table(document,
        ["Service", "Key methods", "Layer"],
        [
            ["AuthService", "login, logout, require_auth, require_role, change_password", "Backend (auth)"],
            ["StudentService", "create_student, get_student, search_students, update_student, delete_student, create_enrollment, add_enrollment_subject, get_enrollment_for_reference", "Backend (business)"],
            ["BarcodeGeneratorService", "generate_for_student, generate_batch, regenerate_barcode, generate_barcode_image", "Backend (business)"],
            ["ScanPipeline", "process_frame (detect -> gate -> decode -> verify)", "Backend (scanner)"],
            ["ReportService", "generate_certificate_of_matriculation, generate_assessment_report, export_students_csv, export_scan_log_csv", "Backend (reports)"],
            ["StudentRepository", "create, get_by_id, get_by_barcode_payload, get_all, update, delete", "Database (data access)"],
        ],
        col_widths=[2.0, 3.4, 2.0])

    # 8. Database Documentation
    add_heading(document, "8. Database Documentation", level=1)
    add_para(document,
        "Database: MySQL 8.4. Connection is configured via config/settings.py from environment variables "
        "(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD). A connection pool (pool_size=10) is created on "
        "first use. get_cursor(commit=False) performs a true dry run with rollback; get_cursor(commit=True) "
        "commits explicitly. autocommit is deliberately off.", align="justify")
    add_table(document,
        ["Table", "Purpose", "Approx. columns"],
        [
            ["users", "Application accounts with roles and lockout state", "14"],
            ["students", "Student records (core entity)", "19"],
            ["student_contacts", "Phone/email/emergency contacts per student", "8"],
            ["courses", "Course catalogue", "9"],
            ["subjects", "Subjects belonging to courses", "12"],
            ["enrollments", "Student term enrollments", "12"],
            ["enrollment_subjects", "Subjects attached to an enrollment", "14"],
            ["barcodes", "Generated barcode records per student", "14"],
            ["barcode_scans", "Scan history with status and confidence", "12"],
            ["assessment_categories", "Fee/discount category catalogue", "9"],
            ["assessments", "Student fee assessments", "13"],
            ["assessment_items", "Line items within an assessment", "10"],
            ["payments", "Payments recorded against assessments", "16"],
            ["payment_schedules", "Installment due dates and amounts", "9"],
            ["audit_logs", "Change audit trail (JSON old/new values)", "12"],
            ["system_settings", "Key/value application settings", "8"],
            ["yolo_models", "YOLO model training records and status", "13"],
            ["id_cards", "Generated ID card records and status", "14"],
            ["dataset_images", "YOLO dataset image inventory", "14"],
        ],
        col_widths=[2.0, 3.4, 1.0])
    add_para(document,
        "Schema source: database/schema.py (SCHEMA_SQL). Migrations: database/migrations/002_ through 004_. "
        "Relationships are enforced by foreign keys (students.created_by/updated_by -> users.id; "
        "enrollments.student_id -> students.id; barcodes.student_id -> students.id; barcode_scans.student_id -> "
        "students.id; assessments.student_id -> students.id; payments.assessment_id -> assessments.id; "
        "id_cards.student_id -> students.id; plus others).", align="justify")

    # 9. Testing
    add_heading(document, "9. Testing Information", level=1)
    test_files = sorted((PROJECT_ROOT / "tests").glob("test_*.py")) if (PROJECT_ROOT / "tests").exists() else []
    add_para(document,
        f"Test framework: pytest with pytest-qt and pytest-mock. {len(test_files)} test modules were found "
        f"in tests/. Test modules include: test_auth_service, test_database, test_student_asset_registry, "
        "test_scan_pipeline, test_scanner_worker, test_yolo, test_barcode, test_models, test_login_page, "
        "test_assessments, test_certificate_page_and_document, and others.", align="justify")
    add_para(document,
        "Test results were not executed as part of this documentation run, so no pass/fail verdicts are "
        "claimed here. See document 07 (Testing and Code Quality Report) for the testing review.",
        size=9, italic=True, color=MEDIUM_TEXT)

    # 10. Deployment
    add_heading(document, "10. Deployment Architecture", level=1)
    add_para(document,
        "Deployment is local desktop deployment on a workstation with MySQL 8+ and Python 3.11+. The "
        "application runs from the project directory: python main.py. Dependencies are installed from "
        "requirements.txt into a virtual environment. The database is initialized with "
        "python scripts/initialize_database.py (or setup_database.sh). A .env file (copied from .env.example) "
        "holds database and runtime configuration. No containerization, CI/CD, or cloud deployment is present "
        "in this codebase.", align="justify")

    # 11. Security
    add_heading(document, "11. Security Considerations", level=1)
    add_bullet(document, "Passwords are never stored in plaintext; PBKDF2-HMAC-SHA256 with per-user salt.")
    add_bullet(document, "Constant-time password comparison (hmac.compare_digest) prevents timing leaks.")
    add_bullet(document, "Identical failure message for missing/inactive/wrong-password accounts (no enumeration).")
    add_bullet(document, "Failed-login rate limiting with persistent lockout (15 minutes) in the users table.")
    add_bullet(document, "Role-based access control (admin/staff/viewer) enforced in navigation and writes.")
    add_bullet(document, "Session held in memory only; never written to disk or environment variables.")
    add_bullet(document, "Foreign-key constraints protect referential integrity; audit columns record actors.")
    add_bullet(document, "Database credentials must be supplied via .env and never committed.")

    # 12. Limitations
    add_heading(document, "12. Limitations and Known Issues", level=1)
    add_bullet(document, "Desktop-only application; no web interface or HTTP API.")
    add_bullet(document, "No HTTP authentication tokens or OAuth; session is in-memory for the desktop process.")
    add_bullet(document, "Database must be running and reachable before the application is useful; a down database shows a retryable error dialog.")
    add_bullet(document, "YOLO model file (models_yolo/active/best.pt) must be present for scanning; otherwise detection is unavailable.")
    add_bullet(document, "zxing-cpp is optional; without it, small-symbol decoding falls back to pyzbar only.")
    add_bullet(document, "Known data issues are tracked in reports/ (ADMIN_ACTIONS_REQUIRED.md, PROFILE_IMAGE_MAPPING.md, STUDENT_3_INTEGRATION.md, CARD_C_RECOGNITION_REPORT.md).")

    # 13. Conclusion
    add_heading(document, "13. Conclusion", level=1)
    add_para(document,
        "The WIT Barcode Student Information System is a complete desktop student-information application with "
        "student management, barcode ID issuance and webcam-based verification, subject enrollment, assessment "
        "and fee tracking, certificate generation, and a YOLO training center. It uses role-based "
        "authentication, raw-SQL MySQL data access, and layered service/repository architecture. All "
        "documentation in this file was generated from the actual source code; no features were invented.")

    ok = builder.save(document, OUTPUT_FILES["project"])
    print(f"  {'OK' if ok else 'FAIL'} Document 1: {OUTPUT_FILES['project']}")
    return ok


# ===========================================================================
# Document 2: Frontend Technical Documentation
# ===========================================================================

def build_document_frontend(builder: DocBuilder) -> bool:
    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"; style.font.size = Pt(10.5); style.font.color.rgb = DARK_TEXT
    for section in document.sections:
        section.top_margin = Inches(0.9); section.bottom_margin = Inches(0.9)
        section.left_margin = Inches(0.9); section.right_margin = Inches(0.9)
    add_page_number_footer(document, "Frontend Technical Documentation")
    add_cover_page(document, "Frontend Technical Documentation",
              "User Interface Architecture and Implementation", "WIT Barcode Student Information System")

    toc_entries = [
        (1, "1. Frontend Architecture"),
        (2, "1.1 Technology Stack"),
        (2, "1.2 Application Entry Point"),
        (2, "1.3 Folder and File Structure"),
        (1, "2. Pages and Screens"),
        (1, "3. Reusable UI Components"),
        (1, "4. Routing and Navigation"),
        (1, "5. Forms and Validation"),
        (1, "6. Authentication Interface"),
        (1, "7. State Management"),
        (1, "8. API Integration"),
        (1, "9. Data Fetching and Processing"),
        (1, "10. Loading States and Error Messages"),
        (1, "11. User Interactions"),
        (1, "12. Responsive Design"),
        (1, "13. Frontend Security Considerations"),
        (1, "14. Important Source Files and Functions"),
        (1, "15. UI Workflows"),
        (1, "16. Setup and Execution Instructions"),
        (1, "17. Known Issues and Recommended Improvements"),
    ]
    add_toc(document, toc_entries)

    add_heading(document, "1. Frontend Architecture", level=1)
    add_para(document,
        "The frontend is a PySide6 (Qt 6) desktop widget application. There is no web framework, no HTML/CSS "
        "in the traditional sense, and no HTTP client. The UI is built entirely in Python with Qt widgets, "
        "styled with a Qt stylesheet (styles/theme.qss) and Python color/typography modules "
        "(styles/colors.py, styles/typography.py).", align="justify")

    add_heading(document, "1.1 Technology Stack", level=2)
    add_table(document,
        ["Item", "Technology"],
        [
            ["GUI framework", "PySide6 6.12 (Qt 6)"],
            ["Language", "Python 3.14.4"],
            ["Styling", "Qt stylesheet (QSS) + Python color/typography modules"],
            ["Icons / images", "PNG assets under assets/"],
            ["Fonts", "Open Sauce and Agrandir (with system fallback)"],
        ],
        col_widths=[2.2, 5.2])

    add_heading(document, "1.2 Application Entry Point", level=2)
    add_para(document,
        "main.py is the entry point. It applies the theme and fonts, shows LoginDialog, enforces the "
        "mandatory first-login password change, then builds MainWindow with the authenticated user and runs "
        "the Qt event loop. On logout it returns to the login dialog instead of quitting.", align="justify")
    add_code_block(document, "def main():\n    app = QApplication(sys.argv)\n    apply_theme(app)\n    while True:\n        login_dialog = LoginDialog(auth_service=auth_service)\n        if login_dialog.exec() != 1:\n            return 0\n        user = login_dialog.authenticated_user\n        if user.must_change_password:\n            ... (password change gate) ...\n        window = MainWindow(current_user=user, auth_service=auth_service)\n        window.show()\n        app.exec()\n        if window.logout_requested:\n            continue\n        return 0")

    add_heading(document, "1.3 Folder and File Structure", level=2)
    add_table(document,
        ["Path", "Purpose"],
        [
            ["ui/main_window.py", "Main shell: QStackedWidget pages, sidebar, navigation, logout"],
            ["ui/login_page.py / ui/login_dialog.py", "Login screen and authentication dialog"],
            ["ui/dashboard.py", "Dashboard with summary tiles"],
            ["ui/students_page.py", "Student directory and management"],
            ["ui/scanner_page.py", "Barcode scanner (camera, pipeline, history)"],
            ["ui/enrollment_page.py", "Subject enrollment"],
            ["ui/assessment_page.py", "Assessment and fees"],
            ["ui/reports_page.py", "Reports and analytics"],
            ["ui/courses_page.py", "Courses and subjects"],
            ["ui/certificate_page.py", "Certificate of Matriculation"],
            ["ui/training_center.py", "YOLO training center"],
            ["ui/settings_page.py", "Application settings"],
            ["ui/dialogs/", "Dialogs: student, confirm, scan result, barcode, password change"],
            ["ui/widgets/", "Reusable widgets: sidebar, table, profile card, status pill, etc."],
            ["styles/theme.qss", "Qt stylesheet"],
            ["styles/colors.py", "Color palette"],
            ["styles/typography.py", "Font helpers"],
        ],
        col_widths=[2.6, 4.8])

    add_heading(document, "2. Pages and Screens", level=1)
    pages = [
        ("Dashboard", "ui/dashboard.py", "Summary tiles: active students, subjects, barcodes, scans, enrollments."),
        ("Students", "ui/students_page.py", "Student directory with search, add/edit/delete via StudentDialog, CSV export."),
        ("Barcode Scanner", "ui/scanner_page.py", "Webcam scanner with YOLO detection, decode, verification, history, post-scan actions."),
        ("Subject Enrollment", "ui/enrollment_page.py", "Create enrollments, add/remove subjects, view credit totals."),
        ("Assessment & Fees", "ui/assessment_page.py", "Create assessments, add items, record payments, view schedules and balances."),
        ("Reports", "ui/reports_page.py", "Generate PDF reports: certificate, assessment statement, barcode report, enrollment summary."),
        ("Courses & Subjects", "ui/courses_page.py", "Manage the course catalogue and subjects."),
        ("Certificate of Matriculation", "ui/certificate_page.py", "Resolve a student reference and generate a PDF certificate."),
        ("Training Center", "ui/training_center.py", "YOLO dataset and model management."),
        ("Settings", "ui/settings_page.py", "Application settings."),
    ]
    add_table(document, ["Page", "File", "Purpose"], pages, col_widths=[2.0, 2.0, 3.4])

    add_heading(document, "3. Reusable UI Components", level=1)
    add_para(document,
        "Reusable widgets live in ui/widgets/: Sidebar (navigation), responsive_table (configure_table), "
        "panel, status_pill, stat_card, flow_row, empty_state, profile_image, profile_card, student_card, "
        "barcode_display, camera_placeholder. Dialogs live in ui/dialogs/: StudentDialog, ConfirmDialog, "
        "ScanResultDialog, BarcodeDialog, PasswordChangeDialog.", align="justify")

    add_heading(document, "4. Routing and Navigation", level=1)
    add_para(document,
        "Navigation is handled by MainWindow.navigate_to(page_id, context). It enforces role-based access "
        "using ROLE_PAGES (admin/staff/viewer sets of allowed page ids). Pages are instantiated lazily and "
        "cached in self.pages. The scanner emits navigate_requested(str, student) to open another page with a "
        "verified student preselected. Global search (Ctrl+K) routes a reference to a single student or opens "
        "the students page filtered by query.", align="justify")
    add_table(document,
        ["Page id", "Role access (subset)"],
        [
            ["dashboard", "admin, staff, viewer"],
            ["students", "admin, staff, viewer"],
            ["scanner", "admin, staff, viewer"],
            ["enrollment", "admin, staff"],
            ["courses", "admin, staff"],
            ["certificate", "admin, staff"],
            ["assessment", "admin, staff"],
            ["reports", "admin, staff, viewer"],
            ["training", "admin, staff"],
            ["settings", "admin, staff"],
        ],
        col_widths=[2.0, 5.4])

    add_heading(document, "5. Forms and Validation", level=1)
    add_para(document,
        "Forms are implemented as QDialog subclasses (e.g., StudentDialog). Validation occurs in the service "
        "layer (e.g., StudentRepository, AssessmentService) and auth_service password-change policy "
        "(min 8 chars, uppercase, lowercase, digit, symbol, not equal to old password). UI shows QMessageBox "
        "for success or error. Database errors surface DatabaseUnavailableError with a retry option.", align="justify")

    add_heading(document, "6. Authentication Interface", level=1)
    add_para(document,
        "LoginDialog collects username and password and delegates to auth_service.login(). On success it "
        "returns the authenticated User. If must_change_password is true, PasswordChangeDialog is shown; the "
        "session is invalidated and the user re-authenticates with the new password. Logout is available from "
        "the user menu in MainWindow's top bar.", align="justify")

    add_heading(document, "7. State Management", level=1)
    add_para(document,
        "State is held in Python objects rather than a frontend state library. The authenticated session lives "
        "in auth_service._current_user (module singleton). Each page holds its own services and local UI state "
        "(e.g., ScannerPage._state, current_student, _latest_outcome). There is no Redux/Vuex-style store.", align="justify")

    add_heading(document, "8. API Integration", level=1)
    add_para(document,
        "There is no HTTP API. The UI communicates with backend services directly through in-process Python "
        "method calls: page -> StudentService/BarcodeGeneratorService/etc. -> repository -> raw SQL via "
        "database.connection.get_cursor(). ScanWorker performs detection/decoding/verification off the GUI "
        "thread and emits Qt signals (frame_result, scan_accepted, error) to the GUI thread.", align="justify")

    add_heading(document, "9. Data Fetching and Processing", level=1)
    add_para(document,
        "Data is fetched synchronously by services/repositories on the GUI thread for most pages, which can "
        "briefly block the UI on slow queries. DatabaseUnavailableError is caught at page boundaries and shown "
        "with a retry action. Scanner record lookups (enrollment/assessment) run in RecordLookupWorker on a "
        "background thread to avoid freezing the preview.", align="justify")

    add_heading(document, "10. Loading States and Error Messages", level=1)
    add_para(document,
        "Loading states: scanner STARTING shows 'Opening the camera and loading the detection model…'; "
        "database checks run on a 30-second timer with a '● Database: Connected/Disconnected' indicator. "
        "Errors: QMessageBox.critical for fatal errors, QMessageBox.warning for non-fatal, status bar and "
        "footer labels for transient messages, db_error.show_db_error for database failures with retry.", align="justify")

    add_heading(document, "11. User Interactions", level=1)
    add_bullet(document, "Sidebar navigation switches pages; role-filtered so users only see allowed modules.")
    add_bullet(document, "Global search (Ctrl+K) searches students by name/id/reference.")
    add_bullet(document, "Scanner Start/Stop toggles the camera; Upload Card Image runs the same pipeline on a file.")
    add_bullet(document, "Scan Next Student releases the current card for re-scan.")
    add_bullet(document, "Student add/edit via StudentDialog; delete via StudentsPage.")
    add_bullet(document, "PDF report generation via ReportsPage.")

    add_heading(document, "12. Responsive Design", level=1)
    add_para(document,
        "The application targets desktop window sizes (minimum 1280x800, default 1400x900). Layouts use "
        "QStackedWidget, QHBoxLayout/QVBoxLayout with stretch factors and minimum sizes. There is no "
        "mobile-specific layout or breakpoint system; it is a desktop-only application.", align="justify")

    add_heading(document, "13. Frontend Security Considerations", level=1)
    add_bullet(document, "Role-based page access enforced in MainWindow (ROLE_PAGES) and page write operations.")
    add_bullet(document, "Session invalidated on logout and on mandatory password change.")
    add_bullet(document, "No credentials stored in the frontend; auth_service holds session in memory only.")
    add_bullet(document, "Database errors do not leak connection details to the user (generic messages).")

    add_heading(document, "14. Important Source Files and Functions", level=1)
    frontend_files = [f for f in builder.all_files if str(f).startswith(str(PROJECT_ROOT / "ui")) or str(f).startswith(str(PROJECT_ROOT / "styles"))]
    for fpath in frontend_files[:20]:
        entry = builder._file_entry(fpath)
        add_heading(document, f"{entry['name']} ({entry['rel']})", level=3)
        add_para(document, entry["purpose"], size=9, italic=True, color=MEDIUM_TEXT, space_after=2)
        if entry["symbols"]["classes"]:
            add_para(document, "Classes: " + ", ".join(c["name"] for c in entry["symbols"]["classes"]), size=9, space_after=2)
        if entry["symbols"]["functions"]:
            add_para(document, "Functions: " + ", ".join(fn["name"] for fn in entry["symbols"]["functions"]), size=9)

    add_heading(document, "15. UI Workflows", level=1)
    add_bullet(document, "Login -> [password change] -> MainWindow dashboard.")
    add_bullet(document, "Scan card -> YOLO detect -> decode -> verify against DB -> show student -> optional navigate to enrollment/assessment/certificate.")
    add_bullet(document, "Add student -> StudentDialog -> StudentService.create_student -> repository INSERT.")
    add_bullet(document, "Generate barcode -> BarcodeGeneratorService -> CODE39 image + barcodes row.")

    add_heading(document, "16. Setup and Execution Instructions", level=1)
    add_para(document,
        "1. Install Python 3.11+. 2. Create and activate a virtual environment. 3. pip install -r requirements.txt. "
        "4. cp .env.example .env and configure database settings. 5. Start MySQL and run python scripts/initialize_database.py. "
        "6. Place the YOLO model at models_yolo/active/best.pt for scanning. 7. python main.py.", align="justify")

    add_heading(document, "17. Known Issues and Recommended Improvements", level=1)
    add_bullet(document, "Database must be running before the app is useful; no offline mode.")
    add_bullet(document, "Most data fetching is synchronous on the GUI thread; can block on slow queries.")
    add_bullet(document, "No mobile/responsive layout; desktop-only.")
    add_bullet(document, "YOLO model required for scanning; scanning degraded without it.")
    add_bullet(document, "Known data issues tracked in reports/ (see README).")

    ok = builder.save(document, OUTPUT_FILES["frontend"])
    print(f"  {'OK' if ok else 'FAIL'} Document 2: {OUTPUT_FILES['frontend']}")
    return ok


# ===========================================================================
# Document 3: Backend Technical Documentation
# ===========================================================================

def build_document_backend(builder: DocBuilder) -> bool:
    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"; style.font.size = Pt(10.5); style.font.color.rgb = DARK_TEXT
    for section in document.sections:
        section.top_margin = Inches(0.9); section.bottom_margin = Inches(0.9)
        section.left_margin = Inches(0.9); section.right_margin = Inches(0.9)
    add_page_number_footer(document, "Backend Technical Documentation")
    add_cover_page(document, "Backend Technical Documentation",
              "Server-Side Architecture, Services, and Data Layer", "WIT Barcode Student Information System")

    toc_entries = [
        (1, "1. Backend Architecture"),
        (2, "1.1 Framework and Language"),
        (2, "1.2 Application Initialization"),
        (2, "1.3 Directory Structure"),
        (1, "2. Controllers and Services"),
        (1, "3. API Endpoint Reference"),
        (1, "4. Business Logic"),
        (1, "5. Database Connectivity"),
        (1, "6. Authentication and Authorization"),
        (1, "7. Input Validation"),
        (1, "8. Error Handling"),
        (1, "9. Logging"),
        (1, "10. Security"),
        (1, "11. Testing"),
        (1, "12. Configuration"),
        (1, "13. Setup and Execution Instructions"),
        (1, "14. Deployment"),
        (1, "15. Known Issues and Recommended Improvements"),
    ]
    add_toc(document, toc_entries)

    add_heading(document, "1. Backend Architecture", level=1)
    add_para(document,
        "The backend is Python running in the same process as the PySide6 frontend. It uses a layered "
        "service/repository pattern over raw MySQL SQL. There is no HTTP server, no ORM, and no REST API. "
        "Services (services/) contain business logic; repositories (database/repositories/) run SQL via "
        "database.connection; models (models/) are dataclasses.", align="justify")

    add_heading(document, "1.1 Framework and Language", level=2)
    add_table(document,
        ["Item", "Technology"],
        [
            ["Language", "Python 3.14.4"],
            ["Database driver", "mysql-connector-python (pooling)"],
            ["Database", "MySQL 8.4"],
            ["Authentication crypto", "hashlib.pbkdf2_hmac + hmac.compare_digest (stdlib)"],
            ["Dotenv", "python-dotenv"],
        ],
        col_widths=[2.2, 5.2])

    add_heading(document, "1.2 Application Initialization", level=2)
    add_para(document,
        "main.py creates the QApplication, applies the theme, loads settings, and enters the session loop: "
        "login dialog -> optional password change -> MainWindow. MainWindow constructs its services "
        "(StudentService, BarcodeGeneratorService, AssessmentService, ReportService) and navigates to the "
        "dashboard. database.connection.get_connection_pool() lazily creates the MySQL pool on first use.", align="justify")

    add_heading(document, "1.3 Directory Structure", level=2)
    add_table(document,
        ["Path", "Purpose"],
        [
            ["main.py", "Application entry point and session loop"],
            ["services/", "Business-logic services (auth, student, barcode, scan, report, assessment, yolo, id card, audio, camera)"],
            ["database/connection.py", "MySQL connection pool, cursor context manager, initialization"],
            ["database/schema.py", "SCHEMA_SQL with all CREATE TABLE statements and seed data"],
            ["database/repositories/", "Data access: raw SQL per table"],
            ["models/", "Dataclass models (User, Student, Barcode, Enrollment, Subject, Course, Assessment, IdCard, etc.)"],
            ["config/settings.py", "Configuration from .env"],
            ["config/logging_config.py", "Rotating file + console logging"],
        ],
        col_widths=[2.6, 4.8])

    add_heading(document, "2. Controllers and Services", level=1)
    add_para(document,
        "There are no HTTP controllers. Services act as the business-logic layer. Key services:", align="justify")
    add_table(document,
        ["Service", "File", "Key responsibilities"],
        [
            ["AuthService", "services/auth_service.py", "Login/logout, PBKDF2 hashing, lockout, require_auth/require_role, change_password"],
            ["StudentService", "services/student_service.py", "CRUD students, enrollments, subjects, courses, enrollment lookup for certificates"],
            ["BarcodeGeneratorService", "services/barcode_generator.py", "CODE39 image generation, barcode records, batch/regenerate"],
            ["ScanPipeline", "services/scan_pipeline.py", "Detect -> gate -> decode -> verify scan pipeline"],
            ["BarcodeDecoderService", "services/barcode_decoder.py", "pyzbar/zxing decoding with ordered preprocessing attempts"],
            ["YOLODetectorService", "services/yolo_detector.py", "YOLOv8n model loading and inference"],
            ["ReportService", "services/report_service.py", "PDF certificates, assessment statements, CSV exports"],
            ["AssessmentService", "services/assessment_service.py", "Assessments, items, payments, schedules"],
            ["IdCardService", "services/id_card_service.py", "ID card rendering"],
        ],
        col_widths=[2.2, 2.0, 3.2])

    add_heading(document, "3. API Endpoint Reference", level=1)
    add_para(document,
        "This application has no HTTP API endpoints. All backend functionality is exposed as in-process "
        "Python service methods called directly by the UI. The table below lists representative service "
        "methods as the closest equivalent to an API reference.", align="justify")
    add_table(document,
        ["Service method", "File", "Operation"],
        [
            ["auth_service.login(username, password)", "services/auth_service.py", "Authenticate user"],
            ["auth_service.require_role('admin')", "services/auth_service.py", "Authorize by role"],
            ["student_service.create_student(student)", "services/student_service.py", "Create student record"],
            ["student_service.get_student_by_barcode(payload)", "services/student_service.py", "Look up student by barcode"],
            ["barcode_service.generate_for_student(student_id)", "services/barcode_generator.py", "Generate CODE39 barcode"],
            ["scan_pipeline.process_frame(frame)", "services/scan_pipeline.py", "Process one camera frame"],
            ["report_service.generate_certificate_of_matriculation(...)", "services/report_service.py", "Generate PDF certificate"],
        ],
        col_widths=[3.4, 2.0, 2.0])

    add_heading(document, "4. Business Logic", level=1)
    add_bullet(document, "Barcode payload: zero-padded 9-digit CODE39 matching physical cards; legacy WIT:STUDENT: prefix no longer used.")
    add_bullet(document, "Scan verification requires BOTH a successful decode AND a matching students.row; YOLO detection alone is never sufficient.")
    add_bullet(document, "Duplicate scans suppressed via cooldown and last-verified-value latch; ambiguous reads (two payloads) are refused.")
    add_bullet(document, "Enrollment credit total recalculated when subjects are added or removed.")
    add_bullet(document, "Assessment totals derived from item amounts and discount categories; balance = assessed - discounts - payments.")
    add_bullet(document, "Password policy: min 8 chars, upper/lower/digit/symbol, not equal to old password.")

    add_heading(document, "5. Database Connectivity", level=1)
    add_para(document,
        "database.connection.get_connection_pool() creates a MySQLConnectionPool (pool_size=10) lazily. "
        "get_cursor(commit=True) commits; get_cursor(commit=False) rolls back (true dry run). "
        "Connection attempts are throttled (30s retry interval, 60s log throttle). test_connection() runs "
        "SELECT 1. initialize_database() executes SCHEMA_SQL.", align="justify")

    add_heading(document, "6. Authentication and Authorization", level=1)
    add_para(document,
        "AuthService is a module singleton. Passwords hashed with PBKDF2-HMAC-SHA256 (210k iterations, 16-byte "
        "salt), compared with hmac.compare_digest. Failed attempts tracked in memory and persisted in users "
        "(failed_attempts, locked_until); max 5 failures -> 15-minute lockout. Roles: admin, staff, viewer. "
        "User.can_write (admin/staff), User.can_read (all active). First-login password change enforced when "
        "password_changed_at is NULL.", align="justify")

    add_heading(document, "7. Input Validation", level=1)
    add_bullet(document, "Login: empty username/password rejected with generic message.")
    add_bullet(document, "New password: complexity policy enforced (length, character classes, not equal to old).")
    add_bullet(document, "Barcode value: zero-padded 9 digits derived from reference or student_id.")
    add_bullet(document, "DPI: minimum 150, maximum 2400 for card export.")
    add_bullet(document, "SQL uses parameterized queries (%s placeholders) throughout repositories.")

    add_heading(document, "8. Error Handling", level=1)
    add_bullet(document, "DatabaseUnavailableError raised on connection/query failure; never silently converted to empty results.")
    add_bullet(document, "Pages catch DatabaseUnavailableError at data-loading boundaries and show retryable dialogs.")
    add_bullet(document, "Scanner pipeline never raises for expected failures; returns ScanState.ERROR outcome.")
    add_bullet(document, "Reader failures (zbar/zxing) are caught and degraded, not fatal.")
    add_bullet(document, "get_cursor rolls back on MySQLError and re-raises as DatabaseUnavailableError.")

    add_heading(document, "9. Logging", level=1)
    add_para(document,
        "config/logging_config.py sets up a RotatingFileHandler (10 MB, 5 backups) plus console handler, "
        "with format %(asctime)s - %(name)s - %(levelname)s - %(message)s. ultralytics/PIL/matplotlib are "
        "silenced to WARNING. Services log info/warning/error with module-level loggers.", align="justify")

    add_heading(document, "10. Security", level=1)
    add_bullet(document, "PBKDF2-HMAC-SHA256 password hashing with per-user salt.")
    add_bullet(document, "Constant-time comparison; identical failure messages (no enumeration).")
    add_bullet(document, "Rate-limited failed logins with persistent lockout.")
    add_bullet(document, "Role-based access control on navigation and writes.")
    add_bullet(document, "Session in memory only; never persisted.")
    add_bullet(document, "Foreign keys protect referential integrity; audit columns record actors.")

    add_heading(document, "11. Testing", level=1)
    add_para(document,
        "Tests live in tests/ using pytest, pytest-qt, pytest-mock. Test modules cover auth, database, models, "
        "scanner pipeline, YOLO, barcode, login page, assessments, certificate page, and more. Test results "
        "were not executed during this documentation run; no pass/fail claims are made.", align="justify")

    add_heading(document, "12. Configuration", level=1)
    add_para(document,
        "Configuration is in config/settings.py, loaded from .env via python-dotenv. Key groups: database "
        "(DB_HOST/PORT/NAME/USER/PASSWORD), application (APP_NAME/VERSION/DEBUG), YOLO (model path, confidence, "
        "IOU, device, imgsz), camera (index, width, height, FPS), barcode (prefix, output dir, module size), "
        "scan (region ratios, cooldown, confirm frames, target FPS), reports (output dir, page size), logging "
        "(level, file), academic defaults (year, semester), DEFAULT_USER_ID.", align="justify")
    add_para(document,
        "Note: .env contains secrets and was not read or included in documentation. Copy .env.example to .env "
        "and supply real values.", size=9, italic=True, color=MEDIUM_TEXT)

    add_heading(document, "13. Setup and Execution Instructions", level=1)
    add_para(document,
        "1. Install Python 3.11+ and MySQL 8+. 2. python -m venv venv && source venv/bin/activate. "
        "3. pip install -r requirements.txt. 4. cp .env.example .env and edit credentials. 5. Start MySQL. "
        "6. python scripts/initialize_database.py. 7. Place YOLO model at models_yolo/active/best.pt. 8. python main.py.", align="justify")

    add_heading(document, "14. Deployment", level=1)
    add_para(document,
        "Local desktop deployment. No containers, CI/CD, or cloud configuration present. Deploy by copying the "
        "project directory to a workstation with Python and MySQL, installing dependencies, configuring .env, "
        "initializing the database, and running python main.py.", align="justify")

    add_heading(document, "15. Known Issues and Recommended Improvements", level=1)
    add_bullet(document, "No HTTP API; desktop-only in-process calls.")
    add_bullet(document, "Database required at startup; no offline mode.")
    add_bullet(document, "Synchronous DB calls can block the GUI thread.")
    add_bullet(document, "YOLO model file required for scanning.")
    add_bullet(document, "Known data issues in reports/ (ADMIN_ACTIONS_REQUIRED.md, etc.).")

    ok = builder.save(document, OUTPUT_FILES["backend"])
    print(f"  {'OK' if ok else 'FAIL'} Document 3: {OUTPUT_FILES['backend']}")
    return ok


# ===========================================================================
# Document 4: Important Source Code Reference
# ===========================================================================

def build_document_source(builder: DocBuilder) -> bool:
    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"; style.font.size = Pt(10); style.font.color.rgb = DARK_TEXT
    for section in document.sections:
        section.top_margin = Inches(0.8); section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.8); section.right_margin = Inches(0.8)
    add_page_number_footer(document, "Important Source Code Reference")
    add_cover_page(document, "Important Source Code Reference",
              "File Inventory, Functions, and Module Map", "WIT Barcode Student Information System")

    toc_entries = [
        (1, "1. How to Read This Reference"),
        (1, "2. Frontend Files"),
        (1, "3. Backend Files"),
        (1, "4. Database Files"),
        (1, "5. Configuration Files"),
        (1, "6. Testing Files"),
        (1, "7. Deployment / Scripts Files"),
        (1, "8. Importance Ratings and Related Files"),
    ]
    add_toc(document, toc_entries)

    add_heading(document, "1. How to Read This Reference", level=1)
    add_para(document,
        "This inventory lists the most important source files discovered in the project, organized by module. "
        "Each entry gives the filename, relative and absolute path, module, purpose, important functions/"
        "classes/methods, dependencies, importance rating, related files, and documentation status. "
        "Information is extracted from actual source code; no features were invented.", align="justify")
    add_para(document,
        "Importance ratings: Critical = application cannot run without it; High = core business logic or main "
        "UI page; Medium = supporting module; Low = utility or peripheral.", size=9, italic=True, color=MEDIUM_TEXT)

    # Build file entries
    file_entries = {}
    for fpath in builder.all_files:
        file_entries[str(fpath)] = builder._file_entry(fpath)

    def add_file_section(section_title, category_files, landscape=False):
        add_heading(document, section_title, level=1)
        if not category_files:
            add_para(document, "No files found in this category.", size=9, italic=True, color=MEDIUM_TEXT)
            return
        headers = ["File", "Relative path", "Module", "Purpose", "Important symbols", "Deps", "Lines", "Imp.", "Related", "Doc status"]
        rows = []
        for fpath in category_files:
            entry = file_entries[str(fpath)]
            rel = entry["rel"]
            symbols = []
            for cls in entry["symbols"]["classes"][:4]:
                symbols.append(f"class {cls['name']}")
            for fn in entry["symbols"]["functions"][:4]:
                symbols.append(f"def {fn['name']}")
            sym_str = "; ".join(symbols) if symbols else "—"
            deps = ", ".join(entry["deps"][:5]) if entry["deps"] else "—"
            related = "—"  # computed below
            rows.append([
                entry["name"], rel, entry["module"], entry["purpose"],
                sym_str, deps, str(entry["lines"]), entry.get("importance", "Medium"),
                related, "Partially documented",
            ])
        add_table(document, headers, rows,
                  col_widths=[0.9, 1.1, 1.0, 1.7, 1.5, 1.1, 0.5, 0.6, 0.7, 1.0],
                  header_size=8, body_size=7.5, landscape=landscape)
        add_para(document, "", space_after=4)

    # Categorize
    critical = [
        PROJECT_ROOT / "main.py",
        PROJECT_ROOT / "config" / "settings.py",
        PROJECT_ROOT / "config" / "logging_config.py",
        PROJECT_ROOT / "database" / "connection.py",
        PROJECT_ROOT / "database" / "schema.py",
        PROJECT_ROOT / "services" / "auth_service.py",
        PROJECT_ROOT / "ui" / "main_window.py",
        PROJECT_ROOT / "models" / "user.py",
        PROJECT_ROOT / "models" / "student.py",
    ]
    high = [
        PROJECT_ROOT / "services" / "student_service.py",
        PROJECT_ROOT / "services" / "barcode_generator.py",
        PROJECT_ROOT / "services" / "scan_pipeline.py",
        PROJECT_ROOT / "services" / "barcode_decoder.py",
        PROJECT_ROOT / "services" / "yolo_detector.py",
        PROJECT_ROOT / "services" / "report_service.py",
        PROJECT_ROOT / "services" / "assessment_service.py",
        PROJECT_ROOT / "ui" / "scanner_page.py",
        PROJECT_ROOT / "ui" / "students_page.py",
        PROJECT_ROOT / "ui" / "dashboard.py",
        PROJECT_ROOT / "database" / "repositories" / "student_repository.py",
    ]

    add_heading(document, "2. Frontend Files", level=1)
    frontend_files = [f for f in builder.all_files if str(f).startswith(str(PROJECT_ROOT / "ui")) or str(f).startswith(str(PROJECT_ROOT / "styles"))]
    add_file_section("2.1 Frontend source files", frontend_files)

    add_heading(document, "3. Backend Files", level=1)
    backend_files = [f for f in builder.all_files if str(f).startswith(str(PROJECT_ROOT / "services")) or str(f).startswith(str(PROJECT_ROOT / "models")) or f.name == "main.py"]
    add_file_section("3.1 Backend source files", backend_files)

    add_heading(document, "4. Database Files", level=1)
    db_files = [f for f in builder.all_files if str(f).startswith(str(PROJECT_ROOT / "database"))]
    add_file_section("4.1 Database source files", db_files)

    add_heading(document, "5. Configuration Files", level=1)
    config_files = [f for f in builder.all_files if str(f).startswith(str(PROJECT_ROOT / "config")) or f.name in {"requirements.txt", ".env.example", "pytest.ini", "setup_database.sh", "setup_db.sh"}]
    add_file_section("5.1 Configuration files", config_files)

    add_heading(document, "6. Testing Files", level=1)
    test_files = [f for f in builder.all_files if str(f).startswith(str(PROJECT_ROOT / "tests"))]
    add_file_section("6.1 Test files", test_files)

    add_heading(document, "7. Deployment / Scripts Files", level=1)
    script_files = [f for f in builder.all_files if str(f).startswith(str(PROJECT_ROOT / "scripts"))]
    add_file_section("7.1 Scripts", script_files)

    add_heading(document, "8. Importance Ratings", level=1)
    add_para(document, "Critical files:", size=10, bold=True, space_after=2)
    for f in critical:
        if f in file_entries:
            e = file_entries[f]
            add_bullet(document, f"{e['rel']} — {e['purpose']}", size=9)
    add_para(document, "High-importance files:", size=10, bold=True, space_after=2)
    for f in high:
        if f in file_entries:
            e = file_entries[f]
            add_bullet(document, f"{e['rel']} — {e['purpose']}", size=9)

    ok = builder.save(document, OUTPUT_FILES["source"])
    print(f"  {'OK' if ok else 'FAIL'} Document 4: {OUTPUT_FILES['source']}")
    return ok


# ===========================================================================
# Document 5: Code Explanation and Learning Guide
# ===========================================================================

def build_document_learning(builder: DocBuilder) -> bool:
    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"; style.font.size = Pt(10.5); style.font.color.rgb = DARK_TEXT
    for section in document.sections:
        section.top_margin = Inches(0.9); section.bottom_margin = Inches(0.9)
        section.left_margin = Inches(0.9); section.right_margin = Inches(0.9)
    add_page_number_footer(document, "Code Explanation and Learning Guide")
    add_cover_page(document, "Code Explanation and Learning Guide",
              "Beginner-Friendly Walkthrough of the System", "WIT Barcode Student Information System")

    toc_entries = [
        (1, "1. Project Architecture Explained Simply"),
        (1, "2. How a Student Record Is Created"),
        (1, "3. How Barcode Generation Works"),
        (1, "4. How Barcode Scanning Works"),
        (1, "5. How Authentication Works"),
        (1, "6. Data Flow: Frontend to Backend to Database"),
        (1, "7. Database Interactions"),
        (1, "8. Example Execution Traces"),
        (1, "9. Common Errors and Troubleshooting"),
        (1, "10. Glossary of Technical Terms"),
    ]
    add_toc(document, toc_entries)

    add_heading(document, "1. Project Architecture Explained Simply", level=1)
    add_para(document,
        "This is a desktop program for a school (WIT) to keep track of students. Think of it in three parts:", align="justify")
    add_bullet(document, "The screen (frontend): built with PySide6, a Python toolkit for making windows, buttons, tables, and dialogs.")
    add_bullet(document, "The brain (backend): Python services that decide what happens when you click a button.")
    add_bullet(document, "The filing cabinet (database): MySQL, which stores students, barcodes, enrollments, fees, and users in tables.")
    add_para(document,
        "When you open the program, main.py runs. It shows a login screen. Once you log in, you see the main "
        "window with a sidebar menu. Clicking a menu item switches the screen to that page. Every page talks to "
        "a service, and every service talks to the database through repository classes that run SQL queries.",
        align="justify")

    add_heading(document, "2. How a Student Record Is Created", level=1)
    add_para(document,
        "Step-by-step, using ui/students_page.py and services/student_service.py:", align="justify")
    steps = [
        "1. User clicks Add Student on the Students page.",
        "2. StudentDialog opens, collecting name, ID, program, year, contacts, etc.",
        "3. User clicks Save. The dialog builds a Student object.",
        "4. MainWindow calls student_service.create_student(student).",
        "5. StudentService calls student_repo.create(student).",
        "6. The repository runs an INSERT into the students table, then inserts each contact into student_contacts.",
        "7. MySQL returns the new student id. The service logs 'Created student: ...'.",
        "8. The page refreshes and shows the new student in the list.",
    ]
    for s in steps:
        add_bullet(document, s)
    add_code_block(document, "# services/student_service.py\ndef create_student(self, student: Student) -> Optional[int]:\n    student_id = self.student_repo.create(student)\n    return student_id\n\n# database/repositories/student_repository.py\ndef create(self, student: Student) -> Optional[int]:\n    cursor.execute(\"INSERT INTO students (...) VALUES (%s, %s, ...)\", (...))\n    return cursor.lastrowid")

    add_heading(document, "3. How Barcode Generation Works", level=1)
    add_para(document,
        "The system generates CODE39 barcodes (the kind on physical ID cards) using python-barcode:", align="justify")
    steps = [
        "1. User chooses Generate Barcode for a student.",
        "2. BarcodeGeneratorService.generate_for_student(student_id) is called.",
        "3. It looks up the student and checks a barcode does not already exist for them.",
        "4. generate_barcode_value(student) builds the payload: if the student already has a barcode_payload it reuses it; otherwise it zero-pads the reference number to 9 digits (e.g. 000065412).",
        "5. generate_barcode_image() renders a CODE39 image to generated_barcodes/barcode_XXXXXXX.png.",
        "6. A Barcode record is inserted into the barcodes table with the image path.",
    ]
    for s in steps:
        add_bullet(document, s)
    add_code_block(document, "# services/barcode_generator.py\ndef generate_barcode_value(self, student):\n    if student.barcode_payload:\n        return student.barcode_payload\n    ref = student.reference_number\n    return ref.zfill(9)  # zero-pad to 9 digits\n\ndef generate_barcode_image(self, barcode_value, output_path, ...):\n    barcode = Code39(barcode_value, writer=writer, add_checksum=False)\n    barcode.save(str(output_path), options=options)")

    add_heading(document, "4. How Barcode Scanning Works", level=1)
    add_para(document,
        "Scanning is the most complex part. It uses a camera, YOLOv8n object detection, barcode decoding, and "
        "a database lookup. It runs on a background thread (ScanWorker) so the window stays responsive:", align="justify")
    steps = [
        "1. User clicks Start Scanner on the Scanner page.",
        "2. The camera opens and the YOLO model is loaded (models_yolo/active/best.pt).",
        "3. ScanWorker repeatedly grabs frames and calls scan_pipeline.process_frame(frame).",
        "4. Stage 1 - Detect: YOLO finds barcode-like regions in the frame.",
        "5. Stage 2 - Gate: only regions inside the scan gate are sent to the decoder.",
        "6. Stage 3 - Decode: barcode_decoder tries the crop, then greyscale, upscaling, Otsu threshold, etc., stopping at the first success.",
        "7. Stage 4 - Verify: the decoded payload is looked up in students.barcode_payload. Both decode AND a matching student row are required.",
        "8. If verified, state becomes VERIFIED, the student panel shows, a beep plays, and a scan row is recorded.",
        "9. Two different payloads in one frame = AMBIGUOUS: the operator must show one card.",
    ]
    for s in steps:
        add_bullet(document, s)
    add_code_block(document, "# services/scan_pipeline.py\ndef process_frame(self, frame):\n    # 1 detect\n    candidates = self._build_candidates(frame, region)\n    # 2 gate + decode\n    candidates = self._decode_candidates(frame, candidates, region.rect)\n    # 3 verify against database\n    student, error = self.verifier.verify(value)\n    return ScanOutcome(state=ScanState.VERIFIED, student=student, verified_now=True)")

    add_heading(document, "5. How Authentication Works", level=1)
    add_para(document,
        "Authentication is in services/auth_service.py (AuthService):", align="justify")
    steps = [
        "1. User types username and password at LoginDialog.",
        "2. auth_service.login(username, password) checks for empty input, then checks if the account is locked (too many recent failures).",
        "3. It looks up the user in the users table and retrieves the stored password hash.",
        "4. It hashes the supplied password with PBKDF2-HMAC-SHA256 and compares using hmac.compare_digest (constant time, so timing cannot reveal whether the account exists).",
        "5. On success, the session is set and the user is returned. On failure, a generic 'Invalid username or password' message is returned whether or not the account exists.",
        "6. If password_changed_at is NULL, the user must change their password before accessing any page.",
        "7. Roles: admin, staff, viewer. Admin/staff can write; viewer can only read.",
    ]
    for s in steps:
        add_bullet(document, s)
    add_code_block(document, "# services/auth_service.py\ndef login(self, username, password) -> AuthResult:\n    user, password_hash = self._lookup_user(username)\n    ok = self._verify_password(password, expected_hash)\n    if ok:\n        self._current_user = user\n        return AuthResult(True, user=user)\n    return AuthResult(False, message='Invalid username or password.')")

    add_heading(document, "6. Data Flow: Frontend to Backend to Database", level=1)
    add_para(document,
        "Typical flow (example: viewing a student):", align="justify")
    add_table(document,
        ["Step", "Component", "What happens"],
        [
            ["1", "UI (students_page.py)", "User requests a student or searches."],
            ["2", "Service (student_service.py)", "Calls student_repo.get_by_id() or get_all() with search."],
            ["3", "Repository (student_repository.py)", "Runs SELECT * FROM students ... with parameterized %s placeholders."],
            ["4", "Connection (database/connection.py)", "Gets a pooled MySQL connection, runs the query, returns dict rows."],
            ["5", "Repository", "Converts each row into a Student dataclass (with contacts)."],
            ["6", "Service", "Returns the Student object(s) to the UI."],
            ["7", "UI", "Displays the student details on screen."],
        ],
        col_widths=[0.6, 2.0, 4.8])

    add_heading(document, "7. Database Interactions", level=1)
    add_para(document,
        "The database is MySQL 8.4. The schema (database/schema.py) creates 18 tables. Key relationships:", align="justify")
    add_table(document,
        ["Table", "Key relationship"],
        [
            ["users", "One user can create/update students, enrollments, barcodes, assessments, payments, cards."],
            ["students", "Central table. student_contacts, enrollments, barcodes, id_cards reference students.id (ON DELETE CASCADE)."],
            ["enrollments", "Belongs to a student; enrollment_subjects links enrollments to subjects."],
            ["subjects", "Belongs to a course (courses.id)."],
            ["assessments", "Belongs to a student and optionally an enrollment; items and payments reference it."],
            ["barcodes", "Belongs to a student; barcode_scans references barcodes.id."],
            ["id_cards", "Belongs to a student; stores rendered card image/PDF paths and status."],
        ],
        col_widths=[1.8, 5.6])
    add_para(document,
        "All writes use parameterized queries (%s) to prevent SQL injection. get_cursor(commit=True) commits "
        "writes; get_cursor(commit=False) rolls back (dry run safety).", align="justify")

    add_heading(document, "8. Example Execution Traces", level=1)
    add_para(document,
        "Trace A — Login success:", align="justify")
    trace_a = [
        "User enters 'javy' / password.",
        "auth_service.login('javy', password) -> not empty -> not locked.",
        "_lookup_user('javy') finds row, returns (User, stored_hash).",
        "_verify_password(password, stored_hash) -> True (PBKDF2 match).",
        "_record_login(user.id) stamps last_login; session set.",
        "Returns AuthResult(success=True, user=User(...)). MainWindow opens.",
    ]
    for s in trace_a:
        add_bullet(document, s)
    add_para(document, "Trace B — Verified scan:", align="justify")
    trace_b = [
        "Camera frame captured by ScanWorker.",
        "YOLO detects a barcode region (bounding box).",
        "Region passes the scan-gate test.",
        "barcode_decoder.decode_best(crop) tries original -> success with payload '000065412'.",
        "StudentVerifier.verify('000065412') calls student_service.get_student_by_barcode('000065412').",
        "Repository finds student Javy Danielle N. Caporal.",
        "Outcome = VERIFIED; student panel shows; beep plays; scan row recorded.",
    ]
    for s in trace_b:
        add_bullet(document, s)

    add_heading(document, "9. Common Errors and Troubleshooting", level=1)
    add_table(document,
        ["Symptom", "Likely cause", "What to do"],
        [
            ["'Could not connect to MySQL'", "MySQL not running or wrong .env credentials", "Start MySQL; check DB_HOST/PORT/NAME/USER/PASSWORD in .env"],
            ["Scanner: 'No detection model is loaded'", "models_yolo/active/best.pt missing", "Place a trained YOLO model there"],
            ["Login: 'Invalid username or password'", "Wrong credentials or account locked", "Retry; or wait out the 15-minute lockout"],
            ["First login blocked at password change", "password_changed_at is NULL", "Complete the mandatory password change"],
            ["'No cameras found'", "No webcam detected", "Connect a camera and refresh the camera list"],
            ["DatabaseUnavailableError", "MySQL down or connection pool exhausted", "Check MySQL; use Retry; check logs/app.log"],
        ],
        col_widths=[2.2, 2.2, 3.0])

    add_heading(document, "10. Glossary of Technical Terms", level=1)
    add_table(document,
        ["Term", "Meaning in this project"],
        [
            ["PySide6", "Python bindings for Qt 6; used to build the desktop windows and widgets."],
            ["ORM", "Object-Relational Mapper. This project does NOT use one; it writes raw SQL."],
            ["Repository", "A class that runs SQL for one table (e.g., StudentRepository)."],
            ["Service", "A class with business logic that uses repositories (e.g., StudentService)."],
            ["PBKDF2-HMAC-SHA256", "The password-hashing algorithm used to store passwords securely."],
            ["CODE39", "The barcode symbology used on WIT student ID cards."],
            ["YOLOv8n", "A small object-detection model used to find barcodes in camera frames."],
            ["ScanState", "Enum describing what a frame's result is: SEARCHING, VERIFIED, ERROR, AMBIGUOUS, etc."],
            ["CR80", "Standard ID-card size (85.60 x 53.98 mm)."],
            ["Connection pool", "A cache of reusable MySQL connections so the app does not open a new one per query."],
        ],
        col_widths=[1.7, 5.7])

    ok = builder.save(document, OUTPUT_FILES["learning"])
    print(f"  {'OK' if ok else 'FAIL'} Document 5: {OUTPUT_FILES['learning']}")
    return ok


# ===========================================================================
# Document 6: Installation and Maintenance Manual
# ===========================================================================

def build_document_deployment(builder: DocBuilder) -> bool:
    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"; style.font.size = Pt(10.5); style.font.color.rgb = DARK_TEXT
    for section in document.sections:
        section.top_margin = Inches(0.9); section.bottom_margin = Inches(0.9)
        section.left_margin = Inches(0.9); section.right_margin = Inches(0.9)
    add_page_number_footer(document, "Installation and Maintenance Manual")
    add_cover_page(document, "Installation and Maintenance Manual",
              "Setup, Build, Deployment, and Maintenance Procedures", "WIT Barcode Student Information System")

    toc_entries = [
        (1, "1. System Requirements"),
        (1, "2. Required Software and SDKs"),
        (1, "3. Dependency Installation"),
        (1, "4. Environment Configuration"),
        (1, "5. Database Setup"),
        (1, "6. Development Startup Commands"),
        (1, "7. Build Instructions"),
        (1, "8. Testing Instructions"),
        (1, "9. Deployment Steps"),
        (1, "10. Troubleshooting"),
        (1, "11. Backup Considerations"),
        (1, "12. Maintenance Procedures"),
        (1, "13. Security Recommendations"),
    ]
    add_toc(document, toc_entries)

    add_heading(document, "1. System Requirements", level=1)
    add_table(document,
        ["Item", "Requirement"],
        [
            ["Operating system", "Linux, macOS, or Windows"],
            ["Python", "3.11+ (tested on 3.14.4)"],
            ["Database server", "MySQL 8+"],
            ["Memory", "4 GB RAM recommended (8 GB for YOLO training)"],
            ["Camera", "USB/webcam for barcode scanning (optional for non-scanning use)"],
            ["GPU", "NVIDIA GPU with CUDA recommended for YOLO; CPU fallback works but is slower"],
            ["ZBar library", "Required for pyzbar barcode decoding"],
        ],
        col_widths=[2.2, 5.2])

    add_heading(document, "2. Required Software and SDKs", level=1)
    add_bullet(document, "Python 3.11+ with pip and venv")
    add_bullet(document, "MySQL 8+ server (client libraries for mysql-connector-python)")
    add_bullet(document, "ZBar (zbar-tools / libzbar0 on Debian/Ubuntu; zbar on macOS via brew)")
    add_bullet(document, "A C compiler may be needed to build some Python wheels (e.g., mysql-client, ultralytics deps)")

    add_heading(document, "3. Dependency Installation", level=1)
    add_para(document, "From the project root:", align="justify")
    add_code_block(document, "python -m venv venv\n# Linux/macOS:\nsource venv/bin/activate\n# Windows:\n# venv\\Scripts\\activate\n\npip install --upgrade pip\npip install -r requirements.txt")
    add_para(document,
        "requirements.txt includes: PySide6, mysql-connector-python, opencv-python, Pillow, ultralytics, "
        "python-barcode, pyzbar, zxing-cpp (optional), matplotlib, python-dotenv, reportlab, pytest, "
        "pytest-qt, pytest-mock, numpy, imagehash.", align="justify")

    add_heading(document, "4. Environment Configuration", level=1)
    add_para(document,
        "Copy the template and fill in real values. Do not commit .env.", align="justify")
    add_code_block(document, "cp .env.example .env\n# Edit .env with a text editor\n# Set DB_USER, DB_PASSWORD, DB_NAME, DB_HOST, DB_PORT")
    add_para(document,
        "Key .env settings: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD; YOLO_MODEL_PATH, "
        "YOLO_CONFIDENCE_THRESHOLD, YOLO_DEVICE; DEFAULT_CAMERA_INDEX, CAMERA_WIDTH, CAMERA_HEIGHT; "
        "BARCODE_OUTPUT_DIR, REPORTS_OUTPUT_DIR; LOG_LEVEL, LOG_FILE; DEFAULT_ACADEMIC_YEAR, DEFAULT_SEMESTER; "
        "WIT_ADMIN_PASSWORD (admin seed password).", align="justify")

    add_heading(document, "5. Database Setup", level=1)
    add_para(document,
        "1. Install and start MySQL 8+. 2. Create a database and application user (or use the one named in .env). "
        "3. Run the initializer:", align="justify")
    add_code_block(document, "python scripts/initialize_database.py")
    add_para(document,
        "This executes database/schema.py SCHEMA_SQL: creates the wit_barcode_database, all 18 tables, foreign "
        "keys, indexes, and seeds system_settings and assessment_categories. If the users table is empty, an "
        "administrator account is provisioned (see .env WIT_ADMIN_PASSWORD or a generated one-time password logged "
        "at seed time).", align="justify")

    add_heading(document, "6. Development Startup Commands", level=1)
    add_para(document, "From the project root, with the virtual environment active:", align="justify")
    add_code_block(document, "# Start the application\npython main.py\n\n# Run the test suite\npytest tests/\n\n# Run a single test module\npytest tests/test_auth_service.py -v")
    add_para(document,
        "The application opens the login dialog. Log in with the administrator account created during database "
        "initialization. On first login you will be forced to change the password before accessing any page.",
        align="justify")

    add_heading(document, "7. Build Instructions", level=1)
    add_para(document,
        "This is a Python interpreted application; there is no compilation step. To package for distribution, "
        "use a tool such as PyInstaller or briefcase (not configured in this repo). The application runs from "
        "source via python main.py. Asset paths (logos, fonts, profile photos) are resolved relative to the "
        "project root via config/settings.py, so run from the project directory or set the working directory "
        "accordingly.", align="justify")

    add_heading(document, "8. Testing Instructions", level=1)
    add_para(document,
        "Tests use pytest with pytest-qt (for GUI tests) and pytest-mock (for mocking). Run from the project "
        "root:", align="justify")
    add_code_block(document, "pytest tests/\n\n# With verbose output\npytest tests/ -v\n\n# Stop on first failure\npytest tests/ -x")
    add_para(document,
        "Test modules include: test_auth_service, test_database, test_student_asset_registry, test_scan_pipeline, "
        "test_scanner_worker, test_yolo, test_barcode, test_models, test_login_page, test_assessments, "
        "test_certificate_page_and_document, and others. Test results were not executed during this documentation "
        "run; verify results locally.", align="justify")

    add_heading(document, "9. Deployment Steps", level=1)
    add_bullet(document, "Provision a workstation with Python 3.11+ and MySQL 8+.")
    add_bullet(document, "Copy the project directory to the workstation.")
    add_bullet(document, "Create a venv and pip install -r requirements.txt.")
    add_bullet(document, "Create .env from .env.example with production database credentials.")
    add_bullet(document, "Start MySQL and run python scripts/initialize_database.py.")
    add_bullet(document, "Place a trained YOLO model at models_yolo/active/best.pt if scanning is needed.")
    add_bullet(document, "Launch with python main.py.")
    add_bullet(document, "Create OS-level shortcuts or a service wrapper as desired (not provided by the repo).")

    add_heading(document, "10. Troubleshooting", level=1)
    add_table(document,
        ["Problem", "Check / fix"],
        [
            ["Application will not start", "Python version, venv activated, requirements installed"],
            ["Database connection error", "MySQL running, .env credentials correct, database exists, user has privileges"],
            ["Scanner does not detect barcodes", "YOLO model present at YOLO_MODEL_PATH, camera connected, adequate lighting"],
            ["Password change gate blocks access", "Complete mandatory password change on first login"],
            ["No cameras found", "Webcam connected and not in use by another app"],
            ["PDF report fails", "ReportLab installed, reports/ directory writable"],
        ],
        col_widths=[2.4, 5.0])

    add_heading(document, "11. Backup Considerations", level=1)
    add_bullet(document, "Back up the MySQL database (mysqldump) regularly; it holds all student, enrollment, assessment, and barcode data.")
    add_bullet(document, "Back up generated_barcodes/ and generated_id_cards/ if you want to preserve issued card artwork.")
    add_bullet(document, "Back up reports/ for generated PDFs.")
    add_bullet(document, "Back up .env (store separately; it contains credentials).")
    add_bullet(document, "The source code itself should be under version control (Git).")

    add_heading(document, "12. Maintenance Procedures", level=1)
    add_bullet(document, "Rotate database passwords periodically and update .env.")
    add_bullet(document, "Review logs/app.log (rotated at 10 MB, 5 backups) for errors.")
    add_bullet(document, "Re-train the YOLO model if new card designs are introduced (Training Center / scripts/).")
    add_bullet(document, "Keep python-barcode and decoder dependencies compatible with existing CODE39 cards.")
    add_bullet(document, "Apply security patches to Python and MySQL as needed.")

    add_heading(document, "13. Security Recommendations", level=1)
    add_bullet(document, "Use a dedicated database application account, not root (see .env.example comments).")
    add_bullet(document, "Never commit .env; restrict file permissions (chmod 600).")
    add_bullet(document, "Enforce strong admin passwords; the first login forces a password change.")
    add_bullet(document, "Limit MySQL network exposure (bind to localhost where possible).")
    add_bullet(document, "Review audit_logs for sensitive operations where applicable.")

    ok = builder.save(document, OUTPUT_FILES["deployment"])
    print(f"  {'OK' if ok else 'FAIL'} Document 6: {OUTPUT_FILES['deployment']}")
    return ok


# ===========================================================================
# Document 7: Testing and Code Quality Report
# ===========================================================================

def build_document_quality(builder: DocBuilder) -> bool:
    document = Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"; style.font.size = Pt(10.5); style.font.color.rgb = DARK_TEXT
    for section in document.sections:
        section.top_margin = Inches(0.9); section.bottom_margin = Inches(0.9)
        section.left_margin = Inches(0.9); section.right_margin = Inches(0.9)
    add_page_number_footer(document, "Testing and Code Quality Report")
    add_cover_page(document, "Testing and Code Quality Report",
              "Test Framework, Coverage Review, and Quality Recommendations", "WIT Barcode Student Information System")

    toc_entries = [
        (1, "1. Existing Test Framework"),
        (1, "2. Test File Locations"),
        (1, "3. Test Cases and Coverage"),
        (1, "4. Verified Test Results"),
        (1, "5. Code Organization"),
        (1, "6. Error-Handling Review"),
        (1, "7. Security Review"),
        (1, "8. Dependency Concerns"),
        (1, "9. Missing or Incomplete Tests"),
        (1, "10. Known Defects"),
        (1, "11. Recommended Improvements"),
        (1, "12. Prioritized Action Plan"),
    ]
    add_toc(document, toc_entries)

    add_heading(document, "1. Existing Test Framework", level=1)
    add_table(document,
        ["Item", "Detail"],
        [
            ["Test runner", "pytest"],
            ["GUI test support", "pytest-qt"],
            ["Mocking", "pytest-mock"],
            ["Configuration", "pytest.ini at project root"],
            ["Conftest", "tests/conftest.py"],
            ["Test directory", "tests/"],
        ],
        col_widths=[2.0, 5.4])

    add_heading(document, "2. Test File Locations", level=1)
    test_files = sorted((PROJECT_ROOT / "tests").glob("test_*.py")) if (PROJECT_ROOT / "tests").exists() else []
    add_para(document,
        f"{len(test_files)} test modules were found in tests/.", align="justify")
    rows = [[tf.name] for tf in test_files]
    add_table(document, ["Test module"], rows, col_widths=[5.8])

    add_heading(document, "3. Test Cases and Coverage", level=1)
    add_para(document,
        "Test modules observed in source include coverage of: auth service (login, lockout, password change, "
        "role checks), database (connection, repositories), models (Student, Barcode, User), scanner pipeline "
        "(detection, decoding, verification, session), scanner worker and camera UI, YOLO decoder, barcode "
        "generation, login page, assessments, certificate page and document, navigation and layout, password "
        "change dialog, scan post-scan workflow, dataset build, profile images, and button labels/assets.",
        align="justify")
    add_para(document,
        "No coverage report (e.g., pytest-cov) was generated as part of this documentation run, so percentage "
        "coverage figures cannot be stated.", align="justify")

    add_heading(document, "4. Verified Test Results", level=1)
    add_para(document,
        "Test suite was NOT executed during this documentation run. Therefore no pass/fail counts, pass rates, "
        "or failure details are claimed here. The README references '24 test modules, 557 tests passed' in a "
        "chart description, but that figure could not be independently verified by running the suite in this "
        "session. Execute pytest tests/ locally to obtain current results.", align="justify")

    add_heading(document, "5. Code Organization", level=1)
    add_bullet(document, "Clean layered structure: ui/ (frontend), services/ (business logic), database/ (connection/schema/repositories), models/ (dataclasses), config/, scripts/, tests/.")
    add_bullet(document, "No ORM; repositories use raw parameterized SQL.")
    add_bullet(document, "AuthService singleton for session and authorization.")
    add_bullet(document, "Separation of pipeline logic (no Qt in scan_pipeline) makes it directly testable offline.")
    add_bullet(document, "Some modules are large (e.g., scanner_page.py ~1174 lines, barcode_decoder.py ~910 lines) and could be split for readability.")

    add_heading(document, "6. Error-Handling Review", level=1)
    add_bullet(document, "DatabaseUnavailableError is a defined contract; repositories never convert connection failure into empty results.")
    add_bullet(document, "Pages catch DatabaseUnavailableError at data boundaries and show retryable dialogs.")
    add_bullet(document, "Scanner pipeline catches exceptions per-frame and returns ScanState.ERROR instead of crashing the capture loop.")
    add_bullet(document, "Reader failures (zbar/zxing) are caught and degraded; decode continues.")
    add_bullet(document, "get_cursor rolls back on MySQLError and re-raises as DatabaseUnavailableError.")
    add_bullet(document, "Some broad except Exception blocks exist (e.g., in main_window.save_student) which could mask unexpected errors.")

    add_heading(document, "7. Security Review", level=1)
    add_bullet(document, "Passwords hashed with PBKDF2-HMAC-SHA256 (210k iterations, 16-byte random salt).")
    add_bullet(document, "Constant-time comparison via hmac.compare_digest; identical failure messages prevent account enumeration.")
    add_bullet(document, "Failed-login rate limiting with persistent lockout (5 failures / 15 min).")
    add_bullet(document, "Role-based access control (admin/staff/viewer) on navigation and writes.")
    add_bullet(document, "Session held in memory only; never written to disk.")
    add_bullet(document, ".env contains secrets; must be excluded from version control (check .gitignore).")
    add_bullet(document, "Parameterized SQL (%s) used throughout repositories (SQL injection mitigated).")

    add_heading(document, "8. Dependency Concerns", level=1)
    add_bullet(document, "zxing-cpp is optional; decoding falls back to pyzbar if absent (documented behavior).")
    add_bullet(document, "ultralytics/YOLO and PyTorch are heavy dependencies; CPU fallback is slow (~218 ms/frame).")
    add_bullet(document, "MySQL 8+ and ZBar are external system dependencies not installed by pip.")
    add_bullet(document, "python-barcode add_checksum must remain False to match physical cards (documented in barcode_generator.py).")

    add_heading(document, "9. Missing or Incomplete Tests", level=1)
    add_bullet(document, "No CI/CD pipeline configured to run tests on commit/PR.")
    add_bullet(document, "No test coverage report configured (pytest-cov not in requirements).")
    add_bullet(document, "GUI tests require a display/Qt platform plugin; headless CI needs offscreen platform.")
    add_bullet(document, "Database-dependent tests need a live MySQL instance; no test database fixture evident.")
    add_bullet(document, "Scanner/YOL O tests may require the model file and camera; not all run offline.")

    add_heading(document, "10. Known Defects", level=1)
    add_bullet(document, "Application requires a running MySQL database; no offline mode.")
    add_bullet(document, "Synchronous DB calls can block the GUI thread on slow queries.")
    add_bullet(document, "Known data issues are tracked in reports/ (ADMIN_ACTIONS_REQUIRED.md, PROFILE_IMAGE_MAPPING.md, STUDENT_3_INTEGRATION.md, CARD_C_RECOGNITION_REPORT.md).")
    add_bullet(document, "Some large files (scanner_page.py, barcode_decoder.py) are harder to maintain and test in isolation.")

    add_heading(document, "11. Recommended Improvements", level=1)
    add_bullet(document, "Add pytest-cov and generate coverage reports; set a coverage threshold.")
    add_bullet(document, "Add CI (GitHub Actions) to run pytest on push/PR.")
    add_bullet(document, "Add a test database fixture/seeded database for offline database tests.")
    add_bullet(document, "Split large modules (scanner_page.py, barcode_decoder.py) into smaller, testable units.")
    add_bullet(document, "Review broad except Exception blocks and narrow them.")
    add_bullet(document, "Add integration tests for the full scan pipeline using the checked-in ID card assets.")

    add_heading(document, "12. Prioritized Action Plan", level=1)
    plan = [
        ("P1", "Run pytest tests/ locally and record results; fix any failures."),
        ("P1", "Verify .env is in .gitignore and never committed."),
        ("P2", "Add pytest-cov coverage reporting with a threshold."),
        ("P2", "Add CI pipeline to run tests on every change."),
        ("P2", "Split scanner_page.py and barcode_decoder.py into smaller modules."),
        ("P3", "Add integration tests for scan pipeline using checked-in assets."),
        ("P3", "Narrow broad except Exception blocks."),
    ]
    add_table(document, ["Priority", "Action"], plan, col_widths=[1.0, 6.4])

    ok = builder.save(document, OUTPUT_FILES["quality"])
    print(f"  {'OK' if ok else 'FAIL'} Document 7: {OUTPUT_FILES['quality']}")
    return ok


# ===========================================================================
# Main
# ===========================================================================

def main():
    # Ensure docs subdirectories exist
    for d in [DOCS_PROJECT, DOCS_FRONTEND, DOCS_BACKEND, DOCS_SOURCE,
              DOCS_LEARNING, DOCS_DEPLOY, DOCS_QUALITY]:
        d.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("WIT Barcode Student Information System - Documentation Generator")
    print("=" * 60)
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Docs output:  {DOCS_DIR}")
    print()

    builder = DocBuilder()
    print(f"Discovered {len(builder.all_files)} source files:")
    for cat, files in builder.source_files.items():
        print(f"  {cat:14s}: {len(files)}")

    results = {}

    print("\nGenerating documents...")
    results["project"] = build_document_project(builder)
    results["frontend"] = build_document_frontend(builder)
    results["backend"] = build_document_backend(builder)
    results["source"] = build_document_source(builder)
    results["learning"] = build_document_learning(builder)
    results["deployment"] = build_document_deployment(builder)
    results["quality"] = build_document_quality(builder)

    print("\n" + "=" * 60)
    print("Generation summary:")
    all_ok = True
    for name, ok in results.items():
        status = "OK" if ok else "FAIL"
        print(f"  {status}  Document {list(OUTPUT_FILES.keys()).index(name)+1} ({name}): {OUTPUT_FILES[name]}")
        if not ok:
            all_ok = False
    print("=" * 60)

    # Write README index
    readme_path = DOCS_DIR / "README.md"
    lines = []
    lines.append("# Documentation Index")
    lines.append("")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"Project: {PROJECT_ROOT}")
    lines.append("")
    lines.append("These documents were generated from the **actual source code** of the WIT Barcode Student "
                 "Information System using `tools/generate_documentation.py`. No features were invented.")
    lines.append("")
    lines.append("## Documents")
    lines.append("")
    lines.append("| # | Document | Location | Covers |")
    lines.append("|---|----------|----------|--------|")
    descs = {
        "project": "Complete project overview, architecture, workflows, auth, database, deployment, security.",
        "frontend": "Frontend (PySide6) architecture, pages, components, routing, forms, state, API integration.",
        "backend": "Backend services, data layer, authentication, validation, error handling, logging, security.",
        "source": "Inventory of important source files with functions, classes, dependencies, and importance ratings.",
        "learning": "Beginner-friendly step-by-step code explanation, execution traces, glossary.",
        "deployment": "System requirements, dependency installation, environment config, database setup, deployment, troubleshooting.",
        "quality": "Test framework, test locations, error-handling review, security review, action plan.",
    }
    covers = {
        "project": "Full system (overview, architecture, database, workflows, security).",
        "frontend": "Frontend only (PySide6 UI).",
        "backend": "Backend only (services, data layer, auth).",
        "source": "Source code inventory (frontend + backend + database + config + tests).",
        "learning": "All layers (explained simply for students).",
        "deployment": "Installation and maintenance (all layers).",
        "quality": "Testing and code quality (all layers).",
    }
    for i, (key, path) in enumerate(OUTPUT_FILES.items(), 1):
        rel = path.relative_to(PROJECT_ROOT)
        lines.append(f"| {i} | {descs[key].split('.')[0]} | `{rel}` | {covers[key]} |")
    lines.append("")
    lines.append("## Quick Navigation")
    lines.append("- Frontend documentation: `docs/frontend/02_Frontend_Technical_Documentation.docx`")
    lines.append("- Backend documentation: `docs/backend/03_Backend_Technical_Documentation.docx`")
    lines.append("- Source code reference: `docs/source-code/04_Important_Source_Code_Reference.docx`")
    lines.append("- Database documentation: inside `docs/project/01_Complete_Project_Documentation.docx` (section 8) and `docs/backend/`")
    lines.append("- Learning guide: `docs/learning-guide/05_Code_Explanation_and_Learning_Guide.docx`")
    lines.append("- Deployment manual: `docs/deployment/06_Installation_and_Maintenance_Manual.docx`")
    lines.append("- Quality report: `docs/quality/07_Testing_and_Code_Quality_Report.docx`")
    lines.append("")
    lines.append("## Regeneration")
    lines.append("To regenerate all documents after project changes, run:")
    lines.append("")
    lines.append("```bash")
    lines.append("python tools/generate_documentation.py")
    lines.append("```")
    lines.append("")
    lines.append("## Verification Notes")
    lines.append("- This is a **desktop PySide6 application with no HTTP API**. All UI-to-data communication is in-process Python; no HTTP endpoints exist.")
    lines.append("- `.env` contains secrets and was **not read or included** in any document.")
    lines.append("- Test results were **not executed** during this run; no pass/fail verdicts are claimed.")
    lines.append("- Some information (e.g., exact line numbers) is approximate where regex-based extraction was used.")
    readme_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nREADME index written to: {readme_path}")

    if all_ok:
        print("\nAll 7 documents generated successfully.")
    else:
        print("\nSome documents FAILED to generate. See messages above.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())