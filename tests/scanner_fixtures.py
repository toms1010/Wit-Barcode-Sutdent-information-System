"""Shared fixtures for the scanner tests.

The frames built here are composited from the *real* checked-in ID card assets
in ``assets/``. Nothing here fabricates a barcode: the barcode band is cropped
out of ``assets/images/Id_1.png`` using the same rectangle as the dataset's
ground-truth annotation, then placed into a 1280x720 canvas the way a webcam
would see it when a student holds their card up to the camera.

The ``offset`` argument slides the card in or out of the centred yellow guide so
both the "inside" and the "outside" code paths can be exercised.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple

import cv2
import numpy as np
import pytest

from config.settings import settings

ASSETS = settings.ASSETS_DIR
CARD_ID_1 = ASSETS / "images" / "Id_1.png"
CARD_ID_2 = ASSETS / "images" / "Id_2.png"
CARD_ID_3 = ASSETS / "images" / "Id_3.png"
BARCODE_1 = ASSETS / "barcodes" / "barcode_1.png"
BARCODE_2 = ASSETS / "barcodes" / "barcode_2.png"
BARCODE_3 = ASSETS / "barcodes" / "barcode_3.png"

#: Ground-truth barcode band of the real ID cards (dataset/labels/train/Id_1.txt).
CARD_BARCODE_BAND: Tuple[int, int] = (740, 985)
#: The barcode rectangle inside that band, from the same annotation.
CARD_BARCODE_Y: Tuple[int, int] = (792, 904)

#: The third student's card is a different print: 270x459 rather than 590x1004,
#: with its symbol at y362-419 instead of y792-904. It therefore needs its own
#: band, measured from the decoder's rect at 4x, rather than the first card's
#: rectangle scaled onto it -- pasting the wrong band would put a patch of photo
#: where the barcode is and make every assertion about it meaningless.
CARD_BARCODE_BAND_BY_NAME: Dict[str, Tuple[int, int]] = {
    "Id_1.png": CARD_BARCODE_BAND,
    "Id_2.png": CARD_BARCODE_BAND,
    "Id_3.png": (335, 430),
}
CARD_BARCODE_Y_BY_NAME: Dict[str, Tuple[int, int]] = {
    "Id_1.png": CARD_BARCODE_Y,
    "Id_2.png": CARD_BARCODE_Y,
    "Id_3.png": (362, 419),
}

WEBCAM_SIZE: Tuple[int, int] = (1280, 720)


def make_webcam_frame(
    card_path: Path = CARD_ID_1,
    canvas_size: Tuple[int, int] = WEBCAM_SIZE,
    zoom: float = 1.0,
    offset: Tuple[int, int] = (0, 0),
    background: int = 55,
) -> np.ndarray:
    """Compose a webcam-like frame showing a real card's barcode band.

    ``offset=(0, 0)`` centres the barcode in the yellow guide. A large negative
    ``offset[1]`` pushes the card up so the barcode falls outside it.
    """
    card = cv2.imread(str(card_path))
    assert card is not None, f"Missing asset: {card_path}"

    y0, y1 = CARD_BARCODE_BAND_BY_NAME.get(card_path.name, CARD_BARCODE_BAND)
    bar_y0, bar_y1 = CARD_BARCODE_Y_BY_NAME.get(card_path.name, CARD_BARCODE_Y)
    band = card[y0:y1, :]
    scale = max(0.1, zoom)
    bw = max(1, int(band.shape[1] * scale))
    bh = max(1, int(band.shape[0] * scale))
    band = cv2.resize(band, (bw, bh), interpolation=cv2.INTER_AREA)

    canvas_w, canvas_h = canvas_size
    canvas = np.full((canvas_h, canvas_w, 3), background, dtype=np.uint8)

    # Where the barcode sits inside the resized band, then the origin that puts
    # it at the requested position in the canvas.
    bar_top = (bar_y0 - y0) * scale
    bar_bottom = (bar_y1 - y0) * scale
    target_y = canvas_h / 2.0 + offset[1]
    oy = int(round(target_y - (bar_top + bar_bottom) / 2.0))
    ox = int(round(canvas_w / 2.0 - bw / 2.0 + offset[0]))

    ox = max(0, min(ox, canvas_w - bw))
    oy = max(0, min(oy, canvas_h - bh))
    if oy + bh <= canvas_h and ox + bw <= canvas_w:
        canvas[oy : oy + bh, ox : ox + bw] = band
    return canvas


@pytest.fixture(scope="session")
def id_card_1() -> Path:
    assert CARD_ID_1.exists(), f"Missing real ID asset: {CARD_ID_1}"
    return CARD_ID_1


@pytest.fixture(scope="session")
def id_card_2() -> Path:
    assert CARD_ID_2.exists(), f"Missing real ID asset: {CARD_ID_2}"
    return CARD_ID_2


@pytest.fixture()
def webcam_frame() -> np.ndarray:
    """A 1280x720 frame with a real card's barcode centred in the guide."""
    return make_webcam_frame()
