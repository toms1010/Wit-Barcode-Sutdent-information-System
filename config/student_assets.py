"""The verified student assets, in one place.

Seven scripts used to spell the first two students' file names out for
themselves, which is how the third student ended up decoded correctly by the
scanner and then invisible to every dataset builder, evaluator and threshold
sweep in the project. Each script now reads this registry instead, so a new card
cannot be half-integrated: adding one here makes it appear everywhere, and
:meth:`StudentAssets.verify` makes it prove itself.

What is recorded here, and why it is safe to record it
-----------------------------------------------------
* **Payloads were decoded from the assets**, not inferred from a filename. The
  two do not agree: ``barcode_1.png`` carries Ronna's payload and
  ``barcode_2.png`` carries Javy's. :func:`verify_registry` re-decodes every card
  and crop and reports any disagreement, so a wrong value here fails a test
  rather than quietly mis-training a model.
* **The split is by physical card, not by file.** Card A trains, card B
  validates, card C is *test only* -- never trained on and never used to select a
  checkpoint. That keeps every augmentation of a card on the same side of the
  boundary as the card itself, which is what makes the held-out numbers mean
  something. With three cards this is the strictest arrangement available: the
  third card contributes an honest held-out measurement and nothing else, and
  that limitation is stated in the dataset manifests rather than papered over.
* **Group names are stable.** ``card_A`` and ``card_B`` keep the names the
  existing datasets and manifests were built with, so regenerating produces the
  same filenames for those two cards and leaves the trained model comparable.

The registry is read-only for the application: identity at runtime still comes
from the database record (``students.barcode_payload``), never from this file.
It exists so the *offline* tooling -- dataset builders, benchmarks, threshold
sweeps -- agrees with the scanner about which file carries which payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config.settings import settings

CLASS_NAME = "barcode"
CLASS_ID = 0

#: Barcode-free documents, kept as negative examples. Split like the cards so a
#: document never appears on both sides either.
NEGATIVE_PANELS: Tuple[str, ...] = (
    "student_1_info.png",
    "student_2_info.png",
)

#: Where the negatives go. There is one per split and no panel belongs to the
#: third card: the project ships no third information panel.
NEGATIVE_SPLITS: Dict[str, str] = {
    "student_1_info.png": "train",
    "student_2_info.png": "val",
}


@dataclass(frozen=True)
class StudentAssets:
    """One student's card, its tight barcode crop, and its portrait."""

    #: Stable group name used in dataset manifests and generated filenames.
    group: str
    #: The decoded barcode payload. Nine digits, zero padded: this project's
    #: encoding of the reference number.
    payload: str
    #: The reference number the payload encodes.
    reference: str
    #: ``WIT-2024-00N``, as recorded in the database.
    student_id: str
    #: Verified student name, used only to report a mismatch.
    full_name: str
    #: The full ID card image.
    card: Path
    #: Project-root-relative card path, for manifests and reports.
    card_path: str
    #: The tight crop of the same physical card, when the project has one.
    #: ``None`` for a card that was supplied without its own crop.
    crop: Optional[Path]
    crop_path: Optional[str]
    #: The portrait checked by eye against the photo on the card.
    portrait: Path
    portrait_path: str
    #: Dataset split. ``test`` means held out from training and from model
    #: selection entirely.
    split: str
    #: Barcode rectangles in each source image, in native pixels: ``(left, top,
    #: width, height)``, read from the decoder's own rect rather than measured by
    #: eye. Recorded here so the dataset builders, the distance benchmark and the
    #: box-propagation checks all agree on where the symbol is; they used to
    #: carry three private copies of these numbers, which is how the third card
    #: ended up with no known box anywhere.
    card_box: Tuple[int, int, int, int] = (0, 0, 0, 0)
    crop_box: Optional[Tuple[int, int, int, int]] = None

    @property
    def barcode_type(self) -> str:
        return "CODE39"

    @property
    def sources(self) -> List[Path]:
        """Every image derived from this physical card."""
        return [self.card] + ([self.crop] if self.crop else [])

    @property
    def source_paths(self) -> List[str]:
        return [self.card_path] + ([self.crop_path] if self.crop_path else [])

    def box_for(self, path: Path) -> Optional[Tuple[int, int, int, int]]:
        """The recorded barcode rectangle for one of this student's images."""
        path = Path(path)
        if path == self.card:
            return self.card_box
        if self.crop is not None and path == self.crop:
            return self.crop_box
        return None

    def payload_of(self, path: Path) -> Optional[str]:
        """The payload expected on ``path``, or ``None`` if it is not ours."""
        return self.payload if Path(path) in self.sources else None

    def verify(self) -> List[str]:
        """Decode this card and crop; return a problem per disagreement.

        Empty means both assets are readable and carry exactly the recorded
        payload. Never raises and never guesses: an unreadable asset is reported,
        because an asset that cannot be decoded cannot be trained on or scanned.
        """
        import cv2

        from services.barcode_decoder import BarcodeDecoderService

        problems: List[str] = []
        decoder = BarcodeDecoderService()
        for label, path in (("card", self.card), ("crop", self.crop)):
            if path is None:
                continue
            if not path.exists():
                problems.append(f"{self.group}: {label} missing at {path}")
                continue
            image = cv2.imread(str(path))
            if image is None:
                problems.append(f"{self.group}: {label} unreadable ({path.name})")
                continue
            result = decoder.decode_best(image)
            if not result.ok:
                problems.append(
                    f"{self.group}: {label} {path.name} carries no readable barcode"
                )
                continue
            if result.data != self.payload:
                problems.append(
                    f"{self.group}: {label} {path.name} decodes {result.data!r}, "
                    f"expected {self.payload!r}"
                )
                continue

            # The recorded rectangle must still be the rectangle the decoder
            # finds. A silent drift here would propagate a wrong annotation into
            # every dataset built from this registry.
            recorded = self.box_for(path)
            found = result.barcodes[0].source_bbox or tuple(result.barcodes[0].bbox)
            height, width = image.shape[:2]
            if recorded is None or recorded == (0, 0, 0, 0):
                problems.append(f"{self.group}: {label} {path.name} has no recorded box")
                continue
            if not (0 <= recorded[0] and recorded[0] + recorded[2] <= width
                    and 0 <= recorded[1] and recorded[1] + recorded[3] <= height):
                problems.append(
                    f"{self.group}: {label} {path.name} recorded box {recorded} "
                    f"does not fit inside {width}x{height}"
                )
            if any(abs(a - b) > 2 for a, b in zip(recorded, found)):
                problems.append(
                    f"{self.group}: {label} {path.name} recorded box {recorded} "
                    f"disagrees with the decoder's {found}"
                )
        return problems


_ASSETS = settings.ASSETS_DIR

#: The verified registry. Order is by payload, not by scan order, and nothing
#: downstream may rely on it -- each entry is addressed by ``group``.
STUDENT_ASSETS: Tuple[StudentAssets, ...] = (
    StudentAssets(
        group="card_A",
        payload="000065412",
        reference="65412",
        student_id="WIT-2024-001",
        full_name="Javy Danielle N. Caporal",
        card=_ASSETS / "images" / "Id_1.png",
        card_path="assets/images/Id_1.png",
        # A tight crop of card A. It is barcode_2.png, *not* barcode_1.png:
        # verified by decoding, so the filename is not the mapping.
        crop=_ASSETS / "barcodes" / "barcode_2.png",
        crop_path="assets/barcodes/barcode_2.png",
        portrait=_ASSETS / "profile" / "javy_profile.png",
        portrait_path="assets/profile/javy_profile.png",
        split="train",
        card_box=(33, 792, 525, 112),
        crop_box=(12, 6, 579, 124),
    ),
    StudentAssets(
        group="card_B",
        payload="000065902",
        reference="65902",
        student_id="WIT-2024-002",
        full_name="Ronna Mae D. Alemania",
        card=_ASSETS / "images" / "Id_2.png",
        card_path="assets/images/Id_2.png",
        crop=_ASSETS / "barcodes" / "barcode_1.png",
        crop_path="assets/barcodes/barcode_1.png",
        portrait=_ASSETS / "profile" / "ronna_profile.png",
        portrait_path="assets/profile/ronna_profile.png",
        split="val",
        card_box=(60, 792, 479, 122),
        crop_box=(18, 19, 528, 135),
    ),
    StudentAssets(
        group="card_C",
        payload="000065736",
        reference="65736",
        student_id="WIT-2024-003",
        full_name="Steven Anthony M. Oquiana",
        card=_ASSETS / "images" / "Id_3.png",
        card_path="assets/images/Id_3.png",
        crop=_ASSETS / "barcodes" / "barcode_3.png",
        crop_path="assets/barcodes/barcode_3.png",
        portrait=_ASSETS / "profile" / "Steven_profile.png",
        portrait_path="assets/profile/Steven_profile.png",
        card_box=(38, 362, 204, 58),
        crop_box=(26, 22, 691, 196),
        # Held out. Training on it would leave nothing unseen to measure with,
        # and the model already detects a card design it has never seen (it does
        # not need this card in its training set to prove that).
        split="test",
    ),
)

