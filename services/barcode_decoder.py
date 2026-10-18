import cv2
import numpy as np
from pyzbar import pyzbar
from typing import Optional, List, Tuple
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

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
#: measured band; 3x covers crops that arrive even smaller.
UPSCALE_FACTORS = (2, 3)


@dataclass
class DecodedBarcode:
    data: str
    type: str
    bbox: Tuple[int, int, int, int]
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
    source_bbox: Optional[Tuple[int, int, int, int]] = None
    source_polygon: List[Tuple[int, int]] = field(default_factory=list)


@dataclass
class DecodeAttempt:
    """One prepared image handed to zbar, and how it was produced."""

    name: str
    image: np.ndarray


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
        item.source_bbox = tuple(item.bbox)
        item.source_polygon = list(item.polygon)
        return

    left, top, width, height = item.bbox
    item.source_bbox = (
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

    def _decode_variant(self, image: np.ndarray, variant: str) -> List[DecodedBarcode]:
        """Run zbar on one prepared image and keep only real symbols."""
        try:
            decoded_objects = pyzbar.decode(image)
        except Exception as e:
            logger.error(f"Barcode decoding failed on {variant} variant: {e}")
            return []

        results = []
        for obj in decoded_objects:
            # Drop zero-area rectangles: zbar emits them for variants it cannot
            # actually read, and they are indistinguishable from a real hit if
            # passed straight through.
            if obj.rect.width < MIN_SYMBOL_SIDE_PX or obj.rect.height < MIN_SYMBOL_SIDE_PX:
                logger.debug(
                    "Discarding degenerate barcode rect %s from %s variant",
                    obj.rect,
                    variant,
                )
                continue
            try:
                data = obj.data.decode("utf-8")
            except UnicodeDecodeError:
                data = obj.data.decode("latin-1")
            results.append(DecodedBarcode(
                data=data,
                type=obj.type,
                bbox=obj.rect,
                polygon=[(p.x, p.y) for p in obj.polygon] if obj.polygon else [],
                quality=getattr(obj, "quality", 0),
            ))
        return results

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

        Each following step changes exactly one thing, and the steps that are
        known to destroy thin bars (morphological closing, heavy denoise) are
        deliberately absent. The order was chosen from measurement rather than
        taste: across the 93 decodes recorded in ``reports/robustness_matrix.json``
        only ``original``, ``clahe`` and ``otsu`` ever won, so the freed slots
        (the ``gray`` entry used to hand zbar the *original* pixels a second
        time) go to interpolation, the one step that adds samples instead of
        rearranging the ones already there.

        Interpolating and filtering are complementary rather than alternatives:
        on ``Id_3.png`` a plain 2x upscale reads the card, while on the same
        card composited at scanning distance only 2x-then-CLAHE reads it. Both
        orders of that pair are therefore present, plain first because it is the
        cheaper of the two.

        Rotations are appended last and only on request: each one costs a full
        zbar pass, so a caller pays for them only after the cheap variants have
        already failed.
        """
        if image is None or image.size == 0:
            return []

        attempts: List[DecodeAttempt] = [DecodeAttempt("original", image)]
        gray = self._gray(image)

        if image.shape[1] < MAX_UPSCALED_CROP_WIDTH_PX:
            # Bicubic, not nearest: the decoder samples bar edges, and blocky
            # pixels give it no transition to sample.
            upscaled = {
                factor: cv2.resize(
                    image, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC
                )
                for factor in UPSCALE_FACTORS
            }
            attempts.append(DecodeAttempt("upscale2x", upscaled[2]))

            up2_gray = self._gray(upscaled[2])
            attempts.append(
                DecodeAttempt(
                    "upscale2x_clahe",
                    cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(up2_gray),
                )
            )

        if gray is not image:
            attempts.append(DecodeAttempt("gray", gray))

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        attempts.append(DecodeAttempt("clahe", clahe))

        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        attempts.append(DecodeAttempt("otsu", otsu))

        if image.shape[1] < MAX_UPSCALED_CROP_WIDTH_PX:
            up2_gray = self._gray(upscaled[2])
            _, up2_otsu = cv2.threshold(
                up2_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )
            attempts.append(DecodeAttempt("upscale2x_otsu", up2_otsu))
            attempts.append(DecodeAttempt("upscale3x", upscaled[3]))
            attempts.append(
                DecodeAttempt(
                    "upscale3x_clahe",
                    cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(
                        self._gray(upscaled[3])
                    ),
                )
            )

        adaptive = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 3
        )
        attempts.append(DecodeAttempt("adaptive", adaptive))

        # Light denoise + unsharp mask. This was
        # ``cv2.fastNlMeansDenoising(h=5, template=7, search=21)``, which cost
        # 140 ms on a 550x140 crop and 343 ms on a 1280x720 frame -- and which
        # never once produced the winning decode across 162 robustness rows.
        # A bilateral filter does the same job for about 1 ms. Honest caveat:
        # no denoise variant decoded these assets even under heavy noise; Otsu
        # is what wins there. The attempt is kept because real sensor noise is
        # not what a synthetic sigma reproduces.
        denoised = cv2.bilateralFilter(gray, 5, 50, 50)
        sharpened = cv2.addWeighted(denoised, 1.6, cv2.GaussianBlur(denoised, (0, 0), 2.0), -0.6, 0)
        attempts.append(DecodeAttempt("denoise_sharpen", sharpened))

        if rotations:
            for angle in angles:
                rotated = rotate_preserve(image, angle)
                if rotated is not None:
                    attempts.append(DecodeAttempt(f"rot{angle:+.0f}", rotated))

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
        return DecodeResult([], attempt=None, attempts_tried=tried)

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