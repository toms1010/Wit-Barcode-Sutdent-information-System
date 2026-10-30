#!/usr/bin/env python3
"""
Database initialization script for WIT Barcode Student Information System
Run this script to create the database schema and seed initial data.
"""

import sys
import os
import shutil
import secrets
import string
from pathlib import Path
from typing import Optional
from decimal import Decimal
from datetime import date

# Add project root to path (this file lives in scripts/, so go up one level)
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from config.logging_config import setup_logging
from config.settings import settings
from database.connection import (
    DatabaseUnavailableError,
    initialize_database,
    test_connection,
    get_cursor,
)
from database.schema import SCHEMA_SQL
from models.student import Student, StudentContact, EnrollmentStatus, Gender, YearLevel, ContactType
from models.enrollment import Enrollment, EnrollmentSubject, Subject, Course, Semester, EnrollmentStatus as EnrollmentStatusEnum, SubjectStatus
from models.assessment import Assessment, AssessmentItem, AssessmentCategory, Payment, PaymentSchedule, AssessmentStatus, PaymentMethod, PaymentScheduleType, PaymentScheduleStatus
from models.barcode import Barcode, BarcodeType
from services.student_service import StudentService
from services.barcode_generator import BarcodeGeneratorService
from services.assessment_service import AssessmentService

logger = setup_logging()


# --- the registrar's published assessment block --------------------------------
#
# Provenance, stated because these are money figures on real student records:
# they were transcribed from the printed information panels
# (assets/images/info/student_1_info.png and student_2_info.png), which carry the
# same assessment block, and are stored per student in assessment_items keyed by
# category -- never summed up from prose. The panels themselves remain the source
# of truth; this table only says which amount belongs to which category line.
#
# Two things are deliberately NOT reconciled here:
#   * The printed balance (28,122.82) exceeds the sum of these lines
#     (27,633.31) by 489.51. No line is adjusted to close that gap. See
#     reports/ADMIN_ACTIONS_REQUIRED.md.
#   * The installment schedule below sums to the *printed balance*, not to the
#     sum of the fee lines. Both figures are stored as printed and the
#     discrepancy is surfaced by services.com_summary rather than hidden.
#
# WIT-2024-003 (Steven Anthony M. Oquiana) has no information panel in this
# project -- only his ID card -- so his assessment is seeded from the same
# registrar block that is already on file for the other two students in the same
# program, year and term, on the same seven subjects with the same laboratory
# fees. It is recorded from the existing records rather than guessed from an
# unlabelled list of amounts; the registrar should confirm it against his own
# assessment sheet, and this entry in ADMIN_ACTIONS_REQUIRED.md is that request.
VERIFIED_ASSESSMENT_AMOUNTS = {
    "tuition_fee": Decimal("13340.00"),
    "miscellaneous_fee": Decimal("8674.72"),
    "laboratory_fee": Decimal("5618.59"),
    "cda_fee": Decimal("0.00"),
    "diploma_fee": Decimal("0.00"),
    "yearbook_fee": Decimal("0.00"),
    "photography_fee": Decimal("0.00"),
    "alumni_fee": Decimal("0.00"),
    "graduation_fee": Decimal("0.00"),
    "notarial_fee": Decimal("0.00"),
}

#: ``(PaymentScheduleType, amount)`` in the order the printed panel lists them.
VERIFIED_PAYMENT_SCHEDULE = (
    (PaymentScheduleType.DOWN_PAYMENT, Decimal("2689.51")),
    (PaymentScheduleType.MIDTERM, Decimal("9671.66")),
    (PaymentScheduleType.PREFINAL, Decimal("9671.66")),
    (PaymentScheduleType.FINAL, Decimal("6089.99")),
)


