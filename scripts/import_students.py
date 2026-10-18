#!/usr/bin/env python3
"""
Import students from CSV or Excel file.
"""

import sys
from pathlib import Path
import csv

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from config.logging_config import setup_logging
from config.settings import settings
from database.connection import test_connection
from services.student_service import StudentService
from models.student import Student, StudentContact, EnrollmentStatus, Gender, YearLevel, ContactType
from datetime import datetime

logger = setup_logging()


def import_from_csv(file_path: Path):
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        return
    
    if not test_connection():
        logger.error("Database connection failed")
        return
    
    student_service = StudentService()
    
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    logger.info(f"Found {len(rows)} students in CSV")
    
    imported = 0
    for row in rows:
        try:
            # Parse student data
            student = Student(
                student_id=row.get('student_id', '').strip() or None,
                reference_number=row.get('reference_number', '').strip(),
                first_name=row.get('first_name', '').strip(),
                middle_name=row.get('middle_name', '').strip() or None,
                last_name=row.get('last_name', '').strip(),
                suffix=row.get('suffix', '').strip() or None,
                program=row.get('program', '').strip(),
                year_level=YearLevel(row.get('year_level', '3rd Year')),
                section=row.get('section', '').strip() or None,
                date_of_birth=datetime.strptime(row['date_of_birth'], '%Y-%m-%d').date() if row.get('date_of_birth') else None,
                gender=Gender(row.get('gender', 'Male')),
                address=row.get('address', '').strip() or None,
                profile_image_path=row.get('profile_image', '').strip() or None,
                enrollment_status=EnrollmentStatus(row.get('enrollment_status', 'active')),
            )
            
            # Parse contacts
            contacts = []
            phone = row.get('phone', '').strip()
            email = row.get('email', '').strip()
            if phone:
                contacts.append(StudentContact(contact_type=ContactType.PHONE, contact_value=phone, is_primary=True))
            if email:
                contacts.append(StudentContact(contact_type=ContactType.EMAIL, contact_value=email, is_primary=False))
            student.contacts = contacts
            
            student_id = student_service.create_student(student)
            if student_id:
                imported += 1
                logger.info(f"Imported: {student.full_name} (ID: {student_id})")
                
        except Exception as e:
            logger.error(f"Failed to import row {row}: {e}")
    
    logger.info(f"Successfully imported {imported} students")


def main():
    if len(sys.argv) < 2:
        logger.error("Usage: python import_students.py <csv_file>")
        sys.exit(1)
    
    file_path = Path(sys.argv[1])
    logger.info(f"Importing students from {file_path}")
    import_from_csv(file_path)


if __name__ == "__main__":
    main()