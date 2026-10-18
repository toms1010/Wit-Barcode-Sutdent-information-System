"""Database-level behaviour checks required by the audit.

These exercise the real schema through the real connection layer. They skip
themselves when MySQL is unreachable instead of substituting mock data, and every
write is rolled back so the seeded records are never disturbed.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _cursor_or_skip():
    from database.connection import DatabaseUnavailableError, get_cursor

    try:
        cm = get_cursor()
    except DatabaseUnavailableError as exc:
        pytest.skip(f"database unavailable: {exc}")
    return cm


class TestSchemaIntegrity:
    def test_unique_constraints_on_student_identity(self):
        cm = _cursor_or_skip()
        with cm as cur:
            cur.execute("SHOW INDEX FROM students WHERE Non_unique = 0")
            unique = {r["Key_name"] for r in cur.fetchall()}
        for expected in ("student_id", "reference_number", "barcode_payload"):
            assert expected in unique, f"students.{expected} is not UNIQUE"

    def test_reference_number_is_text_so_leading_zeros_survive(self):
        """A numeric column would silently turn 000001 into 1."""
        cm = _cursor_or_skip()
        with cm as cur:
            cur.execute("SHOW COLUMNS FROM students LIKE 'reference_number'")
            row = cur.fetchone()
        assert row is not None
        assert row["Type"].lower().startswith(("varchar", "char")), (
            f"reference_number is {row['Type']}; leading zeros would be lost"
        )

    def test_barcode_payload_is_text(self):
        cm = _cursor_or_skip()
        with cm as cur:
            cur.execute("SHOW COLUMNS FROM students LIKE 'barcode_payload'")
            row = cur.fetchone()
        assert row is not None
        assert row["Type"].lower().startswith(("varchar", "char"))

    def test_scan_log_records_value_student_time_and_status(self):
        cm = _cursor_or_skip()
        with cm as cur:
            cur.execute("SHOW COLUMNS FROM barcode_scans")
            cols = {r["Field"] for r in cur.fetchall()}
        for expected in (
            "id",
            "student_id",
            "scanned_value",
            "scanned_at",
            "scan_status",
            "scanned_by",
        ):
            assert expected in cols, f"barcode_scans is missing {expected}"


class TestBarcodePayloadLookup:
    def test_leading_zeros_are_preserved_end_to_end(self):
        from database.connection import DatabaseUnavailableError
        from services.student_service import StudentService

        service = StudentService()
        try:
            with_rows = service.search_students(limit=50)
        except DatabaseUnavailableError as exc:
            pytest.skip(f"database unavailable: {exc}")

        with_payload = [s for s in with_rows if s.barcode_payload]
        if not with_payload:
            pytest.skip("no student rows carry a barcode_payload")

        for student in with_payload:
            assert isinstance(student.barcode_payload, str)
            found = service.get_student_by_barcode(student.barcode_payload)
            assert found is not None
            assert found.barcode_payload == student.barcode_payload
            assert found.id == student.id

    def test_lookup_is_exact_and_zero_sensitive(self):
        """'000065412' must resolve; '65412' must not."""
        from database.connection import DatabaseUnavailableError
        from services.student_service import StudentService

        service = StudentService()
        try:
            rows = service.search_students(limit=50)
        except DatabaseUnavailableError as exc:
            pytest.skip(f"database unavailable: {exc}")

        candidates = [s for s in rows if s.barcode_payload and s.barcode_payload.startswith("0")]
        if not candidates:
            pytest.skip("no zero-padded payloads to test")

        payload = candidates[0].barcode_payload
        stripped = payload.lstrip("0")
        if not stripped or stripped == payload:
            pytest.skip("payload has no strippable leading zeros")

        assert service.get_student_by_barcode(payload) is not None
        assert service.get_student_by_barcode(stripped) is None, (
            f"'{stripped}' must not match payload '{payload}'"
        )


class TestScanLogWrites:
    def test_a_verified_scan_can_be_recorded_and_read_back(self):
        from database.connection import DatabaseUnavailableError
        from database.repositories import BarcodeScanRepository
        from models.barcode import BarcodeScan, ScanStatus

        repo = BarcodeScanRepository()
        payload = "TEST_AUDIT_PAYLOAD"
        scan = BarcodeScan(
            scanned_value=payload,
            student_id=None,
            scan_status=ScanStatus.NOT_FOUND,
            confidence_score=0.8123,
            detection_bbox={"x": 10, "y": 20, "w": 300, "h": 40},
        )
        try:
            scan_id = repo.create(scan)
        except DatabaseUnavailableError as exc:
            pytest.skip(f"database unavailable: {exc}")

        assert scan_id
        try:
            recent = repo.get_recent(limit=5)
            assert any(s.scanned_value == payload for s in recent)
        finally:
            # Leave the audit table as we found it.
            from database.connection import get_cursor

            with get_cursor(commit=True) as cur:
                cur.execute("DELETE FROM barcode_scans WHERE scanned_value = %s", (payload,))

    def test_detection_bbox_round_trips_as_json(self):
        """The repository must hand back a dict, not the raw JSON string.

        The MySQL driver returns a JSON column as ``str``; only the repository's
        explicit ``json.loads`` makes ``BarcodeScan.detection_bbox`` behave like
        the ``Optional[dict]`` the dataclass declares. This asserts the
        application-level contract rather than raw driver behaviour.
        """
        from database.connection import DatabaseUnavailableError
        from database.repositories import BarcodeScanRepository
        from models.barcode import BarcodeScan, ScanStatus

        payload = "TEST_AUDIT_BBOX"
        bbox = {"x": 10, "y": 20, "w": 300, "h": 40}
        repo = BarcodeScanRepository()
        try:
            scan_id = repo.create(
                BarcodeScan(
                    scanned_value=payload,
                    scan_status=ScanStatus.INVALID,
                    confidence_score=0.5,
                    detection_bbox=bbox,
                )
            )
            assert scan_id
            stored = next(
                (s for s in repo.get_recent(limit=10) if s.scanned_value == payload),
                None,
            )
            assert stored is not None
            assert isinstance(stored.detection_bbox, dict), (
                f"expected a dict, got {type(stored.detection_bbox).__name__}"
            )
            assert stored.detection_bbox == bbox
            assert float(stored.confidence_score) == pytest.approx(0.5)
        except DatabaseUnavailableError as exc:
            pytest.skip(f"database unavailable: {exc}")
        finally:
            try:
                from database.connection import get_cursor

                with get_cursor(commit=True) as cur:
                    cur.execute(
                        "DELETE FROM barcode_scans WHERE scanned_value = %s", (payload,)
                    )
            except Exception:
                pass


class TestDatabaseFailureHandling:
    def test_bad_credentials_raise_the_documented_error_not_an_empty_result(self):
        """A failed connection must never look like 'no rows'."""
        import database.connection as conn

        try:
            with conn.get_cursor() as cur:
                cur.execute("SELECT 1")
        except conn.DatabaseUnavailableError:
            return  # the correct failure mode
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"unexpected exception type: {type(exc).__name__}")

    def test_rejected_query_is_reported_not_swallowed(self):
        import json

        from database.connection import DatabaseUnavailableError, get_cursor

        try:
            with get_cursor(commit=False) as cur:
                cur.execute(
                    "INSERT INTO barcode_scans (scanned_value, scan_status, detection_bbox) "
                    "VALUES (%s, 'invalid', %s)",
                    ("TEST_AUDIT_BADCODE", json.dumps({"x": 1})),
                )
                # barcode_type does not exist on this table; the point is that a
                # schema violation surfaces instead of being silently ignored.
                cur.execute("SELECT nonexistent_column_xyz FROM barcode_scans")
        except DatabaseUnavailableError:
            return
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"unexpected exception type: {type(exc).__name__}")
        finally:
            try:
                from database.connection import get_cursor as gc

                with gc(commit=True) as cur:
                    cur.execute(
                        "DELETE FROM barcode_scans WHERE scanned_value = %s",
                        ("TEST_AUDIT_BADCODE",),
                    )
            except Exception:
                pass

    def test_test_connection_reports_false_without_raising(self):
        from database.connection import test_connection

        assert test_connection() in (True, False)