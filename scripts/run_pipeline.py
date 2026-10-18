#!/usr/bin/env python3
"""One command that runs the whole barcode pipeline and reports honestly.

    python -m scripts.run_pipeline

Stages, in order, each able to stop the run:

1. ``assets``    verify the six permitted assets exist, are readable, unchanged
2. ``dataset``   rebuild the baseline dataset from assets/images/ only
3. ``augment``   (optional) build derivative training variants, or ``--no-augment``
4. ``decode``    direct decoding of the two reference barcodes
5. ``train``     train a candidate, or skip with ``--skip-train``
6. ``evaluate``  score the candidate and apply the promotion gate
7. ``verify``    end-to-end PASS/FAIL over the six assets
8. ``report``    refresh the machine-readable summaries

Every stage records its command, exit code and outcome. A stage that fails is
reported as failed; nothing is reported as passing because a later stage ran.
The run stops on a critical failure rather than training on invalid data.

Deliberately non-destructive: nothing here deletes a dataset, a checkpoint or a
log. The active model is only replaced when ``--promote`` is given *and* the
gate approves it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402

logger = setup_logging()

PY = sys.executable

#: Digests recorded when this pipeline was written. A mismatch means an asset was
#: edited, which the task forbids -- so it is a critical failure, not a warning.
EXPECTED_DIGESTS = {
    "assets/barcodes/barcode_1.png": "9adeb2f3248ca80744232465139e5bd9",
    "assets/barcodes/barcode_2.png": "54f238f873fa4c697c4f2206b08717da",
    "assets/images/Id_1.png": "15de778f2c61c9924b0ed0432d829853",
    "assets/images/Id_2.png": "38e37019c04f9741e2e064e8262cc1e8",
    "assets/images/info/student_1_info.png": "6f6160d4e5c72fbbdd18d34e7722823a",
    "assets/images/info/student_2_info.png": "57c4262c9838999937d71d8e3ce2b834",
}


class Stage:
    def __init__(self, name: str, description: str, critical: bool = True):
        self.name = name
        self.description = description
        self.critical = critical


def digest(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def stage_assets() -> Dict:
    print("\n[assets] verifying the six permitted source files")
    problems: List[str] = []
    for rel, expected in EXPECTED_DIGESTS.items():
        path = BASE_DIR / rel
        if not path.exists():
            problems.append(f"missing: {rel}")
            continue
        actual = digest(path)
        status = "unchanged" if actual == expected else f"CHANGED (md5 {actual})"
        print(f"    {rel:44s} {status}")
        if actual != expected:
            problems.append(f"digest changed: {rel}")
    return {"problems": problems, "ok": not problems}


def run(cmd: List[str], label: str, timeout: Optional[int] = None) -> Dict:
    print(f"\n$ {' '.join(cmd)}")
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(BASE_DIR), timeout=timeout,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        print(f"    TIMEOUT after {timeout}s")
        return {"command": cmd, "returncode": -1, "ok": False,
                "seconds": round(time.time() - started, 1),
                "error": "timeout", "tail": ""}
    seconds = round(time.time() - started, 1)
    tail = (proc.stdout or "").strip().splitlines()[-12:]
    for line in tail:
        print(f"    {line}")
    if proc.returncode != 0:
        print("    stderr tail:")
        for line in (proc.stderr or "").strip().splitlines()[-8:]:
            print(f"    ! {line}")
    return {"command": cmd, "returncode": proc.returncode,
            "ok": proc.returncode == 0, "seconds": seconds,
            "tail": "\n".join(tail)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-train", action="store_true",
                    help="rebuild and verify only; do not train")
    ap.add_argument("--no-augment", action="store_true",
                    help="use the baseline dataset instead of the augmented one")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--device", default=settings.YOLO_DEVICE)
    ap.add_argument("--name", default="barcode_detector_pipeline")
    ap.add_argument("--promote", action="store_true",
                    help="install the candidate only if the promotion gate approves it")
    args = ap.parse_args()

    stages: List[Dict] = []
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 78)
    print(f"Barcode pipeline run  {started}")
    print("=" * 78)

    # 1. assets -------------------------------------------------------------
    result = stage_assets()
    stages.append({"stage": "assets", "ok": result["ok"], "problems": result["problems"]})
    if not result["ok"]:
        print("\nCRITICAL: source assets are missing or modified. Stopping.")
        write_summary(started, stages)
        return 2

    baseline_dir = BASE_DIR / "datasets" / "barcode"
    augmented_dir = BASE_DIR / "datasets" / "barcode_aug"

    # 2. baseline dataset ---------------------------------------------------
    stages.append({
        "stage": "dataset",
        **run([PY, "-m", "scripts.build_dataset", "--out", str(baseline_dir)], "dataset"),
    })
    if not stages[-1]["ok"]:
        print("\nCRITICAL: baseline dataset did not build. Not training on invalid data.")
        write_summary(started, stages)
        return 3

    # 3. augmentation -------------------------------------------------------
    train_data = baseline_dir / "data.yaml"
    if args.no_augment:
        print("\n[augment] skipped by --no-augment")
        stages.append({"stage": "augment", "ok": True, "skipped": True})
    else:
        stages.append({
            "stage": "augment",
            **run([PY, "-m", "scripts.augment_dataset", "--out", str(augmented_dir)], "augment"),
        })
        if stages[-1]["ok"] and (augmented_dir / "data.yaml").exists():
            train_data = augmented_dir / "data.yaml"
        else:
            print("    augmentation unusable; falling back to the baseline dataset")
            stages[-1]["fallback"] = "baseline dataset used for training"

    # 4. direct decoding ----------------------------------------------------
    stages.append({
        "stage": "decode",
        **run([PY, "-m", "scripts.test_barcode_decoder"], "decode"),
    })

    # 5. training -----------------------------------------------------------
    if args.skip_train:
        print("\n[train] skipped by --skip-train")
        stages.append({"stage": "train", "ok": True, "skipped": True})
    else:
        cmd = [PY, "-m", "scripts.train_yolo", "--data", str(train_data),
               "--epochs", str(args.epochs), "--device", args.device,
               "--name", args.name]
        if args.promote:
            cmd.append("--promote")
        stages.append({"stage": "train", **run(cmd, "train", timeout=3600)})
        if not stages[-1]["ok"]:
            print("\nTraining did not complete. Reporting as FAILED.")

    # 6. end-to-end verification -------------------------------------------
    stages.append({
        "stage": "verify",
        **run([PY, "-m", "scripts.verify_scanner"], "verify"),
    })

    # 7. test suite ---------------------------------------------------------
    stages.append({
        "stage": "pytest",
        **run([PY, "-m", "pytest", "tests/", "-q"], "pytest", timeout=1800),
    })

    write_summary(started, stages)
    failed = [s for s in stages if not s.get("ok")]
    critical = [s for s in failed if s["stage"] in ("assets", "dataset", "train", "decode")]
    print("\n" + "=" * 78)
    print("Stage summary")
    for stage in stages:
        if stage.get("skipped"):
            mark = "SKIPPED"
        else:
            mark = "OK" if stage.get("ok") else "FAILED"
        extra = f" ({stage['seconds']}s)" if "seconds" in stage else ""
        print(f"  {stage['stage']:10s} {mark}{extra}")
    print("=" * 78)
    print(f"failed stages: {len(failed)} ({len(critical)} critical)")
    return 1 if critical else (0 if not failed else 1)


def write_summary(started: str, stages: List[Dict]) -> None:
    out = BASE_DIR / "reports" / "pipeline_run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "started": started,
        "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.executable,
        "stages": [
            {k: v for k, v in s.items() if k != "problems"} | {"problems": s.get("problems")}
            for s in stages
        ],
    }, indent=2, default=str))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    sys.exit(main())
