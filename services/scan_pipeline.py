"""Real-time scan pipeline: YOLO detection, ROI gating, decoding, verification.

The stages are deliberately separate and each one is allowed to fail on its own:

1. :class:`~services.yolo_detector.YOLODetectorService` *locates* candidate
   barcode regions. It says nothing about what is encoded in them.
2. The scan-region gate decides whether a located region is presented to the
   decoder at all.
3. :class:`~services.barcode_decoder.BarcodeDecoderService` reads the payload out
   of a crop.
4. The repository confirms the payload against a real student row.

A YOLO hit is never sufficient to report a student. Verification requires a
successful decode *and* a matching database record.

This module holds no Qt and no threads; it is driven by whatever loop calls
:meth:`ScanPipeline.process_frame`, which is what makes it directly testable
offline against the checked-in ID card assets.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional, Tuple

import numpy as np

from config.settings import settings
from services.scan_geometry import (
    Rect,
    ScanRegion,
    Size,
    clamp_rect,
    expand_rect,
    is_crop_usable,
    overlap_ratio,
)

logger = logging.getLogger(__name__)


class ScanState(str, Enum):
    """States of the scan state machine."""

    SEARCHING = "searching"
    BARCODE_DETECTED = "barcode_detected"
    DECODING = "decoding"
    VERIFIED = "verified"
    UNKNOWN_BARCODE = "unknown_barcode"
    #: One read returned two different payloads (two cards in frame). Neither is
    #: verified: choosing between them would be arbitrary, and both values map to
    #: real students, so a wrong choice attributes a real card to the wrong
    #: person.
    AMBIGUOUS = "ambiguous"
    ERROR = "error"


#: User-facing text per state. Kept next to the enum so a new state cannot be
#: added without deciding what the user is told.
#:
#: The gate and the drawn yellow guide are no longer the same rectangle (see
#: ``SCAN_GATE_*``), so the "inside the yellow frame" wording became actively
#: misleading: a card aimed squarely at the yellow frame can still be refused,
#: and telling the operator to move into the frame would not help.
STATE_MESSAGES: dict[ScanState, str] = {
    ScanState.SEARCHING: "Searching for barcode...",
    ScanState.BARCODE_DETECTED: "Barcode found at the edge of view - move the card further into frame.",
    ScanState.DECODING: "Reading barcode...",
    ScanState.VERIFIED: "Student verified",
    ScanState.UNKNOWN_BARCODE: "Unknown barcode - no matching student record.",
    ScanState.AMBIGUOUS: (
        "More than one barcode in frame - present a single card."
    ),
    ScanState.ERROR: "Scanner error",
}


class StudentVerifier:
    """Indirection over the student lookup so the pipeline stays testable.

    Wraps ``StudentService`` but is constructed lazily: importing or building
    the service touches the database pool, which would make every offline test
    of the geometry and gating logic depend on a live MySQL server.
    """

    def __init__(self, lookup: Optional[Callable[[str], object]] = None):
        self._lookup = lookup
        self._service = None

    def _resolve_lookup(self) -> Callable[[str], object]:
        if self._lookup is not None:
            return self._lookup
        if self._service is None:
            from services.student_service import StudentService

            self._service = StudentService()
        self._lookup = self._service.get_student_by_barcode
        return self._lookup

    def verify(self, payload: str):
        """Return the student for ``payload``, or ``None``.

        Raises ``DatabaseUnavailableError`` rather than swallowing it: a lookup
        that could not run must surface as :attr:`ScanState.ERROR` and must not
        be reported as "unknown barcode".
        """
        if not payload:
            return None
        return self._resolve_lookup()(payload)


@dataclass
class RoiRead:
    """One decode of one region, including every payload it produced.

    ``values`` is what makes an ambiguous read representable. The pipeline used
    to receive ``value`` alone and had no way to know a second, different symbol
    had also been read from the same pixels.
    """

    value: Optional[str] = None
    type: Optional[str] = None
    method: Optional[str] = None
    attempts: int = 0
    values: List[str] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        return len(self.values) > 1

    def as_tuple(self) -> tuple:
        """The legacy 4-tuple, kept for callers that only need one value."""
        return (self.value, self.type, self.method, self.attempts)


@dataclass
class BarcodeCandidate:
    """One located barcode and how far it got through the pipeline."""

    bbox: Rect
    confidence: float
    class_name: str
    overlap: float = 0.0
    in_scan_region: bool = False
    crop: Optional[Rect] = None
    decoded_value: Optional[str] = None
    decoded_type: Optional[str] = None
    #: Which decode stage produced the value ("original", "clahe", "otsu", ...).
    #: Surfaced so the UI can be honest about how the read was achieved and so
    #: the robustness report can say which filter actually helped.
    decode_method: Optional[str] = None
    #: How many attempts were spent. 1 means the untouched crop was readable.
    decode_attempts: int = 0
    #: Every distinct payload this crop produced. More than one entry means the
    #: crop contains more than one symbol and the read is ambiguous.
    decoded_values: List[str] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        """True when this crop decoded to more than one distinct payload."""
        return len(self.decoded_values) > 1

    @property
    def usable_crop(self) -> bool:
        return self.crop is not None and is_crop_usable(
            self.crop,
            settings.SCAN_MIN_CROP_WIDTH,
            settings.SCAN_MIN_CROP_HEIGHT,
        )


@dataclass
class ScanOutcome:
    """Everything the UI needs for one processed frame."""

    state: ScanState
    message: str
    candidates: List[BarcodeCandidate] = field(default_factory=list)
    scan_region: Optional[Rect] = None
    decoded_value: Optional[str] = None
    decoded_type: Optional[str] = None
    student: object = None
    error: Optional[str] = None
    #: True only on the frame a verification completes. The UI opens the profile
    #: popup on this flag alone, which is what prevents duplicate popups.
    verified_now: bool = False
    #: The conflicting payloads behind an :attr:`ScanState.AMBIGUOUS` outcome.
    #: Empty on every other state, so a caller can test it unconditionally.
    ambiguous_values: List[str] = field(default_factory=list)

    @property
    def in_scan_region(self) -> bool:
        return any(c.in_scan_region for c in self.candidates)

    @property
    def primary_candidate(self) -> Optional[BarcodeCandidate]:
        """Best candidate: highest confidence among those inside the guide."""
        inside = [c for c in self.candidates if c.in_scan_region]
        pool = inside or self.candidates
        if not pool:
            return None
        return max(pool, key=lambda c: c.confidence)


class ScanPipeline:
    """Drives detection -> gating -> decoding -> verification for live frames."""

    def __init__(
        self,
        detector=None,
        decoder=None,
        verifier: Optional[StudentVerifier] = None,
        overlap_threshold: Optional[float] = None,
        cooldown_seconds: Optional[float] = None,
        confirm_frames: Optional[int] = None,
        barcode_class_filter: Optional[List[str]] = None,
        max_decode_attempts: Optional[int] = None,
        decode_rotations: Optional[bool] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.detector = detector
        self.decoder = decoder
        self.verifier = verifier or StudentVerifier()
        self.overlap_threshold = (
            settings.SCAN_REGION_OVERLAP_THRESHOLD
            if overlap_threshold is None
            else overlap_threshold
        )
        self.cooldown_seconds = (
            settings.SCAN_COOLDOWN_SECONDS if cooldown_seconds is None else cooldown_seconds
        )
        self.confirm_frames = (
            settings.SCAN_CONFIRM_FRAMES if confirm_frames is None else confirm_frames
        )
        # Empty means "accept every class the model reports"; the detector's
        # real class names drive the filter so a renamed model still works.
        self.barcode_class_filter = (
            list(barcode_class_filter)
            if barcode_class_filter is not None
            else []
        )
        # Decode work per frame is bounded so the capture loop keeps its frame
        # rate. None means "use the configured cap".
        self.max_decode_attempts = (
            settings.SCAN_MAX_DECODE_ATTEMPTS if max_decode_attempts is None
            else max_decode_attempts
        )
        # Off by default and measured to be so: see ERROR_TRACE_REPORT section
        # 12. zbar already deskews up to ~11 degrees and reads 90/180 degree
        # presentations natively, while these rotations recovered nothing at
        # 20-60 degrees. They cost up to six extra zbar passes per frame.
        self.decode_rotations = (
            bool(settings.SCAN_DECODE_ROTATIONS) if decode_rotations is None
            else bool(decode_rotations)
        )
        self._clock = clock

        self.frame_fallback = bool(settings.SCAN_FRAME_DECODE_FALLBACK)
        self.frame_fallback_after = settings.SCAN_FRAME_FALLBACK_AFTER_SECONDS
        self.frame_fallback_interval = settings.SCAN_FRAME_FALLBACK_INTERVAL_SECONDS
        self.frame_fallback_attempts = settings.SCAN_FRAME_FALLBACK_ATTEMPTS
        self._last_detection_at: float = 0.0
        self._last_frame_decode_at: float = -1e9
        self._started_at: float = self._clock()
        # Boxes overlapping an already-considered box by at least this much are
        # treated as the same physical symbol, so overlapping duplicate
        # detections are not all sent to the decoder.
        self.candidate_overlap_limit: float = settings.SCAN_CANDIDATE_OVERLAP_LIMIT

        self.state = ScanState.SEARCHING
        self._cooldown_until = 0.0
        self._pending_value: Optional[str] = None
        self._pending_count = 0
        self._last_verified_value: Optional[str] = None

    # -- helpers ------------------------------------------------------------

    def reset(self) -> None:
        """Clear cooldown and pending-decoder state (scanner start/stop)."""
        self.state = ScanState.SEARCHING
        self._cooldown_until = 0.0
        self._pending_value = None
        self._pending_count = 0
        self._last_verified_value = None
        self._started_at = self._clock()
        self._last_frame_decode_at = -1e9

    @property
    def in_cooldown(self) -> bool:
        return self._clock() < self._cooldown_until

    def _message(self, state: ScanState, override: Optional[str] = None) -> str:
        return override or STATE_MESSAGES.get(state, "")

    def _is_barcode_class(self, class_name: str) -> bool:
        if not self.barcode_class_filter:
            return True
        lowered = class_name.lower()
        return any(token.lower() in lowered for token in self.barcode_class_filter)

    def scan_region_for(self, frame_size: Size) -> ScanRegion:
        """The rectangle a detection must substantially occupy to be decoded.

        Deliberately not the same rectangle the UI draws as an aiming guide.
        The guide is sized for the operator's hand; this is sized for where the
        barcode actually ends up. See ``SCAN_GATE_*`` in ``config/settings.py``
        for the measurement that forced them apart.
        """
        return ScanRegion.for_frame(
            frame_size,
            settings.SCAN_GATE_WIDTH_RATIO,
            settings.SCAN_GATE_HEIGHT_RATIO,
        )

    # -- stage 1: locate -----------------------------------------------------

    def _build_candidates(self, frame: np.ndarray, region: ScanRegion) -> List[BarcodeCandidate]:
        if self.detector is None:
            return []
        frame_h, frame_w = frame.shape[:2]
        frame_size = (frame_w, frame_h)

        candidates: List[BarcodeCandidate] = []
        for det in self.detector.detect(frame) or []:
            if not self._is_barcode_class(getattr(det, "class_name", "")):
                continue
            box = clamp_rect(tuple(det.bbox), frame_size)
            overlap = overlap_ratio(box, region.rect)
            in_region = overlap >= self.overlap_threshold
            candidates.append(
                BarcodeCandidate(
                    bbox=box,
                    confidence=float(getattr(det, "confidence", 0.0)),
                    class_name=getattr(det, "class_name", ""),
                    overlap=overlap,
                    in_scan_region=in_region,
                    crop=expand_rect(box, settings.SCAN_CROP_MARGIN_RATIO, frame_size),
                )
            )
        # Stable ordering so the UI does not flicker between equally good boxes.
        candidates.sort(key=lambda c: (c.in_scan_region, c.confidence), reverse=True)
        return candidates

    # -- stage 2: gate + decode ---------------------------------------------

    def _decode_roi_read(self, roi: np.ndarray, attempts_cap: Optional[int]) -> Optional[RoiRead]:
        """Run a decoder over one ROI and normalise the result.

        Prefers the ordered :meth:`decode_best` strategy when the decoder
        provides it, and falls back to ``decode_with_preprocessing`` for any
        decoder that only offers the older all-variants API. The pipeline must
        not require one particular method: it keeps test doubles and
        alternative decoders working, and it degrades to a correct (if slower)
        path instead of raising.

        Every payload the decode produced is carried through, not just the first.
        """
        best = getattr(self.decoder, "decode_best", None)
        if callable(best):
            result = best(
                roi,
                rotations=self.decode_rotations,
                max_attempts=attempts_cap,
            )
            if not getattr(result, "ok", False):
                return None

            # `decode_best` may report several distinct payloads from one image;
            # a decoder that cannot be asked for them is treated as single-valued
            # rather than guessed at.
            values = list(getattr(result, "distinct_values", None) or [])
            if not values and getattr(result, "data", None):
                values = [result.data]
            return RoiRead(
                value=getattr(result, "data", None),
                type=getattr(result, "type", None),
                method=getattr(result, "attempt", None),
                attempts=int(getattr(result, "attempts_tried", 0) or 0),
                values=values,
            )

        legacy = self.decoder.decode_with_preprocessing(roi)
        items = list(legacy or [])
        if not items:
            return None
        values = []
        for item in items:
            if item.data not in values:
                values.append(item.data)
        return RoiRead(
            value=items[0].data,
            type=items[0].type,
            method="decode_with_preprocessing",
            attempts=0,
            values=values,
        )

    def _decode_roi(self, roi: np.ndarray, attempts_cap: Optional[int]) -> Optional[tuple]:
        """Single-valued decode of one ROI, for callers that only need a value.

        Kept as the legacy 4-tuple contract; internally the pipeline uses
        :meth:`_decode_roi_read` so a read with conflicting payloads is visible.
        """
        read = self._decode_roi_read(roi, attempts_cap)
        return read.as_tuple() if read else None

    def _try_decode_read(self, frame: np.ndarray, rect: Rect) -> Optional[RoiRead]:
        """Decode one rectangle and return every payload it produced.

        Uses the ordered, bounded strategy: the untouched crop first, then a
        short list of targeted variants, stopping at the first that reads. The
        previous implementation ran *every* variant on *every* frame, which is
        why a frame cost ~520 ms of decoding alone.
        """
        if self.decoder is None:
            return None
        x, y, w, h = clamp_rect(rect, (frame.shape[1], frame.shape[0]))
        roi = frame[y : y + h, x : x + w]
        if roi.size == 0:
            return None
        try:
            return self._decode_roi_read(roi, self.max_decode_attempts)
        except Exception:
            logger.exception("Decoder raised on a crop")
            return None

    def _try_decode(self, frame: np.ndarray, rect: Rect):
        """Decode one rectangle; return ``(value, type, method, attempts)`` or ``None``."""
        read = self._try_decode_read(frame, rect)
        return read.as_tuple() if read else None

    @staticmethod
    def _is_duplicate_of(a: Rect, b: Rect, iou_limit: float) -> bool:
        """True when two boxes describe the same physical symbol.

        A detector that fires several overlapping boxes on one barcode would
        otherwise have every fragment sent to the decoder: wasted work, and a
        chance that a fragment crop wins the race and fails to decode.
        """
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        ix1, iy1 = max(ax, bx), max(ay, by)
        ix2 = min(ax + aw, bx + bw)
        iy2 = min(ay + ah, by + bh)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        union = aw * ah + bw * bh - inter
        return union > 0 and (inter / union) >= iou_limit

    def _decode_candidates(
        self,
        frame: np.ndarray,
        candidates: List[BarcodeCandidate],
        region: Rect,
    ) -> List[BarcodeCandidate]:
        """Decode only candidates that already passed the scan-region gate.

        Two attempts are made, both confined to the yellow guide so the gating
        rule is never bypassed:

        1. the detection box plus margin;
        2. failing that, the guide itself.

        The fallback exists because a confident-looking YOLO box can still be
        noticeably narrower than the real symbol, which silently truncates the
        bars and makes an otherwise readable barcode undecodable. The guide is a
        safe second attempt precisely because a barcode inside it is the one the
        user is deliberately presenting.
        """
        eligible = [c for c in candidates if c.in_scan_region and c.usable_crop]
        # Keep only the strongest box for any one physical symbol. Confidence
        # ordering comes from _build_candidates.
        inside: List[BarcodeCandidate] = []
        for cand in eligible:
            if any(self._is_duplicate_of(cand.bbox, seen.bbox, self.candidate_overlap_limit)
                   for seen in inside):
                continue
            inside.append(cand)

        for cand in inside:
            read = self._try_decode_read(frame, cand.crop)
            if read:
                cand.decoded_value = read.value
                cand.decoded_type = read.type
                cand.decode_method = read.method
                cand.decode_attempts = read.attempts
                cand.decoded_values = list(read.values)
                break

        if inside and not any(c.decoded_value for c in inside):
            read = self._try_decode_read(frame, region)
            if read:
                # Attribute the read to the best candidate so the UI can still
                # highlight a box; the value genuinely came from the wider area.
                best = max(inside, key=lambda c: c.confidence)
                best.decoded_value = read.value
                best.decoded_type = read.type
                best.decode_method = read.method
                best.decode_attempts = read.attempts
                best.decoded_values = list(read.values)
                best.crop = clamp_rect(
                    expand_rect(region, settings.SCAN_CROP_MARGIN_RATIO,
                                (frame.shape[1], frame.shape[0])),
                    (frame.shape[1], frame.shape[0]),
                )
                logger.info(
                    "Decoded from the scan region after the detection crop failed"
                )
        return candidates

    # -- stage 3: verify ----------------------------------------------------

    def _confirm(self, value: str) -> bool:
        """Require ``confirm_frames`` consecutive frames agreeing on ``value``."""
        needed = max(1, int(self.confirm_frames))
        if value == self._pending_value:
            self._pending_count += 1
        else:
            self._pending_value = value
            self._pending_count = 1
        return self._pending_count >= needed

    def _verify_value(self, value: str, candidates: List[BarcodeCandidate], region: Rect):
        """Look the payload up. Returns ``(student, error)``."""
        from database.connection import DatabaseUnavailableError

        try:
            return self.verifier.verify(value), None
        except DatabaseUnavailableError as exc:
            logger.error("Verification unavailable: %s", exc)
            return None, str(exc)
        except Exception:
            logger.exception("Verification lookup failed for payload")
            return None, "Lookup failed"

    # -- main entry point ---------------------------------------------------

    def _frame_decode_fallback(self, frame: np.ndarray) -> Optional[RoiRead]:
        """Last-resort decode of the whole frame when YOLO sees nothing.

        Justified by measurement, not by optimism: a card rotated beyond what
        the detector tolerates (+/-13 deg, or held sideways) still yields its
        payload when the untouched frame is handed to zbar, and across eight
        document variants -- including the dimmed ones that make the detector
        fire at 0.97 confidence -- the decoder returned nothing every time.

        A false *student* still requires a decoded value plus a database row, so
        this cannot manufacture a match; it can only turn a miss into a read.

        Guarded three ways so it cannot dominate the frame budget: it only runs
        after a sustained period without a detection, it is rate limited, and it
        gets a small attempt cap.
        """
        if self.decoder is None:
            return None
        now = self._clock()
        quiet_for = now - max(self._last_detection_at, self._started_at)
        if quiet_for < self.frame_fallback_after:
            return None
        if (now - self._last_frame_decode_at) < self.frame_fallback_interval:
            return None

        self._last_frame_decode_at = now
        try:
            return self._decode_roi_read(frame, self.frame_fallback_attempts)
        except Exception:
            logger.exception("Frame-level decode fallback raised")
            return None

    def process_frame(self, frame: np.ndarray) -> ScanOutcome:
        """Run one full pass over a frame. Never raises for expected failures."""
        if frame is None or getattr(frame, "size", 0) == 0:
            return ScanOutcome(state=ScanState.ERROR, message="Empty frame received")

        try:
            frame_h, frame_w = frame.shape[:2]
            region = self.scan_region_for((frame_w, frame_h))

            candidates = self._build_candidates(frame, region)

            if candidates:
                self._last_detection_at = self._clock()

            if not candidates:
                # No detection. Before giving up, try the whole frame once: a
                # badly tilted or sideways card is decodable even when the
                # detector cannot see it (measured, see _frame_decode_fallback).
                recovered = self._frame_decode_fallback(frame) if self.frame_fallback else None
                if recovered:
                    value, sym_type, method, attempts = recovered.as_tuple()
                    if recovered.ambiguous:
                        # Two different symbols were read from the same frame.
                        # Neither is verified: picking one would be a coin toss
                        # between two real students.
                        self.state = ScanState.AMBIGUOUS
                        return ScanOutcome(
                            state=ScanState.AMBIGUOUS,
                            message=self._message(ScanState.AMBIGUOUS),
                            scan_region=region.rect,
                            ambiguous_values=list(recovered.values),
                            error=(
                                "Conflicting barcodes in frame: "
                                + ", ".join(recovered.values)
                            ),
                        )
                    self._pending_value = None
                    self._pending_count = 0
                    # This branch has no gate to hide behind, so it carries its
                    # own duplicate guard: a card YOLO cannot see would otherwise
                    # be re-decoded, re-looked-up and re-recorded every 1.5 s for
                    # as long as it stayed in front of the camera.
                    if self.in_cooldown and value == self._last_verified_value:
                        self.state = ScanState.VERIFIED
                        return ScanOutcome(
                            state=ScanState.VERIFIED,
                            message=self._message(ScanState.VERIFIED),
                            scan_region=region.rect,
                            decoded_value=value,
                            decoded_type=sym_type,
                            verified_now=False,
                        )
                    student, error = self._verify_value(value, [], region.rect)
                    if error:
                        self.state = ScanState.ERROR
                        return ScanOutcome(
                            state=ScanState.ERROR,
                            message="Database unavailable - cannot verify student.",
                            scan_region=region.rect,
                            decoded_value=value,
                            decoded_type=sym_type,
                            error=error,
                        )
                    if student is None:
                        self.state = ScanState.UNKNOWN_BARCODE
                        return ScanOutcome(
                            state=ScanState.UNKNOWN_BARCODE,
                            message=self._message(ScanState.UNKNOWN_BARCODE),
                            scan_region=region.rect,
                            decoded_value=value,
                            decoded_type=sym_type,
                        )
                    self.state = ScanState.VERIFIED
                    self._cooldown_until = self._clock() + self.cooldown_seconds
                    self._last_verified_value = value
                    candidate = BarcodeCandidate(
                        bbox=(0, 0, frame.shape[1], frame.shape[0]),
                        confidence=0.0,
                        class_name="barcode",
                        in_scan_region=False,
                        decoded_value=value,
                        decoded_type=sym_type,
                        decode_method=method,
                        decode_attempts=attempts,
                    )
                    return ScanOutcome(
                        state=ScanState.VERIFIED,
                        message=self._message(ScanState.VERIFIED),
                        candidates=[candidate],
                        scan_region=region.rect,
                        decoded_value=value,
                        decoded_type=sym_type,
                        student=student,
                        verified_now=True,
                    )
                # Forget any half-finished decode confirmation.
                self._pending_value = None
                self._pending_count = 0
                self.state = ScanState.SEARCHING
                return ScanOutcome(
                    state=ScanState.SEARCHING,
                    message=self._message(ScanState.SEARCHING),
                    scan_region=region.rect,
                )

            if not any(c.in_scan_region for c in candidates):
                self._pending_value = None
                self._pending_count = 0
                self.state = ScanState.BARCODE_DETECTED
                return ScanOutcome(
                    state=ScanState.BARCODE_DETECTED,
                    message=self._message(ScanState.BARCODE_DETECTED),
                    candidates=candidates,
                    scan_region=region.rect,
                )

            # Gate passed. Nothing outside the guide was ever sent to the decoder.
            candidates = self._decode_candidates(frame, candidates, region.rect)
            decoded = [c for c in candidates if c.decoded_value]

            if not decoded:
                self._pending_value = None
                self._pending_count = 0
                self.state = ScanState.BARCODE_DETECTED
                return ScanOutcome(
                    state=ScanState.BARCODE_DETECTED,
                    message="Barcode detected - unable to decode. "
                    "Adjust position or lighting.",
                    candidates=candidates,
                    scan_region=region.rect,
                )

            # A crop that produced more than one distinct payload is not a read,
            # it is a disagreement: two symbols in the same pixels. Report it
            # rather than choosing, because each value maps to a real student.
            conflicting = [c for c in decoded if c.ambiguous]
            if conflicting:
                values = list(conflicting[0].decoded_values)
                self._pending_value = None
                self._pending_count = 0
                self.state = ScanState.AMBIGUOUS
                return ScanOutcome(
                    state=ScanState.AMBIGUOUS,
                    message=self._message(ScanState.AMBIGUOUS),
                    candidates=candidates,
                    scan_region=region.rect,
                    ambiguous_values=values,
                    error="Conflicting barcodes in frame: " + ", ".join(values),
                )

            best = max(decoded, key=lambda c: c.confidence)
            value = best.decoded_value

            if not self._confirm(value):
                self.state = ScanState.DECODING
                return ScanOutcome(
                    state=ScanState.DECODING,
                    message=self._message(ScanState.DECODING),
                    candidates=candidates,
                    scan_region=region.rect,
                    decoded_value=value,
                    decoded_type=best.decoded_type,
                )

            # Repeat of the value we just verified: treat as the same card still
            # in frame rather than a new scan.
            if self.in_cooldown and value == self._last_verified_value:
                self.state = ScanState.VERIFIED
                return ScanOutcome(
                    state=ScanState.VERIFIED,
                    message=self._message(ScanState.VERIFIED),
                    candidates=candidates,
                    scan_region=region.rect,
                    decoded_value=value,
                    decoded_type=best.decoded_type,
                    verified_now=False,
                )

            student, error = self._verify_value(value, candidates, region.rect)
            if error:
                self.state = ScanState.ERROR
                return ScanOutcome(
                    state=ScanState.ERROR,
                    message="Database unavailable - cannot verify student.",
                    candidates=candidates,
                    scan_region=region.rect,
                    decoded_value=value,
                    decoded_type=best.decoded_type,
                    error=error,
                )

            if student is None:
                self._pending_value = None
                self._pending_count = 0
                self.state = ScanState.UNKNOWN_BARCODE
                return ScanOutcome(
                    state=ScanState.UNKNOWN_BARCODE,
                    message=self._message(ScanState.UNKNOWN_BARCODE),
                    candidates=candidates,
                    scan_region=region.rect,
                    decoded_value=value,
                    decoded_type=best.decoded_type,
                )

            self.state = ScanState.VERIFIED
            self._cooldown_until = self._clock() + self.cooldown_seconds
            self._last_verified_value = value
            self._pending_value = None
            self._pending_count = 0
            return ScanOutcome(
                state=ScanState.VERIFIED,
                message=self._message(ScanState.VERIFIED),
                candidates=candidates,
                scan_region=region.rect,
                decoded_value=value,
                decoded_type=best.decoded_type,
                student=student,
                verified_now=True,
            )
        except Exception as exc:  # keep the capture loop alive
            logger.exception("Scan pipeline frame failed")
            return ScanOutcome(
                state=ScanState.ERROR, message="Scanner error", error=str(exc)
            )