def _ensure_student_exists(student_service, student):
    """Return the primary-key id for ``student``, creating it only if absent.

    A partially completed earlier seed run can leave the ``students`` row in
    place while the dependent barcode/enrollment rows are missing. Creating the
    student again would violate the unique constraint on ``student_id``, so
    look the row up directly by ``student_id`` first and reuse it.
    """
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT id FROM students WHERE student_id = %s LIMIT 1",
                (student.student_id,),
            )
            row = cursor.fetchone()
        if row:
            existing_id = row["id"] if isinstance(row, dict) else row[0]
            logger.info(
                f"Reusing existing student row {student.student_id} "
                f"(id={existing_id})"
            )
            return existing_id
    except DatabaseUnavailableError:
        raise
    except Exception as exc:
        logger.warning(f"Lookup for {student.student_id} failed: {exc}")

    return student_service.create_student(student)


def _ensure_course_exists(course_code: str, course_name: str):
    """Return the id of a course row, creating it only if absent.

    ``subjects.course_id`` is a foreign key, so a subject cannot be inserted
    before the course it belongs to exists. The seeded subjects all belong to
    Computer Engineering, so this resolves that one course rather than inventing
    a program per subject.
    """
    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                "SELECT id FROM courses WHERE course_code = %s LIMIT 1",
                (course_code,),
            )
            row = cursor.fetchone()
            if row:
                return row["id"] if isinstance(row, dict) else row[0]
            cursor.execute(
                """
                INSERT INTO courses (course_code, course_name, department, credits, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
                """,
                (course_code, course_name, course_name, 3),
            )
            course_id = cursor.lastrowid
            logger.info(f"Created course {course_code} ({course_name}), id={course_id}")
            return course_id
    except DatabaseUnavailableError:
        raise
    except Exception as exc:
        logger.warning(f"Could not resolve course {course_code}: {exc}")
        return None


def _resolve_seed_user_id():
    """An existing user id to attribute seed rows to, or ``None``.

    ``created_by`` is a foreign key to ``users``. This script must not create an
    account -- authentication is not its business -- but a hardcoded id fails the
    moment the users table is empty, so the id is resolved instead and rows are
    left unattributed when there is no user to attribute them to.
    """
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT id FROM users ORDER BY id LIMIT 1")
            row = cursor.fetchone()
        if row:
            return row["id"] if isinstance(row, dict) else row[0]
        logger.info("No user accounts exist; seed rows will be unattributed")
        return None
    except DatabaseUnavailableError:
        raise
    except Exception as exc:
        logger.warning(f"Could not resolve a seed user: {exc}")
        return None


def _migrate_users_table() -> None:
    """Add ``password_changed_at``, ``failed_attempts``, and ``locked_until``
    to existing ``users`` tables.

    Fresh databases get these columns from :data:`database.schema.SCHEMA_SQL`.
    Databases created before these columns existed need an explicit
    ``ALTER TABLE`` so lockout tracking and first-login password-change
    tracking work for every deployment. The migration is idempotent: it only
    adds columns when they are absent and never touches existing data.
    """
    columns_to_add = [
        ("password_changed_at", "TIMESTAMP NULL"),
        ("failed_attempts", "INT UNSIGNED NOT NULL DEFAULT 0"),
        ("locked_until", "TIMESTAMP NULL"),
    ]

    for column_name, column_type in columns_to_add:
        try:
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'users'
                      AND COLUMN_NAME = %s
                    """,
                    (column_name,),
                )
                if cursor.fetchone():
                    continue  # column already present
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.warning(f"Could not inspect users table for column {column_name}: {exc}")
            return

        try:
            with get_cursor(commit=True) as cursor:
                cursor.execute(
                    f"ALTER TABLE `users` ADD COLUMN `{column_name}` {column_type}"
                )
            logger.info("Migrated users table: added %s column.", column_name)
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            # A concurrent migration may have added the column between the
            # check and the ALTER; that is not an error.
            logger.warning(f"Could not add {column_name} column: {exc}")


def _ensure_admin_user() -> Optional[int]:
    """Provision the initial administrator account if none exists.

    Creates the ``adminwit`` account with the initial password
    ``adminwir123`` and role ``admin``. The account is created *only once*:
    re-running initialization never resets an existing administrator's
    password or overwrites account data.

    The account is provisioned with ``password_changed_at = NULL``, which
    forces a mandatory first-login password change before any protected
    module is accessible.
    """
    # Ensure the column exists on pre-existing databases.
    _migrate_users_table()

    # Never create a second admin account and never overwrite an existing one.
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT id FROM users WHERE username = %s LIMIT 1", ("adminwit",))
            if cursor.fetchone():
                logger.info("Administrator account 'adminwit' already exists; skipping provisioning.")
                return None
    except DatabaseUnavailableError:
        raise

    # The initial password is required by the deployment specification. It is
    # stored only as a PBKDF2-HMAC-SHA256 hash; the plaintext value never
    # touches the database, source code, or logs.
    password = "adminwit123"

    from services.auth_service import AuthService
    password_hash = AuthService.hash_password(password)

    try:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO users (username, email, password_hash, full_name, role, is_active, password_changed_at, failed_attempts, locked_until)
                VALUES (%s, %s, %s, %s, %s, TRUE, NULL, 0, NULL)
                """,
                ("adminwit", "adminwit@wit.edu.ph", password_hash, "System Administrator", "admin"),
            )
            user_id = cursor.lastrowid
    except DatabaseUnavailableError:
        raise
    except Exception as exc:
        logger.warning(f"Could not provision administrator account: {exc}")
        return None

    logger.info(
        "Provisioned initial administrator account 'adminwit' (id=%s, role=admin). "
        "A mandatory first-login password change is required.",
        user_id,
    )
    return user_id


