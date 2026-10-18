"""Temporal smoothing for the on-screen detection box.

Kept free of Qt and of OpenCV so it can be tested directly, and so the same
logic would work for any front end.

Three behaviours the scanner needs, none of which the raw YOLO output provides:

* **Smoothing.** A detection box jitters by a few pixels per frame even when
  the card is perfectly still. The raw box is therefore unusable as a visual.
  Positions are smoothed exponentially, which removes the jitter while still
  tracking a card that is genuinely moving.
* **Grace period.** Dropping one frame is not the same as the barcode being
  gone. Removing the box immediately makes the overlay strobe. The last known
  box is held briefly after the detection disappears.
* **Honesty.** When there is genuinely nothing to show, the smoother returns an
  empty list. It never invents a box, never keeps one forever, and is told about
  reduced-motion preferences by the caller rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Optional, Sequence, Tuple

from services.scan_pipeline import BarcodeCandidate

Rect = Tuple[int, int, int, int]


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


@dataclass
class _Tracked:
    """One box being followed across frames."""

    bbox: Rect
    confidence: float
    class_name: str
    in_scan_region: bool
    overlap: float
    last_seen: float


class DetectionSmoother:
    """Track detection boxes over time and emit a stable box to draw.

    ``alpha`` is the smoothing weight for the *new* observation: 1.0 follows the
    detection exactly (no smoothing), 0.0 never moves. The default in
    ``settings.SCAN_BOX_SMOOTHING`` sits in between.
    """

    def __init__(
        self,
        alpha: float = 0.45,
        grace_seconds: float = 0.6,
        clock=None,
    ) -> None:
        self.alpha = min(1.0, max(0.0, float(alpha)))
        self.grace_seconds = max(0.0, float(grace_seconds))
        self._clock = clock or (lambda: 0.0)
        self._tracked: List[_Tracked] = []
        self._last_update: Optional[float] = None

    def reset(self) -> None:
        """Forget everything. Used when the scanner stops or resets."""
        self._tracked = []
        self._last_update = None

    @property
    def is_empty(self) -> bool:
        return not self._tracked

    def _match(self, bbox: Rect) -> Optional[_Tracked]:
        """Nearest tracked box whose centre is close enough to be the same one.

        Matching by centre distance rather than by index keeps the tracking
        stable when detections arrive in a different order frame to frame, which
        they do -- YOLO does not guarantee ordering.
        """
        if not self._tracked:
            return None
        cx = bbox[0] + bbox[2] / 2.0
        cy = bbox[1] + bbox[3] / 2.0

        best: Optional[_Tracked] = None
        best_distance = float("inf")
        for tracked in self._tracked:
            tcx = tracked.bbox[0] + tracked.bbox[2] / 2.0
            tcy = tracked.bbox[1] + tracked.bbox[3] / 2.0
            distance = ((tcx - cx) ** 2 + (tcy - cy) ** 2) ** 0.5
            if distance < best_distance:
                best, best_distance = tracked, distance
        if best is None:
            return None
        # Allow a jump of up to one box diagonal; anything further is a
        # different object and must not inherit the old position.
        limit = max(24.0, ((bbox[2] ** 2 + bbox[3] ** 2) ** 0.5))
        return best if best_distance <= limit else None

    def _smooth(self, previous: Rect, current: Rect) -> Rect:
        a = self.alpha
        return tuple(  # type: ignore[return-value]
            int(round(_lerp(previous[i], current[i], a))) for i in range(4)
        )

    def update(
        self,
        candidates: Optional[Sequence[BarcodeCandidate]],
        now: Optional[float] = None,
    ) -> List[BarcodeCandidate]:
        """Feed this frame's candidates, get back the boxes that should be drawn.

        Returns lightweight copies; the pipeline's own objects are never mutated,
        so a stalled preview cannot corrupt detection state.
        """
        timestamp = self._clock() if now is None else float(now)
        incoming = list(candidates or [])
        seen = set()

        for cand in incoming:
            key = id(cand)
            bbox = tuple(int(v) for v in cand.bbox)
            tracked = self._match(bbox)
            if tracked is None:
                self._tracked.append(
                    _Tracked(
                        bbox=bbox,
                        confidence=float(cand.confidence),
                        class_name=cand.class_name,
                        in_scan_region=bool(cand.in_scan_region),
                        overlap=float(getattr(cand, "overlap", 0.0) or 0.0),
                        last_seen=timestamp,
                    )
                )
            else:
                tracked.bbox = self._smooth(tracked.bbox, bbox)
                tracked.confidence = float(cand.confidence)
                tracked.class_name = cand.class_name
                tracked.in_scan_region = bool(cand.in_scan_region)
                tracked.overlap = float(getattr(cand, "overlap", 0.0) or 0.0)
                tracked.last_seen = timestamp
            seen.add(key)

        # Grace period: keep boxes that were seen recently, drop the rest.
        alive: List[_Tracked] = []
        drawn: List[Tuple[_Tracked, BarcodeCandidate]] = []
        for tracked in self._tracked:
            fresh = (timestamp - tracked.last_seen) <= self.grace_seconds
            if fresh:
                alive.append(tracked)
                source = next((c for c in incoming if self._match(c.bbox) is tracked), None)
                drawn.append((tracked, source))
        self._tracked = alive

        out: List[BarcodeCandidate] = []
        for tracked, source in drawn:
            if source is None:
                # Held by the grace period: there is no live candidate to copy,
                # so build the minimal stand-in the overlay needs.
                out.append(
                    BarcodeCandidate(
                        bbox=tracked.bbox,
                        confidence=tracked.confidence,
                        class_name=tracked.class_name,
                        overlap=tracked.overlap,
                        in_scan_region=tracked.in_scan_region,
                    )
                )
            else:
                out.append(replace(source, bbox=tracked.bbox))

        self._last_update = timestamp
        return out
