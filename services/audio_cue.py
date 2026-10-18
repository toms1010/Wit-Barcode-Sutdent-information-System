"""Short audible cue played after a scan has been verified.

Design constraints that shaped this module:

* The cue must fire **after** the student row has been confirmed, never per
  video frame and never for an unknown barcode. That decision belongs to the
  caller (:class:`~services.scan_session.ScanSession`); this service only knows
  how to make a sound.
* ``success_beep.mp3`` does not ship with this project. Rather than point Qt at
  a path that does not exist -- which fails silently at runtime -- the service
  resolves the asset at construction time and falls back to the platform beep
  when it is absent. The fallback is reported through :attr:`source` so the UI
  and the log can say which cue is actually in use.
* Audio is never allowed to break scanning. Every entry point swallows playback
  errors; a machine with no sound device must still complete a scan.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional

from config.settings import settings

logger = logging.getLogger(__name__)


class AudioCueService:
    """Plays a short success tone, with a toggle and a rate limit.

    Parameters
    ----------
    enabled:
        Initial state. Mirrors ``SCAN_SUCCESS_BEEP_ENABLED``.
    min_interval_seconds:
        Floor on the gap between two cues, so even a buggy caller cannot
        machine-gun the speaker.
    asset_path:
        Explicit sound file. Defaults to ``assets/audio/<configured file>`` and
        is only used when it exists on disk.
    clock:
        Monotonic time source, injected by tests.
    """

    def __init__(
        self,
        enabled: Optional[bool] = None,
        min_interval_seconds: Optional[float] = None,
        asset_path: Optional[Path] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._clock = clock
        self.min_interval_seconds = (
            settings.SCAN_SUCCESS_BEEP_MIN_INTERVAL_SECONDS
            if min_interval_seconds is None
            else float(min_interval_seconds)
        )
        self._enabled = (
            settings.SCAN_SUCCESS_BEEP_ENABLED if enabled is None else bool(enabled)
        )
        self._last_played_at: Optional[float] = None
        self.play_count = 0
        self.last_error: Optional[str] = None

        self.asset_path: Optional[Path] = self._resolve_asset(asset_path)
        self._effect = None
        self._unavailable_reason: Optional[str] = None
        self._init_backend()

    # -- configuration ---------------------------------------------------

    @staticmethod
    def _resolve_asset(asset_path: Optional[Path]) -> Optional[Path]:
        """Return an existing sound file, or ``None``.

        A configured filename that is not on disk is logged, not used. The
        alternative -- handing Qt a missing path -- produces a cue that never
        plays and an error nobody sees.
        """
        if asset_path is not None:
            candidate = Path(asset_path)
            return candidate if candidate.is_file() else None

        configured = settings.SCAN_SUCCESS_BEEP_FILE
        candidate = settings.AUDIO_DIR / configured
        if candidate.is_file():
            return candidate
        logger.info(
            "Success beep asset not found at %s; using the platform beep instead.",
            candidate,
        )
        return None

    def _init_backend(self) -> None:
        """Pick the best available playback backend for the resolved asset."""
        if self.asset_path is None:
            # No asset: the platform beep is a real, always-present cue rather
            # than a reference to a file that does not exist.
            self.source = "platform"
            return
        try:
            from PySide6.QtMultimedia import QSoundEffect

            effect = QSoundEffect(self.asset_path.parent)
            if not effect.load(self.asset_path):
                self._unavailable_reason = (
                    f"{self.asset_path.name} could not be decoded"
                )
                logger.warning(
                    "Success beep could not be decoded (%s); using the platform beep.",
                    self._unavailable_reason,
                )
                self.asset_path = None
                self.source = "platform"
                return
            self._effect = effect
            self.source = "asset"
            logger.info("Success cue loaded from %s", self.asset_path)
        except Exception as exc:  # QtMultimedia absent, e.g. headless build
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "QtMultimedia unavailable (%s); using the platform beep.",
                self._unavailable_reason,
            )
            self.asset_path = None
            self.source = "platform"

    # -- state -----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the cue at runtime (Settings toggle)."""
        self._enabled = bool(enabled)
        if not self._enabled and self._effect is not None:
            try:
                self._effect.stop()
            except Exception:
                pass

    def toggle(self) -> bool:
        self.set_enabled(not self._enabled)
        return self._enabled

    @property
    def available(self) -> bool:
        """Whether any playback route exists at all."""
        return self.source in ("asset", "platform")

    # -- playback --------------------------------------------------------

    def play_success(self) -> bool:
        """Play the cue once. Returns True only if a sound was actually emitted.

        Call this *after* a verification, never per frame: it is rate limited
        and guarded, but it is not a substitute for the scan state machine.
        """
        if not self._enabled:
            return False

        now = self._clock()
        if (
            self._last_played_at is not None
            and (now - self._last_played_at) < self.min_interval_seconds
        ):
            return False

        if not self.available:
            return False

        try:
            if self.source == "asset" and self._effect is not None:
                # Restart rather than replay(): the clip is shorter than the
                # minimum interval, so isFinished() would rarely be true.
                self._effect.stop()
                self._effect.play()
            else:
                from PySide6.QtWidgets import QApplication

                if QApplication.instance() is None:
                    logger.debug("No QApplication; success cue skipped.")
                    return False
                QApplication.beep()
        except Exception as exc:
            # Never let audio break a scan.
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Success cue failed: %s", self.last_error)
            return False

        self._last_played_at = now
        self.play_count += 1
        return True

    def dispose(self) -> None:
        """Release the backend. Safe to call more than once."""
        if self._effect is not None:
            try:
                self._effect.stop()
                self._effect.deleteLater()
            except Exception:
                pass
            self._effect = None