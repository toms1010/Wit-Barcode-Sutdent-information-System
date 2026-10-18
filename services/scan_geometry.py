"""Coordinate systems and scan-region geometry for the live scanner.

Three coordinate spaces exist in this application, and mixing them is the
easiest way to get a barcode box that drifts away from the barcode:

``frame``
    Pixels of the raw webcam frame. This is the *only* space in which YOLO
    detections, the scan region, and crop rectangles are compared. Ultralytics
    letterboxes internally before inference and undoes that mapping before
    returning results, so ``box.xyxy`` is already in frame pixels and needs no
    conversion. Inference input, ROI gating, cropping and decoding all happen
    here.

``display``
    The letterboxed canvas shown in the preview widget. Reached from frame space
    through exactly one affine map (:class:`DisplayTransform`).

``widget``
    Whatever rectangle the preview label currently occupies. Never used for
    comparisons -- only to decide how large the display canvas should be.

Mirroring is deliberately *not* modelled here. It is applied to the finished
composited canvas as a final horizontal flip, which mirrors the guide, the
detection boxes and the camera pixels together and therefore cannot desync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

Rect = Tuple[int, int, int, int]  # (x, y, width, height) in pixels
Size = Tuple[int, int]  # (width, height) in pixels


@dataclass(frozen=True)
class DisplayTransform:
    """Maps frame pixels onto a letterboxed display canvas.

    The display canvas has the same aspect ratio as the source frame, so the
    mapping is a uniform scale plus an integer offset -- no anisotropic
    scaling, which is what keeps boxes square and labels unstretched.

    ``mirrored`` applies a horizontal flip for the selfie-style preview. It is
    part of this transform rather than a flip of the finished canvas so that
    drawn *text* stays readable: only positions are mirrored. The caller mirrors
    the camera pixels themselves the same way, so image and boxes stay in sync.
    """

    frame_size: Size
    display_size: Size
    mirrored: bool = False

    @property
    def scale(self) -> float:
        fw, fh = self.frame_size
        dw, dh = self.display_size
        if fw <= 0 or fh <= 0:
            return 1.0
        return min(dw / fw, dh / fh)

    @property
    def offset(self) -> Tuple[int, int]:
        """Top-left position of the scaled image inside the canvas."""
        fw, fh = self.frame_size
        dw, dh = self.display_size
        s = self.scale
        return int(round((dw - fw * s) / 2.0)), int(round((dh - fh * s) / 2.0))

    def to_display_point(self, x: float, y: float) -> Tuple[int, int]:
        s = self.scale
        ox, oy = self.offset
        dx = int(round(x * s + ox))
        if self.mirrored:
            dx = self.display_size[0] - dx
        return dx, int(round(y * s + oy))

    def to_display_rect(self, rect: Rect) -> Rect:
        """Map a frame-space rectangle to display space.

        Both corners are mapped and re-normalised, so the result is correct
        whether or not the rectangle is mirrored.
        """
        x, y, w, h = rect
        dx1, dy1 = self.to_display_point(x, y)
        dx2, dy2 = self.to_display_point(x + w, y + h)
        left, right = min(dx1, dx2), max(dx1, dx2)
        top, bottom = min(dy1, dy2), max(dy1, dy2)
        return left, top, max(1, right - left), max(1, bottom - top)

    def to_frame_rect(self, rect: Rect) -> Rect:
        """Inverse mapping, for mapping a click on the canvas back to the frame."""
        s = self.scale
        ox, oy = self.offset
        x, y, w, h = rect
        fx = (x - ox) / s
        fy = (y - oy) / s
        if self.mirrored:
            fx = self.display_size[0] / s - (fx + w / s)
        frame_w, frame_h = self.frame_size
        fx = max(0.0, min(float(frame_w), fx))
        fy = max(0.0, min(float(frame_h), fy))
        fw = max(1.0, min(frame_w - fx, w / s))
        fh = max(1.0, min(frame_h - fy, h / s))
        return int(fx), int(fy), int(round(fw)), int(round(fh))


def letterboxed_size(frame_size: Size, bounds: Size) -> Size:
    """Largest canvas with the frame's aspect ratio that fits inside ``bounds``."""
    fw, fh = frame_size
    bw, bh = bounds
    if fw <= 0 or fh <= 0 or bw <= 0 or bh <= 0:
        return bounds
    s = min(bw / fw, bh / fh)
    return max(1, int(round(fw * s))), max(1, int(round(fh * s)))


@dataclass(frozen=True)
class ScanRegion:
    """The fixed, centred yellow guide, expressed in frame pixels.

    Recomputed per frame size so the guide is always centred and always the
    same relative size, whatever the camera reports.
    """

    x: int
    y: int
    width: int
    height: int

    @classmethod
    def for_frame(
        cls,
        frame_size: Size,
        width_ratio: float,
        height_ratio: float,
    ) -> "ScanRegion":
        fw, fh = frame_size
        w = max(1, int(round(fw * max(0.05, min(1.0, width_ratio)))))
        h = max(1, int(round(fh * max(0.05, min(1.0, height_ratio)))))
        return cls((fw - w) // 2, (fh - h) // 2, w, h)

    @property
    def rect(self) -> Rect:
        return self.x, self.y, self.width, self.height

    @property
    def area(self) -> int:
        return max(1, self.width * self.height)


def clamp_rect(rect: Rect, frame_size: Size) -> Rect:
    """Clip a rectangle to the frame, keeping it non-empty and inside bounds."""
    x, y, w, h = rect
    fw, fh = frame_size
    x = max(0, min(int(x), fw - 1))
    y = max(0, min(int(y), fh - 1))
    w = max(1, min(int(w), fw - x))
    h = max(1, min(int(h), fh - y))
    return x, y, w, h


def intersection(a: Rect, b: Rect) -> Rect:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0, 0, 0, 0
    return x1, y1, x2 - x1, y2 - y1


def overlap_ratio(box: Rect, region: Rect) -> float:
    """Fraction of ``box``'s own area that falls inside ``region``.

    Containment rather than IoU on purpose: a barcode held at the edge of the
    frame has a small box and a small intersection, so IoU would score it
    highly even though most of it is outside the guide. Measuring the share of
    the *barcode* that is inside makes the threshold mean what it says.
    """
    _, _, w, h = box
    box_area = max(1, w * h)
    _, _, iw, ih = intersection(box, region)
    return (iw * ih) / box_area


def expand_rect(rect: Rect, margin_ratio: float, frame_size: Size) -> Rect:
    """Grow a detection box by a proportional margin, then clip to the frame."""
    x, y, w, h = rect
    mx = int(round(w * max(0.0, margin_ratio)))
    my = int(round(h * max(0.0, margin_ratio)))
    return clamp_rect((x - mx, y - my, w + 2 * mx, h + 2 * my), frame_size)


def is_crop_usable(rect: Rect, min_width: int, min_height: int) -> bool:
    """Reject boxes too small to hold a readable barcode.

    A CODE39 symbol needs at least a few pixels per module; a crop under these
    dimensions cannot be decoded no matter how good the preprocessing is.
    """
    _, _, w, h = rect
    return w >= min_width and h >= min_height
