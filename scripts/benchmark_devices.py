#!/usr/bin/env python3
"""Benchmark detection and the full pipeline on CPU and CUDA.

The production device is only worth changing if CUDA is actually *faster and
correct* here, so this measures both on the same model and the same frames, and
checks that the two devices agree on what they detect. A GPU that is faster but
disagrees about detections is not a speedup, it is a different answer.

Stages are timed separately so a slow stage is attributable:

    load      model construction + weight loading
    detect    YOLO inference only
    decode    barcode decoding only
    pipeline  detect + gate + decode + database-free verification
    overlay   preview compositing (what the UI pays every frame)
    capture   camera read, when a camera is present

Usage:
    python -m scripts.benchmark_devices
    python -m scripts.benchmark_devices --device cpu 0 --frames 12
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
        "median_ms": round(statistics.median(ordered) * 1000, 1),
        "min_ms": round(ordered[0] * 1000, 1),
        "max_ms": round(ordered[-1] * 1000, 1),
        "mean_ms": round(statistics.fmean(ordered) * 1000, 1),
    }


def run_device(device: str, frames: int, warmup: int) -> Dict:
    from services.barcode_decoder import BarcodeDecoderService
    from services.scan_pipeline import ScanPipeline, StudentVerifier
    from services.scanner_overlay import OverlayRenderer
    from services.yolo_detector import YOLODetectorService

    result: Dict = {"device": device}

    started = time.perf_counter()
    detector = YOLODetectorService()
    loaded = detector.load_model(settings.YOLO_MODEL_PATH)
    result["load_s"] = round(time.perf_counter() - started, 3)
    if not loaded:
        result["error"] = "model failed to load"
        return result

    detector.device = device
    frames_in = [
        detector.detect  # keep a reference so the loop below reads clearly
    ]
    frame = cv2.imread(str(settings.ASSETS_DIR / "images" / "Id_1.png"))
    if frame is None:
        result["error"] = "fixture image missing"
        return result
    frame = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)

    # Warm up: the first inference includes lazy CUDA/CPU kernel setup and would
    # otherwise dominate the numbers.
    for _ in range(max(1, warmup)):
        detector.detect(frame)

    detect_t: List[float] = []
    for _ in range(frames):
        t = time.perf_counter()
        detector.detect(frame)
        detect_t.append(time.perf_counter() - t)
    result["detect"] = stats(detect_t)

    # Decode only, on a real crop taken from the detected box.
    decoder = BarcodeDecoderService()
    dets = detector.detect(frame)
    crop = frame
    if dets:
        x, y, w, h = max(dets, key=lambda d: d.confidence).bbox
        m = settings.SCAN_CROP_MARGIN_RATIO
        crop = frame[max(0, int(y - h * m)) : int(y + h * (1 + m)),
                     max(0, int(x - w * m)) : int(x + w * (1 + m))]
    decoder.decode_best(crop)
    decode_t: List[float] = []
    for _ in range(frames):
        t = time.perf_counter()
        decoder.decode_best(crop)
        decode_t.append(time.perf_counter() - t)
    result["decode"] = stats(decode_t)

    # Full pipeline, with the database lookup stubbed so this measures vision
    # and decoding rather than MySQL round trips.
    pipeline = ScanPipeline(
        detector=detector,
        decoder=BarcodeDecoderService(),
        verifier=StudentVerifier(lookup=lambda value: None),
        cooldown_seconds=0.0,
    )
    pipeline.process_frame(frame)
    pipe_t: List[float] = []
    for _ in range(frames):
        t = time.perf_counter()
        outcome = pipeline.process_frame(frame)
        pipe_t.append(time.perf_counter() - t)
    result["pipeline"] = stats(pipe_t)
    result["pipeline_fps"] = round(1.0 / statistics.median(pipe_t), 2) if pipe_t else 0.0
    result["pipeline_state"] = outcome.state.value
    result["pipeline_detections"] = len(outcome.candidates)

    # Overlay: what the UI renders every frame regardless of detection.
    overlay = OverlayRenderer()
    overlay.render(frame, outcome, target_size=(960, 540))
    ov_t: List[float] = []
    for _ in range(frames):
        t = time.perf_counter()
        overlay.render(frame, outcome, target_size=(960, 540))
        ov_t.append(time.perf_counter() - t)
    result["overlay"] = stats(ov_t)

    result["detection_confidences"] = sorted(
        (round(d.confidence, 3) for d in dets), reverse=True
    )[:5]
    return result


def probe_cuda() -> Dict:
    try:
        import torch
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    info: Dict = {
        "available": bool(torch.cuda.is_available()),
        "torch": torch.__version__,
        "torch_threads": torch.get_num_threads(),
    }
    if info["available"]:
        info["device_count"] = torch.cuda.device_count()
        info["device_name"] = torch.cuda.get_device_name(0)
        props = torch.cuda.get_device_properties(0)
        info["capability"] = f"{props.major}.{props.minor}"
        info["total_memory_mb"] = round(props.total_memory / 1024 / 1024)
        info["cuda_runtime"] = getattr(torch.version, "cuda", None)
    return info


def probe_camera(frames: int = 5, warmup_seconds: float = 1.5) -> Dict:
    """Capture from the real camera.

    The warm-up matters and was previously missing: the capture thread has to
    start and the UVC device has to negotiate its format, which on this laptop
    takes ~670 ms. Reading immediately after ``start()`` therefore reports
    "no frames" on hardware that is in fact delivering 10 fps, which is exactly
    the false negative this function once produced.
    """
    from services.camera_service import CameraService

    info: Dict = {"warmup_seconds": warmup_seconds}
    service = CameraService()
    try:
        info["enumerated"] = list(service.scan_cameras())
    except Exception as exc:
        info["enumerated"] = f"error: {exc}"
        return info

    index = 0
    try:
        if not service.start(index):
            info["opened"] = False
            return info
        info["opened"] = True
        if service.cap is not None:
            info["requested"] = [service.width, service.height, service.fps]
            info["negotiated"] = [
                int(service.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(service.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                int(service.cap.get(cv2.CAP_PROP_FPS)),
            ]

        time.sleep(warmup_seconds)

        # Measure the capture rate from advancing frame ids, and the consumer
        # latency of one fetch. These are different numbers and conflating them
        # is what made the earlier reading look like 0.0 ms.
        ids, fetches = [], []
        deadline = time.time() + 2.0
        while time.time() < deadline:
            t = time.perf_counter()
            frame, frame_id = service.get_latest_frame_with_id()
            fetches.append(time.perf_counter() - t)
            ids.append(frame_id)
            time.sleep(0.005)

        unique = len(set(ids))
        info["capture_fps"] = round(unique / 2.0, 2)
        info["fetch_ms"] = stats(fetches)
        frame, _ = service.get_latest_frame_with_id()
        info["frame_shape"] = None if frame is None else list(frame.shape)
        info["frame_mean"] = None if frame is None else round(float(frame.mean()), 1)
        info["frames_delivered"] = unique
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            service.stop()
        except Exception:
            pass
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", nargs="+", default=None,
                    help="devices to benchmark (default: whatever torch supports)")
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--no-camera", action="store_true")
    ap.add_argument("--out", type=Path, default=BASE_DIR / "reports" / "device_benchmark.json")
    args = ap.parse_args()

    cuda = probe_cuda()
    print("=" * 78)
    print("Device probe")
    print("=" * 78)
    for key, value in cuda.items():
        print(f"  {key:18s} {value}")
    print(f"  {'configured device':18s} {settings.YOLO_DEVICE}")

    devices = args.device
    if not devices:
        devices = ["cpu"] + (["0"] if cuda.get("available") else [])

    results = []
    for device in devices:
        print(f"\n{'-' * 78}\ndevice = {device!r}")
        r = run_device(device, args.frames, args.warmup)
        results.append(r)
        if r.get("error"):
            print(f"  ERROR: {r['error']}")
            continue
        print(f"  model load        {r['load_s']:.3f} s")
        for stage in ("detect", "decode", "pipeline", "overlay"):
            s = r[stage]
            print(f"  {stage:17s} median {s['median_ms']:8.1f} ms  (min {s['min_ms']:.1f}, max {s['max_ms']:.1f}, n={s['n']})")
        print(f"  pipeline fps      {r['pipeline_fps']}")
        print(f"  pipeline state    {r['pipeline_state']}  detections={r['pipeline_detections']}")
        print(f"  confidences       {r['detection_confidences']}")

    if len(results) >= 2:
        a, b = results[0], results[1]
        if "detect" in a and "detect" in b:
            ratio = a["detect"]["median_ms"] / max(0.001, b["detect"]["median_ms"])
            print(f"\nspeedup ({a['device']} -> {b['device']}): {ratio:.2f}x on detection")
            print(f"same detections: {a['detection_confidences'] == b['detection_confidences']}")

    camera = None
    if not args.no_camera:
        print(f"\n{'-' * 78}\ncamera probe")
        camera = probe_camera()
        for key, value in camera.items():
            print(f"  {key:18s} {value}")

    payload = {
        "cuda": cuda,
        "configured_device": settings.YOLO_MODEL_PATH.name and settings.YOLO_DEVICE,
        "model": str(settings.YOLO_MODEL_PATH),
        "frames": args.frames,
        "warmup": args.warmup,
        "results": results,
        "camera": camera,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
