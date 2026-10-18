"""Persistence for generated student ID cards.

Separate from :mod:`database.repositories.barcode_repository` on purpose. The
two record genuinely different things:

* ``barcodes`` says which identifier a student's card carries and where the
  barcode image lives. It is what the scanner resolves against.
* ``id_cards`` says *whether a card was rendered for this student under this
  template*, where the rendered files were written, and what happened the last
  time it was attempted.

Conflating them is what made the old page report a card status that was really a
barcode status -- a student with a barcode image but no card was shown as
generated, and a card that failed to render left no trace at all.

Every statement is parameterised. ``custom_fields`` is operator-entered
presentation text (an emergency contact not yet in ``student_contacts``, a
signature label, a validity note); it lives here and never touches
``students``, so editing a card can never overwrite a student record.
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from database.connection import get_cursor
from models.id_card import CardStatus, IdCard

logger = logging.getLogger(__name__)


class IdCardRepository:
    """CRUD for the ``id_cards`` table."""

    def get_by_student(self, student_id: int, template_key: str) -> Optional[IdCard]:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM id_cards WHERE student_id = %s AND template_key = %s LIMIT 1",
                (student_id, template_key),
            )
            return self._row_to_card(cursor.fetchone())

    def get_any_by_student(self, student_id: int) -> Optional[IdCard]:
        """The student's most recently touched card, under any template.

        Used to report a status when the operator is looking at a template the
        student has no card for yet: "no card under *this* template" and "no card
        at all" are different answers to "does this student have an ID card?".
        """
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM id_cards WHERE student_id = %s "
                "ORDER BY updated_at DESC LIMIT 1",
                (student_id,),
            )
            return self._row_to_card(cursor.fetchone())

    def status_map_for_students(
        self, student_ids: List[int], template_key: str
    ) -> Dict[int, IdCard]:
        """Cards for many students under one template, in a single query.

        The table populates a status column for every row; doing that with one
        lookup per row is an N+1 that turns a 300-student list into 300 round
        trips. ``student_id IN (...)`` is built from validated integers and the
        placeholders are generated from its length, so no value is ever
        interpolated into the SQL text.
        """
        ids = [int(value) for value in student_ids if value is not None]
        if not ids:
            return {}
        placeholders = ", ".join(["%s"] * len(ids))
        with get_cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM id_cards WHERE student_id IN ({placeholders}) "
                f"AND template_key = %s",
                (*ids, template_key),
            )
            rows = cursor.fetchall()
        return {
            row["student_id"]: self._row_to_card(row)
            for row in rows
            if row.get("student_id") is not None
        }

    def missing_card_student_ids(self, template_key: str) -> List[int]:
        """Student ids with no usable card under ``template_key``.

        "Missing" is defined as *no row, or a row whose latest attempt failed* --
        an ``error`` card is not a card. A row in ``pending`` or ``error`` is
        returned, so a failed card is retried by a batch run rather than being
        skipped forever.
        """
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT s.id FROM students s "
                "LEFT JOIN id_cards c "
                "  ON c.student_id = s.id AND c.template_key = %s "
                "WHERE c.id IS NULL OR c.status IN ('pending', 'error') "
                "ORDER BY s.id",
                (template_key,),
            )
            return [row["id"] for row in cursor.fetchall()]

    def count(self, template_key: Optional[str] = None) -> int:
        with get_cursor() as cursor:
            if template_key:
                cursor.execute(
                    "SELECT COUNT(*) AS total FROM id_cards WHERE template_key = %s",
                    (template_key,),
                )
            else:
                cursor.execute("SELECT COUNT(*) AS total FROM id_cards")
            return int(cursor.fetchone()["total"])

    def count_by_status(self, template_key: Optional[str] = None) -> Dict[str, int]:
        with get_cursor() as cursor:
            query = "SELECT status, COUNT(*) AS total FROM id_cards"
            params: tuple = ()
            if template_key:
                query += " WHERE template_key = %s"
                params = (template_key,)
            query += " GROUP BY status"
            cursor.execute(query, params)
            return {
                row["status"]: int(row["total"])
                for row in cursor.fetchall()
            }

    # -- writes ------------------------------------------------------------

    def upsert_generated(
        self,
        student_id: int,
        template_key: str,
        *,
        front_image_path: Optional[str] = None,
        back_image_path: Optional[str] = None,
        pdf_path: Optional[str] = None,
        custom_fields: Optional[Dict] = None,
        generated_by: Optional[int] = None,
    ) -> IdCard:
        """Record a successful render, replacing any earlier row for the pair.

        ``ON DUPLICATE KEY UPDATE`` is used because ``(student_id,
        template_key)`` is unique: a re-render updates the existing row rather
        than inserting a second card for the same person and template. Only the
        paths actually produced by this render are written, so exporting just
        the back side does not blank the front path.
        """
        payload = json.dumps(custom_fields, sort_keys=True) if custom_fields else None
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO id_cards (
                    student_id, template_key, front_image_path, back_image_path,
                    pdf_path, status, last_error, custom_fields, generated_by,
                    last_generated_at, render_count
                )
                VALUES (%s, %s, %s, %s, %s, 'generated', NULL, %s, %s,
                        CURRENT_TIMESTAMP, 1)
                ON DUPLICATE KEY UPDATE
                    front_image_path = COALESCE(VALUES(front_image_path), front_image_path),
                    back_image_path  = COALESCE(VALUES(back_image_path),  back_image_path),
                    pdf_path         = COALESCE(VALUES(pdf_path),         pdf_path),
                    status           = 'generated',
                    last_error       = NULL,
                    custom_fields    = COALESCE(VALUES(custom_fields), custom_fields),
                    generated_by     = COALESCE(VALUES(generated_by),  generated_by),
                    last_generated_at = CURRENT_TIMESTAMP,
                    render_count     = render_count + 1
                """,
                (
                    student_id,
                    template_key,
                    front_image_path,
                    back_image_path,
                    pdf_path,
                    payload,
                    generated_by,
                ),
            )
        return self.get_by_student(student_id, template_key)

    def record_error(
        self, student_id: int, template_key: str, message: str
    ) -> Optional[IdCard]:
        """Record a failed render.

        The row is created when it does not exist yet so the failure is visible
        on a student who has never had a card -- a first attempt that fails must
        not be indistinguishable from one that was never tried.
        """
        text = (message or "")[:500]
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO id_cards (student_id, template_key, status, last_error)
                VALUES (%s, %s, 'error', %s)
                ON DUPLICATE KEY UPDATE
                    status     = 'error',
                    last_error = VALUES(last_error)
                """,
                (student_id, template_key, text),
            )
        return self.get_by_student(student_id, template_key)

    def record_print_requested(
        self, student_id: int, template_key: str
    ) -> Optional[IdCard]:
        """Record that the operator accepted the print dialog.

        Named *print requested*, not *printed*, on purpose: this application has
        no way to confirm the job reached a printer, so the row asserts the one
        thing that did happen.
        """
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO id_cards (student_id, template_key, status,
                                      last_print_requested_at)
                VALUES (%s, %s, 'print_requested', CURRENT_TIMESTAMP)
                ON DUPLICATE KEY UPDATE
                    status                   = 'print_requested',
                    last_print_requested_at = CURRENT_TIMESTAMP
                """,
                (student_id, template_key),
            )
        return self.get_by_student(student_id, template_key)

    def update_paths(
        self,
        card_id: int,
        *,
        front_image_path: Optional[str] = None,
        back_image_path: Optional[str] = None,
        pdf_path: Optional[str] = None,
    ) -> bool:
        """Record where an export was written, without changing the status.

        Saving a PNG or exporting a PDF is not a re-render, so it must not reset
        an ``error`` recorded by a failed generation or inflate
        ``render_count``.
        """
        assignments: List[str] = []
        params: List = []
        if front_image_path is not None:
            assignments.append("front_image_path = %s")
            params.append(front_image_path)
        if back_image_path is not None:
            assignments.append("back_image_path = %s")
            params.append(back_image_path)
        if pdf_path is not None:
            assignments.append("pdf_path = %s")
            params.append(pdf_path)
        if not assignments:
            return True

        params.append(card_id)
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                f"UPDATE id_cards SET {', '.join(assignments)} WHERE id = %s",
                params,
            )
            return cursor.rowcount > 0

    def set_status(
        self,
        student_id: int,
        template_key: str,
        status: CardStatus,
        *,
        message: Optional[str] = None,
    ) -> Optional[IdCard]:
        value = status.value if isinstance(status, CardStatus) else str(status)
        with get_cursor(commit=True) as cursor:
            cursor.execute(
                """
                INSERT INTO id_cards (student_id, template_key, status, last_error)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    status     = VALUES(status),
                    last_error = VALUES(last_error)
                """,
                (student_id, template_key, value, (message or "")[:500] or None),
            )
        return self.get_by_student(student_id, template_key)

    def delete_by_student(self, student_id: int) -> int:
        """Drop a student's card records. Cascades from ``students`` too."""
        with get_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM id_cards WHERE student_id = %s", (student_id,))
            return cursor.rowcount

    # -- mapping -----------------------------------------------------------

    @staticmethod
    def _row_to_card(row: Optional[dict]) -> Optional[IdCard]:
        if not row:
            return None
        try:
            status = CardStatus(row["status"])
        except ValueError:
            # A status written by a newer build. Treat it as pending rather than
            # failing to show the student at all.
            logger.warning(
                "Unknown id_cards.status %r for card %s; treating it as pending",
                row.get("status"),
                row.get("id"),
            )
            status = CardStatus.PENDING
        return IdCard(
            id=row["id"],
            student_id=row["student_id"],
            template_key=row["template_key"],
            front_image_path=row["front_image_path"],
            back_image_path=row["back_image_path"],
            pdf_path=row["pdf_path"],
            status=status,
            last_error=row["last_error"],
            render_count=int(row["render_count"] or 0),
            custom_fields=row["custom_fields"],
            generated_by=row["generated_by"],
            last_generated_at=row["last_generated_at"],
            last_print_requested_at=row["last_print_requested_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


__all__ = ["IdCardRepository"]