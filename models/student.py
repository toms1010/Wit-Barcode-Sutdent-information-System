from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, List
from enum import Enum


class EnrollmentStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    GRADUATED = "graduated"
    DROPPED = "dropped"
    ON_LEAVE = "on_leave"


class Gender(str, Enum):
    MALE = "Male"
    FEMALE = "Female"
    OTHER = "Other"


class YearLevel(str, Enum):
    FIRST_YEAR = "1st Year"
    SECOND_YEAR = "2nd Year"
    THIRD_YEAR = "3rd Year"
    FOURTH_YEAR = "4th Year"
    FIFTH_YEAR = "5th Year"


class ContactType(str, Enum):
    PHONE = "phone"
    EMAIL = "email"
    EMERGENCY_PHONE = "emergency_phone"
    EMERGENCY_EMAIL = "emergency_email"
    GUARDIAN_PHONE = "guardian_phone"
    GUARDIAN_EMAIL = "guardian_email"


@dataclass
class StudentContact:
    id: Optional[int] = None
    student_id: int = 0
    contact_type: ContactType = ContactType.PHONE
    contact_value: str = ""
    is_primary: bool = False
    label: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class Student:
    id: Optional[int] = None
    student_id: str = ""
    reference_number: str = ""
    first_name: str = ""
    middle_name: Optional[str] = None
    last_name: str = ""
    suffix: Optional[str] = None
    program: str = ""
    year_level: YearLevel = YearLevel.FIRST_YEAR
    section: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[Gender] = None
    address: Optional[str] = None
    profile_image_path: Optional[str] = None
    enrollment_status: EnrollmentStatus = EnrollmentStatus.ACTIVE
    date_enrolled: date = field(default_factory=date.today)
    barcode_payload: Optional[str] = None
    created_by: Optional[int] = None
    updated_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    contacts: List[StudentContact] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        parts = [self.first_name]
        if self.middle_name:
            parts.append(self.middle_name)
        parts.append(self.last_name)
        if self.suffix:
            parts.append(self.suffix)
        return " ".join(parts)

    @property
    def display_name(self) -> str:
        return f"{self.last_name}, {self.first_name}" + (f" {self.middle_name[0]}." if self.middle_name else "")

    @property
    def age(self) -> Optional[int]:
        if self.date_of_birth:
            today = date.today()
            return today.year - self.date_of_birth.year - (
                (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
            )
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "student_id": self.student_id,
            "reference_number": self.reference_number,
            "full_name": self.full_name,
            "display_name": self.display_name,
            "first_name": self.first_name,
            "middle_name": self.middle_name,
            "last_name": self.last_name,
            "suffix": self.suffix,
            "program": self.program,
            "year_level": self.year_level.value if isinstance(self.year_level, YearLevel) else self.year_level,
            "section": self.section,
            "date_of_birth": self.date_of_birth.isoformat() if self.date_of_birth else None,
            "age": self.age,
            "gender": self.gender.value if isinstance(self.gender, Gender) else self.gender,
            "address": self.address,
            "profile_image_path": self.profile_image_path,
            "enrollment_status": self.enrollment_status.value if isinstance(self.enrollment_status, EnrollmentStatus) else self.enrollment_status,
            "date_enrolled": self.date_enrolled.isoformat() if self.date_enrolled else None,
            "barcode_payload": self.barcode_payload,
            "contacts": [c.__dict__ for c in self.contacts],
        }