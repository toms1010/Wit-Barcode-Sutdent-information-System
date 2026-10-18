from dataclasses import dataclass
from typing import Optional, List
from database.repositories import StudentRepository, CourseRepository, SubjectRepository, EnrollmentRepository
from models.student import Student, StudentContact
from models.enrollment import Enrollment, EnrollmentSubject, Subject, Course, Semester, EnrollmentStatus, SubjectStatus
import logging

logger = logging.getLogger(__name__)


class StudentService:
    def __init__(self):
        self.student_repo = StudentRepository()
        self.course_repo = CourseRepository()
        self.subject_repo = SubjectRepository()
        self.enrollment_repo = EnrollmentRepository()

    def create_student(self, student: Student) -> Optional[int]:
        try:
            student_id = self.student_repo.create(student)
            logger.info(f"Created student: {student.full_name} (ID: {student_id})")
            return student_id
        except Exception as e:
            logger.error(f"Failed to create student: {e}")
            raise

    def get_student(self, student_id: int) -> Optional[Student]:
        return self.student_repo.get_by_id(student_id)

    def get_student_by_student_id(self, student_id: str) -> Optional[Student]:
        return self.student_repo.get_by_student_id(student_id)

    def get_student_by_reference(self, reference_number: str) -> Optional[Student]:
        return self.student_repo.get_by_reference_number(reference_number)

    def get_student_by_barcode(self, barcode_payload: str) -> Optional[Student]:
        return self.student_repo.get_by_barcode_payload(barcode_payload)

    def search_students(self, query: str = "", status: str = "", limit: int = 50, offset: int = 0) -> List[Student]:
        return self.student_repo.get_all(limit=limit, offset=offset, search=query, status=status)

    def count_students(self, query: str = "", status: str = "") -> int:
        return self.student_repo.count(search=query, status=status)

    def update_student(self, student: Student) -> bool:
        try:
            result = self.student_repo.update(student)
            if result:
                logger.info(f"Updated student: {student.full_name} (ID: {student.id})")
            return result
        except Exception as e:
            logger.error(f"Failed to update student: {e}")
            raise

    def delete_student(self, student_id: int) -> bool:
        try:
            result = self.student_repo.delete(student_id)
            if result:
                logger.info(f"Deleted student ID: {student_id}")
            return result
        except Exception as e:
            logger.error(f"Failed to delete student: {e}")
            raise

    def get_courses(self) -> List[Course]:
        return self.course_repo.get_all()

    def get_subjects_by_course(self, course_id: int) -> List[Subject]:
        return self.subject_repo.get_by_course(course_id)

    def get_all_subjects(self) -> List[Subject]:
        return self.subject_repo.get_all()

    def count_subjects(self, active_only: bool = True) -> int:
        """Number of subjects on the catalogue (dashboard tile)."""
        return self.subject_repo.count(active_only=active_only)

    def count_enrollments(self, status: str = "") -> int:
        """Number of enrollment records, optionally for one status.

        ``status`` uses :class:`models.enrollment.EnrollmentStatus` values
        (``"enrolled"``, ``"dropped"`` ...), which are *not* the same vocabulary
        as a student's profile status: a profile can be ``active`` while its
        enrollment row is ``dropped``.
        """
        return self.enrollment_repo.count(status=status)

    def create_enrollment(self, enrollment: Enrollment) -> Optional[int]:
        try:
            enrollment_id = self.enrollment_repo.create(enrollment)
            logger.info(f"Created enrollment for student {enrollment.student_id} (Enrollment ID: {enrollment_id})")
            return enrollment_id
        except Exception as e:
            logger.error(f"Failed to create enrollment: {e}")
            raise

    def get_enrollments_by_student(self, student_id: int) -> List[Enrollment]:
        return self.enrollment_repo.get_by_student(student_id)

    def get_enrollment(self, enrollment_id: int) -> Optional[Enrollment]:
        return self.enrollment_repo.get_by_id(enrollment_id)

    def update_enrollment(self, enrollment: Enrollment) -> bool:
        """Persist an edited enrollment (term, date, status, credit total).

        Exposed as its own service call rather than letting the page reach into
        the repository, and separate from the credit refresh so an operator
        editing a term is not silently re-deriving its totals.
        """
        result = self.enrollment_repo.update(enrollment)
        if result:
            logger.info(
                "Updated enrollment %s for student %s (%s %s)",
                getattr(enrollment, "id", "?"),
                enrollment.student_id,
                enrollment.academic_year,
                getattr(enrollment.semester, "value", enrollment.semester),
            )
        return result

    def add_enrollment_subject(self, enrollment_subject: EnrollmentSubject) -> Optional[int]:
        """Attach a subject to an enrollment and refresh its credit total.

        The insert and the credit refresh are reported separately on purpose.
        The subject row is committed before the total is recalculated, so if the
        refresh fails the subject *is* enrolled; raising then would tell the
        caller the operation failed and invite a second, duplicate insert. The
        inconsistency is logged instead and the insert's id is returned.
        """
        try:
            es_id = self.enrollment_repo.add_subject(enrollment_subject)
        except Exception as e:
            logger.error(f"Failed to add enrollment subject: {e}")
            raise

        if not es_id:
            return None

        try:
            enrollment = self.enrollment_repo.get_by_id(enrollment_subject.enrollment_id)
            if enrollment:
                enrollment.total_credits = enrollment.calculate_total_credits()
                self.enrollment_repo.update(enrollment)
        except Exception as e:
            logger.error(
                f"Enrolled subject {enrollment_subject.subject_id} was added to "
                f"enrollment {enrollment_subject.enrollment_id} but the stored "
                f"credit total could not be refreshed: {e}"
            )
        return es_id

    def update_enrollment_subject(self, enrollment_subject: EnrollmentSubject) -> bool:
        return self.enrollment_repo.update_subject(enrollment_subject)

    def remove_enrollment_subject(self, enrollment_subject_id: int) -> bool:
        # Resolved before the delete: afterwards the row that says which
        # enrollment to refresh is gone.
        enrollment_id = self.enrollment_repo.get_enrollment_id_for_subject(
            enrollment_subject_id
        )
        removed = self.enrollment_repo.remove_subject(enrollment_subject_id)
        # The stored total is derived from the subject rows, so it has to be
        # recomputed after a removal or the enrollment keeps reporting units the
        # student is no longer taking.
        if removed and enrollment_id is not None:
            self._refresh_enrollment_credits(
                self.enrollment_repo.get_by_id(enrollment_id)
            )
        return removed

    def _refresh_enrollment_credits(self, enrollment) -> None:
        """Recompute and store an enrollment's credit total.

        Best effort by design: the subject row change has already been
        committed, so a failure here is an inconsistency to log, not a reason to
        tell the caller the removal failed.
        """
        if enrollment is None:
            return
        try:
            enrollment.total_credits = enrollment.calculate_total_credits()
            self.enrollment_repo.update(enrollment)
        except Exception as e:
            logger.error(
                f"Could not refresh the credit total for enrollment "
                f"{getattr(enrollment, 'id', '?')}: {e}"
            )

