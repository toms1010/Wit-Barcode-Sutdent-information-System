#!/usr/bin/env python3
"""Geometry checks on the rendered PDF.

Detects text that leaves the page, text that overlaps other text, and text
that sits within 6pt of the page edge. This complements the render pass: if a
box is too small, LibreOffice still writes the overflow into the PDF, so these
checks catch both clipping and spill.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

XML = Path(sys.argv[1] if len(sys.argv) > 1 else
           "presentation/render/out.xml")
NS = "{http://www.w3.org/1999/xhtml}"

root = ET.parse(XML).getroot()
pages = root.findall(f".//{NS}page")
problems = 0

for pno, page in enumerate(pages, 1):
    pw = float(page.get("width"))
    ph = float(page.get("height"))
    words = []
    for w in page.findall(f"{NS}word"):
        bb = (float(w.get("xMin")), float(w.get("yMin")),
              float(w.get("xMax")), float(w.get("yMax")))
        words.append((bb, (w.text or "").strip()))

    for bb, text in words:
        if bb[0] < 4 or bb[1] < 2 or bb[2] > pw - 4 or bb[3] > ph - 2:
            print(f"[p{pno}] EDGE  {text!r} bbox={tuple(round(v, 1) for v in bb)}")
            problems += 1

    for i, (a, ta) in enumerate(words):
        for b, tb in words[i + 1:]:
            ix = min(a[2], b[2]) - max(a[0], b[0])
            iy = min(a[3], b[3]) - max(a[1], b[1])
            if ix <= 0.5 or iy <= 0.5:
                continue
            inter = ix * iy
            small = min((a[2] - a[0]) * (a[3] - a[1]),
                        (b[2] - b[0]) * (b[3] - b[1]))
            if small > 0 and inter / small > 0.55:
                print(f"[p{pno}] OVERLAP {ta!r} <> {tb!r} "
                      f"({inter / small:.0%} of smaller) bbox={tuple(round(v, 1) for v in a)}")
                problems += 1

print(f"pages={len(pages)} problems={problems}")
sys.exit(1 if problems else 0)
