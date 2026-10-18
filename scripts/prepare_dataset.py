#!/usr/bin/env python3
"""
YOLO Dataset Preparation for Barcode Detection.
Creates dataset structure, annotations, and generates synthetic training data.
"""

import sys
import json
import shutil
import random
from pathlib import Path
from PIL import Image, ImageDraw
import cv2
import numpy as np
from pyzbar import pyzbar
from barcode import Code128
from barcode.writer import ImageWriter
import yaml

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from config.logging_config import setup_logging

logger = setup_logging()

ASSETS_DIR = project_root / "assets"
DATASET_DIR = project_root / "dataset"
IMAGES_DIR = DATASET_DIR / "images"
LABELS_DIR = DATASET_DIR / "labels"
MANIFEST_PATH = ASSETS_DIR / "manifests" / "asset_manifest.json"

# YOLO class mapping
CLASS_NAMES = ["barcode"]
CLASS_ID = 0  # barcode

# Dataset split ratios
TRAIN_RATIO = 0.7
VAL_RATIO = 0.2
TEST_RATIO = 0.1

def load_manifest():
    """Load the asset manifest."""
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH, 'r') as f:
            return json.load(f)
    return None

def detect_barcode_bbox(image_path: Path) -> list:
    """Detect barcode bounding boxes in an image using pyzbar."""
    bboxes = []
    try:
        cv_img = cv2.imread(str(image_path))
        if cv_img is None:
            return bboxes
        
        # Try multiple preprocessing approaches
        decoded = pyzbar.decode(cv_img)
        if not decoded:
            gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
            decoded = pyzbar.decode(gray)
        if not decoded:
            # Try with thresholding
            _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            decoded = pyzbar.decode(thresh)
        
        h, w = cv_img.shape[:2]
        for barcode in decoded:
            # pyzbar returns rect as (left, top, width, height)
            left, top, bw, bh = barcode.rect
            # Convert to YOLO format: x_center, y_center, width, height (normalized)
            x_center = (left + bw / 2) / w
            y_center = (top + bh / 2) / h
            norm_w = bw / w
            norm_h = bh / h
            
            # Validate bounds
            if 0 <= x_center <= 1 and 0 <= y_center <= 1 and 0 < norm_w <= 1 and 0 < norm_h <= 1:
                bboxes.append({
                    "class_id": CLASS_ID,
                    "x_center": x_center,
                    "y_center": y_center,
                    "width": norm_w,
                    "height": norm_h,
                    "barcode_data": barcode.data.decode('utf-8') if isinstance(barcode.data, bytes) else str(barcode.data),
                    "barcode_type": barcode.type
                })
    except Exception as e:
        logger.warning(f"Error detecting barcode in {image_path}: {e}")
    
    return bboxes

def create_yolo_annotation(bboxes: list, output_path: Path):
    """Create YOLO format annotation file."""
    lines = []
    for bbox in bboxes:
        line = f"{bbox['class_id']} {bbox['x_center']:.6f} {bbox['y_center']:.6f} {bbox['width']:.6f} {bbox['height']:.6f}"
        lines.append(line)
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        f.write('\n'.join(lines))
    
    return len(lines) > 0

def copy_image_to_dataset(src_path: Path, split: str, filename: str) -> Path:
    """Copy image to dataset split directory."""
    dst_dir = IMAGES_DIR / split
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst_path = dst_dir / filename
    
    # Handle case where source and destination are the same
    try:
        if src_path.resolve() != dst_path.resolve():
            shutil.copy2(src_path, dst_path)
    except shutil.SameFileError:
        pass  # File already in place
    return dst_path