# ------------------------------------------------------- enrollment lookup --


@dataclass(frozen=True)
class EnrollmentLookup:
    """The result of resolving a reference to a certifiable enrollment.

    ``status`` is the point of this type: "no enrollment record" and "the
    database could not be reached" produce the same empty enrollment object, and
    an operator who is told the first will go looking for a record that is
    already there, or for a registrar to create one that should have been read.
    Every caller gets the distinction instead of inferring it.
    """

    status: str
    student: Optional[Student] = None
    enrollment: Optional[Enrollment] = None
    reference: str = ""
    #: How many enrolled subjects the record carries, for the message shown.
    subject_count: int = 0
    reason: str = ""
    error: str = ""
    #: The assessment fetched alongside the enrollment, so a caller that has just
    #: resolved a reference does not query for it a second time.
    assessment: Optional[object] = None

    @property
    def found(self) -> bool:
        return self.status == "found"

    @property
    def has_subjects(self) -> bool:
        return self.subject_count > 0


def get_enrollment_for_reference(
    reference_number,
    academic_year: Optional[str] = None,
    semester: Optional[str] = None,
) -> EnrollmentLookup:
    """Resolve a printed or scanned reference to that student's real enrollment.

    Accepts every form a reference arrives in -- the stored number, the
    zero-padded barcode payload, or a printed alias such as ``000001`` -- and
    returns the enrollment itself rather than a boolean, so the caller can say
    what is missing and why.

    No record is ever created here. A student with no enrollment is reported as
    having none; the fix belongs to the registrar, and a certificate generated
    from invented subjects would certify a term that did not happen.
    """
    from database.connection import DatabaseUnavailableError
    from services.identifiers import is_alias, matches_reference, normalise_reference, resolve_reference

    printed = normalise_reference(reference_number)
    resolved = resolve_reference(printed)
    if not resolved:
        return EnrollmentLookup(
            status="unknown_student",
            reference=printed,
            reason="No reference number was supplied.",
        )

    service = StudentService()
    try:
        student = service.get_student_by_reference(resolved)
        if student is None:
            # The exact lookup missed; the record may still be filed under the
            # padded payload or an alias the repository does not index.
            candidates = service.search_students(query=resolved, limit=50)
            student = next(
                (row for row in candidates if matches_reference(row, printed)), None
            )
    except DatabaseUnavailableError as exc:
        logger.warning("Enrollment lookup for %s failed: %s", resolved, exc)
        return EnrollmentLookup(
            status="database_unavailable",
            reference=printed,
            reason="The student records could not be read, so the enrollment is unknown.",
            error=str(exc),
        )

    if student is None:
        return EnrollmentLookup(
            status="unknown_student",
            reference=printed,
            reason=(
                f"No student on file has reference number {resolved}."
                + (f" ({printed} is an alias printed on an older panel.)" if is_alias(printed) else "")
            ),
        )

    try:
        enrollments = service.get_enrollments_by_student(student.id)
        assessment = _best_effort_assessment(student.id)
    except DatabaseUnavailableError as exc:
        logger.warning("Enrollment lookup for %s failed: %s", student.student_id, exc)
        return EnrollmentLookup(
            status="database_unavailable",
            student=student,
            reference=printed,
            reason="The enrollment records could not be read.",
            error=str(exc),
        )

    if not enrollments:
        return EnrollmentLookup(
            status="no_enrollment",
            student=student,
            reference=printed,
            reason=(
                f"{student.full_name} has no enrollment record on file. The registrar "
                "must record an enrollment before a Certificate of Matriculation can "
                "be produced."
            ),
        )

    enrollment = _select_enrollment(enrollments, academic_year, semester)
    subjects = [
        row
        for row in enrollment.subjects
        if getattr(row.status, "value", row.status) == SubjectStatus.ENROLLED.value
    ]

    if not subjects:
        return EnrollmentLookup(
            status="no_subjects",
            student=student,
            enrollment=enrollment,
            reference=printed,
            subject_count=0,
            reason=(
                f"{student.full_name} has an enrollment for "
                f"{enrollment.academic_year or 'no academic year'} "
                f"{getattr(enrollment.semester, 'value', enrollment.semester) or 'no semester'}"
                ", but no subjects are in 'enrolled' status in it. Nothing can be "
                "certified from that term."
            ),
        )

    return EnrollmentLookup(
        status="found",
        student=student,
        enrollment=enrollment,
        assessment=assessment,
        reference=printed,
        subject_count=len(subjects),
        reason=f"{len(subjects)} subject(s) in {enrollment.academic_year}.",
    )


def _best_effort_assessment(student_id: int):
    """The latest assessment, or ``None``.

    Optional on purpose: an enrollment is certifiable without one, so a failure
    here must not turn a successful enrollment lookup into an error.
    """
    from database.connection import DatabaseUnavailableError
    from services.assessment_service import AssessmentService

    try:
        return AssessmentService().get_latest_assessment(student_id)
    except DatabaseUnavailableError as exc:
        logger.info("No assessment available for student %s: %s", student_id, exc)
        return None


def _select_enrollment(
    enrollments: List[Enrollment],
    academic_year: Optional[str],
    semester: Optional[str],
) -> Enrollment:
    """Pick the requested term, or the most recent one on file.

    The repository already orders by academic year then semester descending, so
    "the latest" is the first row -- stated here rather than assumed, because
    the ordering is the whole basis for which term a certificate describes.
    """
    if not academic_year and not semester:
        return enrollments[0]

    wanted_semester = getattr(semester, "value", semester) if semester else None
    for enrollment in enrollments:
        if academic_year and enrollment.academic_year != academic_year:
            continue
        if wanted_semester and getattr(enrollment.semester, "value", enrollment.semester) != wanted_semester:
            continue
        return enrollment
    return enrollments[0]
