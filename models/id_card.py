"""Data types for the Student ID Card Generator.

Everything the generator needs to describe *a card* lives here rather than in the
widget that draws it: the persistence row, the four states a card can be in, the
two sides, the CR80 physical dimensions, and the layout knobs an operator is
allowed to turn.

Two rules are encoded rather than left to convention:

* **CR80 is a physical size, not a pixel size.** :data:`CR80_WIDTH_MM` /
  :data:`CR80_HEIGHT_MM` are the ISO/IEC 7810 ID-1 dimensions used by virtually
  every plastic student card. Raster and PDF output derive their size from them
  at a stated resolution (:func:`pixels_for_dpi`), so a card exported at 300 DPI
  is 1011 x 638 px and a card exported as PDF is 85.60 x 53.98 mm. A preview that
  is scaled to whatever the panel happens to be is a *view*, never the source of
  the output size.

* **A missing value is a placeholder, never an invention.** Back-side fields the
  database does not hold (:class:`CardField` with a value of ``None``) render as
  a neutral "not on file" marker. Nothing in this module supplies a default
  emergency contact, a signature, or an expiry date.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Physical card geometry
# --------------------------------------------------------------------------

#: ISO/IEC 7810 ID-1, the de-facto standard student card size (CR80).
CR80_WIDTH_MM = 85.60
CR80_HEIGHT_MM = 53.98

#: Pixel densities a card can be exported at. 300 DPI is what the card printer
#: sub-system expects; 600 is available for a card that will be re-impressed.
EXPORT_DPI_CHOICES: Tuple[int, ...] = (150, 300, 600)

#: Default export density. 300 DPI over CR80 gives 1011 x 638 px, which is
#: coarse enough to keep a batch export fast and fine enough to scan.
DEFAULT_EXPORT_DPI = 300

MM_PER_INCH = 25.4


def pixels_for_dpi(
    dpi: int = DEFAULT_EXPORT_DPI,
    *,
    portrait: bool = False,
) -> Tuple[int, int]:
    """Pixel size of a card rendered at ``dpi``.

    Parameters
    ----------
    dpi:
        Target print resolution. Values below 150 are refused, because a
        barcode on a 1011 px-wide card stops being readable below that and a
        silently degraded card is worse than a rejected export.
    portrait:
        ``True`` returns height x width for a portrait-oriented card. The
        generator only produces landscape CR80 today; the argument exists so a
        future portrait template does not have to change the call sites that
        already reason in millimetres.

    Notes
    -----
    ``round`` is applied at the very end rather than to each dimension
    independently, so the pixel size is exactly the physical size converted once
    -- 85.60 mm at 300 DPI is 1011.02 px, and rounding each term separately is
    how an export ends up one pixel off the physical size on two sides.
    """
    density = int(dpi)
    if density < 150:
        raise ValueError(f"dpi must be at least 150 (got {dpi})")
    if density > 2400:
        raise ValueError(f"dpi above 2400 is not supported (got {dpi})")

    width_mm, height_mm = (
        (CR80_HEIGHT_MM, CR80_WIDTH_MM) if portrait else (CR80_WIDTH_MM, CR80_HEIGHT_MM)
    )
    return (
        round(width_mm / MM_PER_INCH * density),
        round(height_mm / MM_PER_INCH * density),
    )


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


class CardStatus(str, Enum):
    """The state of a student's card, as the database records it.

    There is deliberately no ``printed`` member. This application cannot verify
    that a print job reached a printer: ``QPrintDialog`` reports only that the
    operator accepted a dialog, and there is no spooler integration to ask
    afterwards. Recording ``printed`` on a button click would put a claim in the
    database that nothing in the system can stand behind, so the honest closest
    state is :attr:`PRINT_REQUESTED` -- "the operator asked for this to be
    printed", timestamped when the dialog was accepted.
    """

    #: No card has been generated through this application.
    PENDING = "pending"
    #: A card was rendered and its image written.
    GENERATED = "generated"
    #: The print dialog was accepted; print completion is unverified.
    PRINT_REQUESTED = "print_requested"
    #: The most recent generation attempt failed; ``last_error`` says why.
    ERROR = "error"

    @property
    def label(self) -> str:
        return {
            CardStatus.PENDING: "Pending",
            CardStatus.GENERATED: "Generated",
            CardStatus.PRINT_REQUESTED: "Print Requested",
            CardStatus.ERROR: "Error",
        }[self]

    @property
    def tone(self) -> str:
        """A tone word, so every screen colours this status the same way.

        ``Print Requested`` is deliberately *not* green: it is the weakest
        positive signal the system can honestly give, and colouring it like a
        completed print would reintroduce exactly the lie this enum exists to
        avoid.
        """
        return {
            CardStatus.PENDING: "neutral",
            CardStatus.GENERATED: "success",
            CardStatus.PRINT_REQUESTED: "warning",
            CardStatus.ERROR: "error",
        }[self]

    @property
    def counts_as_generated(self) -> bool:
        """Whether a usable card image exists for this status."""
        return self in (CardStatus.GENERATED, CardStatus.PRINT_REQUESTED)


class CardSide(str, Enum):
    """Which face of the card is being rendered."""

    FRONT = "front"
    BACK = "back"

    @property
    def label(self) -> str:
        return "Front Side" if self is CardSide.FRONT else "Back Side"


# --------------------------------------------------------------------------
# Templates and layout
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CardTemplate:
    """One selectable card layout.

    ``official_asset`` records what the template actually uses. Only
    ``assets/logos/wit_logo.png`` exists in this project, so every template is
    built on that single official logo and differs in *layout and accent*, not
    in artwork. That distinction is stated rather than glossed: a department
    layout configured here is a presentation choice made by an operator, and is
    not evidence that a college approved a card design.
    """

    key: str
    label: str
    #: Accent colour for rules, the header band and the logo tint.
    accent: str
    #: Institution name printed on the header band.
    institution: str
    subtitle: str
    official_asset: str
    description: str = ""
    #: Show the program as a prominent block rather than a single line.
    emphasize_program: bool = False


#: The selectable templates. Kept as a tuple so the dropdown order is stable.
CARD_TEMPLATES: Tuple[CardTemplate, ...] = (
    CardTemplate(
        key="official_wit",
        label="Official WIT Student ID",
        accent="#EE6B05",
        institution="WESTERN INSTITUTE OF TECHNOLOGY",
        subtitle="Student Identity Card",
        official_asset="assets/logos/wit_logo.png",
        description=(
            "The standard WIT layout: institutional header, photograph on the "
            "left, identity fields and barcode on the right."
        ),
    ),
    CardTemplate(
        key="college_of_engineering",
        label="College of Engineering",
        accent="#C2410C",
        institution="COLLEGE OF ENGINEERING",
        subtitle="Western Institute of Technology — Student ID",
        official_asset="assets/logos/wit_logo.png",
        description=(
            "An engineering-flavoured layout that promotes the programme. This "
            "is a configurable presentation layout built on the official WIT "
            "logo, not a separately approved college card design."
        ),
        emphasize_program=True,
    ),
    CardTemplate(
        key="faculty_staff",
        label="Faculty / Staff",
        accent="#B45309",
        institution="WESTERN INSTITUTE OF TECHNOLOGY",
        subtitle="Faculty & Staff Identity Card",
        official_asset="assets/logos/wit_logo.png",
        description=(
            "Uses the personnel vocabulary (department rather than programme, "
            "employee fields left blank rather than invented) for non-student "
            "holders. A configurable layout on the official logo."
        ),
    ),
)

TEMPLATES_BY_KEY: Dict[str, CardTemplate] = {t.key: t for t in CARD_TEMPLATES}
DEFAULT_TEMPLATE_KEY = "official_wit"


def get_template(key: str) -> CardTemplate:
    """The template for ``key``, falling back to the official layout.

    A stored ``template_key`` that no longer resolves must not crash the page on
    a record somebody generated under an older build; it falls back to the
    official layout and says so in the editor instead.
    """
    return TEMPLATES_BY_KEY.get(str(key or "").strip(), TEMPLATES_BY_KEY[DEFAULT_TEMPLATE_KEY])


@dataclass
class CardLayout:
    """Operator-adjustable layout knobs.

    Every value is a *ratio* or a *point size*, never an absolute pixel count, so
    the same layout produces the same card at 150, 300 or 600 DPI and in the
    preview, in a PNG and in a PDF. Absolute pixel geometry is the reason a card
    that looked right on screen came out with a clipped photo in the export.
    """

    #: Photograph box, as fractions of the card's width/height.
    photo_x: float = 0.045
    photo_y: float = 0.200
    photo_w: float = 0.170
    photo_h: float = 0.470

    #: Barcode block, as fractions of the card's width/height.
    #:
    #: Sized so a nine-digit CODE39 symbol lands at **native resolution**. The
    #: generator renders it at 0.42 mm per module -- about 915 x 112 px on a
    #: 1011 x 638 CR80 export at 300 DPI -- so a block 0.92 wide and 0.24 tall
    #: leaves a 930 x 113 symbol area, and ``KeepAspectRatio`` fits 1:1.
    #:
    #: Both dimensions matter. Too narrow and the symbol is scaled *down*, below
    #: the module width zbar reads; too short and the caption leaves no room for
    #: the bars at all. A card whose barcode renders but does not scan is the
    #: failure this geometry exists to prevent, and
    #: :meth:`IdCardService.verify_rendered_barcode` fails the card if it happens.
    barcode_x: float = 0.040
    barcode_y: float = 0.725
    barcode_w: float = 0.920
    barcode_h: float = 0.240

    #: Text block, as fractions of the card's width/height. Given the whole band
    #: between the header and the barcode so a five-field identity block fits
    #: without dropping anything: the renderer reduces type and reports a drop
    #: rather than overlapping rows, so the box has to be sized for the worst
    #: case (the longest name plus every field visible) rather than tuned to
    #: the three students currently in the database.
    text_x: float = 0.235
    text_y: float = 0.185
    text_w: float = 0.725
    text_h: float = 0.528

    #: Type sizes, as a percentage of the card height. Printing an 11 pt name on
    #: an 86 mm card is legible; printing an 11 px name is not.
    #:
    #: The defaults are derived from the block above rather than chosen by eye.
    #: The text band is 0.528 x 638 = 337 px; the name takes one 57 px line plus
    #: 29 px of rule and spacing, leaving 251 px. Three field rows (programme
    #: alone, identifiers paired, year and section paired) divide that to 84 px,
    #: which fits a 37 px label metric above a 41 px value metric. ``name_size_pct``
    #: keeps the longest name on file ("Javy Danielle N. Caporal") on one line.
    name_size_pct: float = 0.074
    label_size_pct: float = 0.048
    value_size_pct: float = 0.053
    mono_size_pct: float = 0.052

    text_align: str = "left"          # left | center
    photo_align: str = "left"         # left | center
    photo_size_pct: float = 100.0     # 70..140, scales the photo inside its box

    #: Optional fields. The barcode is *not* optional: it is the identifier the
    #: scanner verifies, so there is no toggle that can remove it.
    show_reference: bool = True
    show_year_level: bool = True
    show_section: bool = True
    show_program: bool = True
    show_student_id: bool = True
    show_barcode_text: bool = True
    show_institution_header: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "photo_x": self.photo_x, "photo_y": self.photo_y,
            "photo_w": self.photo_w, "photo_h": self.photo_h,
            "barcode_x": self.barcode_x, "barcode_y": self.barcode_y,
            "barcode_w": self.barcode_w, "barcode_h": self.barcode_h,
            "text_x": self.text_x, "text_y": self.text_y,
            "text_w": self.text_w, "text_h": self.text_h,
            "name_size_pct": self.name_size_pct,
            "label_size_pct": self.label_size_pct,
            "value_size_pct": self.value_size_pct,
            "mono_size_pct": self.mono_size_pct,
            "text_align": self.text_align,
            "photo_align": self.photo_align,
            "photo_size_pct": self.photo_size_pct,
            "show_reference": self.show_reference,
            "show_year_level": self.show_year_level,
            "show_section": self.show_section,
            "show_program": self.show_program,
            "show_student_id": self.show_student_id,
            "show_barcode_text": self.show_barcode_text,
            "show_institution_header": self.show_institution_header,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "CardLayout":
        """Build a layout from stored JSON, ignoring anything unrecognised.

        Unknown keys are dropped and wrong types fall back to the default rather
        than raising: a layout blob written by a newer build must still render a
        card on an older one.
        """
        layout = cls()
        if not isinstance(data, dict):
            return layout
        for name, value in data.items():
            if not hasattr(layout, name):
                continue
            current = getattr(layout, name)
            try:
                if isinstance(current, bool):
                    setattr(layout, name, bool(value))
                elif isinstance(current, str):
                    setattr(layout, name, str(value))
                else:
                    setattr(layout, name, float(value))
            except (TypeError, ValueError):
                logger.warning("Ignoring unusable stored layout value for %r", name)
        layout.clamp()
        return layout

    def clamp(self) -> None:
        """Force the layout back inside ranges that cannot clip the card.

        Clamping is applied at construction *and* after every edit, so a slider
        dragged to its extreme degrades the layout rather than pushing the
        barcode off the card.
        """
        self.photo_w = _clamp(self.photo_w, 0.10, 0.60)
        self.photo_h = _clamp(self.photo_h, 0.15, 0.90)
        self.photo_x = _clamp(self.photo_x, 0.0, 1.0 - self.photo_w)
        self.photo_y = _clamp(self.photo_y, 0.0, 1.0 - self.photo_h)

        self.barcode_w = _clamp(self.barcode_w, 0.20, 0.95)
        self.barcode_h = _clamp(self.barcode_h, 0.08, 0.55)
        self.barcode_x = _clamp(self.barcode_x, 0.0, 1.0 - self.barcode_w)
        self.barcode_y = _clamp(self.barcode_y, 0.0, 1.0 - self.barcode_h)

        self.text_w = _clamp(self.text_w, 0.20, 0.98)
        self.text_h = _clamp(self.text_h, 0.10, 0.80)
        self.text_x = _clamp(self.text_x, 0.0, 1.0 - self.text_w)
        self.text_y = _clamp(self.text_y, 0.0, 1.0 - self.text_h)

        self.name_size_pct = _clamp(self.name_size_pct, 0.055, 0.115)
        self.label_size_pct = _clamp(self.label_size_pct, 0.038, 0.075)
        self.value_size_pct = _clamp(self.value_size_pct, 0.050, 0.105)
        self.mono_size_pct = _clamp(self.mono_size_pct, 0.048, 0.100)
        self.photo_size_pct = _clamp(self.photo_size_pct, 70.0, 140.0)

        if self.text_align not in ("left", "center"):
            self.text_align = "left"
        if self.photo_align not in ("left", "center"):
            self.photo_align = "left"


def _clamp(value: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return low
    if number != number:  # NaN
        return low
    return max(low, min(high, number))


# --------------------------------------------------------------------------
# Card content
# --------------------------------------------------------------------------


@dataclass
class CardField:
    """One labelled value on a card.

    ``value is None`` is meaningful and is rendered as a visible placeholder. It
    is the whole point of the type: the alternative -- an empty string, or a
    plausible default -- is how a card ends up carrying a signature, a phone
    number or an expiry date that nobody on file ever supplied.
    """

    label: str
    value: Optional[str] = None
    #: Marker shown when ``value`` is None. Never a plausible-looking value.
    placeholder: str = "— not on file —"
    #: Render in the monospace face (identifiers, payloads).
    mono: bool = False

    @property
    def present(self) -> bool:
        return bool(self.value and str(self.value).strip())

    @property
    def display(self) -> str:
        return str(self.value).strip() if self.present else self.placeholder

    @property
    def is_placeholder(self) -> bool:
        return not self.present


@dataclass
class CardContent:
    """Everything one side of a card prints, already resolved from real records.

    Built by :mod:`services.id_card_service` from the student row, the barcode
    payload the scanner verifies, and whatever contacts are actually in
    ``student_contacts``. Nothing here is synthesised.

    The identity fields are carried as their own attributes rather than looked
    up from the ``Student`` at paint time, so a rendered card depends only on
    this object -- a card cannot end up showing one student's photograph next to
    another student's reference number because a lookup returned a different
    row part-way through drawing.
    """

    side: CardSide
    student_id: Optional[int]
    full_name: str = ""
    #: The student's own identifiers, echoed on the back so a back-only print is
    #: still attributable to a person.
    student_code: Optional[str] = None
    reference_number: Optional[str] = None
    program: Optional[str] = None
    fields: List[CardField] = field(default_factory=list)
    barcode_value: Optional[str] = None
    barcode_text: str = ""
    #: Populated only when a real photograph exists for this student.
    photo_path: Optional[str] = None
    photo_missing_reason: str = ""
    #: Operator-entered text from the Customize Details panel.
    custom: Dict[str, str] = field(default_factory=dict)

    def field(self, name: str) -> Optional[CardField]:
        return next((f for f in self.fields if f.label == name), None)


@dataclass
class CardRenderResult:
    """What one render produced, including whether the barcode survived it."""

    image: Any                      # QImage
    template: CardTemplate
    side: CardSide
    dpi: int
    payload: Optional[str]
    #: True when the rendered bitmap was decoded back to :attr:`payload` using
    #: the application's own decoder. A card whose barcode does not decode is
    #: not a card this system should be printing.
    barcode_verified: bool = False
    barcode_checked: bool = False
    warnings: List[str] = field(default_factory=list)


@dataclass
class IdCard:
    """One row of the ``id_cards`` table."""

    id: Optional[int] = None
    student_id: int = 0
    template_key: str = DEFAULT_TEMPLATE_KEY
    front_image_path: Optional[str] = None
    back_image_path: Optional[str] = None
    pdf_path: Optional[str] = None
    status: CardStatus = CardStatus.PENDING
    last_error: Optional[str] = None
    render_count: int = 0
    #: JSON-serialised operator overrides; see :meth:`id_card_repository`.
    custom_fields: Optional[str] = None
    generated_by: Optional[int] = None
    last_generated_at: Optional[Any] = None
    last_print_requested_at: Optional[Any] = None
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None

    @property
    def has_usable_card(self) -> bool:
        return bool(self.status.counts_as_generated)

    def image_path_for(self, side: CardSide) -> Optional[str]:
        return self.front_image_path if side is CardSide.FRONT else self.back_image_path


__all__ = [
    "CR80_HEIGHT_MM",
    "CR80_WIDTH_MM",
    "CARD_TEMPLATES",
    "DEFAULT_EXPORT_DPI",
    "DEFAULT_TEMPLATE_KEY",
    "EXPORT_DPI_CHOICES",
    "MM_PER_INCH",
    "TEMPLATES_BY_KEY",
    "CardContent",
    "CardField",
    "CardLayout",
    "CardRenderResult",
    "CardSide",
    "CardStatus",
    "CardTemplate",
    "IdCard",
    "get_template",
    "pixels_for_dpi",
]