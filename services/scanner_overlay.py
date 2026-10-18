"""Detection overlay for the live preview.

Everything here is presentation only. The renderer takes a *copy* of the camera
frame, composes the overlay onto it, and returns a new image; the array that the
detector and the decoder saw is never modified. This keeps the overlay from
leaking into inference (a yellow tint or a drawn box can shift the statistics
the model and zbar rely on).

What is drawn: the detection boxes and the status bar. The scan guide, its
sweeping line and the floating hint text were all removed, so nothing frames the
camera image.

Coordinate handling
-------------------
The scan region and every detection box are defined in frame pixels by
:class:`~services.scan_pipeline.ScanPipeline`. They are converted to display
pixels exactly once, through a single :class:`DisplayTransform` derived from the
same letterboxing used to resize the frame. Because the frame is letterboxed
first and the transform is built from that same operation, boxes stay registered
with the image at any preview size.

Mirroring, when enabled, is carried by the :class:`DisplayTransform`: the camera
pixels are flipped before compositing and every drawn position is mirrored to
match. Text is drawn afterwards, so labels and the status bar stay readable while
the camera image still behaves like a selfie view.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np

from config.settings import settings
from services.scan_geometry import DisplayTransform, letterboxed_size
from services.scan_pipeline import BarcodeCandidate, ScanOutcome, ScanState

Rect2 = Tuple[int, int, int, int]  # (x1, y1, x2, y2)

#: Box colours per scan state. The box must always mean the same thing:
#: green = a barcode inside the scan region was read, amber = seen but outside
#: it, red = a failure the user can act on.
STATE_BOX_COLORS = {
    ScanState.BARCODE_DETECTED: (0, 165, 255),
    ScanState.DECODING: (0, 200, 255),
    ScanState.VERIFIED: (60, 220, 60),
    ScanState.UNKNOWN_BARCODE: (0, 165, 255),
    ScanState.ERROR: (60, 60, 235),
    ScanState.SEARCHING: (0, 165, 255),
    # Amber, like the status line: a read that found two symbols is neither the
    # calm blue of "still looking" nor the red of a failure.
    ScanState.AMBIGUOUS: (0, 165, 255),
}


def _pulse(phase: float, reduced_motion: bool) -> float:
    """Breathing factor in ``[0, 1]``; constant when motion is reduced."""
    if reduced_motion:
        return 1.0
    return 0.5 + 0.5 * float(np.sin(phase * 2.0 * np.pi))


def hex_to_bgr(color: str) -> Tuple[int, int, int]:
    """Convert ``#RRGGBB`` to OpenCV's BGR tuple."""
    value = color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return b, g, r


def _alpha_rect(
    image: np.ndarray, rect: Rect2, color: Tuple[int, int, int], alpha: float
) -> None:
    """Blend a filled rectangle into ``image`` without allocating a full copy."""
    if alpha <= 0.0:
        return
    x1, y1, x2, y2 = rect
    x1 = max(0, min(x1, image.shape[1]))
    x2 = max(0, min(x2, image.shape[1]))
    y1 = max(0, min(y1, image.shape[0]))
    y2 = max(0, min(y2, image.shape[0]))
    if x2 <= x1 or y2 <= y1:
        return
    roi = image[y1:y2, x1:x2]
    overlay = np.empty_like(roi)
    overlay[:] = color
    cv2.addWeighted(overlay, alpha, roi, 1.0 - alpha, 0.0, dst=roi)


