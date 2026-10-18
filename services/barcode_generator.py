import os
from pathlib import Path
from typing import Optional, List, Tuple
from barcode import Code128, Code39
from barcode.writer import ImageWriter
from PIL import Image
import logging
from config.settings import settings
from database.repositories import BarcodeRepository
from models.barcode import Barcode, BarcodeType
from models.student import Student
from services.student_service import StudentService

logger = logging.getLogger(__name__)


class BarcodeGeneratorService:
    def __init__(self):
        self.barcode_repo = BarcodeRepository()
        self.student_service = StudentService()
        self.output_dir = settings.BARCODE_OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_barcode_value(self, student: Student) -> str:
        """The payload to encode for ``student``.

        The project's physical ID cards are CODE39 and carry a zero-padded
        numeric payload, and ``students.barcode_payload`` already stores exactly
        that value (verified by decoding the card assets, not by guessing from a
        filename). That stored payload wins whenever it exists, so a newly
        printed card is byte-identical to the one already in a student's wallet
        and the scanner keeps resolving it.

        The ``BARCODE_PREFIX`` scheme is only a fallback for a student who has
        never had a payload assigned. Generating the prefix form for a student
        who already has a card would silently produce a second, different
        identity for the same person.
        """
        if getattr(student, "barcode_payload", None):
            return student.barcode_payload
        return f"{settings.BARCODE_PREFIX}{student.reference_number}"

    def generate_barcode_image(
        self,
        barcode_value: str,
        output_path: Path,
        barcode_type: BarcodeType = None,
        module_width: float = None,
        module_height: float = None,
        quiet_zone: float = 6,
        write_text: bool = True,
        text_distance: float = 5,
    ) -> bool:
        """Encode ``barcode_value`` as a barcode image.

        ``module_width``, ``module_height`` and ``quiet_zone`` are in
        **millimetres** and override the configured defaults. The card renderer
        needs them, because a nine-digit CODE39 payload is 175 module units wide:

        * At python-barcode's default 0.2 mm module the symbol comes out around
          440 px across. Fitting that into a 1011 px-wide 300 DPI CR80 export
          means scaling *up* roughly twofold, which softens the bar edges until
          zbar will not read them back off a printed card.
        * At 0.42 mm the symbol is about 915 px and lands on the card at native
          resolution. :meth:`services.id_card_service.IdCardService.verify_rendered_barcode`
          decodes the finished card to prove it before the card is written.

        The quiet zone defaults to 6 mm, which is what the existing standalone
        barcode images use. The card passes 2 mm: 6 mm on each side would make
        the symbol 1010 px wide, wider than the card it has to sit on.

        The options are passed to ``save(options=...)``, not to
        ``writer.set_options``. That is not a stylistic choice:
        ``Barcode.render`` rebuilds its option dict from
        ``default_writer_options`` and calls ``set_options`` on the writer
        again, so anything set on the writer instance beforehand is discarded.
        Every barcode this service produced before was rendered at
        python-barcode's defaults, and the configured
        ``BARCODE_MODULE_WIDTH``/``BARCODE_MODULE_HEIGHT`` had no effect on the
        output at all.
        """
        try:
            options = {
                "module_width": module_width or settings.BARCODE_MODULE_WIDTH,
                "module_height": module_height or settings.BARCODE_MODULE_HEIGHT,
                "quiet_zone": quiet_zone,
                "font_size": 10 if write_text else 0,
                "text_distance": text_distance,
                "background": "white",
                "foreground": "black",
                "write_text": write_text,
            }

            writer = ImageWriter()

            # CODE39 is what the supplied card assets decode as, so it is the
            # default for a student whose payload came from a real card.
            #
            # add_checksum must be False: python-barcode appends a mod-43 check
            # character by default, which turns the payload 000065412 into
            # 000065412I. The physical cards decode to the bare value, so a
            # generated card would otherwise encode a different identity and
            # the scanner would report "unknown barcode" for it.
            if barcode_type == BarcodeType.CODE39:
                barcode = Code39(barcode_value, writer=writer, add_checksum=False)
            else:
                barcode = Code128(barcode_value, writer=writer)
            barcode.save(str(output_path.with_suffix("")), options=options)
            return True
        except Exception as e:
            logger.error(f"Failed to generate barcode image: {e}")
            return False

    @staticmethod
    def barcode_type_for(student: Student) -> BarcodeType:
        """Symbology for this student: CODE39 when a real payload is assigned."""
        return (
            BarcodeType.CODE39
            if getattr(student, "barcode_payload", None)
            else BarcodeType.CODE128
        )

    def generate_for_student(self, student_id: int, generated_by: int = None) -> Optional[Barcode]:
        student = self.student_service.get_student(student_id)
        if not student:
            logger.error(f"Student not found: {student_id}")
            return None

        existing = self.barcode_repo.get_by_student(student_id)
        if existing:
            logger.warning(f"Student {student_id} already has an active barcode")
            return existing

        barcode_value = self.generate_barcode_value(student)
        
        existing_by_value = self.barcode_repo.get_by_value(barcode_value)
        if existing_by_value:
            logger.error(f"Barcode value already exists: {barcode_value}")
            return None

        output_filename = f"barcode_{student.reference_number}.png"
        output_path = self.output_dir / output_filename

        barcode_type = self.barcode_type_for(student)
        if not self.generate_barcode_image(barcode_value, output_path, barcode_type):
            return None

        barcode = Barcode(
            student_id=student_id,
            barcode_value=barcode_value,
            barcode_type=barcode_type,
            image_path=settings.store_path(output_path),
            is_active=True,
            generated_by=generated_by,
        )

        barcode_id = self.barcode_repo.create(barcode)
        if barcode_id:
            barcode.id = barcode_id
            logger.info(f"Generated barcode for student {student.full_name}: {barcode_value}")
            return barcode
        
        return None

    def generate_batch(self, student_ids: List[int], generated_by: int = None) -> List[Barcode]:
        results = []
        for student_id in student_ids:
            barcode = self.generate_for_student(student_id, generated_by)
            if barcode:
                results.append(barcode)
        return results

    def regenerate_barcode(self, barcode_id: int, regenerated_by: int) -> Optional[Barcode]:
        barcode = self.barcode_repo.get_by_id(barcode_id)
        if not barcode:
            return None

        student = self.student_service.get_student(barcode.student_id)
        if not student:
            return None

        new_value = self.generate_barcode_value(student)
        output_filename = f"barcode_{student.reference_number}_v{barcode.regenerated_count + 1}.png"
        output_path = self.output_dir / output_filename

        if not self.generate_barcode_image(new_value, output_path, self.barcode_type_for(student)):
            return None

        stored_path = settings.store_path(output_path)
        if self.barcode_repo.regenerate(barcode_id, new_value, stored_path, regenerated_by):
            barcode.barcode_value = new_value
            barcode.image_path = stored_path
            barcode.regenerated_count += 1
            logger.info(f"Regenerated barcode for student {student.full_name}: {new_value}")
            return barcode
        
        return None

    def count_barcodes(self, active_only: bool = True) -> int:
        """Number of barcode records (dashboard tile)."""
        return self.barcode_repo.count(active_only=active_only)

    def count_scans(self, status: str = "") -> int:
        """Number of recorded scans (dashboard tile)."""
        return self.barcode_repo.count_scans(status=status)

    def get_recent_scans(self, limit: int = 10):
        """Most recent scans with the student already joined in.

        The repository resolves the student's name in the same query, so a
        history list does not issue one extra lookup per row.
        """
        return self.barcode_repo.get_recent(limit=limit)

    def get_barcode(self, barcode_id: int) -> Optional[Barcode]:
        return self.barcode_repo.get_by_id(barcode_id)

    def get_student_barcode(self, student_id: int) -> Optional[Barcode]:
        return self.barcode_repo.get_by_student(student_id)

    def get_barcode_by_value(self, barcode_value: str) -> Optional[Barcode]:
        return self.barcode_repo.get_by_value(barcode_value)

    def print_barcode(self, barcode: Barcode) -> bool:
        try:
            if not barcode.image_path:
                return False
            
            image_path = settings.resolve_asset(barcode.image_path)
            if image_path is None:
                return False
            
            img = Image.open(image_path)
            img.show()
            return True
        except Exception as e:
            logger.error(f"Failed to print barcode: {e}")
            return False

    def export_barcode(self, barcode: Barcode, export_path: Path) -> bool:
        try:
            if not barcode.image_path:
                return False
            
            src_path = settings.resolve_asset(barcode.image_path)
            if src_path is None:
                return False
            
            import shutil
            shutil.copy2(src_path, export_path)
            return True
        except Exception as e:
            logger.error(f"Failed to export barcode: {e}")
            return False