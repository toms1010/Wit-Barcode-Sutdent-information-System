"""Tests for the dataset builder and the training configuration.

These guard the two things that silently ruin a barcode model: a card leaking
across the train/val boundary, and a dataset that claims more independent data
than it has.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts import build_dataset as build_module
from scripts.build_dataset import (
    ALLOWED_ASSETS,
    CARD_GROUP_SPLIT,
    DECODE_REFERENCE,
    NEGATIVES,
    REAL_CARD_GROUPS,
    _data_yaml,
    _validate_label,
    build,
)

BASE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("dataset") / "barcode"
    manifest = build(out)
    return out, manifest


class TestLabelValidation:
    def test_valid_label_has_no_problems(self, tmp_path):
        p = tmp_path / "ok.txt"
        p.write_text("0 0.5 0.5 0.4 0.1\n")
        assert _validate_label(p) == []

    def test_empty_label_is_a_valid_negative(self, tmp_path):
        p = tmp_path / "neg.txt"
        p.write_text("")
        assert _validate_label(p) == []

    @pytest.mark.parametrize(
        "text",
        [
            "0 0.5 0.5 0.4\n",             # too few fields
            "1 0.5 0.5 0.4 0.1\n",         # wrong class id
            "0 1.5 0.5 0.4 0.1\n",         # centre out of range
            "0 0.5 0.5 0.0 0.1\n",         # zero width
            "0 0.02 0.5 0.4 0.1\n",        # crosses the left edge
            "0 0.5 0.99 0.4 0.1\n",        # crosses the bottom edge
        ],
    )
    def test_malformed_labels_are_rejected(self, tmp_path, text):
        p = tmp_path / "bad.txt"
        p.write_text(text)
        assert _validate_label(p), f"expected a problem for: {text!r}"


class TestDataYaml:
    def test_path_is_absolute(self, tmp_path):
        """Ultralytics resolves a relative `path` against its own datasets dir."""
        yaml_text = _data_yaml(tmp_path / "barcode")
        assert "path: /" in yaml_text, "data.yaml must use an absolute dataset path"

    def test_single_barcode_class(self, tmp_path):
        yaml_text = _data_yaml(tmp_path / "barcode")
        assert "0: barcode" in yaml_text
        assert "nc: 1" in yaml_text


class TestBuiltDataset:
    def test_build_reports_no_problems(self, built):
        _, manifest = built
        assert manifest["problems"] == []

    def test_every_image_has_a_label(self, built):
        out, manifest = built
        for split, counts in manifest["counts"].items():
            assert counts["unmatched_images"] == [], f"{split} has unlabelled images"

    def test_no_exact_duplicates_anywhere(self, built):
        _, manifest = built
        assert manifest.get("exact_duplicates") in (None, [])

    def test_no_card_leaks_across_splits(self, built):
        """No card may appear in two splits, whatever the splits are.

        Checked across every split the build wrote, not just train and val: with
        a third card held out entirely, "train vs val" is no longer the whole
        question -- a group in both `test` and `train` is the same defect and
        would invalidate the held-out numbers just as thoroughly.
        """
        out, manifest = built
        files_by_split = {
            split: {p.name for p in (out / "images" / split).iterdir()}
            for split in manifest["counts"]
        }

        for group, entries in REAL_CARD_GROUPS.items():
            stem_set = {img.stem for img in entries}
            found_in = set()
            for split, files in files_by_split.items():
                if any(
                    t.startswith(f"{group}_") and t.endswith(f"{s}{n}")
                    for s in stem_set
                    for t in files
                    for n in (".png", ".jpg")
                ):
                    found_in.add(split)
            assert len(found_in) <= 1, (
                f"{group} appears in {sorted(found_in)}; a card must live in "
                f"exactly one split"
            )

    def test_the_held_out_card_is_written_to_the_test_split(self, built):
        """Card C is registered as test-only; the build must honour that."""
        _, manifest = built

        held_out = [g for g, split in CARD_GROUP_SPLIT.items() if split == "test"]
        assert held_out, "the registry no longer holds any card out"
        for group in held_out:
            assert manifest["card_groups"][group]["split"] == "test"
            assert manifest["card_groups"][group]["files"], (
                f"{group} is held out but was not written to the test split"
            )
            assert manifest["independent_real_cards_in_test"]

    def test_every_card_group_is_assigned_a_split(self):
        for group in REAL_CARD_GROUPS:
            assert group in CARD_GROUP_SPLIT

    def test_validation_split_contains_a_real_card(self, built):
        """The whole point of the rebuild: a real card the model never trained on."""
        _, manifest = built
        assert manifest["independent_real_cards_in_val"], (
            "validation split has no unseen real card, so its score is not meaningful"
        )
        assert manifest["independent_real_cards_in_train"]

    def test_negatives_are_present_in_the_split_they_are_assigned(self, built):
        """One negative per trained split, and none in a split it was not given.

        Previously this asserted "a negative in train and in val", which quietly
        encoded the same document in two splits. Each panel now lands in exactly
        the split the registry assigns it, and the `test` split -- which holds a
        held-out card -- carries no negative rather than a borrowed one.
        """
        _, manifest = built
        assert set(manifest["negatives"].values()) == {"train", "val"}

        for split, counts in manifest["counts"].items():
            if split == "test":
                assert counts["negative"] == 0, (
                    "the held-out split must not borrow a negative from another split"
                )
            else:
                assert counts["negative"] >= 1, f"{split} has no negative samples"

    def test_negatives_are_genuinely_barcode_free(self):
        """A 'negative' that contains a barcode would poison training."""
        import cv2
        import pyzbar.pyzbar as zbar

        for panel in NEGATIVES:
            image = cv2.imread(str(panel))
            assert image is not None, f"{panel} unreadable"
            assert not zbar.decode(image), (
                f"{panel.name} actually contains a barcode and must not be a negative"
            )

    def test_real_positive_labels_actually_bound_a_barcode(self, built):
        """Annotations must sit on the symbol, not arbitrarily on the image."""
        import cv2
        import numpy as np
        import pyzbar.pyzbar as zbar

        out, _manifest = built
        checked = 0
        for split in ("train", "val"):
            for image_path in sorted((out / "images" / split).iterdir()):
                label_path = out / "labels" / split / f"{image_path.stem}.txt"
                if not label_path.read_text().strip():
                    continue  # negative
                image = cv2.imread(str(image_path))
                h, w = image.shape[:2]
                parts = label_path.read_text().strip().split()
                x, y, bw, bh = (float(v) for v in parts[1:5])
                box = ((x - bw / 2) * w, (y - bh / 2) * h, bw * w, bh * h)

                decoded = zbar.decode(image)
                assert decoded, f"{image_path.name} has no decodable barcode"
                obj = decoded[0]
                if obj.polygon:
                    pts = np.array([(p.x, p.y) for p in obj.polygon])
                    ex1, ey1 = pts.min(axis=0)
                    ex2, ey2 = pts.max(axis=0)
                else:
                    ex1, ey1 = obj.rect.left, obj.rect.top
                    ex2, ey2 = ex1 + obj.rect.width, ey1 + obj.rect.height

                ix1, iy1 = max(box[0], ex1), max(box[1], ey1)
                ix2, iy2 = min(box[0] + box[2], ex2), min(box[1] + box[3], ey2)
                inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
                union = box[2] * box[3] + (ex2 - ex1) * (ey2 - ey1) - inter
                iou = inter / union
                assert iou >= 0.85, (
                    f"{image_path.name}: annotation IoU {iou:.2f} against the real "
                    f"symbol extent; the box does not describe the barcode"
                )
                checked += 1
        assert checked >= 2, f"expected at least 2 annotated positives, checked {checked}"

    def test_only_permitted_images_are_copied(self, built):
        """Nothing outside the permitted cards and panels may enter the dataset.

        Checked in every split the build wrote. A tight crop of a validation card
        is exactly the leak this catches, and it used to be copied in silently
        because the manifest listed it as allowed.
        """
        out, manifest = built
        allowed = [Path(p).name for p in manifest["yolo_images_allowed"]]
        assert "barcode_2.png" not in allowed, (
            "a tight crop must never be a permitted dataset image: it is a "
            "leak of the card it was cut from"
        )
        for split in manifest["counts"]:
            for image_path in (out / "images" / split).iterdir():
                # Copies carry a provenance prefix (card_A_ / card_B_ / negative_).
                matches = [a for a in allowed if image_path.name.endswith(a)]
                assert matches, (
                    f"{image_path.name} is not one of the permitted assets {allowed}"
                )
        assert manifest["synthetic_data_used"] is False
        assert manifest["external_dataset_used"] is False


class TestExistingAssetsUntouched:
    def test_builder_does_not_modify_its_sources(self, built):
        """Building a dataset must never write back into assets/."""
        _, manifest = built
        for asset, digest in manifest["source_digests_before"].items():
            path = Path(asset)
            assert path.exists(), f"source asset vanished: {path}"
            assert build_module._digest(path) == digest, f"source asset changed: {path}"
        for panel in NEGATIVES:
            assert panel.exists(), f"source panel vanished: {panel}"

    def test_build_reports_no_source_modification(self, built):
        _, manifest = built
        assert not [p for p in manifest["problems"] if "SOURCE MODIFIED" in p]
