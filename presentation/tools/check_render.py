#!/usr/bin/env python3
"""Verify the rendered revised deck: bounds, small fonts, overlaps.

Parses the pdftotext -bbox XML of the LibreOffice render and reports:
  * words outside the page safe area
  * words smaller than a legibility floor
  * per-page pairs of words that overlap significantly
"""
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

XML = sys.argv[1] if len(sys.argv) > 1 else "render/out.xml"
# page is 960 x 540 pt; safe area keeps content off the very edge
PW, PH = 960.0, 540.0
SAFE_L, SAFE_R = 8.0, PW - 8.0
SAFE_T, SAFE_B = 6.0, PH - 6.0
MIN_FONT = 16.0          # deck floor for real content
FOOTER_FONT = 14.0       # footer/small print is allowed a little below
OVERLAP_FRAC = 0.55      # fraction of the smaller box that must overlap

ns = {"x": "http://www.w3.org/1999/xhtml"}


def main():
    tree = ET.parse(XML)
    root = tree.getroot()
    pages = root.findall(".//x:page", ns)
    oob = []
    small = []
    overlaps = []
    per_page_min = []
    for pno, page in enumerate(pages, 1):
        words = []
        for w in page.findall(".//x:word", ns):
            x0, y0 = float(w.get("xMin")), float(w.get("yMin"))
            x1, y1 = float(w.get("xMax")), float(w.get("yMax"))
            text = (w.text or "").strip()
            if not text:
                continue
            size = y1 - y0
            words.append((x0, y0, x1, y1, size, text))
            if x0 < SAFE_L or x1 > SAFE_R or y0 < SAFE_T or y1 > SAFE_B:
                oob.append((pno, round(x0, 1), round(y0, 1), round(x1, 1),
                            round(y1, 1), round(size, 1), text))
            if size < MIN_FONT - 0.6:
                if size < FOOTER_FONT - 0.6:
                    small.append((pno, round(size, 1), text))
        if words:
            per_page_min.append((pno, round(min(w[4] for w in words), 1)))
        # crude overlap check: bucket words by size then test pairs
        for i in range(len(words)):
            ax0, ay0, ax1, ay1, asz, at = words[i]
            for j in range(i + 1, len(words)):
                bx0, by0, bx1, by1, bsz, bt = words[j]
                if abs(asz - bsz) > 3:
                    continue
                ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
                iy = max(0.0, min(ay1, by1) - max(ay0, by0))
                if ix <= 0 or iy <= 0:
                    continue
                inter = ix * iy
                amin = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
                # a real collision needs strong overlap in BOTH axes
                hmin = min(ax1 - ax0, bx1 - bx0)
                vmin = min(ay1 - ay0, by1 - by0)
                if (amin > 0 and inter / amin > OVERLAP_FRAC
                        and vmin > 0 and iy / vmin > 0.60
                        and hmin > 0 and ix / hmin > 0.60):
                    overlaps.append((pno, at, bt, round(inter / amin, 2),
                                     round(ix, 1), round(iy, 1)))
    print(f"pages: {len(pages)}")
    print(f"out-of-bounds words: {len(oob)}")
    for row in oob[:40]:
        print("   OOB", row)
    print(f"tiny words (< {FOOTER_FONT}pt): {len(small)}")
    for row in small[:40]:
        print("   TINY", row)
    print(f"overlapping word pairs: {len(overlaps)}")
    for row in overlaps[:40]:
        print("   OVERLAP", row)
    print("smallest word size per page:")
    for pno, mn in per_page_min:
        print(f"   p{pno:02d}: {mn}pt")


if __name__ == "__main__":
    main()
