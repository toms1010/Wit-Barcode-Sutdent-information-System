"""Small matplotlib drawing helpers for presentation-ready system diagrams."""
from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
from matplotlib.lines import Line2D

INK = "#0F172A"
SLATE = "#475569"
MUTED = "#64748B"
ORANGE = "#F97316"
DARK = "#AD4D05"
GOLD = "#FFD24D"
CREAM = "#FFF7ED"
PAPER = "#FFFFFF"
BORDER = "#CBD5E1"
GREEN = "#16A34A"
RED = "#DC2626"
BLUE = "#2563EB"
PALE_GREEN = "#DCFCE7"
PALE_RED = "#FEE2E2"
PALE_BLUE = "#DBEAFE"
PALE_GOLD = "#FEF3C7"

FONT = "DejaVu Sans"


class Canvas:
    """Coordinate system: 0–100 across, 0–56.25 down (16:9)."""

    def __init__(self, width=13.333, height=7.5, dpi=200, bg="white"):
        self.fig = plt.figure(figsize=(width, height), dpi=dpi, facecolor=bg)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, 100)
        self.ax.set_ylim(0, 56.25)
        self.ax.axis("off")
        self.bg = bg
        self._unit_pt = (width * 72) / 100.0  # points per x-unit
        self._band_labels = set()

    # -- primitives ------------------------------------------------------
    def title(self, text, sub=None, x=3.2, y=53.4, size=17, color=INK):
        self.ax.text(x, y, text, fontsize=size, fontweight="bold", color=color,
                     ha="left", va="center", family=FONT)
        if sub:
            self.ax.text(x, y - 2.9, sub, fontsize=9.2, color=MUTED,
                         ha="left", va="center", family=FONT)

    def _wrap(self, text, width_units, size):
        char_units = (0.53 * size) / self._unit_pt
        n = max(6, int((width_units - 1.0) / char_units))
        out = []
        for para in str(text).split("\n"):
            out.extend(textwrap.wrap(para, n) or [""])
        return out

    def box(self, x, y, w, h, title="", body="", *,
            fill=PAPER, edge=BORDER, title_color=INK, body_color=SLATE,
            title_size=9.2, body_size=7.4, line=1.0, radius=0.7, dash=None,
            align="center", bold_body=False):
        patch = FancyBboxPatch(
            (x, y), w, h,
            boxstyle=f"round,pad=0,rounding_size={radius}",
            linewidth=line, edgecolor=edge, facecolor=fill,
            linestyle=dash or "solid", zorder=2,
        )
        self.ax.add_patch(patch)
        cx = x + w / 2 if align == "center" else x + 0.7
        ha = "center" if align == "center" else "left"
        cursor = y + h - 1.25
        if title:
            lines = self._wrap(title, w, title_size)
            for ln in lines:
                self.ax.text(cx, cursor, ln, fontsize=title_size, fontweight="bold",
                             color=title_color, ha=ha, va="top", family=FONT, zorder=3)
                cursor -= (title_size * 1.28) / self._unit_pt
        if body:
            cursor -= 0.25
            for ln in self._wrap(body, w, body_size):
                self.ax.text(cx, cursor, ln, fontsize=body_size,
                             fontweight="bold" if bold_body else "normal",
                             color=body_color, ha=ha, va="top", family=FONT, zorder=3)
                cursor -= (body_size * 1.32) / self._unit_pt

    def band(self, x, y, w, h, label, fill=CREAM, edge="#FDBA74"):
        self.ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.9",
            linewidth=1.1, edgecolor=edge, facecolor=fill, zorder=1))
        # Label sits just above the band so it can never collide with the
        # first row of component boxes drawn inside the band.
        self._band_labels.add(self.ax.text(
            x + 0.6, y + h + 0.45, label, fontsize=7.4, fontweight="bold",
            color=DARK, ha="left", va="bottom", family=FONT, zorder=3))

    def diamond(self, cx, cy, w, h, text, *, fill=PALE_GOLD, edge="#D97706",
                color=INK, size=7.4):
        pts = [(cx, cy + h / 2), (cx + w / 2, cy), (cx, cy - h / 2), (cx - w / 2, cy)]
        from matplotlib.patches import Polygon
        self.ax.add_patch(Polygon(pts, closed=True, facecolor=fill,
                                  edgecolor=edge, linewidth=1.0, zorder=2))
        lines = self._wrap(text, w * 0.62, size)
        total = len(lines) * (size * 1.2) / self._unit_pt
        start = cy + total / 2 - (size * 0.55) / self._unit_pt
        for i, ln in enumerate(lines):
            self.ax.text(cx, start - i * (size * 1.2) / self._unit_pt, ln,
                         fontsize=size, color=color, ha="center", va="center",
                         family=FONT, zorder=3, fontweight="bold")

    def pill(self, x, y, w, h, text, fill=ORANGE, color="white", size=8.0):
        self.ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0,rounding_size=" + str(h / 2.4),
            linewidth=0, facecolor=fill, zorder=4))
        self.ax.text(x + w / 2, y + h / 2, text, fontsize=size, fontweight="bold",
                     color=color, ha="center", va="center", family=FONT, zorder=5)

    def arrow(self, x1, y1, x2, y2, label=None, *, color=SLATE, lw=1.4,
              style="-|>", ls="-", rad=0.0, label_size=6.8, label_offset=(0, 1.1),
              label_color=None, label_rot=0, zorder=3):
        self.ax.add_patch(FancyArrowPatch(
            (x1, y1), (x2, y2), arrowstyle=style, mutation_scale=11,
            linewidth=lw, color=color, linestyle=ls, zorder=zorder,
            connectionstyle=f"arc3,rad={rad}", shrinkA=1, shrinkB=1))
        if label:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            self.ax.text(mx + label_offset[0], my + label_offset[1], label,
                         fontsize=label_size, color=label_color or MUTED,
                         ha="center", va="center", family=FONT, zorder=6,
                         rotation=label_rot, rotation_mode="anchor",
                         bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.9))

    def elbow(self, points, label=None, *, color=SLATE, lw=1.4, label_at=0.5,
              label_offset=(0, 1.0), label_size=6.8):
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        self.ax.plot(xs[:-1], ys[:-1], color=color, lw=lw, zorder=3,
                     solid_capstyle="round")
        self.ax.add_patch(FancyArrowPatch(
            points[-2], points[-1], arrowstyle="-|>", mutation_scale=11,
            linewidth=lw, color=color, zorder=3, shrinkA=0, shrinkB=1))
        if label:
            # place at the midpoint of the longest segment
            segs = list(zip(points[:-1], points[1:]))
            (ax1, ay1), (ax2, ay2) = max(segs, key=lambda s: abs(s[0][0]-s[1][0]) + abs(s[0][1]-s[1][1]))
            mx, my = (ax1 + ax2) / 2, (ay1 + ay2) / 2
            self.ax.text(mx + label_offset[0], my + label_offset[1], label,
                         fontsize=label_size, color=MUTED, ha="center", va="center",
                         family=FONT, zorder=6,
                         bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.92))

    def text(self, x, y, s, size=8, color=INK, weight="normal", ha="left", va="top", style="normal"):
        self.ax.text(x, y, s, fontsize=size, color=color, fontweight=weight,
                     ha=ha, va=va, family=FONT, style=style, zorder=6)

    def legend(self, items, x, y, size=7.4, gap=10.0):
        for i, (label, colour, style) in enumerate(items):
            xi = x + i * gap
            if style == "line":
                self.ax.add_line(Line2D([xi, xi + 2.4], [y, y], color=colour, lw=2))
            else:
                self.ax.add_patch(Rectangle((xi, y - 0.55), 2.2, 1.1, facecolor=colour,
                                            edgecolor="none"))
            self.ax.text(xi + 2.9, y, label, fontsize=size, color=SLATE,
                         va="center", family=FONT)

    def _collisions(self):
        """Return text/text and text/edge collisions in display coordinates.

        The deck is assembled without a visual diff step, so this catches the
        two failure modes that matter most: labels printed on top of each other
        and labels that spill over the border of a component box.
        """
        from matplotlib.patches import FancyBboxPatch as _FBP, Polygon as _Poly

        self.fig.canvas.draw()
        renderer = self.fig.canvas.get_renderer()
        texts = [t for t in self.ax.texts if t.get_text().strip()]
        issues = []

        extents = {}
        fw, fh = self.fig.canvas.get_width_height()
        for t in texts:
            bb = t.get_window_extent(renderer)
            extents[t] = bb
            if bb.x0 < -1 or bb.y0 < -1 or bb.x1 > fw + 1 or bb.y1 > fh + 1:
                issues.append(f"OUTSIDE CANVAS: {t.get_text()!r}")

        for i, a in enumerate(texts):
            ba = extents[a]
            for b in texts[i + 1:]:
                bb = extents[b]
                ix = min(ba.x1, bb.x1) - max(ba.x0, bb.x0)
                iy = min(ba.y1, bb.y1) - max(ba.y0, bb.y0)
                if ix > 2 and iy > 2:
                    issues.append(
                        f"TEXT OVERLAP: {a.get_text()!r} <> {b.get_text()!r} "
                        f"({ix:.0f}x{iy:.0f}px)")

        patches = [
            p for p in self.ax.patches
            if isinstance(p, (_FBP, _Poly)) and p.get_facecolor()[3] > 0.05
        ]
        for t in texts:
            if t in self._band_labels:
                continue
            bb = extents[t]
            own = t.get_bbox_patch()
            area = max(1.0, bb.width * bb.height)
            for p in patches:
                if p is own:
                    continue
                pb = p.get_window_extent(renderer)
                ix = min(bb.x1, pb.x1) - max(bb.x0, pb.x0)
                iy = min(bb.y1, pb.y1) - max(bb.y0, pb.y0)
                if ix <= 1 or iy <= 1:
                    continue
                inter = ix * iy
                inside = (bb.x0 >= pb.x0 - 1 and bb.x1 <= pb.x1 + 1
                          and bb.y0 >= pb.y0 - 1 and bb.y1 <= pb.y1 + 1)
                band_label = (bb.y0 >= pb.y1 - 3 and bb.x0 >= pb.x0
                              and bb.x1 <= pb.x1)
                if not inside and not band_label and inter / area > 0.10:
                    issues.append(
                        f"TEXT SPILLS OVER BOX: {t.get_text()!r} "
                        f"({inter / area:.0%} of label)")
                    break
        return issues

    def save(self, path: Path, dpi=200):
        path.parent.mkdir(parents=True, exist_ok=True)
        issues = self._collisions()
        if issues:
            log = Path("presentation/tools/diagram_collisions.txt")
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as fh:
                fh.write(f"\n== {path.name} ==\n")
                for issue in issues:
                    fh.write(issue + "\n")
            print(f"[diagram] {path.name}: {len(issues)} layout warning(s)")
        self.fig.savefig(path, dpi=dpi, facecolor=self.bg, bbox_inches=None)
        plt.close(self.fig)
        print(f"[diagram] {path.name}")
