from typing import Optional, List
from database.connection import get_cursor
from models.barcode import Barcode, BarcodeScan, BarcodeType, ScanStatus
from models.yolo import YOLOModel, DatasetImage, YOLOModelStatus, YOLOModelType, DatasetSplit
import logging

logger = logging.getLogger(__name__)


class BarcodeRepository:
    def create(self, barcode: Barcode) -> Optional[int]:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO barcodes (student_id, barcode_value, barcode_type, image_path, is_active, generated_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    barcode.student_id,
                    barcode.barcode_value,
                    barcode.barcode_type.value if isinstance(barcode.barcode_type, BarcodeType) else barcode.barcode_type,
                    barcode.image_path,
                    barcode.is_active,
                    barcode.generated_by,
                ),
            )
            return cursor.lastrowid

    def get_by_id(self, barcode_id: int) -> Optional[Barcode]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM barcodes WHERE id = %s", (barcode_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_barcode(row)
    
    def get_by_value(self, barcode_value: str) -> Optional[Barcode]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM barcodes WHERE barcode_value = %s", (barcode_value,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_barcode(row)
    
    def get_by_student(self, student_id: int) -> Optional[Barcode]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM barcodes WHERE student_id = %s AND is_active = TRUE ORDER BY created_at DESC LIMIT 1",
                (student_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_barcode(row)
    
    def get_all_active(self, limit: int = 100, offset: int = 0) -> List[Barcode]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM barcodes WHERE is_active = TRUE ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
            return [self._row_to_barcode(row) for row in cursor.fetchall()]
    
    def deactivate(self, barcode_id: int) -> bool:
        with get_cursor(commit=True) as cursor:
            cursor.execute("UPDATE barcodes SET is_active = FALSE WHERE id = %s", (barcode_id,))
            return cursor.rowcount > 0
    
    def regenerate(self, barcode_id: int, new_value: str, new_image_path: str, regenerated_by: int) -> bool:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                UPDATE barcodes SET
                    barcode_value = %s, image_path = %s, regenerated_count = regenerated_count + 1,
                    last_regenerated_at = CURRENT_TIMESTAMP, regenerated_by = %s
                WHERE id = %s
                """,
                (new_value, new_image_path, regenerated_by, barcode_id),
            )
            return cursor.rowcount > 0

    def count(self, active_only: bool = True) -> int:
        """Number of barcode records, for the dashboard tile."""
        with get_cursor() as cursor:
            if active_only:
                cursor.execute("SELECT COUNT(*) AS count FROM barcodes WHERE is_active = TRUE")
            else:
                cursor.execute("SELECT COUNT(*) AS count FROM barcodes")
            return cursor.fetchone()["count"]

    def count_scans(self, status: str = "", since: Optional[datetime] = None) -> int:
        """Number of recorded scans, optionally filtered by status and time."""
        clauses: List[str] = []
        params: List = []
        if status:
            clauses.append("scan_status = %s")
            params.append(status)
        if since is not None:
            clauses.append("scanned_at >= %s")
            params.append(since)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS count FROM barcode_scans{where}", params)
            return cursor.fetchone()["count"]

    def _row_to_barcode(self, row: dict) -> Barcode:
        return Barcode(
            id=row["id"],
            student_id=row["student_id"],
            barcode_value=row["barcode_value"],
            barcode_type=BarcodeType(row["barcode_type"]),
            image_path=row["image_path"],
            is_active=row["is_active"],
            generated_by=row["generated_by"],
            regenerated_count=row["regenerated_count"],
            last_regenerated_at=row["last_regenerated_at"],
            regenerated_by=row["regenerated_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class BarcodeScanRepository:
    def create(self, scan: BarcodeScan) -> Optional[int]:
        with get_cursor(commit=True) as cursor:
            import json
            cursor.execute(
                """
                INSERT INTO barcode_scans (
                    barcode_id, scanned_value, student_id, scan_status, confidence_score,
                    detection_bbox, camera_index, scanned_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    scan.barcode_id,
                    scan.scanned_value,
                    scan.student_id,
                    scan.scan_status.value if isinstance(scan.scan_status, ScanStatus) else scan.scan_status,
                    scan.confidence_score,
                    json.dumps(scan.detection_bbox) if scan.detection_bbox else None,
                    scan.camera_index,
                    scan.scanned_by,
                ),
            )
            return cursor.lastrowid

    def get_recent(self, limit: int = 50) -> List[BarcodeScan]:
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT bs.*,
                       b.barcode_value AS linked_barcode_value,
                       s.student_id AS student_code,
                       s.first_name AS student_first_name,
                       s.middle_name AS student_middle_name,
                       s.last_name AS student_last_name,
                       s.reference_number AS student_reference
                FROM barcode_scans bs
                LEFT JOIN barcodes b ON bs.barcode_id = b.id
                LEFT JOIN students s ON bs.student_id = s.id
                ORDER BY bs.scanned_at DESC LIMIT %s
                """,
                (limit,),
            )
            return [self._row_to_scan(row) for row in cursor.fetchall()]

    def get_by_student(self, student_id: int, limit: int = 20) -> List[BarcodeScan]:
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT bs.*,
                       b.barcode_value AS linked_barcode_value,
                       s.student_id AS student_code,
                       s.first_name AS student_first_name,
                       s.middle_name AS student_middle_name,
                       s.last_name AS student_last_name,
                       s.reference_number AS student_reference
                FROM barcode_scans bs
                LEFT JOIN barcodes b ON bs.barcode_id = b.id
                LEFT JOIN students s ON bs.student_id = s.id
                WHERE bs.student_id = %s
                ORDER BY bs.scanned_at DESC LIMIT %s
                """,
                (student_id, limit),
            )
            return [self._row_to_scan(row) for row in cursor.fetchall()]

    def check_duplicate(self, scanned_value: str, suppression_seconds: int = 5) -> bool:
        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM barcode_scans
                WHERE scanned_value = %s
                AND scan_status = 'success'
                AND scanned_at >= DATE_SUB(NOW(), INTERVAL %s SECOND)
                LIMIT 1
                """,
                (scanned_value, suppression_seconds),
            )
            return cursor.fetchone() is not None

    def _row_to_scan(self, row: dict) -> BarcodeScan:
        import json

        # The joined columns are aliased (``student_code`` etc.) precisely so the
        # ``bs.*`` projection keeps its own ``student_id``. Selecting ``s.student_id``
        # unaliased overwrote the internal key with the student's code, so every
        # lookup by id failed and history rows were labelled "Unknown".
        name = " ".join(
            part
            for part in (
                row.get("student_first_name"),
                row.get("student_middle_name"),
                row.get("student_last_name"),
            )
            if part
        ).strip()
        return BarcodeScan(
            id=row["id"],
            barcode_id=row["barcode_id"],
            scanned_value=row["scanned_value"],
            student_id=row["student_id"],
            scan_status=ScanStatus(row["scan_status"]),
            confidence_score=float(row["confidence_score"]) if row["confidence_score"] else None,
            detection_bbox=json.loads(row["detection_bbox"]) if row["detection_bbox"] else None,
            camera_index=row["camera_index"],
            scanned_by=row["scanned_by"],
            scanned_at=row["scanned_at"],
            student_code=row.get("student_code"),
            student_name=name or None,
            student_reference=row.get("student_reference"),
        )


class YOLOModelRepository:
    def create(self, model: YOLOModel) -> Optional[int]:
        with get_cursor(commit=True) as cursor:
            import json
            cursor.execute(
                """
                INSERT INTO yolo_models (model_name, version, file_path, model_type, classes, training_config, metrics, status, trained_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    model.model_name,
                    model.version,
                    model.file_path,
                    model.model_type.value if isinstance(model.model_type, YOLOModelType) else model.model_type,
                    json.dumps(model.classes),
                    json.dumps(model.training_config) if model.training_config else None,
                    json.dumps(model.metrics) if model.metrics else None,
                    model.status.value if isinstance(model.status, YOLOModelStatus) else model.status,
                    model.trained_by,
                ),
            )
            return cursor.lastrowid

    def get_active(self, model_type: YOLOModelType = YOLOModelType.DETECTION) -> Optional[YOLOModel]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM yolo_models WHERE status = 'active' AND model_type = %s ORDER BY activated_at DESC LIMIT 1",
                (model_type.value if isinstance(model_type, YOLOModelType) else model_type,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_model(row)

    def get_all(self) -> List[YOLOModel]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM yolo_models ORDER BY created_at DESC")
            return [self._row_to_model(row) for row in cursor.fetchall()]

    def update_status(self, model_id: int, status: YOLOModelStatus, activated_by: int = None) -> bool:
        with get_cursor(commit=True) as cursor:
            if status == YOLOModelStatus.ACTIVE:
                cursor.execute(
                    "UPDATE yolo_models SET status = 'archived' WHERE status = 'active' AND model_type = (SELECT model_type FROM yolo_models WHERE id = %s)",
                    (model_id,),
                )
                cursor.execute(
                    "UPDATE yolo_models SET status = %s, activated_by = %s, activated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (status.value, activated_by, model_id),
                )
            else:
                cursor.execute("UPDATE yolo_models SET status = %s WHERE id = %s", (status.value, model_id))
            return cursor.rowcount > 0

    def update_metrics(self, model_id: int, metrics: dict) -> bool:
        with get_cursor(commit=True) as cursor:
            import json
            cursor.execute(
                "UPDATE yolo_models SET metrics = %s WHERE id = %s",
                (json.dumps(metrics), model_id),
            )
            return cursor.rowcount > 0

    def _row_to_model(self, row: dict) -> YOLOModel:
        import json
        return YOLOModel(
            id=row["id"],
            model_name=row["model_name"],
            version=row["version"],
            file_path=row["file_path"],
            model_type=YOLOModelType(row["model_type"]),
            classes=json.loads(row["classes"]) if row["classes"] else [],
            training_config=json.loads(row["training_config"]) if row["training_config"] else None,
            metrics=json.loads(row["metrics"]) if row["metrics"] else None,
            status=YOLOModelStatus(row["status"]),
            trained_by=row["trained_by"],
            activated_by=row["activated_by"],
            activated_at=row["activated_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class DatasetImageRepository:
    def create(self, image: DatasetImage) -> Optional[int]:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO dataset_images (file_path, file_name, width, height, file_size, split, has_annotations, annotation_file, is_validated, validated_by, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    image.file_path,
                    image.file_name,
                    image.width,
                    image.height,
                    image.file_size,
                    image.split.value if isinstance(image.split, DatasetSplit) else image.split,
                    image.has_annotations,
                    image.annotation_file,
                    image.is_validated,
                    image.validated_by,
                    image.notes,
                ),
            )
            return cursor.lastrowid

    def get_by_split(self, split: DatasetSplit) -> List[DatasetImage]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM dataset_images WHERE split = %s ORDER BY file_name", (split.value if isinstance(split, DatasetSplit) else split,))
            return [self._row_to_image(row) for row in cursor.fetchall()]

    def get_unvalidated(self, limit: int = 100) -> List[DatasetImage]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM dataset_images WHERE is_validated = FALSE ORDER BY created_at LIMIT %s", (limit,))
            return [self._row_to_image(row) for row in cursor.fetchall()]

    def mark_validated(self, image_id: int, validated_by: int, notes: str = None) -> bool:
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE dataset_images SET is_validated = TRUE, validated_by = %s, validated_at = CURRENT_TIMESTAMP, notes = %s WHERE id = %s",
                (validated_by, notes, image_id),
            )
            return cursor.rowcount > 0

    def _row_to_image(self, row: dict) -> DatasetImage:
        return DatasetImage(
            id=row["id"],
            file_path=row["file_path"],
            file_name=row["file_name"],
            width=row["width"],
            height=row["height"],
            file_size=row["file_size"],
            split=DatasetSplit(row["split"]),
            has_annotations=row["has_annotations"],
            annotation_file=row["annotation_file"],
            is_validated=row["is_validated"],
            validated_by=row["validated_by"],
            validated_at=row["validated_at"],
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )