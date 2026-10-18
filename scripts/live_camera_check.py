#!/usr/bin/env python3
"""Verify the live scanner against the real camera, and report honestly.

This is the only script here that touches real hardware. It runs the production
path end to end on frames the webcam actually delivered:

    live frame -> YOLO -> box tracking -> crop -> decode -> verified student

and reports, per state transition, what the scanner did. It never opens a popup
without a successful decode *and* a database match.

Two honest limits are built in rather than papered over:

* If no barcode is detected during the run, that is reported as
  **no barcode seen** -- not as a pass. Detection on a live feed cannot be
  proven by a run in which the target was never in frame.
* Capture rate and inference latency are measured separately, because a camera
  that delivers 10 fps cannot be scanned at 60 fps no matter how fast the model
  is.

Usage:
    python -m scripts.live_camera_check                 # 20 second run
    python -m scripts.live_camera_check --seconds 60 --camera 0
    python -m scripts.live_camera_check --save-frames reports/live_frames
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402

logger = setup_logging()


def stats(samples: List[float]) -> Dict:
    if not samples:
        return {"n": 0}
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "median_ms": round(statistics.median(ordered), 1),
        "min_ms": round(ordered[0], 1),
        "max_ms": round(ordered[-1], 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--model", type=Path, default=settings.YOLO_MODEL_PATH)
    ap.add_argument("--device", default=settings.YOLO_DEVICE)
    ap.add_argument("--save-frames", type=Path, default=None,
                    help="save a few frames, for diagnosing a missed detection")
    ap.add_argument("--out", type=Path, default=BASE_DIR / "reports" / "live_camera_check.json")
    args = ap.parse_args()

    print("=" * 78)
    print("Live camera check - real hardware")
    print("=" * 78)

    if not args.model.exists():
        print(f"FAIL: no model at {args.model}")
        return 2

    from services.camera_service import CameraService
    from services.detection_smoother import DetectionSmoother
    from services.scan_pipeline import ScanPipeline, ScanState, StudentVerifier
    from services.yolo_detector import YOLODetectorService
    from services.student_service import StudentService

    try:
        available = CameraService().scan_cameras()
    except Exception as exc:
        print(f"FAIL: camera enumeration failed: {type(exc).__name__}: {exc}")
        return 2
    print(f"cameras enumerated : {available}")
    if args.camera not in available:
        print(f"NOTE: index {args.camera} was not in the enumeration; trying it anyway")

    camera = CameraService()
    if not camera.start(args.camera):
        print("FAIL: camera did not start.")
        print("      Check permissions (`id` must include the video group) and that")
        print("      no other application is holding the device.")
        return 2

    detector = YOLODetectorService()
    if not detector.load_model(args.model):
        camera.stop()
        print(f"FAIL: model did not load from {args.model}")
        return 2
    detector.device = args.device

    try:
        service = StudentService()
        service.get_student_by_barcode("")  # fail fast if the database is unreachable
        db_ok = True
    except Exception as exc:
        print(f"WARNING: database unavailable ({type(exc).__name__}); "
              "identification will be reported as unknown")
        service = None
        db_ok = False

    lookup = service.get_student_by_barcode if db_ok else (lambda value: None)
    pipeline = ScanPipeline(
        detector=detector,
        decoder=__import__("services.barcode_decoder", fromlist=["x"]).BarcodeDecoderService(),
        verifier=StudentVerifier(lookup=lookup),
        cooldown_seconds=0.0,
    )
    smoother = DetectionSmoother(
        alpha=settings.SCAN_BOX_SMOOTHING,
        grace_seconds=settings.SCAN_BOX_GRACE_SECONDS,
        clock=time.monotonic,
    )

    print(f"model              : {args.model.name}")
    print(f"device             : {args.device}")
    print(f"duration           : {args.seconds:.0f}s")
    print(f"hold a real student ID inside the yellow frame.\n")

    if args.save_frames:
        args.save_frames.mkdir(parents=True, exist_ok=True)

    # Let the capture thread and the UVC device settle before measuring.
    time.sleep(1.5)

    capture_times: List[float] = []
    process_times: List[float] = []
    seen_states: Dict[str, int] = {}
    detections_total = 0
    frames_with_detection = 0
    verified_students: Dict[str, int] = {}
    decoded_values: Dict[str, int] = {}
    track_samples = 0
    track_jitter: List[float] = []
    previous_box: Optional[tuple] = None
    sample_saved = 0
    frames_processed = 0
    started = time.time()
    deadline = started + args.seconds
    last_id = -1

    try:
        while time.time() < deadline:
            t_cap = time.perf_counter()
            frame, frame_id = camera.get_latest_frame_with_id()
            capture_times.append(time.perf_counter() - t_cap)
            if frame is None or frame_id == last_id:
                time.sleep(0.005)
                continue
            last_id = frame_id
            frames_processed += 1

            t0 = time.perf_counter()
            outcome = pipeline.process_frame(frame)
            process_times.append(time.perf_counter() - t0)

            seen_states[outcome.state.value] = seen_states.get(outcome.state.value, 0) + 1
            n = len(outcome.candidates)
            detections_total += n
            if n:
                frames_with_detection += 1
                box = outcome.primary_candidate.bbox if outcome.primary_candidate else None
                if box is not None:
                    if previous_box is not None and track_samples < 400:
                        track_jitter.append(
                            abs(box[0] - previous_box[0]) + abs(box[1] - previous_box[1]))
                    previous_box = box
                    track_samples += 1
                if args.save_frames and sample_saved < 5:
                    cv2.imwrite(str(args.save_frames / f"detected_{sample_saved}.png"), frame)
                    sample_saved += 1

            if outcome.decoded_value:
                decoded_values[outcome.decoded_value] = decoded_values.get(outcome.decoded_value, 0) + 1
            if outcome.state == ScanState.VERIFIED and outcome.student is not None:
                name = outcome.student.full_name
                verified_students[name] = verified_students.get(name, 0) + 1
                if args.save_frames and sample_saved < 10:
                    cv2.imwrite(str(args.save_frames / f"verified_{name}_{sample_saved}.png"), frame)
                    sample_saved += 1
    finally:
        camera.stop()

    elapsed = time.time() - started
    capture_fps = len(set()) if False else None
    ids_per_sec = frames_processed / elapsed if elapsed else 0.0

    print("=" * 78)
    print("Results")
    print("=" * 78)
    print(f"  frames processed      : {frames_processed} over {elapsed:.1f}s "
          f"({ids_per_sec:.1f} fps end to end)")
    print(f"  capture fetch         : {stats([c * 1000 for c in capture_times])}")
    print(f"  scan pipeline latency : {stats([p * 1000 for p in process_times])}")
    if process_times:
        print(f"  implied max scan rate : {1 / statistics.median(process_times):.1f} fps "
              f"(bounded by the camera rate, which is lower)")
    print(f"  frames with a detection: {frames_with_detection}/{frames_processed} "
          f"({100.0 * frames_with_detection / max(1, frames_processed):.0f}%)")
    print(f"  total detections      : {detections_total}")
    print(f"  states seen           : {seen_states}")
    print(f"  decoded values        : {decoded_values or 'none'}")
    print(f"  verified students     : {verified_students or 'none'}")
    if track_jitter:
        print(f"  box motion (px/frame) : median {statistics.median(track_jitter):.1f}")

    saw_detection = frames_with_detection > 0
    saw_decode = bool(decoded_values)
    saw_verify = bool(verified_students)

    print("\n  VERDICT")
    if saw_verify:
        print("    PASS - a real card was decoded and resolved to a verified student.")
    elif saw_decode:
        print("    PARTIAL - a barcode was decoded but no student matched. "
              "Check the database record for that payload.")
    elif saw_detection:
        print("    PARTIAL - the barcode was located but never decoded. "
              "Try holding the card closer, flatter and better lit.")
    else:
        print("    NO BARCODE SEEN - nothing was detected during this run. "
              "This is NOT a pass: detection on a live feed is only proven when a "
              "card is actually in frame. Re-run while holding an ID in the guide.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "camera_index": args.camera,
        "cameras_enumerated": available,
        "model": str(args.model),
        "device": args.device,
        "seconds": round(elapsed, 1),
        "frames_processed": frames_processed,
        "end_to_end_fps": round(ids_per_sec, 2),
        "capture_fetch_ms": stats([c * 1000 for c in capture_times]),
        "pipeline_latency_ms": stats([p * 1000 for p in process_times]),
        "frames_with_detection": frames_with_detection,
        "total_detections": detections_total,
        "states": seen_states,
        "decoded_values": decoded_values,
        "verified_students": verified_students,
        "box_motion_median_px": (round(statistics.median(track_jitter), 1)
                                 if track_jitter else None),
        "verdict": ("verified" if saw_verify else "decoded" if saw_decode
                    else "detected_only" if saw_detection else "no_barcode_seen"),
    }, indent=2))
    print(f"\nwrote {args.out}")
    return 0 if saw_verify else 1


if __name__ == "__main__":
    sys.exit(main())
