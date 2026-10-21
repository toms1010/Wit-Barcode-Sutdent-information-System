import cv2
import numpy as np
from pyzbar import pyzbar
from typing import NamedTuple, Optional, List, Tuple, Callable
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class BarcodeRect(NamedTuple):
    """A symbol's box: ``(left, top, width, height)``.

    pyzbar hands back a ``Rect`` namedtuple and the rest of this project was
    written against that shape -- ``rect.left``, ``rect.width`` -- while the
    zxing reader had to build the same box from four plain corner points.
    Rather than force every caller to remember which reader produced a result,
    both readers now report this one type. It is a ``NamedTuple``, so it also
    unpacks and indexes exactly like the plain four-tuple it replaced:

        left, top, width, height = item.bbox
        item.bbox[2] == item.bbox.width
    """

    left: int
    top: int
    width: int
    height: int

#: The second reader. zxing-cpp reads small, blurred and low-contrast symbols
#: that zbar gives up on, which is the difference between "the card is in the
#: frame" and "the card is far enough away to be unreadable".
#:
#: Measured on this project's own cards (symbol width in frame pixels, read
#: straight off a scaled band):
#:
#:     card A  zbar 210 px   zxing 183 px
#:     card B  zbar 191 px   zxing 167 px
#:     card C  zbar 204 px (interpolation)   zxing 204 px (untouched)
#:
#: and zxing read all three full ID cards *without* any preprocessing, where
#: zbar needed an interpolated copy of card C. The dependency is optional: when
#: it is missing the decoder falls back to zbar alone and everything else in
#: the application keeps working.
try:  # pragma: no cover - the import itself is the test
    import zxingcpp

    ZXING_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency may be absent
    zxingcpp = None
    ZXING_AVAILABLE = False
    logger.info(
        "zxing-cpp is not installed; barcode decoding falls back to pyzbar alone. "
        "Install it (pip install zxing-cpp) to read smaller symbols."
    )

#: Symbology names normalised to the spelling pyzbar uses, so the decoder, the
#: database, the UI and the reports all speak one vocabulary. pyzbar reports
#: "CODE39"; zxing reports "Code39".
ZXING_FORMAT_NAMES = {
    "Codabar": "CODABAR",
    "Code39": "CODE39",
    "Code39Ext": "CODE39",
    "Code93": "CODE93",
    "Code128": "CODE128",
    "DataMatrix": "DATAMATRIX",
    "EAN-13": "EAN-13",
    "EAN-8": "EAN-8",
    "ITF": "ITF",
    "PDF417": "PDF417",
    "QRCode": "QRCODE",
    "UPC-A": "UPC-A",
    "UPC-E": "UPC-E",
}

#: pyzbar reports degenerate rectangles (0 x 0) on some preprocessing variants
#: of the same image. They are decoder noise, never a usable symbol, and they
#: would otherwise be handed to the crop logic as if they were a detection.
MIN_SYMBOL_SIDE_PX = 2

#: Small clockwise/counter-clockwise tilts tried when a crop is found but cannot
#: be read. These are the angles a hand-held card realistically produces; beyond
#: roughly 12 degrees the bars blur together and no filter recovers them.
ROTATION_ANGLES_DEG = (-9.0, -6.0, -3.0, 3.0, 6.0, 9.0)

#: A crop narrower than this is treated as *undersampled* rather than merely
#: untidy, and gets an interpolated copy of itself tried before any filter.
#:
#: Measured on this project's own symbols rather than guessed. barcode_2's
#: 579x124 band decodes untouched down to ~260 px wide and stops decoding at
#: 230 px, where 2x bicubic interpolation recovers it again down to ~200 px;
#: ``assets/images/Id_3.png`` sits inside that dead band with a 204x58 symbol.
#: But crop width alone is not the predictor: a card composited close to the
#: camera produced a 528 px crop whose 304 px symbol still only decoded after
#: interpolation, because the enlargement had already aliased the bar edges.
#: The cut-off is therefore drawn generously, at roughly a 720p frame width --
#: above it, upscaling the crop costs more than it can recover.
MAX_UPSCALED_CROP_WIDTH_PX = 1024

