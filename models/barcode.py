from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class BarcodeType(str, Enum):
    CODE128 = "CODE128"
    CODE39 = "CODE39"
    QR_CODE = "QR_CODE"
    EAN13 = "EAN13"


class ScanStatus(str, Enum):
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    INVALID = "invalid"
    DUPLICATE = "duplicate"
    ERROR = "error"


@dataclass
class Barcode:
    id: Optional[int] = None
    student_id: int = 0
    barcode_value: str = ""
    barcode_type: BarcodeType = BarcodeType.CODE128
    image_path: Optional[str] = None
    is_active: bool = True
    generated_by: Optional[int] = None
    regenerated_count: int = 0
    last_regenerated_at: Optional[datetime] = None
    regenerated_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class BarcodeScan:
    """One recorded scan.

    ``student_id`` is always the internal ``students.id``. The remaining fields
    are read-only extras filled in by the ``LEFT JOIN`` in
    :meth:`BarcodeScanRepository.get_recent` / ``get_by_student``; they let a
    history list label a scan without a second query per row. They stay ``None``
    when the query did not join the student (or the row was written by hand), and
    callers must treat ``None`` as "unknown", never as an empty student.
    """

    id: Optional[int] = None
    barcode_id: Optional[int] = None
    scanned_value: str = ""
    student_id: Optional[int] = None
    scan_status: ScanStatus = ScanStatus.ERROR
    confidence_score: Optional[float] = None
    detection_bbox: Optional[dict] = None
    camera_index: Optional[int] = None
    scanned_by: Optional[int] = None
    scanned_at: Optional[datetime] = None
    student_code: Optional[str] = None
    student_name: Optional[str] = None
    student_reference: Optional[str] = None

    @property
    def has_student(self) -> bool:
        """True when the scan is tied to a student row the join resolved."""
        return bool(self.student_id) and bool(self.student_name)