import cv2
import numpy as np
from pathlib import Path
from typing import Optional, List, Tuple, Dict
import logging
from dataclasses import dataclass
from ultralytics import YOLO
from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    bbox: Tuple[int, int, int, int]
    confidence: float
    class_id: int
    class_name: str


class YOLODetectorService:
    def __init__(self):
        self.model: Optional[YOLO] = None
        self.model_path: Optional[Path] = None
        self.class_names: List[str] = []
        self.confidence_threshold = settings.YOLO_CONFIDENCE_THRESHOLD
        self.iou_threshold = settings.YOLO_IOU_THRESHOLD
        self.device = settings.YOLO_DEVICE
        self.is_loaded = False

    def load_model(self, model_path: Path) -> bool:
        try:
            self.model = YOLO(str(model_path))
            self.model_path = model_path
            # Convert model.names dict {0: 'barcode', 1: 'qr_code'} to list ['barcode', 'qr_code']
            names_dict = self.model.names if hasattr(self.model, "names") else {}
            if isinstance(names_dict, dict):
                max_id = max(names_dict.keys()) if names_dict else -1
                self.class_names = [names_dict.get(i, f"class_{i}") for i in range(max_id + 1)]
            else:
                self.class_names = list(names_dict) if names_dict else []
            self.is_loaded = True
            logger.info(f"Loaded YOLO model from {model_path}")
            logger.info(f"Model classes: {self.class_names}")
            return True
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}")
            self.is_loaded = False
            return False

    def load_active_model(self) -> bool:
        if settings.YOLO_MODEL_PATH.exists():
            return self.load_model(settings.YOLO_MODEL_PATH)
        logger.warning("No active YOLO model found")
        return False

    def detect(self, frame: np.ndarray) -> List[Detection]:
        if not self.is_loaded or self.model is None:
            return []

        try:
            return self._detect_with_device(frame, self.device)
        except Exception as e:
            # A GPU that disappears mid-session (driver reset, out of memory,
            # a laptop switching to integrated graphics) must degrade to a slow
            # answer, not to a dead scanner. Falling back permanently for this
            # process is better than retrying a broken device on every frame.
            if self.device != "cpu":
                logger.error(
                    "Inference on device %r failed (%s); falling back to CPU for "
                    "the rest of this session.", self.device, e,
                )
                self.device = "cpu"
                try:
                    return self._detect_with_device(frame, "cpu")
                except Exception as cpu_error:
                    logger.error(f"CPU inference also failed: {cpu_error}")
                    return []
            logger.error(f"YOLO detection failed: {e}")
            return []

    def _detect_with_device(self, frame: np.ndarray, device: str) -> List[Detection]:
        """Run the model on ``device`` and normalise the result."""
        results = self.model(
            frame,
            conf=self.confidence_threshold,
            iou=self.iou_threshold,
            device=device,
            verbose=False,
        )

        detections = []
        for result in results:
            boxes = result.boxes
            if boxes is not None:
                for box in boxes:
                    xyxy = box.xyxy[0].cpu().numpy()
                    conf = float(box.conf[0].cpu().numpy())
                    cls = int(box.cls[0].cpu().numpy())

                    x1, y1, x2, y2 = map(int, xyxy)
                    class_name = self.class_names[cls] if cls < len(self.class_names) else f"class_{cls}"

                    detections.append(Detection(
                        bbox=(x1, y1, x2 - x1, y2 - y1),
                        confidence=conf,
                        class_id=cls,
                        class_name=class_name,
                    ))
        return detections

    def draw_detections(self, frame: np.ndarray, detections: List[Detection]) -> np.ndarray:
        annotated = frame.copy()
        
        for det in detections:
            x, y, w, h = det.bbox
            color = (0, 255, 0)
            
            cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
            
            label = f"{det.class_name}: {det.confidence:.2f}"
            label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]
            cv2.rectangle(annotated, (x, y - label_size[1] - 5), (x + label_size[0], y), color, -1)
            cv2.putText(annotated, label, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)

        return annotated

    def get_model_info(self) -> Dict:
        if not self.is_loaded:
            return {"loaded": False}
        
        return {
            "loaded": True,
            "model_path": str(self.model_path) if self.model_path else None,
            "classes": self.class_names,
            "confidence_threshold": self.confidence_threshold,
            "iou_threshold": self.iou_threshold,
            "device": self.device,
        }

    def set_confidence_threshold(self, threshold: float):
        self.confidence_threshold = max(0.0, min(1.0, threshold))

    def set_iou_threshold(self, threshold: float):
        self.iou_threshold = max(0.0, min(1.0, threshold))