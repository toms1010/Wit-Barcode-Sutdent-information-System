"""The shared asset registry, and the tooling that must read it.

Seven scripts used to name the first two students' files themselves. The third
card was therefore decoded correctly by the scanner and invisible to every
dataset builder, threshold sweep and robustness matrix -- so the number nobody
had was "how does the model behave on a card design it never trained on", which
is the only number that would have told anyone whether the production confidence
threshold was safe.

These tests pin the arrangement that fixes that:

* the registry describes every card the project has, each with a payload that
  re-decodes from the asset;
* a payload is registered once, and each card sits in exactly one split;
* the training scripts import their sources from the registry rather than
  restating file names, so adding a student cannot half-integrate again.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.student_assets import (  # noqa: E402
    BY_GROUP,
    BY_PAYLOAD,
    CARDS,
    NEGATIVE_PANELS,
    NEGATIVE_SPLITS,
    STUDENT_ASSETS,
    barcode_boxes,
    card_groups,
    cards_only,
    entry_for,
    group_splits,
    negative_panels,
    payloads,
    source_split,
    verify_registry,
)


class TestRegistryIsTruthful:
    def test_every_asset_decodes_to_its_recorded_payload(self):
        assert verify_registry() == [], (
            "the registry disagrees with the assets; payloads and boxes are "
            "decoded values, not notes"
        )

    def test_all_three_students_are_registered(self):
        assert len(STUDENT_ASSETS) == 3
        assert {a.payload for a in STUDENT_ASSETS} == {
            "000065412", "000065902", "000065736",
        }
        assert {a.reference for a in STUDENT_ASSETS} == {"65412", "65902", "65736"}
        assert BY_PAYLOAD["000065736"].full_name == "Steven Anthony M. Oquiana"
        assert BY_PAYLOAD["000065736"].reference == "65736"

    def test_a_payload_is_registered_once(self):
        assert len({a.payload for a in STUDENT_ASSETS}) == len(STUDENT_ASSETS)

    def test_group_names_are_stable(self):
        """The existing datasets were built with these names."""
        assert list(BY_GROUP) == ["card_A", "card_B", "card_C"]

    def test_the_filename_is_not_the_mapping(self):
        """barcode_1.png is Ronna's crop and barcode_2.png is Javy's.

        Pinned because a future tidy-up that "fixes" the filenames to match the
        payloads would silently relabel two students' training data.
        """
        by_payload = BY_PAYLOAD
        assert by_payload["000065902"].crop_path == "assets/barcodes/barcode_1.png"
        assert by_payload["000065412"].crop_path == "assets/barcodes/barcode_2.png"
        assert by_payload["000065736"].crop_path == "assets/barcodes/barcode_3.png"

    def test_each_card_is_in_exactly_one_split(self):
        splits = group_splits()
        assert splits == {"card_A": "train", "card_B": "val", "card_C": "test"}

    def test_a_card_and_its_crop_always_share_a_split(self):
        """Otherwise the crop leaks the card it was cut from."""
        mapping = source_split()
        for entry in STUDENT_ASSETS:
            for path in entry.sources:
                assert mapping[path] == entry.split

    def test_the_held_out_card_is_never_trained_on(self):
        held_out = [a for a in STUDENT_ASSETS if a.split == "test"]
        assert [a.group for a in held_out] == ["card_C"]
        trained = [p for p, s in source_split().items() if s == "train"]
        assert not any(entry.card in trained or entry.crop in trained for entry in held_out)

    def test_recorded_boxes_lie_inside_their_images(self):
        import cv2

        for path, (x, y, w, h) in barcode_boxes().items():
            image = cv2.imread(str(path))
            assert image is not None
            height, width = image.shape[:2]
            assert 0 <= x and x + w <= width, f"{path.name}: box crosses the right edge"
            assert 0 <= y and y + h <= height, f"{path.name}: box crosses the bottom edge"
            assert w > 0 and h > 0

    def test_the_two_grouping_rules_are_distinct(self):
        """Cards-only is what the baseline builder uses; with-crops is not."""
        cards = cards_only()
        groups = card_groups()
        for group in cards:
            assert len(cards[group]) == 1
            assert len(groups[group]) == 2, "each card has its own tight crop"

    def test_entry_for_matches_on_the_file_not_on_order(self):
        for entry in STUDENT_ASSETS:
            assert entry_for(entry.crop) is entry
            assert entry_for(entry.card) is entry
        assert entry_for(negative_panels()[0]) is None

    def test_the_negatives_are_barcode_free_documents(self):
        assert set(NEGATIVE_PANELS) == {"student_1_info.png", "student_2_info.png"}
        assert set(NEGATIVE_SPLITS.values()) == {"train", "val"}
        for name in NEGATIVE_PANELS:
            assert name not in payloads()

    def test_portraits_are_three_different_files(self):
        import hashlib

        digests = {
            entry.group: hashlib.sha256(entry.portrait.read_bytes()).hexdigest()
            for entry in STUDENT_ASSETS
        }
        assert len(set(digests.values())) == 3

    def test_the_registry_does_not_duplicate_the_cards_tuple(self):
        assert list(CARDS) == [a.card for a in STUDENT_ASSETS]


class TestToolingReadsTheRegistry:
    """The point of the registry: no script restates the file names."""

    def _paths(self, value) -> set:
        """Every path in a container that may hold dicts keyed by path, lists, or sets."""
        out = set()
        if isinstance(value, dict):
            for key, item in value.items():
                out |= self._paths(key)
                out |= self._paths(item)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                out |= self._paths(item)
        elif isinstance(value, Path):
            out.add(value)
        return out

    @pytest.mark.parametrize(
        "module, attribute",
        [
            ("scripts.build_dataset", "REAL_CARD_GROUPS"),
            ("scripts.build_detection_dataset", "CARD_GROUPS"),
            ("scripts.augment_dataset", "SOURCE_SPLIT"),
            ("scripts.train_yolo", "NEGATIVE_EVAL_ASSETS"),
        ],
    )
    def test_module_sources_cover_every_asset_they_should(self, module, attribute):
        """No script may name only the students it was written for."""
        import importlib

        value = getattr(importlib.import_module(module), attribute)
        paths = {p.name for p in self._paths(value)}
        cards = {a.card.name for a in STUDENT_ASSETS}

        if attribute == "NEGATIVE_EVAL_ASSETS":
            # Negatives are the barcode-free panels, not cards.
            assert paths == set(NEGATIVE_PANELS)
            return

        # The rest are per-card source containers, so every card must appear.
        assert cards <= paths, (
            f"{module}.{attribute} covers only {sorted(cards & paths)} of "
            f"{sorted(cards)}"
        )

    def test_the_distance_builder_knows_all_six_sources(self):
        from scripts.build_distance_dataset import BOXES, SOURCES

        assert set(SOURCES) == {p.name for p in barcode_boxes()}
        assert set(BOXES) == set(SOURCES)
        for name, meta in SOURCES.items():
            assert meta["split"] in {"train", "val", "test"}

    def test_the_augmenter_knows_the_test_split(self):
        from scripts.augment_dataset import DATASET_SPLITS, SOURCE_SPLIT

        assert DATASET_SPLITS == ("train", "val", "test")
        assert "test" in set(SOURCE_SPLIT.values())

    def test_the_trainer_evaluates_on_every_untrained_card(self):
        from scripts.train_yolo import REAL_EVAL_ASSETS, REAL_TRAIN_ASSETS

        evaluated = {p.name for p in REAL_EVAL_ASSETS}
        trained = {p.name for p in REAL_TRAIN_ASSETS}
        held_out = {p.name for a in STUDENT_ASSETS if a.split != "train"
                    for p in a.sources}

        assert held_out <= evaluated, (
            "a card the model never trained on must be part of candidate selection"
        )
        assert not (evaluated & trained), "an asset cannot be both train and eval"

    def test_the_threshold_sweep_and_robustness_matrix_cover_all_cards(self):
        from scripts import test_barcode_decoder, threshold_sweep

        assert {p.name for p in test_barcode_decoder.CARDS} == {
            a.card.name for a in STUDENT_ASSETS
        }
        assert {p.name for p in test_barcode_decoder.CROPS} == {
            a.crop.name for a in STUDENT_ASSETS
        }
        assert {p.name for p, _ in threshold_sweep.POSITIVES.items()} == {
            p.name for p in barcode_boxes()
        }

    def test_the_distance_benchmark_uses_registry_boxes(self):
        from scripts.benchmark_distances import BOXES, PAYLOADS

        assert set(BOXES) == {p.name for p in barcode_boxes()}
        assert set(PAYLOADS.values()) == set(payloads())


class TestBuildersWriteTheHeldOutCard:
    """A `test` split that nothing writes is not a held-out split."""

    def test_the_baseline_builder_honours_the_split(self, tmp_path):
        from scripts.build_dataset import build

        manifest = build(tmp_path / "ds")
        assert manifest["problems"] == []
        assert manifest["card_groups"]["card_C"]["split"] == "test"
        assert manifest["card_groups"]["card_C"]["files"] == ["Id_3.png"]
        assert (tmp_path / "ds" / "images" / "test" / "card_C_Id_3.png").exists()
        assert (tmp_path / "ds" / "data.yaml").read_text().count("images/test") == 1

    def test_the_baseline_builder_never_copies_a_crop(self, tmp_path):
        """A tight crop of a validated card is a leak."""
        from scripts.build_dataset import build

        out = tmp_path / "ds"
        manifest = build(out)
        copied = {
            p.name for split in manifest["counts"]
            for p in (out / "images" / split).iterdir()
        }
        assert not any(name.startswith("barcode_") for name in copied), copied