from typing import Optional, List
from database.connection import get_cursor
from models.student import Student, StudentContact, EnrollmentStatus, Gender, YearLevel, ContactType
from models.enrollment import Enrollment, EnrollmentSubject, Subject, Course, Semester, EnrollmentStatus as EnrollmentStatusEnum, SubjectStatus
import logging

logger = logging.getLogger(__name__)


class StudentRepository:
    def create(self, student: Student) -> Optional[int]:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO students (
                    student_id, reference_number, first_name, middle_name, last_name, suffix,
                    program, year_level, section, date_of_birth, gender, address,
                    profile_image_path, enrollment_status, date_enrolled, barcode_payload,
                    created_by, updated_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    student.student_id,
                    student.reference_number,
                    student.first_name,
                    student.middle_name,
                    student.last_name,
                    student.suffix,
                    student.program,
                    student.year_level.value if isinstance(student.year_level, YearLevel) else student.year_level,
                    student.section,
                    student.date_of_birth,
                    student.gender.value if isinstance(student.gender, Gender) else student.gender,
                    student.address,
                    student.profile_image_path,
                    student.enrollment_status.value if isinstance(student.enrollment_status, EnrollmentStatus) else student.enrollment_status,
                    student.date_enrolled,
                    student.barcode_payload,
                    student.created_by,
                    student.updated_by,
                ),
            )
            student_id = cursor.lastrowid
            
            for contact in student.contacts:
                cursor.execute(
                    """
                    INSERT INTO student_contacts (student_id, contact_type, contact_value, is_primary, label)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (student_id, contact.contact_type.value if isinstance(contact.contact_type, ContactType) else contact.contact_type,
                     contact.contact_value, contact.is_primary, contact.label),
                )
            return student_id

    def get_by_id(self, student_id: int) -> Optional[Student]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM students WHERE id = %s", (student_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_student(row)

    def get_by_student_id(self, student_id: str) -> Optional[Student]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM students WHERE student_id = %s", (student_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_student(row)

    def get_by_reference_number(self, reference_number: str) -> Optional[Student]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM students WHERE reference_number = %s", (reference_number,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_student(row)

    def get_by_barcode_payload(self, barcode_payload: str) -> Optional[Student]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM students WHERE barcode_payload = %s", (barcode_payload,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_student(row)

    def get_all(self, limit: int = 100, offset: int = 0, search: str = "", status: str = "") -> List[Student]:
        with get_cursor() as cursor:
            query = "SELECT * FROM students WHERE 1=1"
            params = []
            if search:
                query += " AND (first_name LIKE %s OR last_name LIKE %s OR student_id LIKE %s OR reference_number LIKE %s)"
                params.extend([f"%{search}%"] * 4)
            if status:
                query += " AND enrollment_status = %s"
                params.append(status)
            query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
            params.extend([limit, offset])
            cursor.execute(query, params)
            return [self._row_to_student(row) for row in cursor.fetchall()]

    def count(self, search: str = "", status: str = "") -> int:
        with get_cursor() as cursor:
            query = "SELECT COUNT(*) as count FROM students WHERE 1=1"
            params = []
            if search:
                query += " AND (first_name LIKE %s OR last_name LIKE %s OR student_id LIKE %s OR reference_number LIKE %s)"
                params.extend([f"%{search}%"] * 4)
            if status:
                query += " AND enrollment_status = %s"
                params.append(status)
            cursor.execute(query, params)
            return cursor.fetchone()["count"]

    def update(self, student: Student) -> bool:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE students SET
                    student_id = %s, reference_number = %s, first_name = %s, middle_name = %s,
                    last_name = %s, suffix = %s, program = %s, year_level = %s, section = %s,
                    date_of_birth = %s, gender = %s, address = %s, profile_image_path = %s,
                    enrollment_status = %s, date_enrolled = %s, barcode_payload = %s, updated_by = %s
                WHERE id = %s
                """,
                (
                    student.student_id,
                    student.reference_number,
                    student.first_name,
                    student.middle_name,
                    student.last_name,
                    student.suffix,
                    student.program,
                    student.year_level.value if isinstance(student.year_level, YearLevel) else student.year_level,
                    student.section,
                    student.date_of_birth,
                    student.gender.value if isinstance(student.gender, Gender) else student.gender,
                    student.address,
                    student.profile_image_path,
                    student.enrollment_status.value if isinstance(student.enrollment_status, EnrollmentStatus) else student.enrollment_status,
                    student.date_enrolled,
                    student.barcode_payload,
                    student.updated_by,
                    student.id,
                ),
            )
            return cursor.rowcount > 0

    def delete(self, student_id: int) -> bool:
        with get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM students WHERE id = %s", (student_id,))
            return cursor.rowcount > 0

    def _row_to_student(self, row: dict) -> Student:
        contacts = self._get_contacts(row["id"])
        return Student(
            id=row["id"],
            student_id=row["student_id"],
            reference_number=row["reference_number"],
            first_name=row["first_name"],
            middle_name=row["middle_name"],
            last_name=row["last_name"],
            suffix=row["suffix"],
            program=row["program"],
            year_level=YearLevel(row["year_level"]) if row["year_level"] else YearLevel.FIRST_YEAR,
            section=row["section"],
            date_of_birth=row["date_of_birth"],
            gender=Gender(row["gender"]) if row["gender"] else None,
            address=row["address"],
            profile_image_path=row["profile_image_path"],
            enrollment_status=EnrollmentStatus(row["enrollment_status"]),
            date_enrolled=row["date_enrolled"],
            barcode_payload=row["barcode_payload"],
            created_by=row["created_by"],
            updated_by=row["updated_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            contacts=contacts,
        )

    def _get_contacts(self, student_id: int) -> List[StudentContact]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM student_contacts WHERE student_id = %s", (student_id,))
            return [
                StudentContact(
                    id=row["id"],
                    student_id=row["student_id"],
                    contact_type=ContactType(row["contact_type"]),
                    contact_value=row["contact_value"],
                    is_primary=row["is_primary"],
                    label=row["label"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in cursor.fetchall()
            ]


class CourseRepository:
    def create(self, course: Course) -> Optional[int]:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO courses (course_code, course_name, description, department, credits, is_active)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (course.course_code, course.course_name, course.description, course.department, course.credits, course.is_active),
            )
            return cursor.lastrowid

    def get_all(self, active_only: bool = True) -> List[Course]:
        with get_cursor() as cursor:
            query = "SELECT * FROM courses"
            if active_only:
                query += " WHERE is_active = TRUE"
            query += " ORDER BY course_code"
            cursor.execute(query)
            return [self._row_to_course(row) for row in cursor.fetchall()]

    def _row_to_course(self, row: dict) -> Course:
        return Course(
            id=row["id"],
            course_code=row["course_code"],
            course_name=row["course_name"],
            description=row["description"],
            department=row["department"],
            credits=row["credits"],
            is_active=row["is_active"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class SubjectRepository:
    def create(self, subject: Subject) -> Optional[int]:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO subjects (subject_code, subject_name, course_id, credits, lecture_hours, lab_hours, description, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (subject.subject_code, subject.subject_name, subject.course_id, subject.credits,
                 subject.lecture_hours, subject.lab_hours, subject.description, subject.is_active),
            )
            return cursor.lastrowid

    def get_by_course(self, course_id: int, active_only: bool = True) -> List[Subject]:
        with get_cursor() as cursor:
            query = "SELECT * FROM subjects WHERE course_id = %s"
            params = [course_id]
            if active_only:
                query += " AND is_active = TRUE"
            query += " ORDER BY subject_code"
            cursor.execute(query, params)
            return [self._row_to_subject(row) for row in cursor.fetchall()]

    def get_all(self, active_only: bool = True) -> List[Subject]:
        with get_cursor() as cursor:
            query = "SELECT s.*, c.course_code, c.course_name FROM subjects s JOIN courses c ON s.course_id = c.id"
            if active_only:
                query += " WHERE s.is_active = TRUE"
            query += " ORDER BY s.subject_code"
            cursor.execute(query)
            subjects = []
            for row in cursor.fetchall():
                subject = self._row_to_subject(row)
                subject.course = Course(
                    id=row["course_id"],
                    course_code=row["course_code"],
                    course_name=row["course_name"],
                )
                subjects.append(subject)
            return subjects

    def count(self, active_only: bool = True) -> int:
        """Number of subjects, for the dashboard tile.

        Counting in SQL instead of ``len(self.get_all())`` keeps the dashboard's
        figure correct on a large catalogue and costs one round trip instead of
        pulling every row into the UI process.
        """
        with get_cursor() as cursor:
            if active_only:
                cursor.execute("SELECT COUNT(*) AS count FROM subjects WHERE is_active = TRUE")
            else:
                cursor.execute("SELECT COUNT(*) AS count FROM subjects")
            return cursor.fetchone()["count"]

    def _row_to_subject(self, row: dict) -> Subject:
        return Subject(
            id=row["id"],
            subject_code=row["subject_code"],
            subject_name=row["subject_name"],
            course_id=row["course_id"],
            credits=row["credits"],
            lecture_hours=row["lecture_hours"],
            lab_hours=row["lab_hours"],
            description=row["description"],
            is_active=row["is_active"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class EnrollmentRepository:
    def create(self, enrollment: Enrollment) -> Optional[int]:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO enrollments (student_id, academic_year, semester, enrollment_date, status, total_credits, created_by, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    enrollment.student_id,
                    enrollment.academic_year,
                    enrollment.semester.value if isinstance(enrollment.semester, Semester) else enrollment.semester,
                    enrollment.enrollment_date,
                    enrollment.status.value if isinstance(enrollment.status, EnrollmentStatusEnum) else enrollment.status,
                    enrollment.total_credits,
                    enrollment.created_by,
                    enrollment.updated_by,
                ),
            )
            return cursor.lastrowid

    def update(self, enrollment: Enrollment) -> bool:
        """Persist the mutable fields of an enrollment.

        ``student_id`` and ``created_by`` are deliberately absent: they identify
        the row and changing them would be a different record, not an edit of
        this one.
        """
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE enrollments
                SET academic_year = %s,
                    semester = %s,
                    enrollment_date = %s,
                    status = %s,
                    total_credits = %s,
                    updated_by = %s
                WHERE id = %s
                """,
                (
                    enrollment.academic_year,
                    enrollment.semester.value if isinstance(enrollment.semester, Semester) else enrollment.semester,
                    enrollment.enrollment_date,
                    enrollment.status.value if isinstance(enrollment.status, EnrollmentStatusEnum) else enrollment.status,
                    enrollment.total_credits,
                    enrollment.updated_by,
                    enrollment.id,
                ),
            )
            return cursor.rowcount > 0

    def get_by_student(self, student_id: int) -> List[Enrollment]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM enrollments WHERE student_id = %s ORDER BY academic_year DESC, semester DESC",
                (student_id,),
            )
            return [self._row_to_enrollment(row) for row in cursor.fetchall()]

    def count(self, status: str = "") -> int:
        """Number of enrollment records, optionally limited to one status.

        Used by the dashboard's "Active Enrollments" tile. ``status=""`` counts
        every term on file, which is a different number from the count of
        students whose profile is active -- the two must not be conflated.
        """
        with get_cursor() as cursor:
            if status:
                cursor.execute(
                    "SELECT COUNT(*) AS count FROM enrollments WHERE status = %s",
                    (status,),
                )
            else:
                cursor.execute("SELECT COUNT(*) AS count FROM enrollments")
            return cursor.fetchone()["count"]

    def get_by_id(self, enrollment_id: int) -> Optional[Enrollment]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM enrollments WHERE id = %s", (enrollment_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_enrollment(row)

    def _row_to_enrollment(self, row: dict) -> Enrollment:
        enrollment = Enrollment(
            id=row["id"],
            student_id=row["student_id"],
            academic_year=row["academic_year"],
            semester=Semester(row["semester"]),
            enrollment_date=row["enrollment_date"],
            status=EnrollmentStatusEnum(row["status"]),
            total_credits=row["total_credits"],
            created_by=row["created_by"],
            updated_by=row["updated_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        enrollment.subjects = self._get_enrollment_subjects(enrollment.id)
        return enrollment

    def _get_enrollment_subjects(self, enrollment_id: int) -> List[EnrollmentSubject]:
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT es.*, s.subject_code, s.subject_name, s.credits, s.course_id
                FROM enrollment_subjects es
                JOIN subjects s ON es.subject_id = s.id
                WHERE es.enrollment_id = %s
                ORDER BY s.subject_code
                """,
                (enrollment_id,),
            )
            subjects = []
            for row in cursor.fetchall():
                subject = Subject(
                    id=row["subject_id"],
                    subject_code=row["subject_code"],
                    subject_name=row["subject_name"],
                    course_id=row["course_id"],
                    credits=row["credits"],
                )
                es = EnrollmentSubject(
                    id=row["id"],
                    enrollment_id=row["enrollment_id"],
                    subject_id=row["subject_id"],
                    section=row["section"],
                    schedule_time=row["schedule_time"],
                    schedule_days=row["schedule_days"],
                    room=row["room"],
                    period=row["period"],
                    lab_fee=float(row["lab_fee"]) if row["lab_fee"] else None,
                    other_fee=float(row["other_fee"]) if row["other_fee"] else None,
                    grade=row["grade"],
                    status=SubjectStatus(row["status"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    subject=subject,
                )
                subjects.append(es)
            return subjects

    def add_subject(self, enrollment_subject: EnrollmentSubject) -> Optional[int]:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO enrollment_subjects (
                    enrollment_id, subject_id, section, schedule_time, schedule_days,
                    room, period, lab_fee, other_fee, grade, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    enrollment_subject.enrollment_id,
                    enrollment_subject.subject_id,
                    enrollment_subject.section,
                    enrollment_subject.schedule_time,
                    enrollment_subject.schedule_days,
                    enrollment_subject.room,
                    enrollment_subject.period,
                    enrollment_subject.lab_fee,
                    enrollment_subject.other_fee,
                    enrollment_subject.grade,
                    enrollment_subject.status.value if isinstance(enrollment_subject.status, SubjectStatus) else enrollment_subject.status,
                ),
            )
            return cursor.lastrowid

    def update_subject(self, enrollment_subject: EnrollmentSubject) -> bool:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE enrollment_subjects SET
                    section = %s, schedule_time = %s, schedule_days = %s, room = %s,
                    period = %s, lab_fee = %s, other_fee = %s, grade = %s, status = %s
                WHERE id = %s
                """,
                (
                    enrollment_subject.section,
                    enrollment_subject.schedule_time,
                    enrollment_subject.schedule_days,
                    enrollment_subject.room,
                    enrollment_subject.period,
                    enrollment_subject.lab_fee,
                    enrollment_subject.other_fee,
                    enrollment_subject.grade,
                    enrollment_subject.status.value if isinstance(enrollment_subject.status, SubjectStatus) else enrollment_subject.status,
                    enrollment_subject.id,
                ),
            )
            return cursor.rowcount > 0

    def get_enrollment_id_for_subject(self, enrollment_subject_id: int) -> Optional[int]:
        """Which enrollment a subject row belongs to, for post-write cleanup."""
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT enrollment_id FROM enrollment_subjects WHERE id = %s",
                (enrollment_subject_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return row["enrollment_id"] if isinstance(row, dict) else row[0]

    def remove_subject(self, enrollment_subject_id: int) -> bool:
        with get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM enrollment_subjects WHERE id = %s", (enrollment_subject_id,))
            return cursor.rowcount > 0