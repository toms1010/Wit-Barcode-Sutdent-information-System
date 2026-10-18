"""Post-scan session state machine.

The capture pipeline already refuses to raise ``verified_now`` twice inside its
cooldown, but a cooldown is a timer: it says nothing about *what* is in frame.
A card that stays in front of the camera for ten seconds therefore produces a
fresh verification, a fresh database lookup and a fresh history row every time
the cooldown expires -- and a second modal can grab focus from the operator.

:class:`ScanSession` is the second, content-aware guard. It tracks

* the current :class:`ScanPhase` (``Ready -> Detecting -> Decoding ->
  Verifying -> Success -> Ready``),
* the last payload it *processed successfully*, and
* when that payload was processed.

A payload is accepted at most once while it stays continuously visible. It
becomes eligible again when the barcode leaves the frame (the pipeline reports
``SEARCHING``), when a different payload is decoded, or when the operator
explicitly starts a new scan session.

This module deliberately holds no Qt and no database access, so the transition
rules can be exercised without a camera, a GUI or a MySQL server.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from services.scan_pipeline import ScanState


class ScanPhase(str, Enum):
    """States of the operator-facing scan session."""

    READY = "ready"
    DETECTING = "detecting"
    DECODING = "decoding"
    VERIFYING = "verifying"
    SUCCESS = "success"


#: Human readable text per phase, kept next to the enum so a new phase cannot be
#: added without deciding what the operator is told.
PHASE_LABELS: dict[ScanPhase, str] = {
    ScanPhase.READY: "Ready",
    ScanPhase.DETECTING: "Detecting",
    ScanPhase.DECODING: "Decoding",
    ScanPhase.VERIFYING: "Verifying",
    ScanPhase.SUCCESS: "Success",
}

#: Why a confirmed scan was not turned into a history entry / modal / beep.
class RejectReason(str, Enum):
    #: The payload is the one already being displayed.
    DUPLICATE_VISIBLE = "duplicate_visible"
    #: A different payload was accepted too recently.
    DEBOUNCED = "debounced"
    #: The pipeline reported a known barcode but no student was attached.
    NOT_VERIFIED = "not_verified"
    #: One read returned two different payloads, so neither was chosen.
    AMBIGUOUS_READ = "ambiguous_read"


@dataclass(frozen=True)
class ScanDecision:
    """Outcome of feeding one pipeline result to the session."""

    accepted: bool
    phase: ScanPhase
    value: Optional[str]
    reason: str = ""

    @property
    def rejected_because(self) -> Optional[RejectReason]:
        if self.accepted or not self.reason:
            return None
        try:
            return RejectReason(self.reason)
        except ValueError:
            return None


class ScanSession:
    """Content-aware duplicate suppression for the scanner page.

    Parameters
    ----------
    debounce_seconds:
        Minimum gap between two *accepted* payloads. Two different barcodes
        presented back to back are therefore spaced out; a single barcode is
        held back regardless, because of the latch below.
    clock:
        Monotonic time source, injected by tests.
    """

    def __init__(
        self,
        debounce_seconds: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        from config.settings import settings

        self.debounce_seconds = (
            settings.SCAN_POST_SUCCESS_DEBOUNCE_SECONDS
            if debounce_seconds is None
            else float(debounce_seconds)
        )
        self._clock = clock
        self._phase = ScanPhase.READY
        self._latched_value: Optional[str] = None
        self._latched_at: Optional[float] = None
        self.accepted_count = 0
        self.rejected_count = 0

    # -- introspection ---------------------------------------------------

    @property
    def phase(self) -> ScanPhase:
        return self._phase

    @property
    def phase_label(self) -> str:
        return PHASE_LABELS[self._phase]

    @property
    def last_processed_value(self) -> Optional[str]:
        """The payload currently latched, i.e. the one already on screen."""
        return self._latched_value

    @property
    def is_latched(self) -> bool:
        return self._latched_value is not None

    def seconds_since_last_processed(self) -> Optional[float]:
        if self._latched_at is None:
            return None
        return self._clock() - self._latched_at

    def debounce_remaining(self) -> float:
        elapsed = self.seconds_since_last_processed()
        if elapsed is None:
            return 0.0
        return max(0.0, self.debounce_seconds - elapsed)

    # -- transitions -----------------------------------------------------

    def observe(self, state: ScanState, decoded_value: Optional[str] = None,
                confirmed: bool = False) -> ScanDecision:
        """Feed one pipeline result in and get the resulting decision.

        Parameters
        ----------
        state:
            The pipeline state for the frame.
        decoded_value:
            The payload read from the frame, if any.
        confirmed:
            ``True`` only when the pipeline actually completed a student
            verification on this frame (``ScanOutcome.verified_now``). This is
            what separates "a card is still sitting there" from "a new scan just
            happened", and it is why a scan is never reported as successful
            before both decoding and verification have succeeded.
        """
        if state == ScanState.SEARCHING:
            # Nothing is being presented, so whatever was latched has left.
            self.release()
            self._phase = ScanPhase.READY
            return ScanDecision(False, self._phase, None, "no_barcode_in_frame")

        if state == ScanState.BARCODE_DETECTED:
            self._phase = ScanPhase.DETECTING
            return ScanDecision(False, self._phase, decoded_value)

        if state == ScanState.DECODING:
            self._phase = ScanPhase.DECODING
            return ScanDecision(False, self._phase, decoded_value)

        if state == ScanState.UNKNOWN_BARCODE:
            # A readable symbol that is not a student. This is not a success, so
            # nothing is recorded, but a *different* symbol now being visible does
            # mean the previous one is gone.
            if decoded_value and decoded_value != self._latched_value:
                self.release()
            self._phase = ScanPhase.READY
            return ScanDecision(False, self._phase, decoded_value, "no_student_record")

        if state == ScanState.AMBIGUOUS:
            # Two symbols, two students, no way to tell which was meant. Nothing
            # is latched and nothing is accepted, so the operator is asked to
            # present one card rather than shown one of the two.
            self._pending_value = None
            self._phase = ScanPhase.DETECTING
            return ScanDecision(False, self._phase, None, RejectReason.AMBIGUOUS_READ.value)

        if state == ScanState.ERROR:
            # A database or camera failure must never clear the latch: doing so
            # would let a held card through the moment the connection recovers.
            self._phase = ScanPhase.READY
            return ScanDecision(False, self._phase, decoded_value, "scan_error")

        # VERIFIED
        if not confirmed or not decoded_value:
            # The pipeline is echoing a payload it already knows. Keep showing
            # the current state; do not re-record anything.
            self._phase = ScanPhase.SUCCESS if self._latched_value else ScanPhase.VERIFYING
            return ScanDecision(
                False, self._phase, decoded_value, RejectReason.NOT_VERIFIED.value
            )

        if decoded_value == self._latched_value:
            self._phase = ScanPhase.SUCCESS
            self.rejected_count += 1
            return ScanDecision(
                False, self._phase, decoded_value, RejectReason.DUPLICATE_VISIBLE.value
            )

        remaining = self.debounce_remaining()
        if remaining > 0:
            self._phase = ScanPhase.VERIFYING
            self.rejected_count += 1
            return ScanDecision(False, self._phase, decoded_value, RejectReason.DEBOUNCED.value)

        self._latched_value = decoded_value
        self._latched_at = self._clock()
        self._phase = ScanPhase.SUCCESS
        self.accepted_count += 1
        return ScanDecision(True, self._phase, decoded_value)

    def begin_verification(self, decoded_value: Optional[str] = None) -> None:
        """Mark the moment a payload is handed to the student lookup."""
        self._phase = ScanPhase.VERIFYING

    def release(self) -> None:
        """Forget the latched payload without resetting counters or debounce.

        Called when the barcode leaves the frame. The debounce clock keeps
        running, so a card swapped instantly still cannot produce two history
        entries back to back.
        """
        self._latched_value = None
        self._latched_at = None

    def start_new_session(self) -> None:
        """Operator action: forget everything and arm the scanner again.

        This is what "Scan Next Student" does. It is the only way to re-record
        the very same payload without first removing the card from the frame.
        """
        self._phase = ScanPhase.READY
        self._latched_value = None
        self._latched_at = None

    def reset(self) -> None:
        """Full reset (scanner stopped / restarted)."""
        self.start_new_session()
        self.accepted_count = 0
        self.rejected_count = 0