#!/usr/bin/env python3
"""
Generate barcodes for all students without barcodes.
"""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from config.logging_config import setup_logging
from config.settings import settings
from database.connection import test_connection
from services.student_service import StudentService
from services.barcode_generator import BarcodeGeneratorService

logger = setup_logging()


def main():
    logger.info("Generating barcodes for students...")
    
    if not test_connection():
        logger.error("Database connection failed")
        sys.exit(1)
    
    student_service = StudentService()
    barcode_service = BarcodeGeneratorService()
    
    students = student_service.search_students(limit=1000)
    logger.info(f"Found {len(students)} students")
    
    generated = 0
    for student in students:
        existing = barcode_service.get_student_barcode(student.id)
        if not existing:
            barcode = barcode_service.generate_for_student(student.id, generated_by=1)
            if barcode:
                generated += 1
                logger.info(f"Generated barcode for {student.full_name}: {barcode.barcode_value}")
    
    logger.info(f"Generated {generated} new barcodes")


if __name__ == "__main__":
    main()