#!/usr/bin/env python3
"""
Asset Inspection and Manifest Generation for WIT Barcode Student Information System.
Inspects all assets, validates barcode images, detects duplicates, and generates a manifest.
"""

import sys
import hashlib
import json
from pathlib import Path
from PIL import Image
import cv2
import numpy as np
from pyzbar import pyzbar
import imagehash
from config.logging_config import setup_logging

logger = setup_logging()

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
MANIFEST_PATH = Path(__file__).resolve().parent.parent / "assets" / "manifests" / "asset_manifest.json"
DUPLICATE_LOG_PATH = Path(__file__).resolve().parent.parent / "assets" / "manifests" / "duplicate_report.json"

SUPPORTED_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.tif'}
SUPPORTED_FONT_EXTS = {'.ttf', '.otf', '.woff', '.woff2'}

def compute_file_hash(filepath: Path) -> str:
    """Compute SHA256 hash of file."""
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hasher.update(chunk)
    return hasher.hexdigest()

def compute_perceptual_hash(filepath: Path) -> str:
    """Compute perceptual hash for image similarity detection."""
    try:
        img = Image.open(filepath)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        return str(imagehash.phash(img))
    except Exception as e:
        logger.warning(f"Could not compute perceptual hash for {filepath}: {e}")
        return ""

def inspect_image(filepath: Path) -> dict:
    """Inspect image file and return metadata."""
    result = {
        "readable": False,
        "dimensions": None,
        "mode": None,
        "format": None,
        "has_transparency": False,
        "file_size": filepath.stat().st_size,
        "corrupt": False,
        "barcode_detected": False,
        "barcode_data": None,
        "barcode_type": None,
        "barcode_quality": None,
    }
    
    try:
        # PIL inspection
        with Image.open(filepath) as img:
            result["dimensions"] = img.size
            result["mode"] = img.mode
            result["format"] = img.format
            result["has_transparency"] = img.mode in ('RGBA', 'LA') or 'transparency' in img.info
            result["readable"] = True
        
        # OpenCV + pyzbar barcode detection
        cv_img = cv2.imread(str(filepath))
        if cv_img is not None:
            # Try decoding with pyzbar
            decoded = pyzbar.decode(cv_img)
            if decoded:
                result["barcode_detected"] = True
                # Get the first barcode found
                barcode = decoded[0]
                try:
                    result["barcode_data"] = barcode.data.decode('utf-8')
                except UnicodeDecodeError:
                    result["barcode_data"] = str(barcode.data)
                result["barcode_type"] = barcode.type
                result["barcode_quality"] = getattr(barcode, 'quality', None)
            else:
                # Try with grayscale
                gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
                decoded = pyzbar.decode(gray)
                if decoded:
                    result["barcode_detected"] = True
                    barcode = decoded[0]
                    try:
                        result["barcode_data"] = barcode.data.decode('utf-8')
                    except UnicodeDecodeError:
                        result["barcode_data"] = str(barcode.data)
                    result["barcode_type"] = barcode.type
                    result["barcode_quality"] = getattr(barcode, 'quality', None)

            if not result["barcode_detected"]:
                # Last resort, and the same one the application uses: the ordered
                # decode, which interpolates undersampled crops. Without this an
                # image like Id_3.png (a 204 px-wide symbol) would be recorded as
                # carrying no barcode at all, when it carries one the scanner
                # reads every time. The variant that read it is recorded, so the
                # manifest cannot imply a bare decode succeeded.
                from services.barcode_decoder import BarcodeDecoderService

                best = BarcodeDecoderService().decode_best(cv_img)
                if best.ok:
                    result["barcode_detected"] = True
                    result["barcode_data"] = best.data
                    result["barcode_type"] = best.type
                    result["barcode_quality"] = best.barcodes[0].quality
                    result["barcode_decode_attempt"] = best.attempt
                    result["barcode_decode_attempts"] = best.attempts_tried
    
    except Exception as e:
        logger.warning(f"Error inspecting {filepath}: {e}")
        result["corrupt"] = True
        result["error"] = str(e)
    
    return result

def classify_asset(filepath: Path, inspection: dict) -> str:
    """Classify asset based on path and content inspection."""
    rel_path = filepath.relative_to(ASSETS_DIR)
    path_str = str(rel_path).lower()
    
    # Path-based classification
    if 'background' in path_str:
        return 'background'
    elif 'barcode' in path_str:
        return 'barcode'
    elif 'logo' in path_str:
        return 'logo'
    elif 'icon' in path_str:
        return 'icon'
    elif 'font' in path_str or filepath.suffix.lower() in SUPPORTED_FONT_EXTS:
        return 'font'
    elif 'student' in path_str or 'id_' in path_str:
        return 'student_photo'
    elif 'image' in path_str:
        # Content-based classification for images folder
        if inspection.get("barcode_detected"):
            return 'barcode'
        elif inspection.get("dimensions"):
            w, h = inspection["dimensions"]
            # ID cards are typically portrait orientation
            if h > w * 1.5:
                return 'student_photo'
            # Barcodes are typically landscape
            elif w > h * 2:
                return 'barcode'
        return 'illustration'
    
    return 'unknown'