#: Interpolation factors tried, in order. 2x is the one that recovers the
#: measured band; 3x covers crops that arrive even smaller, and 4x is the deep
#: fallback for a symbol that is only a handful of pixels per module.
UPSCALE_FACTORS = (2, 3, 4)

#: Strength and radius of the unsharp mask applied to an interpolated crop.
#:
#: Interpolation alone re-samples the bars; it does not restore the contrast
#: between a bar and its neighbour, so a decoder that thresholds on a local
#: average still sees a washed-out symbol. Sharpening after the enlargement
#: gave the measured recovery: card B's symbol at 167 px, which no zbar
#: variant read, became readable with 3x Lanczos followed by this mask.
UNSHARP_AMOUNT = 1.5
UNSHARP_RADIUS = 1.0

#: Widest crop the CODE39 reconstruction is allowed to look at. The
#: reconstruction is a last resort -- it only runs after both readers have
#: failed -- and its cost grows with the crop, so it is not worth running on
#: a wide crop that a reader would have read anyway.
MAX_RECOVERY_CROP_WIDTH_PX = 760


def boxes_touch(a: BarcodeRect, b: BarcodeRect, slack: int = 4) -> bool:
    """True when two rectangles overlap, or nearly so.

    "Nearly" matters because the two readers do not agree on a symbol's
    exact extent: one reports the inked bars, the other the whole symbol
    including its quiet zone, and a couple of pixels of difference must not
    make the same physical symbol look like two.
    """
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw + slack <= bx
        or bx + bw + slack <= ax
        or ay + ah + slack <= by
        or by + bh + slack <= ay
    )


def zxing_polygon(obj) -> List[Tuple[int, int]]:
    """The four corners of a zxing result, in image pixels.

    zxing-cpp exposes the corners as named attributes
    (``top_left``/``top_right``/``bottom_right``/``bottom_left``) rather than
    as an iterable sequence, and the attribute set has changed between
    releases, so a missing corner yields no polygon at all rather than a
    partial one. The corner order is the same either way: clockwise from the
    top left.
    """
    position = getattr(obj, "position", None)
    if position is None:
        return []

    corners: List[Tuple[int, int]] = []
    for name in ("top_left", "top_right", "bottom_right", "bottom_left"):
        point = getattr(position, name, None)
        if point is None:
            return []
        corners.append((int(point.x), int(point.y)))
    return corners


@dataclass
class DecodedBarcode:
    data: str
    type: str
    bbox: BarcodeRect
    polygon: List[Tuple[int, int]]
    quality: int
    #: Which prepared image produced this symbol ("original", "upscale2x", ...).
    attempt: Optional[str] = None
    #: How much that image was enlarged relative to the input. Always 1.0 for
    #: the untouched crop.
    scale: float = 1.0
    #: The box in the **input image's** pixels, whatever the attempt was.
    #: :attr:`bbox` and :attr:`polygon` are reported in the coordinates of the
    #: image actually handed to zbar, which for an interpolation attempt is
    #: larger than the caller's crop; this is the same box mapped back, so a
    #: caller annotating an image never has to know which variant won.
    source_bbox: Optional[BarcodeRect] = None
    source_polygon: List[Tuple[int, int]] = field(default_factory=list)


class DecodeAttempt:
    """One prepared image handed to a reader, and how it was produced.

    The image is built **on demand**. The chain of attempts contains several
    enlargements and filters, and building them all costs about 300 ms on a
    740x163 crop -- paid on every frame, whether or not the first attempt
    already answered. A reader that needs the deep variants should pay for
    them; one that reads the crop as-is should not.
    """

    def __init__(
        self,
        name: str,
        image: Optional[np.ndarray] = None,
        factory: Optional[Callable[[], np.ndarray]] = None,
    ):
        self.name = name
        self._image = image
        self._factory = factory
        self._built: Optional[np.ndarray] = None

    @property
    def image(self) -> np.ndarray:
        """The prepared image, built once and remembered."""
        if self._built is None:
            if self._image is not None:
                self._built = self._image
            elif self._factory is not None:
                self._built = self._factory()
            else:
                raise ValueError(f"attempt {self.name!r} has no image to build")
        return self._built

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"DecodeAttempt({self.name!r})"


