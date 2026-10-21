import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings:
    #: Project root. Exposed on the class so callers can resolve stored,
    #: project-relative asset paths without importing the module global.
    BASE_DIR = BASE_DIR

    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", "3306"))
    DB_NAME = os.getenv("DB_NAME", "wit_barcode_system")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    
    APP_NAME = os.getenv("APP_NAME", "WIT Student ID & Information System")
    APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"

    #: Institutional identity printed on a card. These are *configuration*, not
    #: constants in a widget: the back of a card states a school contact line and
    #: a registrar's office, and neither is a fact this codebase gets to assert.
    #: Left empty, the card renders a "not configured" marker instead of a
    #: plausible-looking address and telephone number.
    INSTITUTION_NAME = os.getenv("INSTITUTION_NAME", "Western Institute of Technology")
    INSTITUTION_CONTACT_LINE = os.getenv("INSTITUTION_CONTACT_LINE", "")
    REGISTRAR_OFFICE = os.getenv("REGISTRAR_OFFICE", "")
    
    YOLO_MODEL_PATH = BASE_DIR / os.getenv("YOLO_MODEL_PATH", "models_yolo/active/best.pt")
    YOLO_CONFIDENCE_THRESHOLD = float(os.getenv("YOLO_CONFIDENCE_THRESHOLD", "0.5"))
    YOLO_IOU_THRESHOLD = float(os.getenv("YOLO_IOU_THRESHOLD", "0.45"))
    YOLO_DEVICE = os.getenv("YOLO_DEVICE", "cpu")
    #: Square size the detector resizes the frame to before inference.
    #:
    #: This is a *sampling* setting, and it was measured rather than guessed.
    #: The detector cannot represent a box wider than 32 cells of the output
    #: grid (YOLOv8's DFL has ``reg_max=16``), and each cell is 32 input
    #: pixels, so the widest representable box is 1024 input pixels whatever
    #: this is set to. At 1280 a symbol filling more than 80% of the frame
    #: width is therefore *unrepresentable*, and the model was measured
    #: returning nothing at all on a card held square to the lens.
    #:
    #: Within the representable range the picture is not monotonic. Measured on
    #: the six real assets and on the full distance/angle/position sweep, with
    #: the 1024-trained candidate:
    #:
    #: * 640  -- detection 100% everywhere, mean IoU 0.83-0.94, decode 100%
    #:   wherever the symbol is physically readable, no false positives;
    #: * 1024 -- detection 100% but IoU 0.79-0.92, one position lost its
    #:   detection entirely, the same false positives the raw model emits at
    #:   low confidence, and the widest asset was not detected at all.
    #:
    #: So 640 is the setting, and the model is trained at the same size so
    #: that the two agree.
    YOLO_IMGSZ = int(os.getenv("YOLO_IMGSZ", "640"))
    
    DEFAULT_CAMERA_INDEX = int(os.getenv("DEFAULT_CAMERA_INDEX", "0"))
    CAMERA_WIDTH = int(os.getenv("CAMERA_WIDTH", "1280"))
    CAMERA_HEIGHT = int(os.getenv("CAMERA_HEIGHT", "720"))
    CAMERA_FPS = int(os.getenv("CAMERA_FPS", "30"))
    
    BARCODE_PREFIX = os.getenv("BARCODE_PREFIX", "WIT:STUDENT:")
    BARCODE_OUTPUT_DIR = BASE_DIR / os.getenv("BARCODE_OUTPUT_DIR", "generated_barcodes")
    BARCODE_IMAGE_FORMAT = os.getenv("BARCODE_IMAGE_FORMAT", "PNG")
    BARCODE_MODULE_WIDTH = int(os.getenv("BARCODE_MODULE_WIDTH", "2"))
    BARCODE_MODULE_HEIGHT = int(os.getenv("BARCODE_MODULE_HEIGHT", "15"))

    # --- Student ID card generator ------------------------------------------
    #: Where rendered cards are written, relative to the project root. Kept
    #: separate from ``BARCODE_OUTPUT_DIR`` because the two files are different
    #: artefacts: a barcode is an identifier image the scanner reads, a card is a
    #: printed document. Overwriting one must never overwrite the other.
    ID_CARD_OUTPUT_DIR = BASE_DIR / os.getenv("ID_CARD_OUTPUT_DIR", "generated_id_cards")
    #: Raster resolution for card exports. CR80 at 300 DPI is 1011 x 638 px.
    ID_CARD_EXPORT_DPI = int(os.getenv("ID_CARD_EXPORT_DPI", "300"))
    
    SCAN_DUPLICATE_SUPPRESSION_SECONDS = int(os.getenv("SCAN_DUPLICATE_SUPPRESSION_SECONDS", "5"))
    SCAN_HISTORY_LIMIT = int(os.getenv("SCAN_HISTORY_LIMIT", "100"))

    # --- Live scanner pipeline -------------------------------------------------
    # The yellow scan frame is a visual guide, never a detection. Its size is a
    # fraction of the camera frame so it keeps the same on-screen footprint
    # regardless of resolution.
    SCAN_REGION_WIDTH_RATIO = float(os.getenv("SCAN_REGION_WIDTH_RATIO", "0.66"))
    SCAN_REGION_HEIGHT_RATIO = float(os.getenv("SCAN_REGION_HEIGHT_RATIO", "0.42"))

    # --- decode gate (separate from the drawn guide) ------------------------
    # The yellow frame above is a *visual* guide and stays a sensible aiming
    # target. It was also being used as the decode gate, and the two cannot be
    # the same rectangle: on these ID cards the barcode sits about 78% of the
    # way down the card, so a card held centred and filling the frame puts the
    # barcode at roughly y 558-641 in a 720px frame -- entirely below a guide
    # spanning y 209-511. Measured with the real pipeline and the real cards,
    # that gate refused every close and medium scan before the decoder ever saw
    # them, while *far* scans passed only because a small card shrinks toward
    # the frame centre. Widening only the gate, with no model change and no
    # threshold change, took close/medium decode from 0% to 100% and left zero
    # false positives on barcode-free frames.
    SCAN_GATE_WIDTH_RATIO = float(os.getenv("SCAN_GATE_WIDTH_RATIO", "0.88"))
    SCAN_GATE_HEIGHT_RATIO = float(os.getenv("SCAN_GATE_HEIGHT_RATIO", "0.88"))

    # A barcode qualifies for decoding only when this fraction of its own area
    # lies inside the yellow frame. 1.0 means "completely inside".
    SCAN_REGION_OVERLAP_THRESHOLD = float(os.getenv("SCAN_REGION_OVERLAP_THRESHOLD", "0.65"))

    # Extra context kept around the detected box when cropping for the decoder.
    # Bars and the quiet zone must survive the crop, so this is generous.
    SCAN_CROP_MARGIN_RATIO = float(os.getenv("SCAN_CROP_MARGIN_RATIO", "0.20"))
    # Crops smaller than this (px) are rejected outright: too few modules to read.
    SCAN_MIN_CROP_WIDTH = int(os.getenv("SCAN_MIN_CROP_WIDTH", "80"))
    SCAN_MIN_CROP_HEIGHT = int(os.getenv("SCAN_MIN_CROP_HEIGHT", "24"))

    # Seconds after a verified scan during which further verifications are
    # suppressed. Prevents duplicate popups while a card is still in frame.
    SCAN_COOLDOWN_SECONDS = float(os.getenv("SCAN_COOLDOWN_SECONDS", "3.0"))
    # Consecutive frames that must decode to the same value before it is
    # accepted. 1 disables confirmation.
    SCAN_CONFIRM_FRAMES = int(os.getenv("SCAN_CONFIRM_FRAMES", "1"))
    # Upper bound on decode attempts per crop per frame. The ordered strategy
    # tries the untouched crop first, so a readable barcode costs 1 attempt and
    # this cap only bites on a hard crop.
    SCAN_MAX_DECODE_ATTEMPTS = int(os.getenv("SCAN_MAX_DECODE_ATTEMPTS", "6"))
    # Detections overlapping an already-considered box by at least this much are
    # treated as the same physical symbol and are not decoded again. Without
    # this, a detector that fires several boxes on one barcode has every
    # fragment crop sent to the decoder.
    SCAN_CANDIDATE_OVERLAP_LIMIT = float(os.getenv("SCAN_CANDIDATE_OVERLAP_LIMIT", "0.70"))
    # Whole-frame decode fallback. Measured: on a card rotated past the point
    # YOLO can still see it (+/-13 deg, 90 deg) the decoder reads the payload
    # from the untouched frame, and it produced no false decode on any of 8
    # document variants. It costs a full-frame pass, so it only runs once the
    # pipeline has seen no detection for a sustained period, and is rate
    # limited so it can never dominate the frame budget.
    SCAN_FRAME_DECODE_FALLBACK = os.getenv("SCAN_FRAME_DECODE_FALLBACK", "True").lower() == "true"
    SCAN_FRAME_FALLBACK_AFTER_SECONDS = float(os.getenv("SCAN_FRAME_FALLBACK_AFTER_SECONDS", "0.8"))
    SCAN_FRAME_FALLBACK_INTERVAL_SECONDS = float(os.getenv("SCAN_FRAME_FALLBACK_INTERVAL_SECONDS", "1.5"))
    # Attempts allowed on the full frame, for the case where the detector sees
    # nothing but the symbol is still readable.
    #
    # The cap was 2, on the belief that every recovery came on the first
    # untouched attempt. Measured on a 1280x720 frame holding a real card, that
    # is wrong: card A (WIT-2024-001) needs attempt 4 (`otsu`) to read at all,
    # and its dim variant needs attempt 2. With the cap at 2 that whole-frame
    # safety net could not read card A in a normal frame.
    #
    # Four attempts cost ~240 ms instead of ~150 ms, paid at most once per
    # SCAN_FRAME_FALLBACK_INTERVAL_SECONDS and only after the quiet period, so
    # the capture loop is unaffected. This stays well under the number of
    # attempts a *crop* gets (all of them): the crop is the primary path.
    SCAN_FRAME_FALLBACK_ATTEMPTS = int(os.getenv("SCAN_FRAME_FALLBACK_ATTEMPTS", "4"))
    # Try small rotations of the crop after the cheap variants fail. Measured
    # to recover nothing on the project's own cards (zbar deskews to ~11 deg and
    # reads 90/180 deg natively), so it is off: it is pure per-frame cost.
    SCAN_DECODE_ROTATIONS = os.getenv("SCAN_DECODE_ROTATIONS", "False").lower() == "true"

    # --- Detection box presentation -----------------------------------------
    # Exponential smoothing factor for the on-screen box (0 = frozen, 1 = no
    # smoothing). Small values remove frame-to-frame jitter without the box
    # lagging behind a moving card.
    SCAN_BOX_SMOOTHING = float(os.getenv("SCAN_BOX_SMOOTHING", "0.45"))
    # How long the box lingers after the detection disappears, so a single
    # dropped frame does not make the overlay flicker.
    SCAN_BOX_GRACE_SECONDS = float(os.getenv("SCAN_BOX_GRACE_SECONDS", "0.6"))
    # Honour a reduced-motion preference: no sweeping line, no pulsing box.
    SCAN_REDUCED_MOTION = os.getenv("SCAN_REDUCED_MOTION", "False").lower() == "true"
    # Target inference rate; the capture thread runs faster than this.
    SCAN_TARGET_FPS = int(os.getenv("SCAN_TARGET_FPS", "12"))

    # --- Post-scan workflow ---------------------------------------------------
    # Quiet period after a successful scan before the same payload is allowed to
    # start a new scan. This is a *floor*, not the only guard: the scanner also
    # latches the last processed payload and requires the barcode to leave the
    # frame (or the operator to start a new session) before the same card can be
    # recorded again. See services/scan_session.py.
    SCAN_POST_SUCCESS_DEBOUNCE_SECONDS = float(
        os.getenv("SCAN_POST_SUCCESS_DEBOUNCE_SECONDS", "1.75")
    )
    # Rows kept in the on-screen Scan History table.
    SCAN_HISTORY_DISPLAY_LIMIT = int(os.getenv("SCAN_HISTORY_DISPLAY_LIMIT", "50"))

    # --- Success audio cue ----------------------------------------------------
    # The cue plays only after a barcode has been decoded AND the student row has
    # been confirmed in the database. The asset is optional: when the configured
    # file is absent the service falls back to the platform beep rather than
    # pointing at a nonexistent path.
    SCAN_SUCCESS_BEEP_ENABLED = os.getenv("SCAN_SUCCESS_BEEP_ENABLED", "True").lower() == "true"
    SCAN_SUCCESS_BEEP_FILE = os.getenv("SCAN_SUCCESS_BEEP_FILE", "success_beep.mp3")
    # Minimum gap between two cues, so a burst of frames cannot machine-gun it.
    SCAN_SUCCESS_BEEP_MIN_INTERVAL_SECONDS = float(
        os.getenv("SCAN_SUCCESS_BEEP_MIN_INTERVAL_SECONDS", "0.5")
    )
    # Mirror the preview like a selfie camera. Applied to the composited canvas,
    # so overlay and detection boxes stay aligned automatically.
    SCAN_MIRROR_PREVIEW = os.getenv("SCAN_MIRROR_PREVIEW", "True").lower() == "true"
    SCAN_OVERLAY_COLOR = os.getenv("SCAN_OVERLAY_COLOR", "FFD600")
    SCAN_OVERLAY_HINT = os.getenv("SCAN_OVERLAY_HINT", "Align barcode inside the yellow frame")
    
    REPORTS_OUTPUT_DIR = BASE_DIR / os.getenv("REPORTS_OUTPUT_DIR", "reports")
    PDF_PAGE_SIZE = os.getenv("PDF_PAGE_SIZE", "A4")

    # --- Academic defaults ---------------------------------------------------
    # The term the "New Enrollment" and "New Assessment" dialogs open on. They
    # used to hardcode "2024-2025" in two different widgets, so changing the
    # default meant editing both, and a stale default silently created a term
    # the registrar did not intend.
    DEFAULT_ACADEMIC_YEAR = os.getenv("DEFAULT_ACADEMIC_YEAR", "2024-2025")
    DEFAULT_SEMESTER = os.getenv("DEFAULT_SEMESTER", "1st Semester")

    #: The ``users.id`` recorded in audit columns (``created_by``,
    #: ``received_by``, ``regenerated_by``). The application has no sign-in yet,
    #: so the acting user is configuration rather than a literal buried in a
    #: widget; point it at a real account once authentication exists.
    DEFAULT_USER_ID = int(os.getenv("DEFAULT_USER_ID", "1"))
    
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = BASE_DIR / os.getenv("LOG_FILE", "logs/app.log")
    
    ASSETS_DIR = BASE_DIR / "assets"
    FONTS_DIR = ASSETS_DIR / "fonts"
    LOGOS_DIR = ASSETS_DIR / "logos"
    LOGO_PATH = LOGOS_DIR / os.getenv("LOGO_FILE", "wit_logo.png")
    BACKGROUNDS_DIR = ASSETS_DIR / "backgrounds"
    BARCODES_DIR = ASSETS_DIR / "barcodes"
    IMAGES_DIR = ASSETS_DIR / "images"
    #: Student information / certificate panels. These live in a subfolder;
    #: see :meth:`info_image` for the tolerant lookup.
    INFO_IMAGES_DIR = IMAGES_DIR / "info"
    #: Verified student portraits (``javy_profile.png``, ``ronna_profile.png``).
    #: These are the *cropped* photographs that match the face on each ID card;
    #: ``INFO_IMAGES_DIR`` holds the full-page information panels, which are
    #: documents rather than photos and must never be used as an avatar.
    PROFILE_DIR = ASSETS_DIR / "profile"
    AUDIO_DIR = ASSETS_DIR / "audio"
    DATASET_DIR = BASE_DIR / "dataset"
    MODELS_YOLO_DIR = BASE_DIR / "models_yolo"
    
    @classmethod
    def info_image(cls, filename: str) -> Path:
        """Resolve a student information panel by filename.

        An earlier revision of the project kept these panels directly in
        ``assets/images/``; they now live in ``assets/images/info/``. Both
        locations are accepted so callers do not break on either layout, and
        nothing is moved or created by this lookup.
        """
        for candidate in (cls.INFO_IMAGES_DIR / filename, cls.IMAGES_DIR / filename):
            if candidate.exists():
                return candidate
        return cls.INFO_IMAGES_DIR / filename
    
    @classmethod
    def resolve_asset(cls, stored_path: Optional[str]) -> Optional[Path]:
        """Resolve a path stored in the database into an existing file.

        ``profile_image_path`` and friends hold project-relative paths such as
        ``assets/profile/javy_profile.png``. Resolving them against the process
        working directory is what makes a portrait load during development and
        vanish in a packaged build launched from another directory, so the
        project root is used instead. Absolute paths are respected as-is.

        Two layouts are accepted for the same reason ``info_image`` accepts two:
        deployments and checkouts differ on whether the portraits sit in
        ``assets/profile/`` or in a top-level ``profile/``, and the stored value
        names one of them. A stored path is tried as written, then under
        ``assets/``, then by file name inside the configured profile directory --
        so ``profile/javy_profile.png`` and ``assets/profile/javy_profile.png``
        both resolve to the same photograph without editing stored records.

        Returns ``None`` when nothing is stored or the file is absent, so
        callers can render a neutral placeholder rather than a broken image.
        """
        if not stored_path:
            return None

        candidate = Path(str(stored_path).strip())
        if candidate.is_absolute():
            return candidate if candidate.is_file() else None

        for root in (BASE_DIR, Path.cwd()):
            for relative in cls._asset_candidates(candidate):
                resolved = root / relative
                if resolved.is_file():
                    return resolved
        return None

    @staticmethod
    def _asset_candidates(stored: Path) -> List[Path]:
        """The layouts a stored asset path may legitimately use."""
        candidates = [stored, Path("assets") / stored]
        if stored.parts and stored.parts[0] == "assets" and len(stored.parts) > 1:
            # assets/profile/x.png -> profile/x.png
            candidates.append(Path(*stored.parts[1:]))
        candidates.append(settings.PROFILE_DIR / stored.name)
        return candidates

    @classmethod
    def store_path(cls, path: Path) -> str:
        """Format a generated file's location for storing in the database.

        Generated images and PDFs are written under the project tree, so they are
        recorded project-relative (``generated_barcodes/barcode_000065412.png``).
        Recording ``str(Path.cwd() / ...)`` instead -- which is what the codebase
        used to do -- silently turned every stored path into a promise about the
        directory the application happened to be launched from: read it back from
        another working directory and the file cannot be found. Anything outside
        the project tree is stored absolute, because there is no relative form of
        it that would survive a move.
        """
        path = Path(path)
        if not path.is_absolute():
            return path.as_posix()
        try:
            return path.relative_to(BASE_DIR).as_posix()
        except ValueError:
            return path.as_posix()

    @classmethod
    def get_database_config(cls):
        return {
            "host": cls.DB_HOST,
            "port": cls.DB_PORT,
            "database": cls.DB_NAME,
            "user": cls.DB_USER,
            "password": cls.DB_PASSWORD,
            # autocommit is deliberately left off. With autocommit=True every
            # statement commits on its own, which silently makes
            # get_cursor(commit=False) -- and its rollback path -- a no-op, so a
            # "dry run" would still write. Writes go through
            # get_cursor(commit=True), which commits explicitly.
            "charset": "utf8mb4",
            "collation": "utf8mb4_unicode_ci",
        }

settings = Settings()