def _text(
    image: np.ndarray,
    text: str,
    org: Tuple[int, int],
    color: Tuple[int, int, int],
    scale: float = 0.5,
    thickness: int = 1,
) -> None:
    if not text:
        return
    cv2.putText(image, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 2, cv2.LINE_AA)
    cv2.putText(image, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


class OverlayRenderer:
    """Draws the detection boxes and the status bar onto a copy of the frame."""

    def __init__(self, mirrored: Optional[bool] = None, reduced_motion: Optional[bool] = None) -> None:
        self.mirrored = (
            settings.SCAN_MIRROR_PREVIEW if mirrored is None else bool(mirrored)
        )
        self.reduced_motion = (
            bool(settings.SCAN_REDUCED_MOTION) if reduced_motion is None else bool(reduced_motion)
        )

    # -- pieces -------------------------------------------------------------

    def _draw_detection(
        self,
        canvas: np.ndarray,
        transform: DisplayTransform,
        cand: BarcodeCandidate,
        state: Optional[ScanState] = None,
        phase: float = 0.0,
    ) -> None:
        """Outline one YOLO detection.

        The box is driven by the real pipeline state: a pulse while a read is in
        progress, a steady outline once there is a result. It is never drawn for
        a detection the smoother has dropped, and it disappears with the
        detection (subject to the smoother's grace period) rather than lingering.
        """
        dx, dy, dw, dh = transform.to_display_rect(cand.bbox)
        rect2 = (dx, dy, dx + dw, dy + dh)

        # A read is in flight: pulse. Otherwise: steady. An unknown barcode is a
        # successful read of an unrecognised student, so it stays steady amber.
        in_flight = state in (ScanState.BARCODE_DETECTED, ScanState.DECODING)
        if in_flight and not self.reduced_motion:
            grow = int(round(3 * _pulse(phase, False)))
            rect2 = (dx - grow, dy - grow, dx + dw + grow, dy + dh + grow)
        thickness = max(2, int(round(min(canvas.shape[:2]) * 0.0028)))

        if state is not None and state in STATE_BOX_COLORS:
            base = STATE_BOX_COLORS[state]
        else:
            base = (60, 220, 60) if cand.in_scan_region else (0, 165, 255)
        if in_flight:
            # Brighten the outline while decoding so activity is visible even
            # when confidence does not change between frames.
            base = tuple(min(255, int(c * (1.0 + 0.25 * _pulse(phase, self.reduced_motion)))) for c in base)

        cv2.rectangle(canvas, (rect2[0], rect2[1]), (rect2[2], rect2[3]), base, thickness, cv2.LINE_AA)

        label = f"{cand.class_name or 'barcode'} {cand.confidence:.0%}"
        if cand.decoded_value:
            label += f"  |  {cand.decoded_value}"

        scale = 0.5
        (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
        ly = dy - th - baseline - 6
        if ly < 0:
            ly = dy + th + 6
        lx = max(0, min(dx, canvas.shape[1] - tw - 8))
        _alpha_rect(canvas, (lx, ly - th - 4, lx + tw + 8, ly + baseline), base, 0.80)
        _text(canvas, label, (lx + 4, ly), (255, 255, 255), scale, 1)

    def _draw_status(self, canvas: np.ndarray, outcome: ScanOutcome) -> None:
        """Status pill along the bottom. Mirrors the state machine's wording."""
        palette = {
            ScanState.SEARCHING: (200, 200, 200),
            ScanState.BARCODE_DETECTED: (0, 165, 255),
            ScanState.DECODING: (0, 215, 255),
            ScanState.VERIFIED: (60, 220, 60),
            ScanState.UNKNOWN_BARCODE: (60, 60, 230),
            ScanState.ERROR: (40, 40, 230),
        }
        color = palette.get(outcome.state, (200, 200, 200))
        text = outcome.message
        scale = 0.55
        (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
        pad = 12
        bar_h = th + baseline + pad * 2
        y1 = canvas.shape[0] - bar_h
        _alpha_rect(canvas, (0, y1, canvas.shape[1], canvas.shape[0]), (0, 0, 0), 0.55)
        _alpha_rect(canvas, (0, y1, 6, canvas.shape[0]), color, 1.0)
        _text(canvas, text, (pad + 8, y1 + pad + th), color, scale, 1)

    # -- entry point --------------------------------------------------------

    def render(
        self,
        frame: np.ndarray,
        outcome: Optional[ScanOutcome] = None,
        target_size: Optional[Tuple[int, int]] = None,
        progress: float = 0.0,
    ) -> np.ndarray:
        """Compose the preview canvas. ``frame`` is never modified.

        The overlay follows the pipeline state rather than running as decoration:

        ==================  =========================================================
        state               presentation
        ==================  =========================================================
        SEARCHING           nothing; the status bar reads "Searching..."
        BARCODE_DETECTED    pulsing amber box
        DECODING            pulsing amber box
        VERIFIED            steady green box
        UNKNOWN_BARCODE     steady amber box
        ERROR               steady red box
        ==================  =========================================================

        The scan guide, its sweeping line and the floating hint text were all
        removed: the preview is the camera image, the detection boxes and the
        status bar along the bottom.
        """
        if frame is None or frame.size == 0:
            raise ValueError("Cannot render an empty frame")

        fh, fw = frame.shape[:2]
        if target_size is None:
            target_size = (fw, fh)
        tw, th = letterboxed_size((fw, fh), target_size)

        # The camera pixels are mirrored for the selfie view; the transform below
        # mirrors drawn positions to match. Text is drawn *after* this, so
        # labels and the status bar stay readable.
        source = cv2.flip(frame, 1) if self.mirrored else frame

        # The canvas keeps the frame's aspect ratio (letterboxed_size above), so
        # the single uniform scale in DisplayTransform is exact. cv2.resize
        # returns a new array, leaving the inference frame untouched.
        canvas = cv2.resize(source, (tw, th), interpolation=cv2.INTER_AREA)

        transform = DisplayTransform(
            frame_size=(fw, fh), display_size=(tw, th), mirrored=self.mirrored
        )

        state = outcome.state if outcome is not None else ScanState.SEARCHING

        if outcome is not None:
            for cand in outcome.candidates:
                self._draw_detection(canvas, transform, cand, state=state, phase=progress)

        if outcome is not None:
            self._draw_status(canvas, outcome)

        return canvas
