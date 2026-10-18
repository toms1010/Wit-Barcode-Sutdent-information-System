from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, List
from enum import Enum


class Semester(str, Enum):
    FIRST = "1st Semester"
    SECOND = "2nd Semester"
    SUMMER = "Summer"
    MIDYEAR = "Midyear"


class EnrollmentStatus(str, Enum):
    ENROLLED = "enrolled"
    DROPPED = "dropped"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SubjectStatus(str, Enum):
    ENROLLED = "enrolled"
    DROPPED = "dropped"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Course:
    id: Optional[int] = None
    course_code: str = ""
    course_name: str = ""
    description: Optional[str] = None
    department: Optional[str] = None
    credits: int = 3
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class Subject:
    id: Optional[int] = None
    subject_code: str = ""
    subject_name: str = ""
    course_id: int = 0
    credits: int = 3
    lecture_hours: int = 0
    lab_hours: int = 0
    description: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    course: Optional[Course] = None


@dataclass
class EnrollmentSubject:
    id: Optional[int] = None
    enrollment_id: int = 0
    subject_id: int = 0
    section: Optional[str] = None
    schedule_time: Optional[str] = None
    schedule_days: Optional[str] = None
    room: Optional[str] = None
    period: Optional[int] = None
    lab_fee: Optional[float] = None
    other_fee: Optional[float] = None
    grade: Optional[str] = None
    status: SubjectStatus = SubjectStatus.ENROLLED
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    subject: Optional[Subject] = None


@dataclass
class Enrollment:
    id: Optional[int] = None
    student_id: int = 0
    academic_year: str = ""
    semester: Semester = Semester.FIRST
    enrollment_date: date = field(default_factory=date.today)
    status: EnrollmentStatus = EnrollmentStatus.ENROLLED
    total_credits: int = 0
    created_by: Optional[int] = None
    updated_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    subjects: List[EnrollmentSubject] = field(default_factory=list)

    def calculate_total_credits(self) -> int:
        return sum(es.subject.credits for es in self.subjects if es.subject and es.status == SubjectStatus.ENROLLED)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "student_id": self.student_id,
            "academic_year": self.academic_year,
            "semester": self.semester.value if isinstance(self.semester, Semester) else self.semester,
            "enrollment_date": self.enrollment_date.isoformat() if self.enrollment_date else None,
            "status": self.status.value if isinstance(self.status, EnrollmentStatus) else self.status,
            "total_credits": self.total_credits,
            "subjects": [
                {
                    "id": es.id,
                    "subject_id": es.subject_id,
                    "subject_code": es.subject.subject_code if es.subject else None,
                    "subject_name": es.subject.subject_name if es.subject else None,
                    "credits": es.subject.credits if es.subject else None,
                    "section": es.section,
                    "schedule_time": es.schedule_time,
                    "schedule_days": es.schedule_days,
                    "room": es.room,
                    "period": es.period,
                    "lab_fee": es.lab_fee,
                    "other_fee": es.other_fee,
                    "grade": es.grade,
                    "status": es.status.value if isinstance(es.status, SubjectStatus) else es.status,
                }
                for es in self.subjects
            ],
        }