def find_duplicates(assets: list) -> dict:
    """Find exact and near-duplicate assets."""
    exact_duplicates = {}
    perceptual_groups = {}
    
    # Group by exact hash
    hash_map = {}
    for asset in assets:
        if asset.get("file_hash"):
            if asset["file_hash"] not in hash_map:
                hash_map[asset["file_hash"]] = []
            hash_map[asset["file_hash"]].append(asset["original_path"])
    
    for hash_val, paths in hash_map.items():
        if len(paths) > 1:
            exact_duplicates[hash_val] = paths
    
    # Group by perceptual hash
    phash_map = {}
    for asset in assets:
        if asset.get("perceptual_hash"):
            if asset["perceptual_hash"] not in phash_map:
                phash_map[asset["perceptual_hash"]] = []
            phash_map[asset["perceptual_hash"]].append(asset["original_path"])
    
    for phash, paths in phash_map.items():
        if len(paths) > 1:
            perceptual_groups[phash] = paths
    
    return {
        "exact_duplicates": exact_duplicates,
        "near_duplicates": perceptual_groups
    }

def inspect_all_assets():
    """Main inspection function."""
    logger.info(f"Starting asset inspection in {ASSETS_DIR}")
    
    if not ASSETS_DIR.exists():
        logger.error(f"Assets directory not found: {ASSETS_DIR}")
        return
    
    assets = []
    all_files = list(ASSETS_DIR.rglob("*"))
    files = [f for f in all_files if f.is_file()]
    
    logger.info(f"Found {len(files)} files to inspect")
    
    for filepath in files:
        rel_path = filepath.relative_to(ASSETS_DIR)
        ext = filepath.suffix.lower()
        
        asset_info = {
            "original_path": str(rel_path),
            "absolute_path": str(filepath),
            "filename": filepath.name,
            "extension": ext,
            "file_size": filepath.stat().st_size,
            "file_hash": compute_file_hash(filepath),
            "perceptual_hash": "",
            "category": "unknown",
            "inspection": {},
            "suitable_for_yolo": False,
            "yolo_notes": "",
            "recommended_action": "review",
        }
        
        if ext in SUPPORTED_IMAGE_EXTS:
            asset_info["perceptual_hash"] = compute_perceptual_hash(filepath)
            asset_info["inspection"] = inspect_image(filepath)
            asset_info["category"] = classify_asset(filepath, asset_info["inspection"])
            
            # Determine YOLO suitability
            if asset_info["category"] == "barcode":
                if asset_info["inspection"].get("barcode_detected"):
                    asset_info["suitable_for_yolo"] = True
                    asset_info["yolo_notes"] = "Valid barcode detected and decoded"
                    asset_info["recommended_action"] = "include_in_training"
                else:
                    asset_info["suitable_for_yolo"] = False
                    asset_info["yolo_notes"] = "No barcode detected in image"
                    asset_info["recommended_action"] = "manual_review_required"
            elif asset_info["category"] in ['student_photo', 'background', 'logo', 'icon']:
                asset_info["suitable_for_yolo"] = False
                asset_info["yolo_notes"] = f"Not a barcode image (category: {asset_info['category']})"
                asset_info["recommended_action"] = "exclude_from_training"
        
        elif ext in SUPPORTED_FONT_EXTS:
            asset_info["category"] = "font"
            asset_info["recommended_action"] = "preserve_for_ui"
        
        assets.append(asset_info)
        logger.info(f"Inspected: {rel_path} -> {asset_info['category']}")
    
    # Find duplicates
    duplicates = find_duplicates(assets)
    
    # Create manifest
    manifest = {
        "generated_at": str(Path(__file__).resolve().parent.parent),
        "total_assets": len(assets),
        "assets": assets,
        "duplicates": duplicates,
        "summary": {
            "by_category": {},
            "yolo_suitable": 0,
            "yolo_unsuitable": 0,
            "exact_duplicate_groups": len(duplicates["exact_duplicates"]),
            "near_duplicate_groups": len(duplicates["near_duplicates"]),
        }
    }
    
    for asset in assets:
        cat = asset["category"]
        manifest["summary"]["by_category"][cat] = manifest["summary"]["by_category"].get(cat, 0) + 1
        if asset["suitable_for_yolo"]:
            manifest["summary"]["yolo_suitable"] += 1
        else:
            manifest["summary"]["yolo_unsuitable"] += 1
    
    # Ensure manifest directory exists
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    # Save manifest
    with open(MANIFEST_PATH, 'w') as f:
        json.dump(manifest, f, indent=2)
    
    # Save duplicate report
    with open(DUPLICATE_LOG_PATH, 'w') as f:
        json.dump(duplicates, f, indent=2)
    
    logger.info(f"Manifest saved to {MANIFEST_PATH}")
    logger.info(f"Duplicate report saved to {DUPLICATE_LOG_PATH}")
    logger.info(f"Summary: {manifest['summary']}")
    
    return manifest

if __name__ == "__main__":
    manifest = inspect_all_assets()
    print(json.dumps(manifest, indent=2))