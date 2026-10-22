# WIT Barcode Student Information System - Dataset Analysis Report

**Date**: October 20, 2026  
**Source**: WIT Barcode Student Information System  
**Dataset Root**: `datasets/`  
**Analysis Method**: Filesystem scan of all images and YOLO text labels; no fabricated values

## Executive Summary

The dataset comprises eight curated variants of barcode images collected to train and evaluate a single-class barcode detector for student ID cards. All metrics below are measured directly from the filesystem and label files. The active model is trained on `barcode_distance/data.yaml` (v7-distance).

## Inventory

- **Total dataset images (all variants)**: 2,551
- **Total label files (.txt)**: 2,551 (one per annotated image)
- **Total bounding boxes**: 2,515
- **Classes**: 1 (`barcode`)
- **Deliberate negative images (no barcode)**: 36
- **Total disk size**: 2.77 GB
- **Distinct resolutions**: 43
- **Duplicate image files detected**: 8 (within `barcode_distance`)

## Per-Variant Breakdown

| Variant | train | val | test | images | annotations | negatives | resolutions |
|---|---|------|------|--------|-------------|-----------|-------------|
| barcode | 2 | 2 | 0 | 4 | 2 | 2 | 2 |
| barcode_all_students | 3 | 3 | 2 | 8 | 6 | 2 | 6 |
| barcode_aug | 22 | 22 | 0 | 44 | 42 | 2 | 6 |
| barcode_aug_all | 43 | 43 | 22 | 108 | 106 | 2 | 22 |
| barcode_detection | 67 | 69 | 0 | 136 | 134 | 2 | 19 |
| barcode_detection_all | 68 | 69 | 60 | 197 | 195 | 2 | 30 |
| barcode_distance | 414 | 414 | 0 | 828 | 812 | 16 | 1 |
| barcode_distance_all | 410 | 410 | 406 | 1226 | 1218 | 8 | 1 |

## Resolution Distribution (Top 20)

- 1280×720: 2054
- 590×1004: 175
- 562×202: 71
- 608×187: 69
- 736×278: 44
- 270×459: 22
- 659×1043: 16
- 1414×2000: 12
- 724×1076: 8
- 620×229: 6
- 575×241: 6
- 383×652: 4
- 472×803: 4
- 737×1255: 4
- 754×329: 4
- 815×1117: 4
- 413×702: 4
- 767×1305: 4
- 628×270: 4
- 585×278: 4

## Box Geometry

- **Median box width (relative)**: 0.5035 (50.4% of frame width)
- **Median box height (relative)**: 0.1744 (17.4% of frame height)
- **Annotation validity**: 0 missing label files; 0 invalid/malformed boxes detected

## Split Strategy Assessment

- The **core training variant** (`barcode_distance`, used by active model `models_yolo/active/best.pt`) has **0 test images**. Model selection was performed using validation metrics only; no held-out test set exists for this specific variant.
- A larger variant (`barcode_distance_all`) provides 406 test images (33% of that pool) across the combined distance corpus.
- Augmentation/detection variants follow a mixed split strategy; some include a test split, others do not.

## Notes on Quality

- High-resolution native captures dominate (1280×720 at 2,054 images) alongside a long tail of varying card orientations and distances.
- Negative samples (36) are explicitly provided to reduce false positives.
- The high number of near-identical frames per source card (especially in distance series) inflates counts; the file-level duplicates (8) are tracked and documented.
- Labels are in YOLO format (normalized x,y,w,h; class 0). All labels correspond to real barcode regions on student ID card images.

## Reference

- Dataset manifest/charts: `presentation/charts/dataset_statistics.json`, `presentation/charts/chart_dataset_inventory.png`, `presentation/charts/chart_dataset_annotations.png`, `presentation/charts/chart_bbox_distribution.png`, `presentation/charts/chart_image_resolutions.png`
- Label/box gallery (region crops only, no student data): `presentation/screenshots/24_dataset_gallery.png`