def unsharp(image: np.ndarray, amount: float = UNSHARP_AMOUNT,
            radius: float = UNSHARP_RADIUS) -> np.ndarray:
    """Sharpen an image without moving its edges.

    Interpolation re-samples the bars but leaves them low-contrast, because a
    narrow bar that occupied a single source pixel now occupies a couple of
    soft ones. A decoder that thresholds on a local average sees a washed-out
    symbol. Adding the local contrast back is what turned card B's 167 px
    symbol -- which no zbar variant read -- into a readable one.
    """
    if image is None or image.size == 0:
        return image
    blurred = cv2.GaussianBlur(image, (0, 0), max(0.1, radius))
    return cv2.addWeighted(image, 1.0 + amount, blurred, -amount, 0)


def rotate_preserve(image: np.ndarray, angle_deg: float) -> Optional[np.ndarray]:
    """Rotate about the centre, growing the canvas so nothing is cropped away.

    A barcode rotated inside a fixed canvas loses its outer bars, which is worse
    than not rotating at all: the symbol stops being decodable for a reason that
    has nothing to do with the tilt being tested. The border is filled with the
    image's own median colour so the quiet zone stays quiet.
    """
    if image is None or image.size == 0:
        return None

    h, w = image.shape[:2]
    centre = (w / 2.0, h / 2.0)
    matrix = cv2.getRotationMatrix2D(centre, angle_deg, 1.0)

    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w = int(round(h * sin + w * cos))
    new_h = int(round(h * cos + w * sin))
    if new_w <= 0 or new_h <= 0:
        return None

    matrix[0, 2] += new_w / 2.0 - centre[0]
    matrix[1, 2] += new_h / 2.0 - centre[1]

    fill = tuple(int(v) for v in np.median(image.reshape(-1, image.shape[-1]), axis=0))
    return cv2.warpAffine(
        image,
        matrix,
        (new_w, new_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=fill,
    )


def _annotate_geometry(item: DecodedBarcode, attempt: str, scale: float) -> None:
    """Stamp a hit with its attempt, scale, and box in the caller's pixels.

    zbar reports its rectangle in the pixels of whatever image it was handed.
    For an interpolation attempt that image is larger than the caller's, so the
    raw rectangle is *not* a position in the caller's image -- taking it as one
    produced a one-pixel-tall box at the bottom edge of ``Id_3.png`` when the
    dataset builder labelled it. Both coordinate spaces are therefore kept, and
    the mapped one is the one a caller should use.
    """
    item.attempt = attempt
    item.scale = scale
    if scale == 1.0:
        item.source_bbox = BarcodeRect(*item.bbox)
        item.source_polygon = list(item.polygon)
        return

    left, top, width, height = item.bbox
    item.source_bbox = BarcodeRect(
        int(round(left / scale)),
        int(round(top / scale)),
        max(1, int(round(width / scale))),
        max(1, int(round(height / scale))),
    )
    item.source_polygon = [
        (int(round(x / scale)), int(round(y / scale))) for x, y in item.polygon
    ]


@dataclass
class DecodeResult:
    """Outcome of an ordered decode, including which attempt won.

    ``attempt`` is the interesting field for diagnosis: it says whether the
    original crop was readable as-is or whether some preprocessing or rotation
    was required, which is what the robustness report quotes.

    One attempt can legitimately return more than one symbol: a frame holding two
    cards at the same scale was measured returning both ``000065736`` and
    ``000065412`` in a single ``decode_best`` call. :attr:`distinct_values` and
    :attr:`ambiguous` exist so a caller can refuse to choose between them
    instead of silently taking whichever zbar listed first -- that choice is
    arbitrary, and each of those values maps to a different real student.
    """

    barcodes: List[DecodedBarcode]
    attempt: Optional[str] = None
    attempts_tried: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.barcodes)

    @property
    def data(self) -> Optional[str]:
        return self.barcodes[0].data if self.barcodes else None

    @property
    def type(self) -> Optional[str]:
        return self.barcodes[0].type if self.barcodes else None

    @property
    def distinct_values(self) -> List[str]:
        """Every distinct payload read, in the order zbar reported them."""
        seen: List[str] = []
        for item in self.barcodes:
            if item.data not in seen:
                seen.append(item.data)
        return seen

    @property
    def ambiguous(self) -> bool:
        """True when this read contains more than one different payload.

        The same symbol read twice (two boxes, one payload) is not ambiguous:
        there is nothing to choose between.
        """
        return len(self.distinct_values) > 1


