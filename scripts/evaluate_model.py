#!/usr/bin/env python3
"""
Evaluate trained YOLO model on test dataset.
"""

import sys
from pathlib import Path
import cv2
import yaml
from ultralytics import YOLO

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from config.logging_config import setup_logging

logger = setup_logging()

MODEL_PATH = project_root / "models_yolo" / "candidates" / "barcode_detector" / "weights" / "best.pt"
DATASET_DIR = project_root / "dataset"
DATA_YAML = DATASET_DIR / "data.yaml"

def evaluate_model():
    """Evaluate the trained model on test set."""
    if not MODEL_PATH.exists():
        logger.error(f"Model not found: {MODEL_PATH}")
        return False
    
    if not DATA_YAML.exists():
        logger.error(f"data.yaml not found: {DATA_YAML}")
        return False
    
    logger.info(f"Loading model from {MODEL_PATH}")
    model = YOLO(str(MODEL_PATH))
    
    logger.info("Running validation on test set...")
    results = model.val(
        data=str(DATA_YAML),
        split='test',
        imgsz=640,
        batch=16,
        device='cpu',
        verbose=True,
    )
    
    logger.info(f"Test Results:")
    logger.info(f"  mAP50: {results.box.map50:.4f}")
    logger.info(f"  mAP50-95: {results.box.map:.4f}")
    logger.info(f"  Precision: {results.box.mp:.4f}")
    logger.info(f"  Recall: {results.box.mr:.4f}")
    
    # Per-class metrics
    if hasattr(results.box, 'ap50'):
        for i, ap50 in enumerate(results.box.ap50):
            logger.info(f"  Class {i} AP50: {ap50:.4f}")
    
    return True

def test_on_sample_images():
    """Test model on a few sample images from test set."""
    model = YOLO(str(MODEL_PATH))
    
    test_images_dir = DATASET_DIR / "images" / "test"
    if not test_images_dir.exists():
        logger.warning("Test images directory not found")
        return
    
    images = list(test_images_dir.glob("*.png")) + list(test_images_dir.glob("*.jpg"))
    
    logger.info(f"Testing on {len(images)} test images...")
    
    for img_path in images[:5]:  # Test first 5
        logger.info(f"\nTesting: {img_path.name}")
        results = model(str(img_path), conf=0.25, iou=0.45, verbose=False)
        
        for result in results:
            boxes = result.boxes
            if boxes is not None:
                for box in boxes:
                    xyxy = box.xyxy[0].cpu().numpy()
                    conf = float(box.conf[0].cpu().numpy())
                    cls = int(box.cls[0].cpu().numpy())
                    logger.info(f"  Detection: class={cls}, conf={conf:.3f}, bbox={xyxy}")
            else:
                logger.info("  No detections")

def main():
    logger.info("Evaluating trained YOLO model...")
    
    if evaluate_model():
        logger.info("Model evaluation completed!")
        test_on_sample_images()
    else:
        logger.error("Model evaluation failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()