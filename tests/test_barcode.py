import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import tempfile
import os

from services.barcode_generator import BarcodeGeneratorService
from services.barcode_decoder import BarcodeDecoderService, DecodedBarcode
from models.student import Student, EnrollmentStatus, Gender, YearLevel
from models.barcode import Barcode, BarcodeType


class TestBarcodeGenerator:
    @patch('services.barcode_generator.Code128')
    def test_generate_barcode_image(self, mock_code128):
        mock_writer = Mock()
        mock_code128_instance = Mock()
        mock_code128.return_value = mock_code128_instance
        
        service = BarcodeGeneratorService()
        with tempfile.TemporaryDirectory() as tmpdir:
            service.output_dir = Path(tmpdir)
            
            result = service.generate_barcode_image("TEST123", Path(tmpdir) / "test.png")
            
            assert result is True
            mock_code128_instance.save.assert_called_once()
    
    def test_generate_barcode_value(self):
        service = BarcodeGeneratorService()
        student = Student(
            id=1,
            reference_number="65412",
            first_name="John",
            last_name="Doe",
            program="Computer Engineering",
            year_level=YearLevel.THIRD_YEAR,
        )
        
        value = service.generate_barcode_value(student)
        assert value == "WIT:STUDENT:65412"


class TestBarcodeDecoder:
    def test_decoder_initialization(self):
        decoder = BarcodeDecoderService()
        assert decoder.last_decoded is None
        assert decoder.last_decode_time == 0
    
    def test_is_duplicate(self):
        decoder = BarcodeDecoderService()
        
        # First scan - not duplicate
        assert decoder.is_duplicate("TEST123", suppression_seconds=5) is False
        
        # Immediate second scan - duplicate
        assert decoder.is_duplicate("TEST123", suppression_seconds=5) is True
    
    @patch('services.barcode_decoder.pyzbar.decode')
    def test_decode_empty(self, mock_decode):
        mock_decode.return_value = []
        decoder = BarcodeDecoderService()
        
        import numpy as np
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        results = decoder.decode(image)
        
        assert results == []


class TestBarcodeGenerationIntegration:
    @patch('services.barcode_generator.BarcodeRepository')
    @patch('services.barcode_generator.StudentService')
    def test_generate_for_student(self, mock_student_service, mock_barcode_repo):
        mock_student = Student(
            id=1,
            reference_number="65412",
            first_name="John",
            last_name="Doe",
            program="Computer Engineering",
            year_level=YearLevel.THIRD_YEAR,
        )
        mock_student_service.return_value.get_student.return_value = mock_student
        mock_barcode_repo.return_value.get_by_student.return_value = None
        mock_barcode_repo.return_value.get_by_value.return_value = None
        mock_barcode_repo.return_value.create.return_value = 1
        
        with patch('services.barcode_generator.BarcodeGeneratorService.generate_barcode_image', return_value=True):
            service = BarcodeGeneratorService()
            barcode = service.generate_for_student(1, generated_by=1)
            
            assert barcode is not None
            assert barcode.barcode_value == "WIT:STUDENT:65412"
            assert barcode.student_id == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])