BY_PAYLOAD: Dict[str, StudentAssets] = {a.payload: a for a in STUDENT_ASSETS}
BY_GROUP: Dict[str, StudentAssets] = {a.group: a for a in STUDENT_ASSETS}
BY_REFERENCE: Dict[str, StudentAssets] = {a.reference: a for a in STUDENT_ASSETS}

#: Every card image, in registry order.
CARDS: Tuple[Path, ...] = tuple(a.card for a in STUDENT_ASSETS)

#: Every tight barcode crop that exists.
CROPS: Tuple[Path, ...] = tuple(a.crop for a in STUDENT_ASSETS if a.crop)

#: Portraits, for the no-cross-contamination checks.
PORTRAITS: Tuple[Path, ...] = tuple(a.portrait for a in STUDENT_ASSETS)


def negative_panels(split: Optional[str] = None) -> List[Path]:
    """The barcode-free documents, optionally for one split only."""
    names = NEGATIVE_PANELS
    if split is not None:
        names = tuple(n for n in NEGATIVE_PANELS if NEGATIVE_SPLITS.get(n) == split)
    return [settings.info_image(name) for name in names]


def card_groups(splits: Optional[Tuple[str, ...]] = None) -> Dict[str, List[Path]]:
    """``{group: [card, crop]}`` for the requested splits, in registry order.

    Each card travels with its own tight crop. Builders that reason about a
    *physical card* -- the detection and augmentation sets -- want that, because
    a crop of a validated card in the training split leaks the card.
    """
    return {
        a.group: a.sources
        for a in STUDENT_ASSETS
        if splits is None or a.split in splits
    }


def cards_only(splits: Optional[Tuple[str, ...]] = None) -> Dict[str, List[Path]]:
    """``{group: [card]}`` -- no crops.

    The baseline dataset takes whole cards only, and keeps the crops as
    decoding reference material that is never copied in. Using
    :func:`card_groups` there would quietly violate that rule and add a tight
    crop of a validation card to the training set.
    """
    return {
        a.group: [a.card]
        for a in STUDENT_ASSETS
        if splits is None or a.split in splits
    }


def group_splits() -> Dict[str, str]:
    return {a.group: a.split for a in STUDENT_ASSETS}


def source_split() -> Dict[Path, str]:
    """Every source image mapped to its split, for the augmentation builders."""
    mapping: Dict[Path, str] = {}
    for entry in STUDENT_ASSETS:
        for path in entry.sources:
            mapping[path] = entry.split
    for name, split in NEGATIVE_SPLITS.items():
        mapping[settings.info_image(name)] = split
    return mapping


def payloads() -> Tuple[str, ...]:
    return tuple(a.payload for a in STUDENT_ASSETS)


def barcode_boxes() -> Dict[Path, Tuple[int, int, int, int]]:
    """Every source image mapped to its recorded barcode rectangle."""
    boxes: Dict[Path, Tuple[int, int, int, int]] = {}
    for entry in STUDENT_ASSETS:
        boxes[entry.card] = entry.card_box
        if entry.crop is not None and entry.crop_box is not None:
            boxes[entry.crop] = entry.crop_box
    return boxes


def entry_for(path: Path) -> Optional[StudentAssets]:
    """The student an image belongs to, matched on the file, not on its order."""
    path = Path(path)
    for entry in STUDENT_ASSETS:
        if path in entry.sources:
            return entry
    return None


def verify_registry() -> List[str]:
    """Decode every registered card and crop; return the problems found."""
    problems: List[str] = []
    for entry in STUDENT_ASSETS:
        problems.extend(entry.verify())
        if not entry.portrait.exists():
            problems.append(f"{entry.group}: portrait missing at {entry.portrait}")
    seen_payloads = set()
    for entry in STUDENT_ASSETS:
        if entry.payload in seen_payloads:
            problems.append(f"{entry.group}: payload {entry.payload} is registered twice")
        seen_payloads.add(entry.payload)
    for name in NEGATIVE_PANELS:
        path = settings.info_image(name)
        if not path.exists():
            problems.append(f"negative panel missing: {path}")
    return problems


__all__ = [
    "BY_GROUP",
    "BY_PAYLOAD",
    "BY_REFERENCE",
    "CARDS",
    "CLASS_ID",
    "CLASS_NAME",
    "CROPS",
    "NEGATIVE_PANELS",
    "NEGATIVE_SPLITS",
    "PORTRAITS",
    "STUDENT_ASSETS",
    "StudentAssets",
    "barcode_boxes",
    "card_groups",
    "cards_only",
    "entry_for",
    "group_splits",
    "negative_panels",
    "payloads",
    "source_split",
    "verify_registry",
]