"""The dashboard must actually show its student list.

The dashboard caught the exception raised while filling its table, logged
"Student list load failed" and carried on with an empty table. Nothing failed
loudly: the page looked like it had simply found no students. A test that only
asserts "the page constructs" cannot see that, so these tests assert on the
rows.

The failure they guard against was a call to the shared ``set_cell`` helper
with a ``family`` argument the helper did not have.
"""
import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dashboard(qapp):
    """A dashboard wired the way the main window wires it."""
    from services.barcode_generator import BarcodeGeneratorService
    from services.student_service import StudentService
    from ui.dashboard import DashboardPage

    page = DashboardPage(StudentService(), BarcodeGeneratorService())
    yield page
    page.deleteLater()


class TestDashboardStudentList:
    def test_the_student_table_is_filled(self, dashboard):
        dashboard._load_student_list()
        table = dashboard.student_table
        assert table.rowCount() > 0, (
            "the dashboard's student table is empty: the list failed to load"
        )
        # Every column of the first row must be written. A cell helper that
        # raises half way through the row leaves a *partly* filled table,
        # which a row-count assertion alone would not see.
        for column in range(table.columnCount()):
            item = table.item(0, column)
            assert item is not None, f"column {column} of the first row is empty"
            assert item.text().strip(), f"column {column} of the first row is blank"

    def test_the_barcode_column_is_written_in_a_fixed_pitch_font(self, dashboard):
        dashboard._load_student_list()
        table = dashboard.student_table
        if table.rowCount() == 0:
            pytest.skip("no students in the database")
        item = table.item(0, 5)
        assert item is not None, "the barcode column is empty"
        assert item.font().family().lower() == "monospace", (
            "the barcode column lost its fixed-pitch font"
        )


class TestSetCellFamily:
    def test_a_family_is_applied_to_the_cell(self, qapp):
        from PySide6.QtWidgets import QTableWidget

        from ui.widgets.responsive_table import set_cell

        table = QTableWidget()
        table.setRowCount(1)
        item = set_cell(table, 0, 0, "000065412", family="monospace")
        assert item.font().family().lower() == "monospace"
        table.deleteLater()

    def test_a_cell_without_a_family_keeps_the_default(self, qapp):
        from PySide6.QtWidgets import QTableWidget

        from ui.widgets.responsive_table import set_cell

        table = QTableWidget()
        table.setRowCount(1)
        item = set_cell(table, 0, 0, "000065412")
        assert item.font().family().lower() != "monospace"
        table.deleteLater()