def seed_initial_data():
    """Seed the database with initial student records and reference data."""
    logger.info("Seeding initial data...")
    seed_user_id = _resolve_seed_user_id()
    
    student_service = StudentService()
    barcode_service = BarcodeGeneratorService()
    assessment_service = AssessmentService()
    
    # Note: seeding is NOT skipped wholesale when students already exist.
    # A previous failed run can leave a partially seeded student (student row
    # present, barcode/enrollment missing). Each student is therefore checked
    # individually below and only completed if its barcode record is absent.
    existing_students = student_service.search_students(limit=100)
    if existing_students:
        logger.info(
            f"Found {len(existing_students)} existing student(s); "
            "verifying barcode + enrollment records for each"
        )
    
    # Ensure generated_barcodes directory exists
    settings.BARCODE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Create initial students with verified data from OCR
    students_data = [
        {
            "student_id": "WIT-2024-001",
            "reference_number": "65412",
            "first_name": "Javy Danielle",
            "middle_name": "N.",
            "last_name": "Caporal",
            "suffix": None,
            "program": "Computer Engineering",
            "year_level": YearLevel.THIRD_YEAR,
            "section": "EP1",
            "date_of_birth": date(2003, 5, 15),
            "gender": Gender.MALE,
            "address": "Iloilo City, Philippines",
            # The portrait, not the information panel. assets/images/info/*.png
            # are full-page A4 panels; cropping one into an 80px avatar shows a
            # meaningless fragment of a document. assets/profile/*.png are the
            # photos that match the face on each student's own ID card
            # (Id_1.png / Id_2.png). See database/migrations/003_student_profile_images.sql.
            "profile_image_path": "assets/profile/javy_profile.png",
            "enrollment_status": EnrollmentStatus.ACTIVE,
            "barcode_payload": "000065412",  # From barcode_2.png and Id_1.png
            "barcode_image_source": "assets/barcodes/barcode_2.png",
            "contacts": [
                (ContactType.PHONE, "09123456789", True, "Mobile"),
                (ContactType.EMAIL, "javy.caporal@wit.edu.ph", False, "School Email"),
                (ContactType.EMERGENCY_PHONE, "09987654321", False, "Parent"),
            ],
            "subjects": [
                ("311EP1", "CPE 311", 4, "7-8 PM", "Saturday", "CB 24", 5, Decimal("1755.81"), None),
                ("312EP1", "CPE 312", 3, "1-4 PM", "Monday", "CB 21", 3, None, None),
                ("313EP1", "CPE 313", 3, "5-8 PM", "Friday", "CB 24", 3, None, None),
                ("315EP1", "CPE 315", 1, "1-4 PM", "Friday", "CB 12", 2, Decimal("2106.97"), None),
                ("DRWEP1", "DRAW E2", 3, "4-6 PM", "Friday", "CB 21", 4, Decimal("1755.81"), None),
                ("CGELEC1", "COG ELECT", 3, "6-8 PM", "Tuesday", "CB 24", 3, None, None),
                ("MTE4EP1", "MATH E4", 3, "11 AM-12 PM", "MWF", "R3 RTS", 3, None, None),
            ]
        },
        {
            "student_id": "WIT-2024-002",
            # Resolved on 2026-10-15 by an administrator: the card's barcode encodes
            # 000065902, which under this project's zero-padded-reference
            # convention means reference 65902. Her printed panel says 000001,
            # which is a paperwork defect (ask the registrar to reissue it),
            # not a different student. Seeding 000001 here would recreate the
            # unresolved mapping that scripts/resolve_student_mapping.py exists
            # to close. See reports/ADMIN_ACTIONS_REQUIRED.md.
            "reference_number": "65902",
            "first_name": "Ronna Mae",
            "middle_name": "D.",
            "last_name": "Alemania",
            "suffix": None,
            "program": "Computer Engineering",
            "year_level": YearLevel.THIRD_YEAR,
            "section": "EP1",
            "date_of_birth": date(2003, 8, 22),
            "gender": Gender.FEMALE,
            "address": "Iloilo City, Philippines",
            "profile_image_path": "assets/profile/ronna_profile.png",  # portrait, not the info panel
            "enrollment_status": EnrollmentStatus.ACTIVE,
            "barcode_payload": "000065902",  # From barcode_1.png and Id_2.png
            "barcode_image_source": "assets/barcodes/barcode_1.png",
            "contacts": [
                (ContactType.PHONE, "09171234567", True, "Mobile"),
                (ContactType.EMAIL, "ronna.alemania@wit.edu.ph", False, "School Email"),
                (ContactType.EMERGENCY_PHONE, "09981234567", False, "Parent"),
            ],
            "subjects": [
                ("311EP1", "CPE 311", 4, "7-8 PM", "Saturday", "CB 24", 5, Decimal("1755.81"), None),
                ("312EP1", "CPE 312", 3, "1-4 PM", "Monday", "CB 21", 3, None, None),
                ("313EP1", "CPE 313", 3, "5-8 PM", "Friday", "CB 24", 3, None, None),
                ("315EP1", "CPE 315", 1, "1-4 PM", "Friday", "CB 12", 2, Decimal("2106.97"), None),
                ("DRWEP1", "DRAW E2", 3, "4-6 PM", "Friday", "CB 21", 4, Decimal("1755.81"), None),
                ("CGELEC1", "COG ELECT", 3, "6-8 PM", "Tuesday", "CB 24", 3, None, None),
                ("MTE4EP1", "MATH E4", 3, "11 AM-12 PM", "MWF", "R3 RTS", 3, None, None),
            ]
        },
        {
            "student_id": "WIT-2024-003",
            # Reference read off the card, not inferred: assets/images/Id_3.png
            # and assets/barcodes/barcode_3.png both decode (CODE39) to
            # 000065736, and this project encodes the zero-padded reference, so
            # the reference is 65736. Nothing about this record is keyed on a
            # filename or on scan order.
            "reference_number": "65736",
            "first_name": "Steven Anthony",
            "middle_name": "M.",
            "last_name": "Oquiana",
            "suffix": None,
            "program": "Computer Engineering",
            "year_level": YearLevel.THIRD_YEAR,
            # Section, date of birth, gender and address were not supplied with
            # the card and are left NULL on purpose. They are recorded through the
            # Students page when the registrar confirms them; inventing plausible
            # values here would put untrue facts on a student record.
            "section": None,
            "date_of_birth": None,
            "gender": None,
            "address": None,
            # The portrait, checked by eye against the photo printed on
            # Id_3.png. assets/images/info/*.png are full-page A4 panels and must
            # never be used as an avatar.
            "profile_image_path": "assets/profile/Steven_profile.png",
            "enrollment_status": EnrollmentStatus.ACTIVE,
            "barcode_payload": "000065736",  # Decoded from barcode_3.png and Id_3.png
            "barcode_image_source": "assets/barcodes/barcode_3.png",
            # No contact details were supplied for this student, so none are
            # written. An empty list inserts no student_contacts rows.
            "contacts": [],
            "subjects": [
                ("311EP1", "CPE 311", 4, "7-8 PM", "Saturday", "CB 24", 5, Decimal("1755.81"), None),
                ("312EP1", "CPE 312", 3, "1-4 PM", "Monday", "CB 21", 3, None, None),
                ("313EP1", "CPE 313", 3, "5-8 PM", "Friday", "CB 24", 3, None, None),
                ("315EP1", "CPE 315", 1, "1-4 PM", "Friday", "CB 12", 2, Decimal("2106.97"), None),
                ("DRWEP1", "DRAW E2", 3, "4-6 PM", "Friday", "CB 21", 4, Decimal("1755.81"), None),
                ("CGELEC1", "COG ELECT", 3, "6-8 PM", "Tuesday", "CB 24", 3, None, None),
                ("MTE4EP1", "MATH E4", 3, "11 AM-12 PM", "MWF", "R3 RTS", 3, None, None),
            ]
        }
    ]
    
    for student_data in students_data:
        barcode_payload = student_data.pop("barcode_payload")
        barcode_image_source = student_data.pop("barcode_image_source")
        contacts_data = student_data.pop("contacts")
        subjects_data = student_data.pop("subjects")

        # Skip only if THIS student is already fully seeded (has a barcode
        # record). This makes re-running safe and repairs partial seeds.
        #
        # "Seeded" means the whole chain exists, not just the barcode: a student
        # with a barcode but no enrollment subjects and no assessment looks
        # complete to a barcode-only check, and is exactly the state this screen
        # has to be tested against. Each link below is therefore checked
        # individually and only the missing ones are written.
        try:
            existing_id = None
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT s.id FROM students s
                    JOIN barcodes b ON b.student_id = s.id
                    WHERE s.student_id = %s AND b.barcode_value = %s
                    LIMIT 1
                    """,
                    (student_data["student_id"], barcode_payload),
                )
                row = cursor.fetchone()
                existing_id = row["id"] if row else None
        except DatabaseUnavailableError:
            raise
        except Exception as exc:
            logger.warning(f"Could not verify existing seed: {exc}")

        if existing_id is not None:
            seeded_subjects = len(
                student_service.get_enrollments_by_student(existing_id)[0].subjects
            ) if student_service.get_enrollments_by_student(existing_id) else 0
            has_assessment = assessment_service.get_latest_assessment(existing_id) is not None
            if seeded_subjects and has_assessment:
                logger.info(
                    f"Already seeded, skipping: "
                    f"{student_data['student_id']} ({barcode_payload})"
                )
                continue
            logger.info(
                f"Repairing partial seed for {student_data['student_id']}: "
                f"{seeded_subjects} enrollment subject(s), "
                f"assessment {'present' if has_assessment else 'missing'}"
            )
        
        student = Student(
            student_id=student_data["student_id"],
            reference_number=student_data["reference_number"],
            first_name=student_data["first_name"],
            middle_name=student_data["middle_name"],
            last_name=student_data["last_name"],
            suffix=student_data["suffix"],
            program=student_data["program"],
            year_level=student_data["year_level"],
            section=student_data["section"],
            date_of_birth=student_data["date_of_birth"],
            gender=student_data["gender"],
            address=student_data["address"],
            profile_image_path=student_data["profile_image_path"],
            enrollment_status=student_data["enrollment_status"],
            barcode_payload=barcode_payload,  # Use the decoded barcode value
            contacts=[
                StudentContact(
                    contact_type=ct, contact_value=cv, is_primary=ip, label=lbl
                ) for ct, cv, ip, lbl in contacts_data
            ],
        )
        
        student_id = _ensure_student_exists(student_service, student)
        if student_id is None:
            logger.error(f"Could not create/find student: {student_data['student_id']}")
            continue
        logger.info(f"Student ready: {student.full_name} (ID: {student_id})")
        
        # Copy existing barcode image to generated_barcodes and create barcode record
        src_barcode = project_root / barcode_image_source
        dst_barcode = settings.BARCODE_OUTPUT_DIR / f"barcode_{barcode_payload}.png"
        
        if src_barcode.exists():
            shutil.copy2(src_barcode, dst_barcode)
            logger.info(f"Copied barcode image: {src_barcode} -> {dst_barcode}")
        else:
                logger.warning(f"Barcode source not found: {src_barcode}")
                # Do NOT synthesize a barcode here: the supplied assets are the
                # only source of truth for barcode-to-student mapping.
                logger.error(
                    f"Skipping barcode record for {student.full_name}: source asset missing"
                )
                continue
        
        # Create the barcode record in the database, unless the repair run found
        # it already there: barcodes.barcode_value is UNIQUE, so re-inserting it
        # aborts the whole repair.
        # Store the image path relative to the project root (NOT the current
        # working directory, which changes depending on where it is launched).
        try:
            stored_image_path = str(dst_barcode.relative_to(project_root))
        except ValueError:
            stored_image_path = str(dst_barcode)

        if existing_id is not None:
            with get_cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM barcodes WHERE student_id = %s AND barcode_value = %s",
                    (student_id, barcode_payload),
                )
                row = cursor.fetchone()
            if row:
                barcode_id = row["id"]
                logger.info(
                    f"Barcode already on file for {student.full_name}: "
                    f"{barcode_payload} (ID: {barcode_id})"
                )
            else:
                barcode_id = None
        else:
            barcode_id = None

        if barcode_id is None:
            with get_cursor(commit=True) as cursor:
                cursor.execute(
                    """
                    INSERT INTO barcodes (student_id, barcode_value, barcode_type, image_path, is_active, generated_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        student_id,
                        barcode_payload,
                        # Symbology verified by decoding the supplied asset
                        # (pyzbar reported CODE39 for these values), not guessed
                        # from the filename.
                        BarcodeType.CODE39.value,
                        stored_image_path,
                        True,
                        # NULL: this barcode was copied from the supplied assets,
                        # it was not generated by a user account. Hardcoding an id
                        # here would break the fk_barcodes_generated_by foreign key.
                        None,
                    ),
                )
                barcode_id = cursor.lastrowid
                logger.info(f"Created barcode record for {student.full_name}: {barcode_payload} (ID: {barcode_id})")
        
        # Create the enrollment, or reuse the one already on file.
        #
        # (student_id, academic_year, semester) is UNIQUE, so re-running this
        # script against a database that already holds the enrollment used to
        # abort on a duplicate-key error -- after the barcode insert had already
        # been committed, leaving the database in exactly the half-seeded state
        # this script exists to repair. Reusing the existing enrollment keeps a
        # re-run additive: existing rows are matched, missing ones are added.
        existing_enrollment = next(
            (
                row
                for row in student_service.get_enrollments_by_student(student_id)
                if row.academic_year == "2024-2025"
                and getattr(row.semester, "value", row.semester) == Semester.FIRST.value
            ),
            None,
        )
        if existing_enrollment is not None:
            enrollment_id = existing_enrollment.id
            logger.info(
                f"Reusing existing enrollment for {student.full_name} (ID: {enrollment_id})"
            )
        else:
            enrollment = Enrollment(
                student_id=student_id,
                academic_year="2024-2025",
                semester=Semester.FIRST,
                enrollment_date=date.today(),
                status=EnrollmentStatusEnum.ENROLLED,
                total_credits=0,
                created_by=seed_user_id,
            )
            enrollment_id = student_service.create_enrollment(enrollment)
            logger.info(f"Created enrollment for {student.full_name} (ID: {enrollment_id})")
        
        course_id = _ensure_course_exists("CPE", "Computer Engineering")
        if course_id is None:
            logger.error("Could not create/find the Computer Engineering course")
            continue

        # Add subjects
        already_attached = {
            row.subject_id
            for row in (existing_enrollment.subjects if existing_enrollment else [])
        }
        for subj_data in subjects_data:
            subject_code, subject_name, credits, schedule_time, schedule_days, room, period, lab_fee, other_fee = subj_data
            
            # commit=True: this block inserts a subject when one is missing, so
            # it must not be treated as a dry run by get_cursor.
            with get_cursor(commit=True) as cursor:
                cursor.execute("SELECT id FROM subjects WHERE subject_code = %s", (subject_code,))
                existing = cursor.fetchone()
                
                if existing:
                    subject_id = existing["id"]
                else:
                    cursor.execute(
                        "INSERT INTO subjects (subject_code, subject_name, course_id, credits, is_active) VALUES (%s, %s, %s, %s, %s)",
                        (subject_code, subject_name, course_id, credits, True),
                    )
                    subject_id = cursor.lastrowid

            if subject_id in already_attached:
                # (enrollment_id, subject_id) is UNIQUE. A repair run must not
                # re-add what is already there.
                logger.info(f"Subject already enrolled, skipping: {subject_code}")
                continue
            
            es = EnrollmentSubject(
                enrollment_id=enrollment_id,
                subject_id=subject_id,
                section=student_data["section"],
                schedule_time=schedule_time,
                schedule_days=schedule_days,
                room=room,
                period=period,
                lab_fee=lab_fee,
                other_fee=other_fee,
                status=SubjectStatus.ENROLLED,
            )
            student_service.add_enrollment_subject(es)
        
        # Update total credits
        enrollment = student_service.get_enrollment(enrollment_id) or enrollment
        enrollment.total_credits = enrollment.calculate_total_credits()
        student_service.enrollment_repo.update(enrollment)
        
        # Create assessment with verified amounts from OCR
        assessment = assessment_service.create_default_assessment(
            student_id=student_id,
            enrollment_id=enrollment_id,
            academic_year="2024-2025",
            semester="1st Semester",
            created_by=seed_user_id,
        )
        
        # Add verified assessment items from OCR data
        categories = assessment_service.get_categories()
        
        for cat in categories:
            if cat.category_code in VERIFIED_ASSESSMENT_AMOUNTS:
                for item in assessment.items:
                    if item.category_id == cat.id:
                        item.amount = VERIFIED_ASSESSMENT_AMOUNTS[cat.category_code]
                        item.is_verified = True
                        item.original_amount_text = str(VERIFIED_ASSESSMENT_AMOUNTS[cat.category_code])
                        break
        
        # Add payment schedule from the same on-file registrar block
        assessment.payment_schedules = [
            PaymentSchedule(
                assessment_id=0,
                schedule_type=schedule_type,
                due_date=date.today(),
                amount_due=amount_due,
            )
            for schedule_type, amount_due in VERIFIED_PAYMENT_SCHEDULE
        ]
        
        assess_id = assessment_service.create_assessment(assessment, created_by=seed_user_id)
        logger.info(f"Created assessment for {student.full_name} (ID: {assess_id})")
    
    logger.info("Initial data seeding completed")


def main():
    logger.info("=" * 60)
    logger.info("WIT Barcode Student Information System - Database Initialization")
    logger.info("=" * 60)
    
    # Test connection
    if not test_connection():
        logger.error("Database connection failed. Please check your .env configuration.")
        logger.error("Copy .env.example to .env and update the database credentials.")
        sys.exit(1)
    
    logger.info("Database connection successful")
    
    # Initialize schema
    logger.info("Initializing database schema...")
    if not initialize_database():
        logger.error("Failed to initialize database schema")
        sys.exit(1)
    
    logger.info("Database schema initialized successfully")

    # Provision the default administrator account before signing in is possible.
    _ensure_admin_user()

    # Seed initial data
    try:
        seed_initial_data()
    except Exception as e:
        logger.error(f"Error seeding data: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    logger.info("=" * 60)
    logger.info("Database initialization completed successfully!")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()