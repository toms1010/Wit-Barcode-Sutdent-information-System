"""Renders, exports and prints complete student ID cards.

This is the generator behind the ID Card Generator page. It draws a real card --
logo, photograph, identity fields, barcode -- at a real physical size, from the
student's own database record.

Design rules this module holds to, each of them a decision that was previously
made the other way:

**The card is drawn at CR80, not at the size of the preview.** Every geometry
value in :class:`~models.id_card.CardLayout` is a fraction of the card and every
font size is a fraction of the *card height converted to points at the render
resolution*, so one layout produces a correct card at 150, 300 and 600 DPI and
in the preview, the PNG and the PDF alike. The preview scales the finished
bitmap; it never becomes the source of the output geometry.

**The barcode carries the identifier the scanner verifies.** It is
``students.barcode_payload`` -- the same value
:meth:`services.barcode_generator.BarcodeGeneratorService.generate_barcode_value`
returns and the same value ``StudentRepository.get_by_barcode_payload`` looks
up -- encoded by the project's existing CODE39 writer. Nothing here invents an
identifier, and a card is never written without its payload.

**A rendered barcode is decoded back before the card is accepted.** The card is
run through the application's own
:class:`~services.barcode_decoder.BarcodeDecoderService` and the result compared
to the payload. A card whose barcode does not decode is rejected with an
explanation rather than silently written out.

**Nothing is invented.** Fields the database does not hold -- an emergency
contact, a signature, a validity date, the school's contact line -- render as a
visible "not on file" marker, or as text an operator typed into the Customize
Details panel. There is no default signature, no default phone number and no
default expiry anywhere in this file.

**Printing is not claiming.** :meth:`IdCardService.print_card` returns ``False``
when the operator cancels and ``True`` only once ``QPrintDialog`` has been
accepted; the caller records *print requested*, never *printed*, because nothing
in this application can confirm where the job went.
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QMarginsF, QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from config.settings import settings
from database.connection import DatabaseUnavailableError, existing_user_id
from database.repositories import IdCardRepository
from models.id_card import (
    CR80_HEIGHT_MM,
    CR80_WIDTH_MM,
    DEFAULT_EXPORT_DPI,
    DEFAULT_TEMPLATE_KEY,
    MM_PER_INCH,
    CardContent,
    CardField,
    CardLayout,
    CardRenderResult,
    CardSide,
    CardStatus,
    CardTemplate,
    IdCard,
    get_template,
    pixels_for_dpi,
)
from services.barcode_generator import BarcodeGeneratorService
from services.student_service import StudentService

logger = logging.getLogger(__name__)


#: Barcode module size for card printing, in **millimetres**.
#:
#: A nine-digit CODE39 payload is 175 module units wide plus the quiet zones, so
#: the symbol's printed width is essentially ``175 x module_width``. Measured:
#:
#: ==========  ================  ================================
#: Module      Symbol at 300 DPI Fits a 1011 px CR80 export?
#: ==========  ================  ================================
#: 0.20 mm     ~440 px           yes, but only by scaling UP ~2.3x
#: 0.42 mm     ~915 px           yes, at native resolution
#: ==========  ================  ================================
#:
#: The 0.20 mm row is the trap: it *looks* fine scaled up, and zbar reads the
#: rendered bitmap, but interpolated bar edges are what a printer plus a plastic
#: laminate makes worse. 0.42 mm lands the symbol on the card at 1:1. The card
#: is decoded back through :class:`~services.barcode_decoder.BarcodeDecoderService`
#: before it is written, so this constant is verified rather than trusted.
CARD_BARCODE_MODULE_WIDTH_MM = 0.42

#: Bar height, 7.5 mm: a 1:18 bar-to-module ratio, taller than the 1:10 typical
#: of a label barcode because a card is read at a distance and through plastic.
CARD_BARCODE_MODULE_HEIGHT_MM = 7.5

#: Quiet zone, 2 mm per side. The 6 mm default suits a standalone barcode image
#: but would add 12 mm to a symbol already 73 mm wide, which does not fit a
#: CR80 card with margins. A CODE39 quiet zone of 2 mm (about 4.7 modules) is
#: comfortably above the two-module minimum the symbology requires.
CARD_BARCODE_QUIET_ZONE_MM = 2.0

#: Directory generated cards are written to, relative to the project root.
ID_CARD_OUTPUT_DIRNAME = "generated_id_cards"

_SANS = "DejaVu Sans"
_SERIF = "DejaVu Serif"
_MONO = "DejaVu Sans Mono"


class CardGenerationError(RuntimeError):
    """A card could not be produced. The message is operator-facing."""

    def __init__(self, message: str, *, warning: bool = False):
        super().__init__(message)
        #: True when something was produced but is degraded (a missing
        #: photograph, say). Warnings never become an ``error`` status.
        self.warning = warning


@dataclass
class CardPreviewData:
    """Everything the page needs to show a preview without re-querying.

    Bundled so a side toggle or a template change costs no database round trip;
    the student and their contacts are read once per selection.
    """

    student: object
    front: CardContent
    back: CardContent
    template: CardTemplate
    layout: CardLayout
    card: Optional[IdCard] = None
    warnings: List[str] = dataclass_field(default_factory=list)

    def content(self, side: CardSide) -> CardContent:
        return self.front if side is CardSide.FRONT else self.back


class IdCardService:
    """Generates, exports and prints student ID cards."""

    #: Smallest type the name may be fitted down to, as a percentage of the card
    #: height. It mirrors :meth:`models.id_card.CardLayout.clamp`'s floor for
    #: ``name_size_pct``, so the renderer never produces a name smaller than the
    #: smallest an operator can set by hand.
    NAME_SIZE_FLOOR_PCT = 0.055

    def __init__(self, barcode_service: Optional[BarcodeGeneratorService] = None):
        self.card_repo = IdCardRepository()
        self.student_service = StudentService()
        self.barcode_service = barcode_service or BarcodeGeneratorService()
        self.output_dir = settings.ID_CARD_OUTPUT_DIR
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # a read-only install must not break the page
            logger.warning(
                "Could not create the ID card output directory %s: %s",
                self.output_dir, exc,
            )

    # ------------------------------------------------------------------
    # Content assembly
    # ------------------------------------------------------------------

    def build_content(
        self,
        student,
        side: CardSide,
        *,
        template: Optional[CardTemplate] = None,
        custom: Optional[Dict[str, str]] = None,
        layout: Optional[CardLayout] = None,
    ) -> CardContent:
        """Resolve one side's content from real records.

        Every value comes from the ``students`` row, the ``student_contacts``
        rows, the enrolment record, or text the operator supplied. A lookup that
        finds nothing produces a :class:`~models.id_card.CardField` whose value is
        ``None``, which renders as a placeholder rather than as an empty line
        someone could mistake for a blank field.
        """
        side = side if isinstance(side, CardSide) else CardSide(str(side))
        template = template or get_template(DEFAULT_TEMPLATE_KEY)
        custom = dict(custom or {})
        layout = layout or CardLayout()

        # The payload the scanner verifies. A student with no payload still gets
        # content; the barcode block renders an explicit marker rather than a
        # substitute value.
        payload = self._safe_payload(student)
        photo_path, photo_reason = self._resolve_photo(student)

        common = dict(
            student_id=getattr(student, "id", None),
            full_name=self._full_name(student),
            student_code=self._safe(getattr(student, "student_id", "")) or None,
            reference_number=self._safe(getattr(student, "reference_number", "")) or None,
            program=self._safe(getattr(student, "program", "")) or None,
            photo_path=photo_path,
            photo_missing_reason=photo_reason,
            custom=custom,
        )

        if side is CardSide.FRONT:
            return CardContent(
                side=side,
                barcode_value=payload,
                barcode_text=self._custom(custom, "barcode_caption") or "",
                fields=self._front_fields(student, payload, layout),
                **common,
            )

        return CardContent(
            side=side,
            barcode_value=payload,
            barcode_text="",
            fields=self._back_fields(student, custom, template),
            **common,
        )

    def _front_fields(
        self, student, payload: Optional[str], layout: CardLayout
    ) -> List[CardField]:
        year_level = getattr(student, "year_level", None)
        year_level = self._safe(getattr(year_level, "value", year_level))

        fields: List[CardField] = []
        if layout.show_program:
            fields.append(
                CardField("Programme", self._safe(getattr(student, "program", "")) or None)
            )
        if layout.show_student_id:
            fields.append(
                CardField("Student ID", self._safe(getattr(student, "student_id", "")) or None, mono=True)
            )
        if layout.show_reference:
            fields.append(
                CardField(
                    "Reference No.",
                    self._safe(getattr(student, "reference_number", "")) or None,
                    mono=True,
                )
            )
        if layout.show_year_level:
            fields.append(CardField("Year Level", year_level or None))
        if layout.show_section:
            fields.append(
                CardField("Section", self._safe(getattr(student, "section", "")) or None)
            )
        return fields

    def _back_fields(
        self, student, custom: Dict[str, str], template: CardTemplate
    ) -> List[CardField]:
        """Back-side fields, in print order.

        The three sources are kept strictly separate, which is the point:

        1. ``student_contacts`` -- contacts actually recorded for this student.
        2. Operator text from the Customize panel, consulted only where the
           database has nothing, and only when the operator actually typed it.
        3. ``None`` -- renders as "not on file".
        """
        contacts = self._safe_contacts(student)

        def recorded(*types: str) -> Optional[str]:
            for contact_type in types:
                value = contacts.get(contact_type)
                if value:
                    return value
            return None

        enrolled = getattr(student, "date_enrolled", None)
        school_contact = (
            self._safe(settings.INSTITUTION_CONTACT_LINE)
            or self._custom(custom, "school_contact")
        )

        return [
            CardField(
                "Emergency Contact",
                recorded("emergency_phone", "guardian_phone")
                or self._custom(custom, "emergency_contact"),
            ),
            CardField(
                "Emergency E-mail",
                recorded("emergency_email", "guardian_email")
                or self._custom(custom, "emergency_email"),
            ),
            CardField(
                "Student Contact",
                recorded("phone") or self._custom(custom, "student_contact"),
            ),
            CardField(
                "School E-mail",
                recorded("email") or self._custom(custom, "student_email"),
            ),
            CardField(
                "Academic Year",
                self._safe_academic_year(student) or self._custom(custom, "academic_year"),
            ),
            CardField(
                "Date Enrolled",
                enrolled.isoformat() if hasattr(enrolled, "isoformat") else None,
            ),
            CardField(
                "School Contact",
                school_contact,
                placeholder="— school contact not configured —",
            ),
            CardField(
                "Registrar's Office",
                self._safe(settings.REGISTRAR_OFFICE)
                or self._custom(custom, "registrar_office"),
                placeholder="— registrar not configured —",
            ),
            CardField(
                "Valid Until",
                # Deliberately never defaulted. A card that silently printed an
                # expiry nobody approved is a document that lies.
                self._custom(custom, "valid_until"),
                placeholder="— validity not set —",
            ),
            CardField(
                "Authorised Signature",
                self._custom(custom, "authorised_signature"),
                placeholder="— signature line, unsigned —",
            ),
        ]

    # -- small helpers, all of which fail closed ------------------------

    @staticmethod
    def _safe(value) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        return "" if text.lower() in ("none", "null") else text

    @staticmethod
    def _custom(custom: Dict[str, str], key: str) -> Optional[str]:
        value = (custom or {}).get(key)
        text = str(value).strip() if value is not None else ""
        return text or None

    @staticmethod
    def _full_name(student) -> str:
        return " ".join(str(getattr(student, "full_name", "") or "").split())

    def _safe_payload(self, student) -> Optional[str]:
        """The payload to encode -- the only one the scanner will resolve."""
        try:
            return self.barcode_service.generate_barcode_value(student) or None
        except Exception:  # noqa: BLE001 - a card must still render without one
            logger.exception("Could not derive a barcode payload; rendering without")
            return None

    def _safe_contacts(self, student) -> Dict[str, str]:
        """Recorded contacts keyed by ``student_contacts.contact_type``.

        A failure yields an empty mapping, so every contact renders as a
        placeholder. That is the correct degradation: a database read failure on
        the back of a card must not produce a card that looks complete.
        """
        student_id = getattr(student, "id", None)
        if student_id is None:
            return {}

        try:
            records = list(getattr(student, "contacts", None) or [])
            if not records:
                from database.connection import get_cursor

                with get_cursor() as cursor:
                    cursor.execute(
                        "SELECT contact_type, contact_value FROM student_contacts "
                        "WHERE student_id = %s ORDER BY is_primary DESC, id",
                        (student_id,),
                    )
                    records = cursor.fetchall()
        except DatabaseUnavailableError:
            raise
        except Exception:  # noqa: BLE001 - logged, then shown as placeholders
            logger.exception("Could not read contacts for student %s", student_id)
            return {}

        contacts: Dict[str, str] = {}
        for row in records:
            key = getattr(row, "contact_type", None)
            if key is None and isinstance(row, dict):
                key = row.get("contact_type")
            value = getattr(row, "contact_value", None)
            if value is None and isinstance(row, dict):
                value = row.get("contact_value")
            if not key or not value:
                continue
            # ``StudentContact.contact_type`` is a ``ContactType`` enum, so
            # ``str(key)`` is "ContactType.PHONE", never "phone". Keying the map
            # with it made every lookup in :meth:`_back_fields` miss, which is
            # why a card printed "— not on file —" for an emergency contact that
            # was in fact recorded against the student.
            name = str(getattr(key, "value", key))
            contacts.setdefault(name, str(value))
        return contacts

    def _safe_academic_year(self, student) -> Optional[str]:
        student_id = getattr(student, "id", None)
        if student_id is None:
            return None
        try:
            rows = self.student_service.get_enrollments_by_student(student_id)
        except DatabaseUnavailableError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("Could not read enrolments for student %s", student_id)
            return None
        years = [
            str(getattr(row, "academic_year", "")).strip()
            for row in rows or []
            if getattr(row, "academic_year", None)
        ]
        return years[0] if years else None

    def _resolve_photo(self, student) -> Tuple[Optional[str], str]:
        """The student's own photograph, resolved from the record.

        Returns ``(path, reason)``. Identity comes from
        ``students.profile_image_path`` and nothing else -- no filename matching,
        no nearest lookalike -- so two students can never share a face because
        their files happen to be named alike. ``reason`` explains an empty
        ``path`` and is shown next to the card, so a missing photograph is
        visible rather than silently absent.
        """
        stored = getattr(student, "profile_image_path", None)
        name = self._full_name(student) or "this student"
        if not stored:
            return None, f"No profile photograph is recorded for {name}."

        resolved = settings.resolve_asset(stored)
        if resolved is None:
            logger.warning("Profile photo for %s does not resolve: %r", name, stored)
            return None, (
                f"The recorded photograph for {name} ({stored}) could not be found. "
                f"Correct students.profile_image_path to an existing file."
            )
        return str(resolved), ""

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(
        self,
        content: CardContent,
        *,
        template: Optional[CardTemplate] = None,
        layout: Optional[CardLayout] = None,
        dpi: int = DEFAULT_EXPORT_DPI,
        barcode_image: Optional[QPixmap] = None,
        verify_barcode: bool = True,
    ) -> CardRenderResult:
        """Draw one side of a card at CR80 and ``dpi``.

        ``dpi`` sets the raster size via
        :func:`~models.id_card.pixels_for_dpi`; the physical card is CR80
        regardless, so 300 DPI yields 1011 x 638 px.

        ``verify_barcode`` runs the finished bitmap through the application's own
        decoder and sets
        :attr:`~models.id_card.CardRenderResult.barcode_verified`. A failure is
        reported, never suppressed.
        """
        template = template or get_template(DEFAULT_TEMPLATE_KEY)
        layout = CardLayout.from_dict((layout or CardLayout()).to_dict())
        layout.clamp()

        width_px, height_px = pixels_for_dpi(dpi)
        image = QImage(width_px, height_px, QImage.Format.Format_RGB32)
        image.fill(QColor("#FFFFFF"))
        image.setDotsPerMeterX(int(round(dpi / MM_PER_INCH * 1000)))
        image.setDotsPerMeterY(int(round(dpi / MM_PER_INCH * 1000)))

        card = QRectF(0, 0, width_px, height_px)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        warnings: List[str] = []
        try:
            self._draw_background(painter, card, template, layout, dpi)
            if content.side is CardSide.FRONT:
                self._draw_front(
                    painter, card, content, template, layout, dpi,
                    barcode_image, warnings,
                )
            else:
                self._draw_back(painter, card, content, template, layout, dpi)
        finally:
            painter.end()

        result = CardRenderResult(
            image=image,
            template=template,
            side=content.side,
            dpi=dpi,
            payload=content.barcode_value,
            warnings=warnings,
        )

        if content.side is CardSide.FRONT and verify_barcode:
            result.barcode_checked = True
            result.barcode_verified = self.verify_rendered_barcode(
                image, content.barcode_value
            )
            if not result.barcode_verified:
                warnings.append(
                    "The barcode on the rendered card did not decode back to "
                    f"{content.barcode_value or '(no payload)'}. See the log for "
                    f"details before printing."
                )
        return result

    # -- drawing primitives ---------------------------------------------

    @staticmethod
    def _box(card: QRectF, layout: CardLayout, prefix: str) -> QRectF:
        """The layout's box for ``prefix``, in pixels of this render."""
        return QRectF(
            card.width() * getattr(layout, f"{prefix}_x"),
            card.height() * getattr(layout, f"{prefix}_y"),
            card.width() * getattr(layout, f"{prefix}_w"),
            card.height() * getattr(layout, f"{prefix}_h"),
        )

    @staticmethod
    def _font(pct_of_card_height: float, card_height: float, dpi: int, *,
              family: str = _SANS, bold: bool = False,
              italic: bool = False) -> QFont:
        """A font sized as ``pct_of_card_height`` of the card, at ``dpi``.

        QFont sizes are in points, i.e. 1/72 inch, and a card rendered at ``dpi``
        maps one pixel to 1/``dpi`` inch -- so a requested pixel height converts
        with ``pixels * 72 / dpi``. A fixed point size is the bug this replaces:
        an 11 pt font is 11 px in the preview and roughly 46 px in a 300 DPI
        export, so the two never agree.
        """
        pixels = float(card_height) * float(pct_of_card_height)
        font = QFont(family)
        font.setPointSizeF(max(3.0, pixels * 72.0 / float(dpi or 1)))
        font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Medium)
        font.setItalic(italic)
        return font

    def _draw_background(
        self, painter: QPainter, card: QRectF, template: CardTemplate,
        layout: CardLayout, dpi: int
    ) -> None:
        painter.fillRect(card, QColor("#FFFFFF"))

        # A coloured bleed around the edge, as every WIT plastic card carries.
        accent = QColor(template.accent)
        bleed = max(2.0, card.height() * 0.030)
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(0, 0, card.width(), card.height()), bleed / 3.0, bleed / 3.0
        )
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(card, accent)
        painter.restore()

        # The inner field is white so text and the photograph sit on paper, not
        # on saturated orange.
        inset = bleed
        painter.fillRect(
            QRectF(inset, inset, card.width() - 2 * inset, card.height() - 2 * inset),
            QColor("#FFFFFF"),
        )

    def _draw_front(
        self, painter: QPainter, card: QRectF, content: CardContent,
        template: CardTemplate, layout: CardLayout, dpi: int,
        barcode_image: Optional[QPixmap], warnings: List[str],
    ) -> None:
        if layout.show_institution_header:
            self._draw_header(painter, card, template, dpi)

        photo_box = self._box(card, layout, "photo")
        if content.photo_path:
            self._draw_photo(painter, photo_box, content.photo_path, layout)
        else:
            self._draw_photo_placeholder(painter, photo_box)
            warnings.append(
                content.photo_missing_reason or "No profile photograph on file."
            )

        self._draw_text_block(painter, card, self._box(card, layout, "text"),
                              content, template, layout, dpi)

        barcode_box = self._box(card, layout, "barcode")
        if content.barcode_value and barcode_image is not None and not barcode_image.isNull():
            self._draw_barcode(painter, card, barcode_box, barcode_image, layout, content, dpi)
        else:
            self._draw_barcode_missing(painter, card, barcode_box, dpi)

    def _draw_header(
        self, painter: QPainter, card: QRectF, template: CardTemplate, dpi: int
    ) -> None:
        band = QRectF(0, 0, card.width(), card.height() * 0.175)
        painter.fillRect(band, QColor(template.accent))

        # The logo sits on a white plate: the supplied mark is RGBA with
        # transparent surroundings, and dropping it straight onto the orange
        # band left it illegible.
        plate = QRectF(
            card.width() * 0.030, band.height() * 0.14,
            band.height() * 0.72, band.height() * 0.72,
        )
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(plate, plate.height() * 0.16, plate.height() * 0.16)

        logo = self._logo_pixmap()
        if logo is not None:
            painter.drawPixmap(
                _fit_rect(QRectF(logo.rect()), plate), logo, QRectF(logo.rect())
            )
        painter.restore()

        text_x = plate.right() + card.width() * 0.020
        avail = card.width() - text_x - card.width() * 0.03

        # The institution name is shrunk to fit rather than clipped: a card
        # reading "WESTERN INSTITUTE OF TE" is worse than one with a smaller
        # title, and the full name is the one thing that must be unambiguous.
        name_font = self._font(0.082, card.height(), dpi, family=_SERIF, bold=True)
        name_font = self._fit_font(painter, name_font, template.institution, avail,
                                   floor_pct=0.045, card_height=card.height(), dpi=dpi)
        painter.setFont(name_font)
        painter.setPen(QColor("#FFFFFF"))
        _draw_elided(
            painter,
            QRectF(text_x, band.height() * 0.16, avail, band.height() * 0.48),
            template.institution,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        )

        sub_font = self._font(0.058, card.height(), dpi)
        sub_font = self._fit_font(painter, sub_font, template.subtitle, avail,
                                  floor_pct=0.038, card_height=card.height(), dpi=dpi)
        painter.setFont(sub_font)
        painter.setPen(QColor(255, 255, 255, 235))
        _draw_elided(
            painter,
            QRectF(text_x, band.height() * 0.60, avail, band.height() * 0.32),
            template.subtitle,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        )

    def _fit_font(self, painter: QPainter, font: QFont, text: str, width: float,
                  *, floor_pct: float, card_height: float, dpi: int) -> QFont:
        """Reduce ``font`` until ``text`` fits ``width``, down to a floor.

        A hard failure at the floor is reported and the text is elided, because
        something readable-but-short beats something that silently runs off the
        card.
        """
        metrics_cache: Dict[float, QFont] = {}

        def advance(candidate: QFont) -> float:
            previous = painter.font()
            painter.setFont(candidate)
            value = float(painter.fontMetrics().horizontalAdvance(text))
            painter.setFont(previous)
            return value

        if advance(font) <= width or not text:
            return font

        pct = font_point_pct(font, card_height, dpi)
        while pct > floor_pct:
            pct = max(floor_pct, pct - 0.004)
            candidate = metrics_cache.get(pct)
            if candidate is None:
                candidate = self._font(pct, card_height, dpi, bold=font.bold(),
                                       italic=font.italic(),
                                       family=font.family().split(",")[0])
                metrics_cache[pct] = candidate
            if advance(candidate) <= width:
                return candidate

        logger.warning(
            "Text %r still exceeds its box at the minimum size; it will be elided",
            text[:48],
        )
        return metrics_cache.get(floor_pct, font)

    def _fit_name(
        self, painter: QPainter, card_height: float, dpi: int, name: str,
        width: float, *, reserved: float, pct: float,
    ) -> Tuple[QFont, List[str], float]:
        """The largest name type at or below ``pct`` whose block fits ``reserved``.

        The name is the one field that is never elided, so it may wrap -- but it
        must not wrap at the cost of an identity field falling off the card. The
        caller has already reserved the height its field rows need, so the choice
        made here is between type sizes, not between a field and a name.

        Returns ``(font, lines, line_height)``. When even
        :attr:`NAME_SIZE_FLOOR_PCT` does not fit, the floor is returned and the
        problem is logged: the caller's own drop path then reports it, rather
        than this silently shrinking past a size an operator could not have
        chosen.
        """
        text_width = max(1.0, width * 0.99)
        floor = min(self.NAME_SIZE_FLOOR_PCT, pct)

        def measure(candidate_pct: float) -> Tuple[QFont, List[str], float]:
            font = self._font(candidate_pct, card_height, dpi, bold=True)
            previous = painter.font()
            painter.setFont(font)
            metrics = painter.fontMetrics()
            lines = _wrap_text(metrics, name, int(text_width))
            result = (font, lines, metrics.height() * 1.06)
            painter.setFont(previous)
            return result

        candidate = pct
        while True:
            trial = measure(candidate)
            if trial[1] and len(trial[1]) * trial[2] <= reserved:
                return trial
            if candidate <= floor + 1e-9:
                break
            candidate = max(floor, candidate - 0.004)

        logger.warning(
            "The name %r needs more room than the text block can give it once every "
            "identity field is reserved; some fields may be dropped",
            name[:48],
        )
        return measure(floor)

    def _draw_text_block(
        self, painter: QPainter, card: QRectF, box: QRectF, content: CardContent,
        template: CardTemplate, layout: CardLayout, dpi: int,
    ) -> None:
        """Name, a rule, then the identity fields flowed into as many rows as fit.

        Two things this does that a fixed grid could not:

        * **The name is wrapped, never elided.** It is the one field that must
          never be truncated, so it takes the lines it needs and the field rows
          get whatever vertical space is left.
        * **Fields share rows by measured width.** A nine-digit reference and a
          "3rd Year" fit side by side on one line; a long programme name does
          not, and gets its own. The row height is then derived from the space
          that is left, and the value type size is reduced to fit it -- so
          hiding a field can never push the last one off the card, and adding one
          can never overlap the row above it.
        """
        if not content.full_name and not content.fields:
            return

        # Every type size resolves against the *card* height, never the box.
        # That is what keeps the typography identical whichever block a value
        # lands in: an operator who hides the year level does not silently
        # enlarge the section line to fill the gap.
        card_h = card.height()
        centred = layout.text_align == "center"

        #: Smallest value type the flow will shrink to before it starts dropping
        #: fields. A name may be sized around this; a field may not.
        min_value_pct = 0.044

        ordered = [f for f in content.fields if f.present] + [
            f for f in content.fields if not f.present
        ]
        label_font = self._font(layout.label_size_pct, card_h, dpi)
        value_font = self._font(layout.value_size_pct, card_h, dpi, bold=True)

        # The rows are grouped before the name is sized. Grouping depends only on
        # the value font and the box width -- never on the cursor -- so deciding
        # them first costs nothing and lets the name be fitted against a block
        # that is known to be big enough for every field.
        rows = self._flow_rows(painter, ordered, value_font, box.width()) if ordered else []

        # A row holds a label band above a value band, so the height a row needs
        # is the sum of the two type sizes -- not the value size alone, which is
        # what let the label band overlap the row above it.
        def needed_height() -> float:
            return (
                _font_metrics_height(painter, label_font)
                + _font_metrics_height(painter, value_font) * 1.15
            )

        cursor = box.top()

        if content.full_name:
            # The name is the field that may wrap, but it must not wrap at the
            # cost of pushing an identity field off the card. Sizing it against
            # the room the rows actually need is what makes two cards for
            # different students carry the same fields: before this, "Steven
            # Anthony M. Oquiana" wrapped to two lines, ate the last row, and
            # the flow dropped Year Level and Section, while a shorter name kept
            # them.
            floor_rows = len(rows) * (
                _font_metrics_height(painter, label_font)
                + _font_metrics_height(
                    painter, self._font(min_value_pct, card_h, dpi, bold=True)
                ) * 1.15
            )
            # The rule and the gaps either side of it, as a fraction of this box
            # so they stay in proportion with whatever height it has.
            name_overhead = box.height() * (0.030 + 0.055)
            name_font, lines, line_h = self._fit_name(
                painter, card_h, dpi, content.full_name, box.width(),
                reserved=box.height() - name_overhead - floor_rows,
                pct=layout.name_size_pct,
            )
            painter.setFont(name_font)
            painter.setPen(QColor("#0F172A"))
            for line in lines:
                if cursor + line_h > box.bottom():
                    logger.warning(
                        "Name wrapped to %d lines and does not fit the card's text "
                        "block; the name font is too large for this layout.",
                        len(lines),
                    )
                    break
                painter.drawText(
                    QRectF(box.left(), cursor, box.width(), line_h),
                    int(Qt.AlignmentFlag.AlignHCenter if centred
                        else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    line,
                )
                cursor += line_h

            rule_y = cursor + box.height() * 0.030
            painter.setPen(QPen(QColor(template.accent), max(1.0, box.height() * 0.014)))
            painter.drawLine(QPointF(box.left(), rule_y), QPointF(box.right(), rule_y))
            cursor = rule_y + box.height() * 0.055

        if not rows:
            return

        available = box.bottom() - cursor
        if available <= 0:
            logger.warning("No room left on the card for the identity fields")
            return

        # Share the leftover space evenly, then shrink type only if a row still
        # does not fit. A field is dropped only when it cannot be drawn at a
        # legible size, and that drop is logged rather than hidden.
        row_h = min(available / len(rows), needed_height() * 1.10)
        while rows and row_h < needed_height() and layout.value_size_pct > min_value_pct:
            layout.value_size_pct = max(min_value_pct, layout.value_size_pct - 0.003)
            value_font = self._font(layout.value_size_pct, card_h, dpi, bold=True)
            row_h = min(available / len(rows), needed_height() * 1.10)

        while rows and row_h < needed_height() * 0.94:
            dropped = rows.pop()
            logger.warning(
                "Dropped %d identity field(s) from the card: no room at a legible size",
                len(dropped),
            )
            if not rows:
                return
            row_h = min(available / len(rows), needed_height() * 1.10)

        label_fraction = min(
            0.46,
            _font_metrics_height(painter, label_font)
            / max(1.0, _font_metrics_height(painter, label_font)
                  + _font_metrics_height(painter, value_font)),
        )

        for row in rows:
            if cursor + row_h > box.bottom() + row_h * 0.05:
                break

            # Label above value inside each cell. Stacked rather than side by
            # side because a cell then only has to fit the *value*: a
            # label-and-value pair on one line leaves the value about half a
            # cell, which is not enough for "Computer Engineering" and forces it
            # to be elided.
            gap = box.width() * 0.04 / max(1, len(row) - 1)
            slot = (box.width() - gap * (len(row) - 1)) / max(1, len(row))
            left = box.left()

            label_band = row_h * (label_fraction)
            for item in row:
                cell = QRectF(left, cursor, slot, row_h)

                painter.setFont(label_font)
                painter.setPen(QColor("#64748B"))
                _draw_elided(
                    painter,
                    QRectF(cell.left(), cell.top(), cell.width(), label_band),
                    item.label.upper(),
                    int(Qt.AlignmentFlag.AlignHCenter if centred
                        else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                )

                if item.present:
                    painter.setFont(
                        self._font(layout.value_size_pct, card_h, dpi,
                                   family=_MONO if item.mono else _SANS, bold=True)
                    )
                    painter.setPen(QColor("#0F172A"))
                else:
                    painter.setFont(self._font(layout.label_size_pct, card_h, dpi, italic=True))
                    painter.setPen(QColor("#94A3B8"))
                _draw_elided(
                    painter,
                    QRectF(cell.left(), cell.top() + label_band, cell.width(),
                           row_h - label_band),
                    item.display,
                    int(Qt.AlignmentFlag.AlignHCenter if centred
                        else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                )
                left += slot + gap

            cursor += row_h

    @staticmethod
    def _flow_rows(
        painter: QPainter, fields: Sequence[CardField], value_font: QFont,
        width: float, gap_ratio: float = 0.04,
    ) -> List[List[CardField]]:
        """Pack fields into rows of as many as will fit, label above value.

        Measured, because the difference is in the pixels: at card scale
        "Computer Engineering" needs roughly 535 px and "3rd Year" about 190, so
        the first takes a row to itself and the last two share one. Packing on
        the *value* width is what makes that safe -- a side-by-side row also had
        to fit the label beside the value, which left "3rd Year" about half the
        space it needed and forced it to be elided.
        """
        gap = width * gap_ratio

        def fits(row: Sequence[CardField]) -> bool:
            slot = (width - gap * (len(row) - 1)) / max(1, len(row))
            return all(
                painter_font_advance(painter, value_font, item.display) <= slot - slot * 0.04
                for item in row
            )

        rows: List[List[CardField]] = []
        current: List[CardField] = []
        for item in fields:
            trial = [*current, item]
            if current and not fits(trial):
                rows.append(current)
                current = [item]
            else:
                current = trial
        if current:
            rows.append(current)
        return rows

    def _draw_photo(self, painter: QPainter, box: QRectF, path: str, layout: CardLayout) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            logger.warning("Photograph %s could not be decoded", path)
            self._draw_photo_placeholder(painter, box)
            return

        # The box is the intended footprint; ``photo_size_pct`` then scales the
        # picture *inside* it without moving the frame, so resizing the photo
        # cannot shove the fields beside it out of place.
        side = min(box.width(), box.height()) * (layout.photo_size_pct / 100.0)
        side = min(side, min(box.width(), box.height()))
        if layout.photo_align == "center":
            x = box.center().x() - side / 2.0
        else:
            x = box.left()
        frame = QRectF(x, box.center().y() - side / 2.0, side, side)

        # Contain, never stretch: a face is the one thing on a card that must
        # keep its proportions, so the pixmap is scaled with KeepAspectRatio and
        # centred rather than forced to the frame.
        scaled = pixmap.scaled(
            max(1, int(round(side))), max(1, int(round(side))),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        # drawPixmap(QRectF, pixmap) does not exist in Qt -- the two-argument
        # overload takes an integer QRect. Drawing at a QPointF keeps the
        # fractional position instead of rounding the card to whole pixels.
        painter.drawPixmap(
            QPointF(
                frame.center().x() - scaled.width() / 2.0,
                frame.center().y() - scaled.height() / 2.0,
            ),
            scaled,
        )
        painter.setPen(QPen(QColor("#CBD5E1"), max(1.0, side * 0.008)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(frame, side * 0.05, side * 0.05)

    def _draw_photo_placeholder(self, painter: QPainter, box: QRectF) -> None:
        side = min(box.width(), box.height())
        frame = QRectF(box.left(), box.center().y() - side / 2.0, side, side)
        painter.setPen(QPen(QColor("#CBD5E1"), max(1.0, side * 0.008)))
        painter.setBrush(QColor("#F1F5F9"))
        painter.drawRoundedRect(frame, side * 0.05, side * 0.05)

        # A neutral silhouette and no initials. A letter avatar reads as an
        # identity; this states only "no photograph on file", which is what the
        # record actually says.
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#94A3B8"))
        head = side * 0.26
        painter.drawEllipse(
            QRectF(frame.center().x() - head / 2.0, frame.top() + side * 0.20, head, head)
        )
        painter.drawRoundedRect(
            QRectF(frame.center().x() - side * 0.24, frame.top() + side * 0.52,
                   side * 0.48, side * 0.34),
            side * 0.12, side * 0.12,
        )
        painter.restore()

    def _draw_barcode(
        self, painter: QPainter, card: QRectF, box: QRectF, barcode: QPixmap,
        layout: CardLayout, content: CardContent, dpi: int,
    ) -> None:
        caption_h = box.height() * 0.26
        symbol = QRectF(box.left(), box.top(), box.width(), box.height() - caption_h)
        painter.fillRect(symbol, QColor("#FFFFFF"))

        # KeepAspectRatio centres the symbol in its reserved box, so the barcode
        # -- whose aspect ratio is fixed by its module count -- is never
        # stretched to fill the layout slot.
        fitted = barcode.scaled(
            symbol.size().toSize(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(
            QPointF(
                symbol.center().x() - fitted.width() / 2.0,
                symbol.center().y() - fitted.height() / 2.0,
            ),
            fitted,
        )

        if layout.show_barcode_text:
            painter.setFont(
                self._font(layout.mono_size_pct, card.height(), dpi, family=_MONO)
            )
            painter.setPen(QColor("#0F172A"))
            _draw_elided(
                painter,
                QRectF(box.left(), symbol.bottom(), box.width(), caption_h),
                content.barcode_text or content.barcode_value or "",
                int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter),
            )

    def _draw_barcode_missing(self, painter: QPainter, card: QRectF, box: QRectF, dpi: int) -> None:
        painter.setPen(QPen(QColor("#DC2626"), max(1.0, box.height() * 0.012)))
        painter.setBrush(QColor("#FEE2E2"))
        painter.drawRoundedRect(box, box.height() * 0.05, box.height() * 0.05)
        painter.setFont(self._font(0.070, card.height(), dpi))
        painter.setPen(QColor("#DC2626"))
        painter.drawText(
            box.adjusted(box.width() * 0.03, 4, -box.width() * 0.03, -4),
            int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
            "No barcode payload on file.\n"
            "This card cannot be scanned until the student's payload is recorded.",
        )

    def _draw_back(
        self, painter: QPainter, card: QRectF, content: CardContent,
        template: CardTemplate, layout: CardLayout, dpi: int,
    ) -> None:
        accent = QColor(template.accent)
        header_h = card.height() * 0.20
        painter.fillRect(QRectF(0, 0, card.width(), header_h), accent)

        painter.setFont(self._font(0.092, card.height(), dpi, family=_SERIF, bold=True))
        painter.setPen(QColor("#FFFFFF"))
        _draw_elided(
            painter,
            QRectF(card.width() * 0.05, header_h * 0.14, card.width() * 0.90, header_h * 0.44),
            "STUDENT INFORMATION",
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        )
        painter.setFont(self._font(0.062, card.height(), dpi))
        painter.setPen(QColor(255, 255, 255, 232))
        subtitle = " — ".join(
            part for part in (content.full_name, content.program) if part
        )
        _draw_elided(
            painter,
            QRectF(card.width() * 0.05, header_h * 0.56, card.width() * 0.90, header_h * 0.32),
            subtitle,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        )

        # Identity strip: the front's identifiers repeated, so a back-only print
        # -- or a card found without its front -- still names its holder.
        strip = QRectF(card.width() * 0.05, header_h + card.height() * 0.045,
                       card.width() * 0.90, card.height() * 0.13)
        painter.setPen(QPen(QColor("#E2E8F0"), max(1.0, card.height() * 0.008)))
        painter.setBrush(QColor("#F8FAFC"))
        painter.drawRoundedRect(strip, card.height() * 0.025, card.height() * 0.025)
        painter.setFont(self._font(0.082, card.height(), dpi, family=_MONO))
        painter.setPen(QColor("#0F172A"))
        _draw_elided(
            painter,
            strip.adjusted(card.width() * 0.02, 0, -card.width() * 0.02, 0),
            f"Student ID {content.student_code or '—'}      "
            f"Reference No. {content.reference_number or '—'}",
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        )

        signature = content.field("Authorised Signature")
        grid_fields = [f for f in content.fields if f is not signature]
        signature_row = signature is not None

        reserved = card.height() * (0.22 if signature_row else 0.06)
        grid = QRectF(
            card.width() * 0.05,
            strip.bottom() + card.height() * 0.05,
            card.width() * 0.90,
            card.height() - strip.bottom() - card.height() * 0.06 - reserved
            - card.height() * 0.05,
        )
        self._draw_field_grid(painter, card, grid, grid_fields, layout, dpi)

        if signature_row:
            sig_box = QRectF(
                grid.left(), grid.bottom() + card.height() * 0.045,
                grid.width() * 0.50, card.height() * 0.16,
            )
            painter.setPen(QPen(QColor("#94A3B8"), max(1.0, card.height() * 0.006)))
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawRect(sig_box)
            painter.setFont(self._font(0.058, card.height(), dpi))
            painter.setPen(
                QColor("#0F172A") if signature.present else QColor("#94A3B8")
            )
            _draw_elided(
                painter,
                QRectF(sig_box.left() + 4, sig_box.bottom() - card.height() * 0.062,
                       sig_box.width() - 8, card.height() * 0.058),
                signature.display,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            )

        # Terms / return instructions, printed only if an operator supplied them.
        terms = (content.custom.get("terms") or "").strip()
        if terms:
            left = sig_box.right() + card.width() * 0.03 if signature_row else grid.left()
            terms_box = QRectF(
                left,
                sig_box.top() if signature_row else grid.bottom() + card.height() * 0.045,
                grid.left() + grid.width() - left,
                sig_box.height() if signature_row else card.height() * 0.16,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFF7ED"))
            painter.drawRoundedRect(
                terms_box, card.height() * 0.02, card.height() * 0.02
            )
            painter.setFont(self._font(0.056, card.height(), dpi))
            painter.setPen(QColor("#7C2D12"))
            painter.drawText(
                terms_box.adjusted(4, 3, -4, -3),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    | Qt.TextFlag.TextWordWrap),
                terms,
            )

    def _draw_field_grid(
        self, painter: QPainter, card: QRectF, grid: QRectF,
        fields: Sequence[CardField], layout: CardLayout, dpi: int,
    ) -> None:
        if not fields or grid.height() <= 0:
            return

        columns = 2 if len(fields) > 4 else 1
        rows = -(-len(fields) // columns)
        row_h = grid.height() / max(1, rows)
        col_w = grid.width() / columns
        card_h = card.height()

        for index, item in enumerate(fields):
            column, row = index % columns, index // columns
            cell = QRectF(
                grid.left() + column * col_w,
                grid.top() + row * row_h,
                col_w - card_gap(grid),
                row_h,
            )
            if cell.height() <= 0:
                break

            painter.setFont(self._font(layout.label_size_pct, card_h, dpi))
            painter.setPen(QColor("#64748B"))
            _draw_elided(
                painter,
                QRectF(cell.left(), cell.top(), cell.width(), row_h * 0.38),
                item.label.upper(),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            )

            if item.present:
                painter.setFont(
                    self._font(layout.value_size_pct, card_h, dpi,
                               family=_MONO if item.mono else _SANS, bold=True)
                )
                painter.setPen(QColor("#0F172A"))
            else:
                painter.setFont(
                    self._font(layout.label_size_pct, card_h, dpi, italic=True)
                )
                painter.setPen(QColor("#94A3B8"))

            _draw_elided(
                painter,
                QRectF(cell.left(), cell.top() + row_h * 0.36, cell.width(), row_h * 0.58),
                item.display,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            )

    def _logo_pixmap(self) -> Optional[QPixmap]:
        path = settings.LOGO_PATH
        if not path.exists():
            logger.warning("WIT logo missing at %s; the header renders without it", path)
            return None
        pixmap = QPixmap(str(path))
        return None if pixmap.isNull() else pixmap

    # ------------------------------------------------------------------
    # Barcode
    # ------------------------------------------------------------------

    def barcode_pixmap(
        self, student, dpi: int = DEFAULT_EXPORT_DPI
    ) -> Tuple[Optional[QPixmap], Optional[str], str]:
        """Render the student's barcode for a card.

        Returns ``(pixmap, payload, barcode_type)``. The image comes from the
        project's own :class:`BarcodeGeneratorService` at a module size chosen for
        print rather than the configured screen default.

        A student with no payload gets ``(None, None, "")`` and the card renders
        an explicit marker -- the generator never substitutes the reference
        number or any other value for a payload the scanner would not resolve.
        """
        if student is None:
            return None, None, ""
        payload = self._safe_payload(student)
        if not payload:
            logger.warning(
                "No barcode payload for %s; the card will render without a barcode",
                self._full_name(student) or student,
            )
            return None, None, ""

        barcode_type = self.barcode_service.barcode_type_for(student)
        with tempfile.TemporaryDirectory(prefix="wit_card_barcode_") as tmp:
            path = Path(tmp) / "barcode.png"
            if not self.barcode_service.generate_barcode_image(
                payload, path, barcode_type,
                module_width=CARD_BARCODE_MODULE_WIDTH_MM,
                module_height=CARD_BARCODE_MODULE_HEIGHT_MM,
                quiet_zone=CARD_BARCODE_QUIET_ZONE_MM,
                # The payload is already printed underneath the symbol in the
                # card's own typeface; python-barcode's baked-in text would be a
                # second, differently-styled copy inside the symbol's box.
                write_text=False,
            ):
                raise CardGenerationError(
                    f"The barcode for payload {payload} could not be generated."
                )
            # python-barcode appends the suffix itself when given a stem.
            produced = path if path.exists() else path.with_suffix(".png")
            if not produced.exists():
                raise CardGenerationError(
                    f"The barcode writer reported success but wrote no file for {payload}."
                )
            pixmap = QPixmap(str(produced))

        if pixmap.isNull():
            raise CardGenerationError(
                f"The generated barcode image for {payload} could not be decoded."
            )
        return pixmap, payload, getattr(barcode_type, "value", str(barcode_type))

    def verify_rendered_barcode(self, image: QImage, payload: Optional[str]) -> bool:
        """Decode the finished card and check it reads back as ``payload``.

        This is what makes "the barcode on the printed card is scannable by this
        application" a tested statement rather than a hope: the card is fed to
        the same :class:`~services.barcode_decoder.BarcodeDecoderService` the
        live scanner uses, and the result is compared to the stored payload.
        """
        if not payload:
            return False
        try:
            import cv2
            import numpy as np

            from services.barcode_decoder import BarcodeDecoderService

            rgb = image.convertToFormat(QImage.Format.Format_RGB888)
            width, height = rgb.width(), rgb.height()
            array = np.frombuffer(rgb.constBits(), dtype=np.uint8).reshape(
                height, rgb.bytesPerLine()
            )[:, : width * 3].reshape(height, width, 3)
            bgr = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)

            result = BarcodeDecoderService().decode_best(bgr)
            values = result.distinct_values
            if payload in values:
                logger.info("Rendered card barcode verified as %s (attempt %s)",
                            payload, result.attempt)
                return True
            logger.warning(
                "Rendered card barcode did not decode to %s (read %s, attempt %s)",
                payload, values or ["<nothing>"], result.attempt,
            )
            return False
        except Exception:  # noqa: BLE001 - verification must not break rendering
            logger.exception("Could not verify the rendered card barcode")
            return False

    # ------------------------------------------------------------------
    # Preview assembly
    # ------------------------------------------------------------------

    def preview_for(
        self,
        student,
        *,
        template_key: str = DEFAULT_TEMPLATE_KEY,
        custom: Optional[Dict[str, str]] = None,
        layout: Optional[CardLayout] = None,
        use_stored_layout: bool = True,
    ) -> CardPreviewData:
        """Bundle everything the preview needs, reading each source once.

        ``use_stored_layout`` reads the operator's saved layout off the student's
        card row, so reopening the page shows the card as they last configured it
        rather than silently reverting to the defaults.
        """
        template = get_template(template_key)
        student_id = getattr(student, "id", None)
        card = (
            self.card_repo.get_by_student(student_id, template.key)
            if student_id is not None
            else None
        )

        resolved_layout = CardLayout()
        if layout is not None:
            resolved_layout = CardLayout.from_dict(layout.to_dict())
        elif use_stored_layout and card is not None and card.custom_fields:
            resolved_layout, stored_fields = self._stored_custom(card)
            custom = {**stored_fields, **(custom or {})}

        resolved_layout.clamp()
        warnings: List[str] = []

        front = self.build_content(
            student, CardSide.FRONT, template=template, custom=custom,
            layout=resolved_layout,
        )
        back = self.build_content(
            student, CardSide.BACK, template=template, custom=custom,
            layout=resolved_layout,
        )
        if front.photo_missing_reason:
            warnings.append(front.photo_missing_reason)

        return CardPreviewData(
            student=student,
            front=front,
            back=back,
            template=template,
            layout=resolved_layout,
            card=card,
            warnings=warnings,
        )

    @staticmethod
    def _stored_custom(card: IdCard) -> Tuple[CardLayout, Dict[str, str]]:
        """The layout and operator fields saved on a card row."""
        try:
            stored = json.loads(card.custom_fields or "{}")
        except (TypeError, ValueError):
            logger.warning(
                "Card %s has unparseable custom_fields; using defaults", card.id
            )
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        layout = CardLayout.from_dict(stored.get("layout"))
        fields = stored.get("fields")
        return layout, dict(fields) if isinstance(fields, dict) else {}

    def render_preview(
        self,
        preview: CardPreviewData,
        side: CardSide,
        *,
        dpi: int = DEFAULT_EXPORT_DPI,
        verify_barcode: bool = False,
    ) -> CardRenderResult:
        """Render one side of a bundled preview.

        ``verify_barcode`` defaults to ``False`` because a preview repaints on
        every side toggle and template change, and decoding the card each time
        makes those interactions visibly slow. Verification runs on the render
        that is actually written out (:meth:`generate_for_student`) -- the one
        whose barcode has to survive being printed.
        """
        content = preview.content(side)
        barcode_pixmap = None
        if side is CardSide.FRONT and content.barcode_value:
            try:
                barcode_pixmap, _, _ = self.barcode_pixmap(preview.student, dpi)
            except CardGenerationError as exc:
                logger.warning("Barcode unavailable for the preview: %s", exc)

        return self.render(
            content,
            template=preview.template,
            layout=preview.layout,
            dpi=dpi,
            barcode_image=barcode_pixmap,
            verify_barcode=verify_barcode,
        )

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate_for_student(
        self,
        student,
        *,
        template_key: str = DEFAULT_TEMPLATE_KEY,
        custom: Optional[Dict[str, str]] = None,
        layout: Optional[CardLayout] = None,
        dpi: int = DEFAULT_EXPORT_DPI,
        both_sides: bool = True,
        generated_by: Optional[int] = None,
    ) -> CardRenderResult:
        """Render and write a card, then record the real outcome.

        Returns the last render produced (``back`` when both sides were written).
        Raises :class:`CardGenerationError` on failure and records an ``error``
        status with the reason first -- a failure that leaves no trace is how a
        student stays permanently "pending" while every attempt silently failed.

        Both sides are rendered before either is written, so an interruption
        cannot leave a half-written card recorded as generated.
        """
        template = get_template(template_key)
        student_id = getattr(student, "id", None)
        if student_id is None:
            raise CardGenerationError(
                "This student record has no id, so no card can be recorded."
            )
        name = self._full_name(student) or f"student {student_id}"
        reference = self._safe(getattr(student, "reference_number", "")) or f"student{student_id}"

        resolved_layout = CardLayout.from_dict((layout or CardLayout()).to_dict())
        resolved_layout.clamp()

        written: Dict[str, str] = {}
        last: Optional[CardRenderResult] = None

        try:
            for side in ((CardSide.FRONT, CardSide.BACK) if both_sides else (CardSide.FRONT,)):
                content = self.build_content(
                    student, side, template=template, custom=custom, layout=resolved_layout
                )

                barcode_pixmap = None
                if side is CardSide.FRONT:
                    barcode_pixmap, payload, _ = self.barcode_pixmap(student, dpi)
                    if payload is None:
                        raise CardGenerationError(
                            f"{name} has no barcode payload on file, so no card was "
                            f"written: a card without one cannot be scanned. Record "
                            f"students.barcode_payload first."
                        )

                result = self.render(
                    content,
                    template=template,
                    layout=resolved_layout,
                    dpi=dpi,
                    barcode_image=barcode_pixmap,
                    verify_barcode=True,
                )
                if side is CardSide.FRONT and not result.barcode_verified:
                    raise CardGenerationError(
                        f"The barcode rendered onto {name}'s card did not decode back "
                        f"to {result.payload} using this application's decoder, so the "
                        f"card was not written."
                    )

                path = self.output_dir / (
                    f"ID_{reference}_{template.key}_{side.value}_{dpi}dpi.png"
                )
                if not image_save(result.image, path):
                    raise CardGenerationError(f"The card image could not be written to {path}.")

                written[f"{side.value}_image_path"] = settings.store_path(path)
                last = result

        except CardGenerationError as exc:
            self._record_failure(student_id, template.key, str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 - converted, recorded, re-raised
            logger.exception("Card generation failed for student %s", student_id)
            message = f"The card for {name} could not be generated: {exc}"
            self._record_failure(student_id, template.key, message)
            raise CardGenerationError(message) from exc

        self.card_repo.upsert_generated(
            student_id,
            template.key,
            front_image_path=written.get("front_image_path"),
            back_image_path=written.get("back_image_path"),
            custom_fields={
                "layout": resolved_layout.to_dict(),
                "fields": dict(custom or {}),
            },
            generated_by=self._safe_actor(generated_by),
        )
        assert last is not None
        return last

    def _record_failure(self, student_id: int, template_key: str, message: str) -> None:
        try:
            self.card_repo.record_error(student_id, template_key, message)
        except DatabaseUnavailableError:
            logger.warning(
                "Could not record the card failure for student %s: the database is down",
                student_id,
            )
        except Exception:  # noqa: BLE001 - never mask the original failure
            logger.exception(
                "Could not record the card failure for student %s", student_id
            )

    def _safe_actor(self, user_id: Optional[int]) -> Optional[int]:
        """The audit user, or ``None`` when that account does not exist.

        ``users`` is empty on a freshly initialised database, so writing
        ``settings.DEFAULT_USER_ID`` into a foreign-keyed audit column would fail
        the whole insert with ``ERROR 1452``. See
        :func:`database.connection.existing_user_id`.
        """
        candidate = user_id if user_id is not None else settings.DEFAULT_USER_ID
        try:
            return existing_user_id(candidate)
        except DatabaseUnavailableError:
            logger.warning("Could not verify the acting user; recording a NULL audit column.")
            return None
        except Exception:  # noqa: BLE001
            logger.exception("Could not verify the acting user; recording a NULL audit column.")
            return None

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def save_png(self, image: QImage, path: Path) -> Path:
        """Write ``image`` to ``path`` with its physical resolution embedded."""
        path = Path(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CardGenerationError(
                f"The folder {path.parent} could not be created: {exc}"
            ) from exc
        if not image_save(image, path):
            raise CardGenerationError(f"The image could not be written to {path}.")
        return path

    def export_pdf(
        self,
        front: QImage,
        back: Optional[QImage],
        path: Path,
        *,
        title: str = "Student ID Card",
    ) -> Path:
        """Write a print-ready PDF whose page is exactly CR80.

        ``QPdfWriter`` gets a custom page size in millimetres and zero margins,
        so the page *is* 85.60 x 53.98 mm and the card covers it edge to edge.
        Sizing the card to fit A4 would produce a PDF that prints on the wrong
        stock; sizing the page from the screen preview would size it from
        whatever panel the preview happened to be in.

        Two pages are written when both faces are supplied, in the order given.
        """
        from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter

        path = Path(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CardGenerationError(
                f"The folder {path.parent} could not be created: {exc}"
            ) from exc

        writer = QPdfWriter(str(path))
        writer.setPageSize(
            QPageSize(
                QSizeF(CR80_WIDTH_MM, CR80_HEIGHT_MM),
                QPageSize.Unit.Millimeter,
                "CR80",
                QPageSize.SizeMatchPolicy.ExactMatch,
            )
        )
        writer.setPageOrientation(QPageLayout.Orientation.Portrait)
        writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
        writer.setTitle(title)
        writer.setCreator(settings.APP_NAME)

        pages = [img for img in (front, back) if img is not None and not img.isNull()]
        if not pages:
            raise CardGenerationError("There is no rendered card to export.")

        for image in pages:
            painter = QPainter()
            if not painter.begin(writer):
                raise CardGenerationError(
                    f"The PDF writer could not open {path} for writing."
                )
            try:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawImage(QRectF(0, 0, CR80_WIDTH_MM, CR80_HEIGHT_MM), image)
            finally:
                painter.end()

        if not path.exists() or path.stat().st_size == 0:
            raise CardGenerationError(f"The PDF at {path} was not written.")
        return path

    def print_card(self, image: QImage, *, doc_name: str = "Student ID Card",
                   parent=None) -> bool:
        """Open the system print dialog for a card.

        Returns ``True`` only when the operator accepted the dialog *and* the
        painter started on the selected printer. It deliberately does not claim
        the job printed: ``QPrintDialog`` has no completion signal, so the caller
        records a print *request*.
        """
        from PySide6.QtGui import QPageLayout, QPageSize
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(doc_name)
        printer.setPageSize(
            QPageSize(
                QSizeF(CR80_WIDTH_MM, CR80_HEIGHT_MM),
                QPageSize.Unit.Millimeter,
                "CR80",
                QPageSize.SizeMatchPolicy.ExactMatch,
            )
        )
        printer.setPageOrientation(QPageLayout.Orientation.Portrait)
        printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
        printer.setFullPage(True)

        dialog = QPrintDialog(printer, parent)
        dialog.setWindowTitle("Print Student ID Card")
        dialog.setOption(QPrintDialog.PrintDialogOption.PrintPageRange, False)
        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            return False

        painter = QPainter()
        if not painter.begin(printer):
            logger.error("Could not start painting onto the selected printer")
            return False
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(QRectF(0, 0, CR80_WIDTH_MM, CR80_HEIGHT_MM), image)
        finally:
            painter.end()
        return True

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def status_for(
        self, student_id: int, template_key: str = DEFAULT_TEMPLATE_KEY
    ) -> CardStatus:
        card = self.card_repo.get_by_student(student_id, get_template(template_key).key)
        return card.status if card else CardStatus.PENDING

    def status_map(
        self, student_ids: Sequence[int], template_key: str = DEFAULT_TEMPLATE_KEY
    ) -> Dict[int, CardStatus]:
        """Statuses for a whole page of students, in one query."""
        cards = self.card_repo.status_map_for_students(
            list(student_ids), get_template(template_key).key
        )
        return {
            student_id: cards.get(student_id, IdCard(student_id=student_id)).status
            for student_id in student_ids
        }

    def record_print_requested(
        self, student_id: int, template_key: str = DEFAULT_TEMPLATE_KEY
    ) -> Optional[IdCard]:
        """Record that a print was requested -- never that it printed."""
        return self.card_repo.record_print_requested(
            student_id, get_template(template_key).key
        )

    def missing_card_student_ids(
        self, template_key: str = DEFAULT_TEMPLATE_KEY
    ) -> List[int]:
        """Students with no usable card under this template."""
        return self.card_repo.missing_card_student_ids(get_template(template_key).key)

    def count_cards(self, template_key: Optional[str] = None) -> int:
        return self.card_repo.count(get_template(template_key).key if template_key else None)


# ----------------------------------------------------------------------
# Module helpers
# ----------------------------------------------------------------------


def card_gap(grid: QRectF) -> float:
    """Horizontal breathing room between two-column field cells."""
    return grid.width() * 0.03


def _fit_rect(source: QRectF, target: QRectF) -> QRectF:
    """Centre ``source`` inside ``target`` without changing its aspect ratio.

    Equivalent to ``source.scaled(target.size(), KeepAspectRatio)`` -- which
    ``QRectF`` has no method for -- but computed here so the logo is neither
    stretched nor cropped on a plate whose aspect ratio differs from its own.
    """
    if source.isEmpty() or target.isEmpty():
        return target
    scale = min(target.width() / source.width(), target.height() / source.height())
    width = source.width() * scale
    height = source.height() * scale
    return QRectF(
        target.center().x() - width / 2.0,
        target.center().y() - height / 2.0,
        width,
        height,
    )


def image_save(image: QImage, path: Path) -> bool:
    """``QImage.save`` with the format taken from the suffix, errors logged."""
    path = Path(path)
    suffix = path.suffix or ".png"
    if image.save(str(path), suffix.lstrip(".").upper()):
        return True
    logger.error("QImage.save failed for %s", path)
    return False


def _draw_elided(painter: QPainter, rect: QRectF, text: str, flags: int) -> None:
    """Draw ``text`` inside ``rect``, eliding it when it does not fit.

    ``QPainter.drawText(rect, flags, text)`` will happily paint a right-aligned
    string wider than ``rect`` *outside* the rectangle, straight over whatever
    sits next to it -- which is how "3rd Year" came to overwrite the tail of its
    own "YEAR LEVEL" label. The elide mode has to be applied here because
    ``TextElideMode`` cannot be passed in the flags argument: its low bits
    collide with ``Qt::AlignmentFlag`` and Qt reads them as alignment.
    """
    if not text:
        return
    painter.drawText(
        rect,
        flags,
        painter.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, int(rect.width())
        ),
    )


def font_point_pct(font: QFont, card_height: float, dpi: int) -> float:
    """The card-height percentage a ``font``'s size corresponds to.

    The inverse of :meth:`IdCardService._font`, used to keep shrinking a font
    without having to guess its original percentage.
    """
    from PySide6.QtGui import QFontMetricsF

    points = float(font.pointSizeF())
    pixels = points * float(dpi or 1) / 72.0
    return pixels / float(card_height) if card_height else 0.0


def painter_font_advance(painter: QPainter, font: QFont, text: str) -> float:
    """Width ``text`` will occupy in ``font`` on this painter, in pixels.

    Measured through the painter so the answer matches what will actually be
    drawn; a font-metrics object built without a device reports logical-DPI
    widths, which on a 300 DPI card are a third of the real figure.
    """
    previous = painter.font()
    painter.setFont(font)
    width = float(painter.fontMetrics().horizontalAdvance(text))
    painter.setFont(previous)
    return width


def _font_metrics_height(painter: QPainter, font: QFont) -> float:
    """The pixel height ``font`` will actually occupy *on this painter*.

    Measured through the painter rather than through a standalone
    ``QFontMetricsF``, deliberately: a font-metrics object built without a device
    reports heights at the application's logical DPI, while the card is painted
    onto an image tagged at the render resolution. At 300 DPI that made every
    row height a third of what was drawn, and the identity fields overlapped
    each other on the finished card.
    """
    previous = painter.font()
    painter.setFont(font)
    height = painter.fontMetrics().height()
    painter.setFont(previous)
    return float(height)


def _wrap_text(metrics, text: str, width: int) -> List[str]:
    """Greedy word wrap, with an over-long single word broken by character."""
    if not text:
        return []
    lines: List[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if not current or metrics.horizontalAdvance(candidate) <= width:
            current = candidate
            while current and metrics.horizontalAdvance(current) > width and len(current) > 1:
                cut = len(current)
                while cut > 1 and metrics.horizontalAdvance(current[:cut]) > width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


__all__ = [
    "CARD_BARCODE_MODULE_HEIGHT_MM",
    "CARD_BARCODE_MODULE_WIDTH_MM",
    "CARD_BARCODE_QUIET_ZONE_MM",
    "ID_CARD_OUTPUT_DIRNAME",
    "CardGenerationError",
    "CardPreviewData",
    "IdCardService",
]