class BarcodeDecoderService:
    def __init__(self):
        self.last_decoded = None
        self.last_decode_time = 0

    # -- readers -------------------------------------------------------------

    @staticmethod
    def _read_zxing(
        image: np.ndarray,
        binarizer=None,
    ) -> Tuple[List[DecodedBarcode], List[BarcodeRect]]:
        """Read every symbol zxing-cpp can see in ``image``.

        Returns the symbols zxing vouches for, and the places where it found
        something it *rejected*.

        Results zxing reports as *invalid* are not returned as payloads. zxing
        sets that flag when the symbol's own checksum or format does not hold,
        which is the signature of a misread -- exactly the failure this
        application cannot afford, because a wrong payload is a real student's
        card attributed to the wrong person. A reader that cannot vouch for a
        hit must not return it.

        The rejected places are returned anyway, because "there is a symbol
        here and I cannot vouch for it" is information the caller needs: a
        second reading of that same spot is not a second opinion, it is the
        same doubtful symbol read less carefully.

        ``binarizer`` selects how the image is thresholded. zxing's default
        (local average) copes with uneven lighting, which is what a card held
        under a desk lamp looks like; the fixed threshold copes with high
        contrast, which is what a second symbol in the same frame looks like.
        Neither finds everything the other does.
        """
        if not ZXING_AVAILABLE:
            return [], []
        options = {} if binarizer is None else {"binarizer": binarizer}
        try:
            decoded_objects = zxingcpp.read_barcodes(image, **options)
        except Exception as e:  # noqa: BLE001 - a reader failure is not fatal
            logger.error(f"zxing decode failed: {e}")
            return [], []

        results: List[DecodedBarcode] = []
        rejected: List[BarcodeRect] = []
        for obj in decoded_objects:
            polygon = zxing_polygon(obj)
            if not polygon:
                continue
            xs = [p[0] for p in polygon]
            ys = [p[1] for p in polygon]
            left, top = min(xs), min(ys)
            width, height = max(xs) - left, max(ys) - top
            if width < MIN_SYMBOL_SIDE_PX or height < MIN_SYMBOL_SIDE_PX:
                continue
            fmt = getattr(obj, "format", None)
            name = getattr(fmt, "name", "") or ""
            if not getattr(obj, "valid", True):
                logger.debug(
                    "Discarding zxing result %r with error %s",
                    getattr(obj, "text", None),
                    getattr(obj, "error", None),
                )
                rejected.append(BarcodeRect(left, top, width, height))
                continue
            results.append(DecodedBarcode(
                data=str(getattr(obj, "text", "")),
                type=ZXING_FORMAT_NAMES.get(name, name.replace(" ", "").upper()),
                bbox=BarcodeRect(left, top, width, height),
                polygon=polygon,
                # zxing reports no per-symbol quality figure; the payload
                # length is the only honest proxy available.
                quality=len(str(getattr(obj, "text", ""))),
            ))
        return results, rejected

    @staticmethod
    def _read_zbar(image: np.ndarray) -> List[DecodedBarcode]:
        """Read every symbol pyzbar can see in ``image``."""
        try:
            decoded_objects = pyzbar.decode(image)
        except Exception as e:  # noqa: BLE001 - a reader failure is not fatal
            logger.error(f"Barcode decoding failed: {e}")
            return []

        results = []
        for obj in decoded_objects:
            # Drop zero-area rectangles: zbar emits them for variants it cannot
            # actually read, and they are indistinguishable from a real hit if
            # passed straight through.
            if obj.rect.width < MIN_SYMBOL_SIDE_PX or obj.rect.height < MIN_SYMBOL_SIDE_PX:
                logger.debug(
                    "Discarding degenerate barcode rect %s", obj.rect,
                )
                continue
            try:
                data = obj.data.decode("utf-8")
            except UnicodeDecodeError:
                data = obj.data.decode("latin-1")
            results.append(DecodedBarcode(
                data=data,
                type=obj.type,
                bbox=BarcodeRect(
                    int(obj.rect.left), int(obj.rect.top),
                    int(obj.rect.width), int(obj.rect.height),
                ),
                polygon=[(p.x, p.y) for p in obj.polygon] if obj.polygon else [],
                quality=getattr(obj, "quality", 0),
            ))
        return results

    def _decode_variant(self, image: np.ndarray, variant: str) -> List[DecodedBarcode]:
        """Run the readers on one prepared image and keep only real symbols.

        zxing-cpp goes first because it is the stronger reader on small,
        blurred and low-contrast symbols; pyzbar is the fallback, and is the
        only reader when zxing-cpp is not installed.

        zxing is asked twice, with two different thresholdings, for one reason:
        one thresholding localises one symbol well and then stops, so on its
        own it cannot see that a frame holds two cards. The pipeline's whole
        protection against "which of these two students is this?" rests on the
        decoder reporting both payloads, and a reader that structurally cannot
        report more than one leaves that protection switched off.

        The merge is deliberately narrow:

        * zxing's payloads are authoritative and keep their order, so the
          value a caller verifies is the one the stronger reader vouched for;
        * a payload that sits on the *same* symbol zxing already reported is
          dropped, so a second reading of the same bars cannot turn one
          symbol into two;
        * a payload on a spot zxing explicitly *rejected* is dropped, because
          zxing's verdict there was that the symbol does not check out, and a
          reader with no checksum to fail is exactly how a wrong payload gets
          in;
        * anything else is a different symbol, and is reported.
        """
        if not ZXING_AVAILABLE:
            return self._read_zbar(image)

        found, rejected = self._read_zxing(image)
        if not found:
            return self._read_zbar(image)

        merged = list(found)
        extra, _ = self._read_zxing(
            image, binarizer=zxingcpp.Binarizer.FixedThreshold
        )
        for item in extra:
            if any(boxes_touch(item.bbox, spot) for spot in rejected):
                continue
            if any(boxes_touch(item.bbox, kept.bbox) for kept in found):
                continue
            merged.append(item)
        return merged

    def decode(self, image: np.ndarray) -> List[DecodedBarcode]:
        """Decode a crop, trying colour first and then greyscale.

        Colour is not redundant: on the real card ``assets/images/Id_1.png``
        zbar reads the CODE39 symbol from the BGR image but returns nothing on
        the greyscale conversion of the very same pixels. Greyscale alone is
        therefore not a safe default.
        """
        is_colour = len(image.shape) == 3
        results = self._decode_variant(image, "colour" if is_colour else "gray")
        if results or not is_colour:
            return results
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return self._decode_variant(gray, "gray")

    def decode_from_region(self, image: np.ndarray, bbox: Tuple[int, int, int, int]) -> List[DecodedBarcode]:
        x, y, w, h = bbox
        x = max(0, x)
        y = max(0, y)
        w = min(w, image.shape[1] - x)
        h = min(h, image.shape[0] - y)
        
        if w <= 0 or h <= 0:
            return []
        
        roi = image[y:y+h, x:x+w]
        return self.decode(roi)

    def preprocess_for_decoding(self, image: np.ndarray) -> List[np.ndarray]:
        variants = []
        
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        variants.append(gray)
        
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(thresh)
        
        adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        variants.append(adaptive)
        
        # Equalising the local contrast recovers symbols on a card whose bars
        # are dark-on-colour rather than black-on-white, which is the case for
        # both WIT cards.
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        variants.append(clahe)
        
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        variants.append(blurred)
        
        kernel = np.array([[-1,-1,-1], [-1,9,-1], [-1,-1,-1]])
        sharpened = cv2.filter2D(gray, -1, kernel)
        variants.append(sharpened)
        
        return variants

    def decode_with_preprocessing(self, image: np.ndarray) -> List[DecodedBarcode]:
        all_results = []
        seen_data = set()

        for variant in self.preprocess_for_decoding(image):
            results = self.decode(variant)
            for result in results:
                if result.data not in seen_data:
                    seen_data.add(result.data)
                    all_results.append(result)

        return all_results

    # -- ordered, bounded decoding -------------------------------------------

    @staticmethod
    def _upscale(image: np.ndarray, factor: int) -> np.ndarray:
        """Enlarge an image for the decoder, preserving the bar edges.

        Lanczos-4, not bicubic and not nearest. The decoder measures the
        *transition* between a bar and its neighbour, so the resampler has to
        keep a sharp step; Lanczos-4 measured better than bicubic on every
        card in this project, and nearest is worse still because it turns the
        bars into blocks with no transition at all to sample.
        """
        return cv2.resize(
            image, None, fx=factor, fy=factor, interpolation=cv2.INTER_LANCZOS4
        )

    def _sharp_upscaled(self, image: np.ndarray, factor: int) -> np.ndarray:
        """An enlarged *and* sharpened greyscale copy.

        Interpolation re-samples the bars but leaves them low-contrast: a bar
        that occupied a single source pixel now occupies a couple of soft ones,
        and a reader that thresholds on a local average sees a washed-out
        symbol. Adding the local contrast back is what turned card B's 167 px
        symbol -- which no variant read -- into a readable one.

        The greyscale conversion is deliberate: sharpening each colour channel
        separately amplifies chroma noise on a photographed card, and both
        readers threshold on luminance anyway.
        """
        return unsharp(self._gray(self._upscale(image, factor)))

    def build_attempts(
        self,
        image: np.ndarray,
        rotations: bool = False,
        angles: Tuple[float, ...] = ROTATION_ANGLES_DEG,
    ) -> List[DecodeAttempt]:
        """Ordered attempts: the original first, then targeted alternatives.

        Ordering is the whole point. The original crop is attempted before any
        filter touches it, so a perfectly readable barcode is never degraded
        first -- and because the loop stops at the first success, the extra
        variants cost nothing on a frame that reads cleanly.

        The order was chosen from measurement on this project's own three cards
        rather than from taste. The decisive change was the arrival of a second
        reader: zxing-cpp reads symbols down to ~167-183 px of symbol width
        where pyzbar needed ~191-210 px, and it does so on the *untouched*
        pixels. Interpolation is therefore no longer the first thing to try --
        it is the second, and it exists to catch what the reader still cannot
        see.

        The first six attempts are the ones a crop actually gets
        (``SCAN_MAX_DECODE_ATTEMPTS``), so they carry all the weight:

        ``original``
            The untouched colour crop.
        ``gray``
            The same pixels as greyscale. Not redundant: on the real card
            ``assets/images/Id_1.png`` pyzbar reads the CODE39 symbol from the
            BGR image but returns nothing on the greyscale conversion of the
            very same pixels.
        ``upscale2x``
            Lanczos-4 enlargement, no sharpening.
        ``upscale2x_unsharp``
            The same enlargement with the local contrast restored.
        ``upscale3x_unsharp``
            Deeper still, for symbols that arrive at a handful of pixels per
            module.
        ``otsu``
            Global threshold. It won more of the recorded decodes than any
            other filter, and it is the one filter that survives a very small
            symbol: a local threshold needs enough samples to estimate a
            local average.

        Everything after that is only reached by a caller that allows more
        attempts. ``clahe`` follows ``otsu`` because it is the other filter
        with a recorded win, and the deep variants come after it.

        Rotations are appended last and only on request: each one costs a full
        reader pass, so a caller pays for them only after the cheap variants
        have already failed.
        """
        if image is None or image.size == 0:
            return []

        # The prepared images are built lazily: a crop that reads as-is must
        # not pay for four enlargements and four filters it will never use.
        # ``gray`` is cached because five of the variants need it.
        gray_cache: List[np.ndarray] = []

        def gray() -> np.ndarray:
            if not gray_cache:
                gray_cache.append(self._gray(image))
            return gray_cache[0]

        attempts: List[DecodeAttempt] = [DecodeAttempt("original", image)]

        if len(image.shape) == 3:
            # A colour image has a greyscale reading worth trying; an image
            # that is already grey has nothing to convert.
            attempts.append(DecodeAttempt("gray", factory=gray))

        small = image.shape[1] < MAX_UPSCALED_CROP_WIDTH_PX
        if small:
            attempts.append(
                DecodeAttempt("upscale2x", factory=lambda: self._upscale(image, 2))
            )
            attempts.append(
                DecodeAttempt(
                    "upscale2x_unsharp", factory=lambda: self._sharp_upscaled(image, 2)
                )
            )
            attempts.append(
                DecodeAttempt(
                    "upscale3x_unsharp", factory=lambda: self._sharp_upscaled(image, 3)
                )
            )

        attempts.append(
            DecodeAttempt(
                "otsu",
                factory=lambda: cv2.threshold(
                    gray(), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
                )[1],
            )
        )

        attempts.append(
            DecodeAttempt(
                "clahe",
                factory=lambda: cv2.createCLAHE(
                    clipLimit=2.0, tileGridSize=(8, 8)
                ).apply(gray()),
            )
        )

        if small:
            attempts.append(
                DecodeAttempt(
                    "upscale4x_unsharp", factory=lambda: self._sharp_upscaled(image, 4)
                )
            )
            attempts.append(
                DecodeAttempt("upscale3x", factory=lambda: self._upscale(image, 3))
            )
            attempts.append(
                DecodeAttempt(
                    "upscale2x_otsu",
                    factory=lambda: cv2.threshold(
                        self._gray(self._upscale(image, 2)),
                        0,
                        255,
                        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
                    )[1],
                )
            )
            attempts.append(
                DecodeAttempt(
                    "upscale3x_clahe",
                    factory=lambda: cv2.createCLAHE(
                        clipLimit=2.0, tileGridSize=(8, 8)
                    ).apply(self._gray(self._upscale(image, 3))),
                )
            )

        attempts.append(
            DecodeAttempt(
                "adaptive",
                factory=lambda: cv2.adaptiveThreshold(
                    gray(), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 3
                ),
            )
        )

        # Light denoise + unsharp mask. This was
        # ``cv2.fastNlMeansDenoising(h=5, template=7, search=21)``, which cost
        # 140 ms on a 550x140 crop and 343 ms on a 1280x720 frame -- and which
        # never once produced the winning decode across 162 robustness rows.
        # A bilateral filter does the same job for about 1 ms. Honest caveat:
        # no denoise variant decoded these assets even under heavy noise; Otsu
        # is what wins there. The attempt is kept because real sensor noise is
        # not what a synthetic sigma reproduces.
        attempts.append(
            DecodeAttempt(
                "denoise_sharpen",
                factory=lambda: cv2.addWeighted(
                    cv2.bilateralFilter(gray(), 5, 50, 50),
                    1.6,
                    cv2.GaussianBlur(gray(), (0, 0), 2.0),
                    -0.6,
                    0,
                ),
            )
        )

        if rotations:
            for angle in angles:
                attempts.append(
                    DecodeAttempt(
                        f"rot{angle:+.0f}",
                        factory=lambda a=angle: rotate_preserve(image, a),
                    )
                )

        return attempts

    def decode_best(
        self,
        image: np.ndarray,
        rotations: bool = False,
        max_attempts: Optional[int] = None,
        angles: Tuple[float, ...] = ROTATION_ANGLES_DEG,
    ) -> DecodeResult:
        """Try attempts in order and stop at the first that reads a symbol.

        Early exit matters: running every variant on every frame is what made the
        pipeline cost ~520 ms a frame. On a readable crop this is a single zbar
        call.

        Each hit is stamped with the attempt that produced it and with its box
        mapped back into the coordinates of ``image``, so a caller that annotates
        or crops using the result is working in the caller's own pixel space even
        when the symbol only became readable after interpolation.

        When every reader has failed, the CODE39 reconstruction gets one turn.
        It is slower than a reader and, unlike a reader, it has to be *right*,
        so it only answers when several independent grids agree on the same
        payload -- see :mod:`services.code39_recovery`.
        """
        attempts = self.build_attempts(image, rotations=rotations, angles=angles)
        if max_attempts is not None:
            attempts = attempts[: max(0, int(max_attempts))]

        tried = 0
        for attempt in attempts:
            tried += 1
            found = self._decode_variant(attempt.image, attempt.name)
            if found:
                scale = self._attempt_scale(attempt.name)
                for item in found:
                    _annotate_geometry(item, attempt.name, scale)
                return DecodeResult(found, attempt=attempt.name, attempts_tried=tried)

        recovered = self._recover_code39(image)
        if recovered:
            return DecodeResult(
                recovered, attempt="code39_recovery", attempts_tried=tried
            )
        return DecodeResult([], attempt=None, attempts_tried=tried)

    @staticmethod
    def _recover_code39(image: np.ndarray) -> List[DecodedBarcode]:
        """Reconstruct a CODE39 symbol the readers could not read.

        The reconstruction works on the original pixels: it reads the module
        grid out of the darkness profile, and an interpolated copy of a symbol
        that is already too small to resolve its bars carries no extra
        information to find.
        """
        if image is None or image.size == 0:
            return []
        if image.shape[1] > MAX_RECOVERY_CROP_WIDTH_PX:
            return []
        try:
            from services.code39_recovery import recover_code39
        except Exception:  # pragma: no cover - the module is optional
            logger.warning("CODE39 recovery is unavailable", exc_info=True)
            return []
        recovery = recover_code39(image)
        if recovery is None:
            return []
        quality = int(max(0, min(100, (1.0 - recovery.residual) * 100)))
        return [
            DecodedBarcode(
                data=recovery.data,
                type="CODE39",
                bbox=BarcodeRect(
                    recovery.left, recovery.top, recovery.width, recovery.height
                ),
                polygon=[
                    (recovery.left, recovery.top),
                    (recovery.left + recovery.width, recovery.top),
                    (recovery.left + recovery.width, recovery.top + recovery.height),
                    (recovery.left, recovery.top + recovery.height),
                ],
                quality=quality,
            )
        ]

    @staticmethod
    def _attempt_scale(name: str) -> float:
        """The enlargement an attempt name implies; 1.0 for every filter."""
        if name.startswith("upscale") and "x" in name:
            digits = name[len("upscale"):].split("x", 1)[0]
            if digits.isdigit():
                return float(digits)
        return 1.0

    def _gray(self, image: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

    def recently_decoded(self, barcode_data: str, suppression_seconds: float = 5.0) -> bool:
        """Pure check: was this exact payload decoded within the window?

        Safe to call any number of times; it never mutates state. Prefer this
        when the decision must be repeatable (tests, dry runs, re-checking a
        frame). See :meth:`is_duplicate` for the state-advancing variant.
        """
        import time

        return (
            self.last_decoded == barcode_data
            and (time.time() - self.last_decode_time) < suppression_seconds
        )

    def is_duplicate(self, barcode_data: str, suppression_seconds: float = 5.0) -> bool:
        """Advance the suppression window and report whether this is a repeat.

        Note the side effect: this is not a pure predicate. Calling it twice
        with the same payload returns ``False`` then ``True``, because the first
        call records the payload as the current one. Call it exactly once per
        decode attempt. Use :meth:`recently_decoded` for a repeatable check.
        """
        import time
        current_time = time.time()

        if self.last_decoded == barcode_data and (current_time - self.last_decode_time) < suppression_seconds:
            return True

        self.last_decoded = barcode_data
        self.last_decode_time = current_time
        return False