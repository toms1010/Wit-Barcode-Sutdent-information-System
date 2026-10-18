"""Loading a verified student's portrait into a round avatar.

Kept in one place because three separate widgets need it (the scanner page's
student panel, the scan-result modal and the Students management cards) and they
previously carried three copies of the same logic, each resolving the stored path
a different way.

The rules this module enforces:

* **Identity comes from the database record, never from the file.** The path is
  read from ``student.profile_image_path``; nothing here inspects a filename or a
  face. Two students can therefore never end up sharing a portrait because the
  pictures happen to look alike or be named alike.
* **A missing or unreadable file is reported, not hidden.** The caller receives
  ``False`` so it can log the missing asset and show a neutral placeholder
  (a neutral silhouette) instead of an empty or broken frame.
* **Resolution is project-root relative.** See
  :meth:`~config.settings.Settings.resolve_asset`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QPainter, QPixmap

from config.settings import settings
from database.connection import DatabaseUnavailableError
from services.identifiers import REFERENCE_ALIASES, resolve_reference
from styles.colors import Colors

logger = logging.getLogger(__name__)

#: Avatar edge length in logical pixels.
AVATAR_SIZE = 80


def load_portrait(student, size: int = AVATAR_SIZE) -> tuple[Optional[QPixmap], bool]:
    """Return ``(pixmap, ok)`` for a student's portrait.

    ``ok`` is ``False`` whenever the caller should fall back to the neutral
    silhouette: no
    student, no stored path, a path that does not resolve to a file, or a file
    Qt cannot decode.
    """
    stored = getattr(student, "profile_image_path", None) if student else None
    if not stored:
        logger.info(
            "No profile image recorded for student %s",
            getattr(student, "student_id", "<unknown>") if student else "<unknown>",
        )
        return None, False

    path = settings.resolve_asset(stored)
    if path is None:
        logger.warning(
            "Profile image missing for student %s: %r does not resolve to a file. "
            "Showing a placeholder.",
            getattr(student, "student_id", "<unknown>"),
            stored,
        )
        return None, False

    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        logger.warning(
            "Profile image could not be decoded for student %s: %s",
            getattr(student, "student_id", "<unknown>"),
            path,
        )
        return None, False

    return _circular(pixmap, size), True


def _circular(pixmap: QPixmap, size: int) -> QPixmap:
    """Crop to a centred square and mask it into a circle.

    ``KeepAspectRatioByExpanding`` plus a centred source rect is the Qt equivalent
    of ``object-fit: cover``: the portrait fills the avatar without being
    stretched, and a non-square source is cropped rather than squashed.
    """
    scaled = pixmap.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    # Centre-crop the over-long axis.
    x = max(0, (scaled.width() - size) // 2)
    y = max(0, (scaled.height() - size) // 2)
    scaled = scaled.copy(x, y, size, size)

    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setBrush(QBrush(scaled))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return result


def apply_portrait(label, student, size: int = AVATAR_SIZE) -> bool:
    """Paint a student's portrait onto ``label``, or a neutral placeholder.

    Returns ``True`` when a real photograph was shown. The placeholder is a plain
    silhouette with no text, so a missing asset can neither be mistaken for a
    verified photo of someone else nor be read as the student's identity.

    This is the single entry point for student photographs: every screen that
    shows a student calls it, so no view can quietly fall back to a text avatar
    or to a picture belonging to the previously displayed student.
    """
    pixmap, ok = load_portrait(student, size)
    if ok and pixmap is not None:
        label.setPixmap(pixmap)
        name = (getattr(student, "full_name", "") or "").strip() or "this student"
        label.setToolTip(f"Photo on file for {name}")
        return True

    _paint_placeholder(label, size)
    return False


def _paint_placeholder(label, size: int) -> None:
    """Draw a neutral, image-only silhouette.

    Deliberately no initials and no letter: a text avatar is read as a person's
    identity, so on a record whose photograph is missing it asserts something the
    database does not know. A generic silhouette states only "no photograph on
    file", and the warning above it says which student and which path failed.
    """
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

    disc = QPixmap(size, size)
    disc.fill(Qt.GlobalColor.transparent)
    painter = QPainter(disc)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    figure = QColor(Colors.TEXT_SECONDARY)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(figure.red(), figure.green(), figure.blue(), 46))
    painter.drawEllipse(0, 0, size, size)

    # Head and shoulders, in a darker tint of the same neutral.
    painter.setBrush(QColor(figure.red(), figure.green(), figure.blue(), 92))
    head = size * 0.30
    painter.drawEllipse(
        int((size - head) / 2), int(size * 0.22), int(head), int(head)
    )
    body = QRectF(size * 0.20, size * 0.58, size * 0.60, size * 0.42)
    painter.drawRoundedRect(body, size * 0.22, size * 0.22)

    painter.setPen(QPen(QColor(Colors.BORDER), max(1, size // 40)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(0, 0, size - 1, size - 1)
    painter.end()

    label.setPixmap(disc)
    label.setToolTip("No profile photo on file for this student")


def identifier_conflict(student) -> Optional[str]:
    """Return a warning when a student's identifiers disagree, else ``None``.

    This project encodes a barcode as the zero-padded 9-digit reference number
    (reference ``65412`` -> payload ``000065412``). When the record violates that
    convention the identity behind a scanned card is genuinely in question --
    the case that existed for reference ``65902``, whose card encodes
    ``000065902`` while her printed panel showed ``000001``.

    Such a record is still scanned and still shown, but it is marked as
    unresolved rather than presented as a confirmed identity. The check is on
    the identifiers the scanner actually verifies against, never on the name or
    the picture.
    """
    if student is None:
        return None

    payload = (getattr(student, "barcode_payload", "") or "").strip()
    reference = (getattr(student, "reference_number", "") or "").strip()
    if not payload or not reference:
        return None
    if payload.isdigit() and reference.isdigit():
        # Compare the digits, not the strings: "000000" and "0" strip to the same
        # empty string and would have been reported as agreeing.
        if payload.lstrip("0") == reference.lstrip("0") and payload.lstrip("0"):
            return None

    return (
        f"Barcode {payload} and reference {reference} do not agree under this "
        "system's numbering convention. The scanned identity is unresolved and "
        "has not been confirmed."
    )


#: The alias table itself lives in :mod:`services.identifiers`, so services and
#: screens resolve a printed reference identically; it is re-exported here for the
#: screens that already import this module.
REFERENCE_ALIASES = REFERENCE_ALIASES

def _alias_profile_files() -> dict:
    """Reference/alias -> portrait file, built from the verified registry.

    The mapping used to be a second hand-written copy of
    ``config.student_assets``. Two sources of truth for a person's photograph is
    how a face ends up attached to the wrong student after either is edited, so
    this derives the table from the registry and adds the printed alias the
    registry cannot know about.
    """
    from config import student_assets

    table: dict = {}
    for assets in student_assets.STUDENT_ASSETS:
        filename = assets.portrait.name
        for key in (assets.reference, assets.payload):
            table[key] = filename
    # ``000001`` is Ronna's printed-panel alias, not her reference; her card
    # encodes ``000065902`` and the record stores reference ``65902``.
    for assets in student_assets.STUDENT_ASSETS:
        if assets.reference == "65902":
            table["000001"] = assets.portrait.name
    return table


def get_student_profile_image(reference_number) -> Optional[Path]:
    """The photograph file for a reference number, or ``None``.

    Resolution order, and why:

    1. **The database record.** ``students.profile_image_path`` is the identity
       of record; if it names a file, that file is the answer. Nothing else is
       consulted, so a record that later gains a different photograph wins
       automatically.
    2. **The alias table.** Only when no record is readable (offline, or the
       lookup failed) so that a printed reference still shows the right face
       rather than a silhouette.
    3. ``None``, which callers render as the neutral silhouette with a logged
       warning -- never as initials.

    Paths are resolved through :meth:`config.settings.Settings.resolve_asset`, so
    the result does not depend on the working directory and works whether the
    portraits sit in ``assets/profile/`` or a top-level ``profile/``.
    """
    text = str(reference_number or "").strip()
    if not text:
        return None

    resolved_reference = resolve_reference(text)
    try:
        from services.student_service import StudentService

        student = StudentService().get_student_by_reference(resolved_reference)
        stored = getattr(student, "profile_image_path", None) if student else None
        if stored:
            path = settings.resolve_asset(stored)
            if path is not None:
                return path
            logger.warning(
                "Profile image named by the record is missing: %r (reference %s)",
                stored, text,
            )
    except DatabaseUnavailableError as exc:
        # The registry holds this exact mapping (reference and payload to
        # portrait) from verifying the card assets, so a database outage does not
        # mean the face is unknown. Any other failure is logged and falls through
        # to the same table rather than being guessed at.
        logger.warning(
            "Could not read the student record for reference %s (%s); using the "
            "verified asset registry instead.",
            text, exc,
        )
    except Exception as exc:  # noqa: BLE001 - logged, never guessed around
        logger.warning(
            "Could not resolve the portrait for reference %s (%s); using the "
            "verified asset registry instead.",
            text, exc,
        )

    alias_files = _alias_profile_files()
    filename = alias_files.get(text) or alias_files.get(resolved_reference)
    if filename:
        path = settings.resolve_asset(f"assets/profile/{filename}")
        if path is not None:
            return path
    logger.warning("No profile photograph on file for reference %s", text)
    return None


__all__ = [
    "AVATAR_SIZE",
    "REFERENCE_ALIASES",
    "apply_portrait",
    "get_student_profile_image",
    "identifier_conflict",
    "load_portrait",
    "resolve_reference",
]