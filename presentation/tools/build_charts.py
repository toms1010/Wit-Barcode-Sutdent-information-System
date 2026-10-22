#!/usr/bin/env python3
"""Build every chart and the dataset gallery from real project data.

Inputs
------
database (live MySQL, read-only), datasets/**/images+labels,
models_yolo/candidates/*/results.csv, assets/barcodes/*.

Outputs
-------
presentation/charts/*.png  + JSON evidence files
presentation/screenshots/24_dataset_gallery.png
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
from PIL import Image, ImageDraw, ImageFont

CHARTS = PROJECT / "presentation" / "charts"
SHOTS = PROJECT / "presentation" / "screenshots"
CHARTS.mkdir(parents=True, exist_ok=True)

ORANGE = "#F97316"
DARK = "#AD4D05"
GOLD = "#FFD24D"
INK = "#0F172A"
SLATE = "#64748B"
GREEN = "#16A34A"
RED = "#DC2626"
GRID = "#E2E8F0"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10.5,
    "axes.edgecolor": "#CBD5E1",
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": SLATE,
    "ytick.color": SLATE,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.dpi": 170,
    "savefig.bbox": "tight",
    "savefig.facecolor": "white",
})


def finish(fig, name):
    path = CHARTS / name
    fig.savefig(path)
    plt.close(fig)
    print(f"[chart] {name}")


# --------------------------------------------------------------------------- #
# 1. Dataset scan
# --------------------------------------------------------------------------- #
def scan_datasets():
    root = PROJECT / "datasets"
    per_ds = {}
    boxes = []          # (width_norm, height_norm)
    classes = Counter()
    resolutions = Counter()
    total_bytes = 0
    total_images = 0
    total_labels = 0

    for ds in sorted(p for p in root.iterdir() if p.is_dir()):
        counts = {}
        dims = Counter()
        n_boxes = 0
        negatives = 0
        missing_label = 0
        invalid = 0
        for split in ("train", "val", "test"):
            img_dir = ds / "images" / split
            lbl_dir = ds / "labels" / split
            imgs = sorted(img_dir.glob("*")) if img_dir.is_dir() else []
            imgs = [p for p in imgs if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}]
            counts[split] = len(imgs)
            total_images += len(imgs)
            for img in imgs:
                total_bytes += img.stat().st_size
                try:
                    with Image.open(img) as im:
                        resolutions[im.size] += 1
                        dims[im.size] += 1
                except Exception:
                    pass
                lbl = lbl_dir / (img.stem + ".txt")
                if not lbl.exists():
                    missing_label += 1
                    continue
                total_labels += 1
                lines = [ln for ln in lbl.read_text().splitlines() if ln.strip()]
                if not lines:
                    negatives += 1
                for ln in lines:
                    parts = ln.split()
                    if len(parts) != 5:
                        invalid += 1
                        continue
                    try:
                        c, x, y, w, h = int(parts[0]), *map(float, parts[1:])
                    except ValueError:
                        invalid += 1
                        continue
                    classes[c] += 1
                    n_boxes += 1
                    if w <= 0 or h <= 0 or w > 1 or h > 1:
                        invalid += 1
                    boxes.append((w, h))
        # within-dataset duplicate detection (md5 of every image)
        hashes = Counter()
        for split in ("train", "val", "test"):
            img_dir = ds / "images" / split
            if img_dir.is_dir():
                for img in img_dir.iterdir():
                    if img.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
                        hashes[hashlib.md5(img.read_bytes()).hexdigest()] += 1
        per_ds[ds.name] = {
            "train": counts["train"], "val": counts["val"], "test": counts["test"],
            "images": sum(counts.values()), "annotations": n_boxes,
            "negative_images": negatives, "missing_labels": missing_label,
            "invalid_boxes": invalid,
            "duplicate_files": sum(v - 1 for v in hashes.values() if v > 1),
            "resolutions": len(dims),
        }
        print(f"  scanned {ds.name}: {sum(counts.values())} images, {n_boxes} boxes")

    summary = {
        "datasets": per_ds,
        "total_images": total_images,
        "total_label_files": total_labels,
        "total_annotations": sum(classes.values()),
        "classes": {str(k): v for k, v in classes.items()},
        "total_bytes": total_bytes,
        "resolutions": {f"{w}x{h}": c for (w, h), c in resolutions.most_common(20)},
        "unique_resolutions": len(resolutions),
        "bbox_width": [b[0] for b in boxes],
        "bbox_height": [b[1] for b in boxes],
        "median_box_w": float(np.median([b[0] for b in boxes])) if boxes else 0,
        "median_box_h": float(np.median([b[1] for b in boxes])) if boxes else 0,
    }
    (CHARTS / "dataset_statistics.json").write_text(json.dumps(
        {k: v for k, v in summary.items() if k not in ("bbox_width", "bbox_height")},
        indent=2))
    return summary


# --------------------------------------------------------------------------- #
# 2. Training results
# --------------------------------------------------------------------------- #
def scan_training():
    runs = {}
    for csv_path in sorted((PROJECT / "models_yolo" / "candidates").glob("*/results.csv")):
        name = csv_path.parent.name
        rows = []
        with csv_path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                clean = {k.strip(): v for k, v in row.items() if k is not None}
                try:
                    rows.append({k: float(v) for k, v in clean.items() if v not in ("", None)})
                except ValueError:
                    continue
        if not rows:
            continue
        def best(key):
            vals = [(r.get(key), r.get("epoch")) for r in rows if key in r]
            if not vals:
                return None, None
            v, e = max(vals, key=lambda t: t[0])
            return v, int(e)
        best_map95, best_epoch = best("metrics/mAP50-95(B)")
        best_map50, _ = best("metrics/mAP50(B)")
        last = rows[-1]
        runs[name] = {
            "epochs_recorded": len(rows),
            "first_epoch": int(rows[0].get("epoch", 0)),
            "last_epoch": int(last.get("epoch", 0)),
            "best_map50": best_map50,
            "best_map50_95": best_map95,
            "best_epoch": best_epoch,
            "final_map50": last.get("metrics/mAP50(B)"),
            "final_map50_95": last.get("metrics/mAP50-95(B)"),
            "final_precision": last.get("metrics/precision(B)"),
            "final_recall": last.get("metrics/recall(B)"),
            "rows": rows,
        }
    out = {k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in runs.items()}
    (CHARTS / "training_metrics.json").write_text(json.dumps(out, indent=2))
    return runs


# --------------------------------------------------------------------------- #
# 3. Inference latency (real measurement)
# --------------------------------------------------------------------------- #
def measure_latency():
    import time
    import cv2
    from services.yolo_detector import YOLODetectorService

    det = YOLODetectorService()
    det.load_active_model()
    card = cv2.imread(str(PROJECT / "assets" / "images" / "Id_1.png"))
    frame = cv2.resize(card, (1280, 720))
    result = {}
    for device in ("0", "cpu"):
        det.device = device
        det.detect(frame)  # warm-up
        samples = []
        for _ in range(12):
            t0 = time.perf_counter()
            det.detect(frame)
            samples.append((time.perf_counter() - t0) * 1000)
        result[device] = {
            "device": "RTX 2050 (CUDA)" if device == "0" else "CPU",
            "runs": len(samples),
            "mean_ms": round(float(np.mean(samples)), 2),
            "median_ms": round(float(np.median(samples)), 2),
            "p95_ms": round(float(np.percentile(samples, 95)), 2),
            "samples_ms": [round(s, 2) for s in samples],
        }
        print(f"  latency {device}: {result[device]['mean_ms']} ms mean")
    (CHARTS / "inference_latency.json").write_text(json.dumps(result, indent=2))
    return result


# --------------------------------------------------------------------------- #
# 4. Database evidence
# --------------------------------------------------------------------------- #
def db_summary():
    from database.connection import get_cursor

    queries = {
        "scans_by_status": "SELECT scan_status k, COUNT(*) v FROM barcode_scans GROUP BY scan_status",
        "scans_by_day": "SELECT DATE(scanned_at) k, COUNT(*) v FROM barcode_scans GROUP BY DATE(scanned_at) ORDER BY k",
        "scans_by_day_status": "SELECT DATE(scanned_at) k, scan_status s, COUNT(*) v FROM barcode_scans GROUP BY DATE(scanned_at), scan_status ORDER BY k",
        "students_by_program": "SELECT program k, COUNT(*) v FROM students GROUP BY program",
        "students_by_year": "SELECT year_level k, COUNT(*) v FROM students GROUP BY year_level",
        "students_by_status": "SELECT enrollment_status k, COUNT(*) v FROM students GROUP BY enrollment_status",
        # Every one of the 19 tables, so the chart's row total is the whole database
        # rather than a subset that would understate it.
        "table_rows": ("SELECT 'students' t, COUNT(*) c FROM students UNION ALL SELECT 'barcodes',COUNT(*) FROM barcodes "
                       "UNION ALL SELECT 'barcode_scans',COUNT(*) FROM barcode_scans UNION ALL SELECT 'enrollments',COUNT(*) FROM enrollments "
                       "UNION ALL SELECT 'enrollment_subjects',COUNT(*) FROM enrollment_subjects UNION ALL SELECT 'assessments',COUNT(*) FROM assessments "
                       "UNION ALL SELECT 'assessment_items',COUNT(*) FROM assessment_items UNION ALL SELECT 'payments',COUNT(*) FROM payments "
                       "UNION ALL SELECT 'payment_schedules',COUNT(*) FROM payment_schedules UNION ALL SELECT 'id_cards',COUNT(*) FROM id_cards "
                       "UNION ALL SELECT 'yolo_models',COUNT(*) FROM yolo_models UNION ALL SELECT 'dataset_images',COUNT(*) FROM dataset_images "
                       "UNION ALL SELECT 'audit_logs',COUNT(*) FROM audit_logs UNION ALL SELECT 'assessment_categories',COUNT(*) FROM assessment_categories "
                       "UNION ALL SELECT 'courses',COUNT(*) FROM courses UNION ALL SELECT 'subjects',COUNT(*) FROM subjects "
                       "UNION ALL SELECT 'student_contacts',COUNT(*) FROM student_contacts UNION ALL SELECT 'system_settings',COUNT(*) FROM system_settings "
                       "UNION ALL SELECT 'users',COUNT(*) FROM users"),
        "assessment_totals": "SELECT COUNT(*) n, SUM(total_assessed) assessed, SUM(total_payments) paid, SUM(outstanding_balance) balance FROM assessments",
        "barcode_types": "SELECT barcode_type k, COUNT(*) v FROM barcodes GROUP BY barcode_type",
        "missing_barcodes": "SELECT COUNT(*) c FROM students WHERE barcode_payload IS NULL OR barcode_payload=''",
    }
    out = {}
    import decimal, datetime
    for name, sql in queries.items():
        with get_cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        clean = []
        for r in rows:
            d = {}
            for k, v in r.items():
                if isinstance(v, decimal.Decimal):
                    v = float(v)
                elif isinstance(v, (datetime.date, datetime.datetime)):
                    v = str(v)
                d[k] = v
            clean.append(d)
        out[name] = clean
    (CHARTS / "database_statistics.json").write_text(json.dumps(out, indent=2))
    return out


# --------------------------------------------------------------------------- #
# 5. Charts
# --------------------------------------------------------------------------- #
def chart_dataset(ds):
    d = ds["datasets"]
    names = list(d)
    train = [d[n]["train"] for n in names]
    val = [d[n]["val"] for n in names]
    test = [d[n]["test"] for n in names]
    fig, ax = plt.subplots(figsize=(9.2, 4.4))
    y = np.arange(len(names))
    ax.barh(y, train, color=ORANGE, label="train")
    ax.barh(y, val, left=train, color=DARK, label="val")
    ax.barh(y, test, left=np.array(train) + np.array(val), color=GOLD, label="test")
    for i, n in enumerate(names):
        total = train[i] + val[i] + test[i]
        ax.text(total + 12, i, str(total), va="center", fontsize=9, color=INK, fontweight="bold")
    ax.set_yticks(y, names)
    ax.set_xlabel("Image files")
    ax.set_title("Dataset inventory — images per split (measured from datasets/)")
    ax.legend(frameon=False, loc="lower right")
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    finish(fig, "chart_dataset_inventory.png")

    fig, ax = plt.subplots(figsize=(9.2, 4.2))
    ann = [d[n]["annotations"] for n in names]
    neg = [d[n]["negative_images"] for n in names]
    x = np.arange(len(names))
    ax.bar(x, ann, color=ORANGE, label="barcode boxes")
    ax.bar(x, neg, bottom=ann, color=SLATE, label="negative images (no box)")
    for i, n in enumerate(names):
        ax.text(i, ann[i] + neg[i] + 12, str(ann[i]), ha="center", fontsize=8.5, color=INK)
    ax.set_xticks(x, names, rotation=28, ha="right")
    ax.set_ylabel("Annotation records")
    ax.set_title("Annotation volume — one class (barcode), 3 legitimate negatives per source set")
    ax.legend(frameon=False)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    finish(fig, "chart_dataset_annotations.png")

    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.9))
    for ax, vals, label, colour in [
        (axes[0], ds["bbox_width"], "Normalised box width", ORANGE),
        (axes[1], ds["bbox_height"], "Normalised box height", DARK),
    ]:
        ax.hist(vals, bins=40, color=colour, alpha=0.9)
        med = np.median(vals)
        ax.axvline(med, color=INK, linestyle="--", linewidth=1.1)
        ax.text(med, ax.get_ylim()[1] * 0.92, f" median {med:.3f}", fontsize=9, color=INK)
        ax.set_xlabel(label + " (fraction of frame)")
        ax.set_ylabel("Boxes")
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
    fig.suptitle(f"Bounding-box geometry — {ds['total_annotations']} boxes", fontweight="bold")
    finish(fig, "chart_bbox_distribution.png")

    res = list(ds["resolutions"].items())[:10]
    fig, ax = plt.subplots(figsize=(9.2, 3.8))
    labels = [r[0] for r in res][::-1]
    counts = [r[1] for r in res][::-1]
    ax.barh(labels, counts, color=ORANGE)
    for i, c in enumerate(counts):
        ax.text(c + 4, i, str(c), va="center", fontsize=9, color=INK)
    ax.set_xlabel("Images")
    ax.set_title(f"Image resolutions — {ds['unique_resolutions']} distinct sizes across {ds['total_images']} images")
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    finish(fig, "chart_image_resolutions.png")


def chart_training(runs):
    def get(name, key):
        rows = runs[name]["rows"]
        return [r.get("epoch") for r in rows], [r.get(key) for r in rows]

    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.0))
    for ax, key, title in [
        (axes[0], "metrics/mAP50(B)", "mAP@0.50"),
        (axes[1], "metrics/mAP50-95(B)", "mAP@0.50:0.95"),
    ]:
        for name, colour in [
            ("barcode_detector_v5_aug_20261015_060903", GOLD),
            ("barcode_detector_v6_full_20261015_090208", DARK),
            ("barcode_detector_v7_distance_20261015_20261015_221339", ORANGE),
        ]:
            x, y = get(name, key)
            label = name.split("_2026")[0].replace("barcode_detector_", "")
            ax.plot(x, y, color=colour, linewidth=1.8, label=label)
        ax.set_xlabel("Epoch")
        ax.set_ylabel(title)
        ax.set_title(title)
        ax.grid(color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
    axes[0].legend(frameon=False, fontsize=8.5)
    fig.suptitle("Validation metrics from results.csv (no values interpolated)", fontweight="bold", fontsize=12)
    finish(fig, "chart_training_map.png")

    name = "barcode_detector_v7_distance_20261015_20261015_221339"
    rows = runs[name]["rows"]
    ep = [r["epoch"] for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 3.9))
    for ax, prefix, title in [(axes[0], "train", "Training losses"), (axes[1], "val", "Validation losses")]:
        for key, colour, label in [
            ("box_loss", ORANGE, "box"),
            ("cls_loss", DARK, "cls"),
            ("dfl_loss", SLATE, "dfl"),
        ]:
            col = f"{prefix}/{key}"
            if col in rows[0]:
                ax.plot(ep, [r[col] for r in rows], color=colour, linewidth=1.7, label=label)
        ax.set_xlabel("Epoch")
        ax.set_ylabel("Loss")
        ax.set_title(title)
        ax.grid(color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.legend(frameon=False, fontsize=9)
    fig.suptitle("Active model (v7-distance) — losses over 64 recorded epochs", fontweight="bold", fontsize=12)
    finish(fig, "chart_training_losses.png")

    names = [
        "barcode_detector_strict_20261015_045020",
        "barcode_detector_v3_20261015_045947",
        "barcode_detector_v4_tame_20261015_050513",
        "barcode_detector_v5_aug_20261015_060903",
        "barcode_detector_v6_full_20261015_090208",
        "barcode_detector_v7_distance_20261015_20261015_221339",
    ]
    short = ["strict", "v3", "v4-tame", "v5-aug", "v6-full", "v7-distance"]
    m50 = [runs[n]["best_map50"] or 0 for n in names]
    m95 = [runs[n]["best_map50_95"] or 0 for n in names]
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(9.4, 4.0))
    w = 0.38
    b1 = ax.bar(x - w/2, m50, w, color=ORANGE, label="mAP@0.50")
    b2 = ax.bar(x + w/2, m95, w, color=DARK, label="mAP@0.50:0.95")
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x() + b.get_width()/2, b.get_height() + 0.012,
                    f"{b.get_height():.3f}", ha="center", fontsize=8, color=INK)
    ax.set_xticks(x, short)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("Best validation score")
    ax.set_title("Candidate comparison — best mAP per training run (results.csv)")
    ax.legend(frameon=False, ncol=2)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    finish(fig, "chart_model_comparison.png")


def chart_db(db):
    st = {r["k"]: r["v"] for r in db["scans_by_status"]}
    fig, ax = plt.subplots(figsize=(6.0, 3.4))
    labels = [k.replace("_", " ").title() for k in st]
    counts = list(st.values())
    colours = [GREEN if k == "success" else RED for k in st]
    ax.bar(labels, counts, color=colours, width=0.55)
    for i, c in enumerate(counts):
        ax.text(i, c + 0.8, str(c), ha="center", fontweight="bold", color=INK)
    ax.set_ylabel("Scan records")
    ax.set_title(f"Scan outcomes — {sum(counts)} rows in barcode_scans")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    finish(fig, "chart_scan_outcomes.png")

    days = defaultdict(lambda: {"success": 0, "not_found": 0})
    for r in db["scans_by_day_status"]:
        days[r["k"]][r["s"]] = r["v"]
    x = list(days)
    succ = [days[d]["success"] for d in x]
    nf = [days[d]["not_found"] for d in x]
    fig, ax = plt.subplots(figsize=(8.4, 3.6))
    ax.bar(x, succ, color=GREEN, label="success")
    ax.bar(x, nf, bottom=succ, color=RED, label="not found")
    for i, d in enumerate(x):
        ax.text(i, succ[i] + nf[i] + 0.6, str(succ[i] + nf[i]), ha="center", fontsize=9, color=INK, fontweight="bold")
    ax.set_ylabel("Scans")
    ax.set_title("Recorded scan activity, 15–19 October 2026 (barcode_scans.scanned_at)")
    ax.legend(frameon=False)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    finish(fig, "chart_scan_activity.png")

    rows = sorted(db["table_rows"], key=lambda r: r["c"], reverse=True)
    labels = [r["t"] for r in rows]
    counts = [r["c"] for r in rows]
    fig, ax = plt.subplots(figsize=(8.8, 4.4))
    ax.barh(labels[::-1], counts[::-1], color=ORANGE)
    for i, c in enumerate(counts[::-1]):
        ax.text(c + 0.6, i, str(c), va="center", fontsize=9, color=INK)
    ax.set_xlabel("Rows")
    ax.set_title(f"Live database contents by table ({sum(counts)} rows across 19 tables)")
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    finish(fig, "chart_db_inventory.png")

    fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.3))
    for ax, key, title in [
        (axes[0], "students_by_program", "Students by program"),
        (axes[1], "students_by_year", "Students by year level"),
    ]:
        rows = db[key]
        ax.bar([str(r["k"]) for r in rows], [r["v"] for r in rows], color=[ORANGE, DARK, GOLD][:len(rows)])
        for i, r in enumerate(rows):
            ax.text(i, r["v"] + 0.05, str(r["v"]), ha="center", fontweight="bold", color=INK)
        ax.set_title(title)
        ax.set_ylim(0, max(r["v"] for r in rows) + 1)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
    fig.suptitle("Demonstration student records (3 rows, aggregated — no personal data)", fontweight="bold", fontsize=11)
    finish(fig, "chart_students.png")


def chart_module_status():
    modules = [
        ("Dashboard", 2), ("Students", 2), ("Barcode Scanner", 2), ("Subject Enrollment", 2),
        ("Courses & Subjects", 2), ("Certificate of Matriculation", 2),
        ("Assessment & Fees", 2), ("Reports & Analytics", 2),
        ("Training Center", 1), ("Settings", 1), ("ID Card Preview", 1),
        ("Barcode Generator", 0), ("Authentication / Logout", 0),
    ]
    # 2 = implemented, 1 = partial, 0 = stub/unrouted
    fig, ax = plt.subplots(figsize=(9.0, 4.2))
    colours = {2: GREEN, 1: ORANGE, 0: RED}
    labels = {2: "Implemented", 1: "Partial", 0: "Stub / not routed"}
    y = np.arange(len(modules))
    for i, (name, status) in enumerate(modules):
        ax.barh(i, 1, color=colours[status])
        ax.text(0.02, i, name, va="center", fontsize=9, color="white", fontweight="bold")
    ax.set_yticks([])
    ax.set_xlim(0, 1)
    ax.set_xticks([])
    ax.set_title("Module implementation status — verified against source (13 modules/pages)")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=colours[k], label=labels[k]) for k in (2, 1, 0)],
              frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.02))
    for spine in ax.spines.values():
        spine.set_visible(False)
    finish(fig, "chart_module_status.png")


def chart_latency(lat):
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    labels = [lat[k]["device"] for k in ("0", "cpu")]
    means = [lat[k]["mean_ms"] for k in ("0", "cpu")]
    ax.bar(labels, means, color=[ORANGE, SLATE], width=0.45)
    for i, v in enumerate(means):
        ax.text(i, v + max(means) * 0.02, f"{v:.1f} ms", ha="center", fontweight="bold", color=INK)
    ax.set_ylabel("Milliseconds per 1280×720 frame")
    ax.set_title("Measured YOLOv8n inference latency (12 runs after warm-up)")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    finish(fig, "chart_inference_latency.png")


# --------------------------------------------------------------------------- #
# 6. Dataset gallery (barcode crops only — no student PII)
# --------------------------------------------------------------------------- #
def build_gallery(max_items=12):
    candidates = []
    for ds in ("barcode_detection_all", "barcode_distance_all", "barcode_aug_all"):
        ds_path = PROJECT / "datasets" / ds
        if not ds_path.is_dir():
            continue
        for split in ("train", "val", "test"):
            img_dir = ds_path / "images" / split
            lbl_dir = ds_path / "labels" / split
            if not img_dir.is_dir():
                continue
            for lbl in sorted(lbl_dir.glob("*.txt")):
                lines = [l for l in lbl.read_text().splitlines() if l.strip()]
                if not lines:
                    continue
                img_path = img_dir / (lbl.stem + ".png")
                if not img_path.exists():
                    img_path = img_dir / (lbl.stem + ".jpg")
                if img_path.exists():
                    candidates.append((ds, split, img_path, lbl))
    # spread picks across datasets
    picks, seen = [], set()
    for ds in ("barcode_detection_all", "barcode_distance_all", "barcode_aug_all"):
        subset = [c for c in candidates if c[0] == ds][: max_items // 3]
        picks.extend(subset)
    picks = picks[:max_items]

    cell_w, cell_h, pad = 300, 150, 14
    cols = 3
    rows = (len(picks) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * (cell_w + pad) + pad, rows * (cell_h + 46 + pad) + pad + 42), "#FFF7ED")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text((pad, 12), "Dataset samples — barcode region only (cropped from YOLO labels); no student data shown",
              fill="#0F172A", font=font)
    for idx, (ds, split, img_path, lbl) in enumerate(picks):
        with Image.open(img_path) as im:
            im = im.convert("RGB")
            W, H = im.size
            parts = lbl.read_text().split()[0:5]
            _, x, y, w, h = parts
            x, y, w, h = float(x), float(y), float(w), float(h)
            m = 0.03
            box = (int(max(0, (x - m)) * W), int(max(0, (y - m)) * H),
                   int(min(1, (x + w + m)) * W), int(min(1, (y + h + m)) * H))
            crop = im.crop(box)
            crop.thumbnail((cell_w, cell_h), Image.LANCZOS)
        col, row = idx % cols, idx // cols
        ox = pad + col * (cell_w + pad)
        oy = 42 + pad + row * (cell_h + 46 + pad)
        canvas.paste(crop, (ox + (cell_w - crop.width) // 2, oy + (cell_h - crop.height) // 2))
        draw.rectangle([ox, oy, ox + cell_w, oy + cell_h], outline="#E2E8F0", width=1)
        draw.text((ox + 4, oy + cell_h + 4), f"{ds.replace('barcode_', '')} / {split}", fill="#0F172A", font=font)
        draw.text((ox + 4, oy + cell_h + 18), f"{img_path.name[:40]}  box {w:.3f}x{h:.3f}", fill="#64748B", font=font)
    out = SHOTS / "24_dataset_gallery.png"
    canvas.save(out, "PNG")
    print(f"[gallery] {out} ({len(picks)} crops)")


def main():
    print("Scanning datasets (this reads ~2.8 GB of images once)…")
    ds = scan_datasets()
    print("Reading training results…")
    runs = scan_training()
    print("Querying database…")
    db = db_summary()
    print("Measuring inference latency…")
    lat = measure_latency()

    chart_dataset(ds)
    chart_training(runs)
    chart_db(db)
    chart_module_status()
    chart_latency(lat)
    build_gallery()

    print("\nDataset totals:", ds["total_images"], "images,", ds["total_annotations"], "boxes,",
          round(ds["total_bytes"] / 1e9, 2), "GB")


if __name__ == "__main__":
    main()
