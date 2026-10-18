import pytest
from unittest.mock import Mock, patch, MagicMock
import numpy as np

from services.yolo_detector import YOLODetectorService, Detection


class TestYOLODetector:
    def test_detector_initialization(self):
        detector = YOLODetectorService()
        assert detector.model is None
        assert detector.is_loaded is False
        assert detector.class_names == []
    
    @patch('services.yolo_detector.YOLO')
    def test_load_model_success(self, mock_yolo_class):
        mock_model = Mock()
        mock_model.names = {0: "barcode", 1: "qr_code"}
        mock_yolo_class.return_value = mock_model
        
        detector = YOLODetectorService()
        result = detector.load_model("test_model.pt")
        
        assert result is True
        assert detector.is_loaded is True
        assert detector.class_names == ["barcode", "qr_code"]
        mock_yolo_class.assert_called_once_with("test_model.pt")
    
    @patch('services.yolo_detector.YOLO')
    def test_load_model_failure(self, mock_yolo_class):
        mock_yolo_class.side_effect = Exception("Model not found")
        
        detector = YOLODetectorService()
        result = detector.load_model("nonexistent.pt")
        
        assert result is False
        assert detector.is_loaded is False
    
    def test_detect_not_loaded(self):
        detector = YOLODetectorService()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        
        detections = detector.detect(frame)
        
        assert detections == []
    
    @patch('services.yolo_detector.YOLO')
    def test_detect_with_detections(self, mock_yolo_class):
        mock_model = Mock()
        mock_model.names = {0: "barcode"}
        
        # Mock detection results
        mock_box = Mock()
        mock_box.xyxy = [Mock()]
        mock_box.xyxy[0].cpu().numpy.return_value = [100, 100, 200, 150]
        mock_box.conf = [Mock()]
        mock_box.conf[0].cpu().numpy.return_value = 0.85
        mock_box.cls = [Mock()]
        mock_box.cls[0].cpu().numpy.return_value = 0
        
        mock_result = Mock()
        mock_result.boxes = [mock_box]
        
        mock_model.return_value = [mock_result]
        mock_yolo_class.return_value = mock_model
        
        detector = YOLODetectorService()
        detector.load_model("test.pt")
        
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        detections = detector.detect(frame)
        
        assert len(detections) == 1
        assert detections[0].class_name == "barcode"
        assert detections[0].confidence == 0.85
        assert detections[0].bbox == (100, 100, 100, 50)
    
    def test_draw_detections(self):
        detector = YOLODetectorService()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        
        detections = [
            Detection(bbox=(100, 100, 100, 50), confidence=0.85, class_id=0, class_name="barcode"),
        ]
        
        annotated = detector.draw_detections(frame, detections)
        
        assert annotated.shape == frame.shape
        # Check that rectangle was drawn (non-zero pixels in bbox area)
        assert np.any(annotated[100:150, 100:200] != 0)
    
    def test_get_model_info(self):
        detector = YOLODetectorService()
        info = detector.get_model_info()
        
        assert info["loaded"] is False
        
        detector.is_loaded = True
        detector.model_path = "test.pt"
        detector.class_names = ["barcode"]
        
        info = detector.get_model_info()
        assert info["loaded"] is True
        assert info["model_path"] == "test.pt"
        assert info["classes"] == ["barcode"]
    
    def test_set_thresholds(self):
        detector = YOLODetectorService()
        
        detector.set_confidence_threshold(0.7)
        assert detector.confidence_threshold == 0.7
        
        detector.set_confidence_threshold(1.5)  # Should clamp
        assert detector.confidence_threshold == 1.0
        
        detector.set_iou_threshold(0.3)
        assert detector.iou_threshold == 0.3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])