def generate_synthetic_barcode(output_path: Path, data: str, add_noise: bool = True) -> bool:
    """Generate a synthetic Code 128 barcode image."""
    try:
        writer = ImageWriter()
        writer.set_options({
            "module_width": 2,
            "module_height": 20,
            "quiet_zone": 6,
            "font_size": 10,
            "text_distance": 5,
            "background": "white",
            "foreground": "black",
            "write_text": True,
        })
        
        code128 = Code128(data, writer=writer)
        # Save without extension (ImageWriter adds .png)
        code128.save(str(output_path.with_suffix('')))
        
        if add_noise:
            # Add some realistic noise/augmentation
            img = cv2.imread(str(output_path))
            if img is not None:
                # Random slight rotation
                angle = random.uniform(-3, 3)
                h, w = img.shape[:2]
                M = cv2.getRotationMatrix2D((w/2, h/2), angle, 1.0)
                img = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
                
                # Random brightness/contrast
                alpha = random.uniform(0.8, 1.2)
                beta = random.uniform(-20, 20)
                img = cv2.convertScaleAbs(img, alpha=alpha, beta=beta)
                
                # Random blur
                if random.random() < 0.3:
                    ksize = random.choice([1, 3])
                    img = cv2.GaussianBlur(img, (ksize, ksize), 0)
                
                # Random perspective (slight)
                if random.random() < 0.2:
                    h, w = img.shape[:2]
                    src = np.float32([[0,0], [w,0], [w,h], [0,h]])
                    dst = src + np.random.uniform(-5, 5, (4, 2))
                    M = cv2.getPerspectiveTransform(src, dst.astype(np.float32))
                    img = cv2.warpPerspective(img, M, (w, h), borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
                
                cv2.imwrite(str(output_path), img)
        
        return True
    except Exception as e:
        logger.error(f"Failed to generate barcode {data}: {e}")
        return False

def prepare_dataset():
    """Main dataset preparation function."""
    logger.info("Starting dataset preparation...")
    
    # Load manifest
    manifest = load_manifest()
    if not manifest:
        logger.error("No asset manifest found. Run inspect_assets.py first.")
        return False
    
    # Clear existing dataset
    if DATASET_DIR.exists():
        logger.info("Clearing existing dataset...")
        shutil.rmtree(DATASET_DIR)
    
    # Create directory structure
    for split in ['train', 'val', 'test']:
        (IMAGES_DIR / split).mkdir(parents=True, exist_ok=True)
        (LABELS_DIR / split).mkdir(parents=True, exist_ok=True)
    
    # Collect barcode images from manifest
    barcode_assets = []
    for asset in manifest.get("assets", []):
        if asset.get("category") == "barcode" and asset.get("suitable_for_yolo"):
            barcode_assets.append(asset)
        # Also include ID cards that have barcodes
        elif asset.get("category") == "student_photo" and asset.get("inspection", {}).get("barcode_detected"):
            barcode_assets.append(asset)
    
    logger.info(f"Found {len(barcode_assets)} barcode-containing assets")
    
    # Process each barcode asset
    processed_samples = []
    
    for asset in barcode_assets:
        src_path = Path(asset["absolute_path"])
        bboxes = detect_barcode_bbox(src_path)
        
        if not bboxes:
            logger.warning(f"No barcode detected in {asset['original_path']}, skipping")
            continue
        
        # Use the first detected barcode (usually the main one)
        bbox = bboxes[0]
        logger.info(f"Detected barcode in {asset['original_path']}: {bbox['barcode_data']} ({bbox['barcode_type']}) at ({bbox['x_center']:.3f}, {bbox['y_center']:.3f}, {bbox['width']:.3f}, {bbox['height']:.3f})")
        
        processed_samples.append({
            "source_path": src_path,
            "filename": src_path.name,
            "bbox": bbox,
            "asset_info": asset
        })
    
    # Generate synthetic Code 128 barcodes for training
    logger.info("Generating synthetic Code 128 barcodes...")
    synthetic_count = 50  # Generate 50 synthetic barcodes
    student_refs = [
        "65412", "000001", "65902", "65413", "65414", "65415", "65416", "65417", "65418", "65419",
        "65420", "65421", "65422", "65423", "65424", "65425", "65426", "65427", "65428", "65429",
        "65430", "65431", "65432", "65433", "65434", "65435", "65436", "65437", "65438", "65439",
        "65440", "65441", "65442", "65443", "65444", "65445", "65446", "65447", "65448", "65449",
        "65450", "65451", "65452", "65453", "65454", "65455", "65456", "65457", "65458", "65459",
    ]
    
    # Create temp directory for synthetic barcodes
    temp_synthetic_dir = DATASET_DIR / "temp_synthetic"
    temp_synthetic_dir.mkdir(parents=True, exist_ok=True)
    
    for i, ref in enumerate(student_refs[:synthetic_count]):
        barcode_data = f"WIT:STUDENT:{ref}"
        filename = f"synthetic_barcode_{ref}.png"
        output_path = temp_synthetic_dir / filename
        
        if generate_synthetic_barcode(output_path, barcode_data):
            # For synthetic barcodes, we know the barcode occupies most of the image
            # Use a standard bbox covering the barcode area
            bbox = {
                "class_id": CLASS_ID,
                "x_center": 0.5,
                "y_center": 0.5,
                "width": 0.85,  # Code 128 typically spans ~85% of width
                "height": 0.6,  # and ~60% of height
                "barcode_data": barcode_data,
                "barcode_type": "CODE128"
            }
            
            # We'll assign split later, so don't create label yet
            processed_samples.append({
                "source_path": output_path,
                "filename": filename,
                "bbox": bbox,
                "asset_info": {"category": "synthetic", "original_path": f"synthetic/{filename}"},
                "is_synthetic": True
            })
    
    logger.info(f"Generated {synthetic_count} synthetic barcodes")
    
    # Split samples into train/val/test
    random.shuffle(processed_samples)
    total = len(processed_samples)
    train_end = int(total * TRAIN_RATIO)
    val_end = train_end + int(total * VAL_RATIO)
    
    splits = {
        'train': processed_samples[:train_end],
        'val': processed_samples[train_end:val_end],
        'test': processed_samples[val_end:]
    }
    
    # Copy images to appropriate splits and create annotations
    for split_name, samples in splits.items():
        logger.info(f"Processing {split_name} split: {len(samples)} samples")
        for sample in samples:
            src = sample["source_path"]
            filename = sample["filename"]
            bbox = sample["bbox"]
            
            # Copy image (skip if already in correct location)
            dst_img = copy_image_to_dataset(src, split_name, filename)
            
            # Create annotation
            label_path = LABELS_DIR / split_name / f"{Path(filename).stem}.txt"
            create_yolo_annotation([bbox], label_path)
    
    # Create data.yaml
    data_yaml = {
        'path': str(DATASET_DIR.absolute()),
        'train': 'images/train',
        'val': 'images/val',
        'test': 'images/test',
        'nc': len(CLASS_NAMES),
        'names': CLASS_NAMES
    }
    
    with open(DATASET_DIR / "data.yaml", 'w') as f:
        yaml.dump(data_yaml, f, default_flow_style=False)
    
    logger.info(f"Dataset prepared successfully!")
    logger.info(f"Train: {len(splits['train'])} samples")
    logger.info(f"Val: {len(splits['val'])} samples")
    logger.info(f"Test: {len(splits['test'])} samples")
    logger.info(f"data.yaml created at {DATASET_DIR / 'data.yaml'}")
    
    return True

def validate_prepared_dataset():
    """Validate the prepared dataset."""
    logger.info("Validating prepared dataset...")
    
    data_yaml = DATASET_DIR / "data.yaml"
    if not data_yaml.exists():
        logger.error("data.yaml not found")
        return False
    
    with open(data_yaml, 'r') as f:
        data_config = yaml.safe_load(f)
    
    class_names = data_config.get('names', [])
    logger.info(f"Classes: {class_names}")
    
    splits = ['train', 'val', 'test']
    total_images = 0
    total_valid = 0
    total_invalid = 0
    
    for split in splits:
        img_dir = IMAGES_DIR / split
        lbl_dir = LABELS_DIR / split
        
        if not img_dir.exists():
            logger.warning(f"Images directory not found: {img_dir}")
            continue
        
        images = list(img_dir.glob("*.png")) + list(img_dir.glob("*.jpg")) + list(img_dir.glob("*.jpeg"))
        logger.info(f"{split}: {len(images)} images")
        
        for img_path in images:
            total_images += 1
            lbl_path = lbl_dir / f"{img_path.stem}.txt"
            
            # Check image
            img = cv2.imread(str(img_path))
            if img is None:
                logger.warning(f"Cannot read image: {img_path}")
                total_invalid += 1
                continue
            
            h, w = img.shape[:2]
            
            # Check label
            if not lbl_path.exists():
                logger.warning(f"Missing label: {lbl_path}")
                total_invalid += 1
                continue
            
            with open(lbl_path, 'r') as f:
                lines = f.readlines()
            
            if not lines:
                logger.warning(f"Empty label: {lbl_path}")
                total_invalid += 1
                continue
            
            valid = True
            for line in lines:
                parts = line.strip().split()
                if len(parts) != 5:
                    logger.warning(f"Invalid annotation format: {lbl_path} - {line}")
                    valid = False
                    break
                
                try:
                    class_id = int(parts[0])
                    x_center, y_center, width, height = map(float, parts[1:])
                    
                    if class_id >= len(class_names):
                        logger.warning(f"Invalid class ID {class_id} in {lbl_path}")
                        valid = False
                        break
                    
                    if not (0 <= x_center <= 1 and 0 <= y_center <= 1 and 0 <= width <= 1 and 0 <= height <= 1):
                        logger.warning(f"Invalid bbox coordinates in {lbl_path}: {parts[1:]}")
                        valid = False
                        break
                except ValueError:
                    logger.warning(f"Non-numeric values in {lbl_path}: {line}")
                    valid = False
                    break
            
            if valid:
                total_valid += 1
            else:
                total_invalid += 1
    
    logger.info(f"Total images: {total_images}")
    logger.info(f"Valid: {total_valid}")
    logger.info(f"Invalid: {total_invalid}")
    
    return total_invalid == 0

def main():
    if prepare_dataset():
        logger.info("Dataset preparation completed successfully!")
        if validate_prepared_dataset():
            logger.info("Dataset validation passed!")
        else:
            logger.error("Dataset validation failed!")
            sys.exit(1)
    else:
        logger.error("Dataset preparation failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()