from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QComboBox, QDateEdit, QPushButton, QDialogButtonBox, QMessageBox,
    QFileDialog, QFrame, QScrollArea, QWidget, QGridLayout
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QPixmap, QFont
from styles.typography import get_open_sauce_family, get_agrandir_family, body_font, body_bold_font, section_heading_font, page_heading_font, small_label_font, button_font
from styles.colors import Colors
from models.student import Student, StudentContact, EnrollmentStatus, Gender, YearLevel, ContactType
import shutil
from pathlib import Path

from config.settings import settings
import logging

logger = logging.getLogger(__name__)


class StudentDialog(QDialog):
    def __init__(self, student: Student = None, parent=None):
        super().__init__(parent)
        self.student = student
        self.is_edit = student is not None
        self.photo_path = student.profile_image_path if student else None
        
        self.setWindowTitle("Edit Student" if self.is_edit else "Add Student")
        self.setModal(True)
        # A floor, not a fixed size: 500x650 could not fit a 1366x768 laptop with
        # the task bar, and the dialog could not be shrunk at all.
        self.setMinimumSize(480, 560)
        self.resize(560, 680)
        self.resize(500, 700)
        
        self._setup_ui()
        if self.is_edit:
            self._populate_data()
    
    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Header
        header = QFrame()
        header.setMinimumHeight(80)
        header.setMaximumHeight(96)
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.ORANGE};
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }}
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 24, 0)
        
        title = QLabel("Edit Student" if self.is_edit else "Add New Student")
        title.setFont(page_heading_font(20))
        title.setStyleSheet("color: white;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        main_layout.addWidget(header)
        
        # Scrollable form area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameStyle(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        
        form_widget = QWidget()
        form_layout = QVBoxLayout(form_widget)
        form_layout.setContentsMargins(24, 24, 24, 24)
        form_layout.setSpacing(20)
        
        # Photo section
        photo_section = QFrame()
        photo_section.setObjectName("PhotoSectionSurface")
        photo_section.setStyleSheet(f"""
            QFrame#PhotoSectionSurface {{
                background-color: {Colors.CREAM};
                border-radius: 12px;
            }}
        """)
        photo_layout = QHBoxLayout(photo_section)
        photo_layout.setContentsMargins(20, 20, 20, 20)
        
        self.photo_label = QLabel()
        self.photo_label.setFixedSize(100, 100)
        self.photo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_label.setStyleSheet(f"""
            QLabel {{
                background-color: {Colors.WARM_BEIGE};
                border-radius: 50px;
                border: 3px solid {Colors.WHITE};
                color: {Colors.DARK_BURNT_ORANGE};
            }}
        """)
        self._update_photo_display()
        
        photo_btn_layout = QVBoxLayout()
        photo_btn_layout.setSpacing(8)
        
        choose_btn = QPushButton("Choose Photo")
        choose_btn.setProperty("secondary", "true")
        choose_btn.clicked.connect(self._choose_photo)
        
        remove_btn = QPushButton("Remove")
        remove_btn.setProperty("ghost", "true")
        remove_btn.setProperty("danger", "true")
        remove_btn.clicked.connect(self._remove_photo)
        
        photo_btn_layout.addWidget(choose_btn)
        photo_btn_layout.addWidget(remove_btn)
        photo_btn_layout.addStretch()
        
        photo_layout.addWidget(self.photo_label)
        photo_layout.addLayout(photo_btn_layout)
        photo_layout.addStretch()
        
        form_layout.addWidget(photo_section)
        
        # Personal Info Section
        personal_label = QLabel("Personal Information")
        personal_label.setFont(section_heading_font(16))
        personal_label.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE};")
        form_layout.addWidget(personal_label)
        
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        
        # Row 0: Student ID, Reference Number
        self.student_id_edit = QLineEdit()
        self.student_id_edit.setPlaceholderText("Auto-generated if empty")
        self.reference_number_edit = QLineEdit()
        self.reference_number_edit.setPlaceholderText("e.g., 65412 or 000001")
        
        grid.addWidget(QLabel("Student ID:"), 0, 0)
        grid.addWidget(self.student_id_edit, 0, 1)
        grid.addWidget(QLabel("Reference Number:*"), 0, 2)
        grid.addWidget(self.reference_number_edit, 0, 3)
        
        # Row 1: First Name, Middle Name
        self.first_name_edit = QLineEdit()
        self.first_name_edit.setPlaceholderText("Required")
        self.middle_name_edit = QLineEdit()
        self.middle_name_edit.setPlaceholderText("Optional")
        
        grid.addWidget(QLabel("First Name:*"), 1, 0)
        grid.addWidget(self.first_name_edit, 1, 1)
        grid.addWidget(QLabel("Middle Name:"), 1, 2)
        grid.addWidget(self.middle_name_edit, 1, 3)
        
        # Row 2: Last Name, Suffix
        self.last_name_edit = QLineEdit()
        self.last_name_edit.setPlaceholderText("Required")
        self.suffix_edit = QLineEdit()
        self.suffix_edit.setPlaceholderText("Jr., Sr., III, etc.")
        
        grid.addWidget(QLabel("Last Name:*"), 2, 0)
        grid.addWidget(self.last_name_edit, 2, 1)
        grid.addWidget(QLabel("Suffix:"), 2, 2)
        grid.addWidget(self.suffix_edit, 2, 3)
        
        # Row 3: Date of Birth, Gender
        self.dob_edit = QDateEdit()
        self.dob_edit.setCalendarPopup(True)
        self.dob_edit.setDate(QDate.currentDate().addYears(-18))
        self.dob_edit.setDisplayFormat("yyyy-MM-dd")
        self.gender_combo = QComboBox()
        self.gender_combo.addItems([g.value for g in Gender])
        
        grid.addWidget(QLabel("Date of Birth:"), 3, 0)
        grid.addWidget(self.dob_edit, 3, 1)
        grid.addWidget(QLabel("Gender:"), 3, 2)
        grid.addWidget(self.gender_combo, 3, 3)
        
        # Row 4: Program, Year Level
        self.program_edit = QLineEdit()
        self.program_edit.setPlaceholderText("e.g., Computer Engineering")
        self.year_level_combo = QComboBox()
        self.year_level_combo.addItems([y.value for y in YearLevel])
        
        grid.addWidget(QLabel("Program:*"), 4, 0)
        grid.addWidget(self.program_edit, 4, 1)
        grid.addWidget(QLabel("Year Level:*"), 4, 2)
        grid.addWidget(self.year_level_combo, 4, 3)
        
        # Row 5: Section, Enrollment Status
        self.section_edit = QLineEdit()
        self.section_edit.setPlaceholderText("Optional")
        self.status_combo = QComboBox()
        self.status_combo.addItems([s.value for s in EnrollmentStatus])
        
        grid.addWidget(QLabel("Section:"), 5, 0)
        grid.addWidget(self.section_edit, 5, 1)
        grid.addWidget(QLabel("Enrollment Status:*"), 5, 2)
        grid.addWidget(self.status_combo, 5, 3)
        
        form_layout.addLayout(grid)
        
        # Address Section
        address_label = QLabel("Address")
        address_label.setFont(section_heading_font(16))
        address_label.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE};")
        form_layout.addWidget(address_label)
        
        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("Complete address")
        form_layout.addWidget(self.address_edit)
        
        # Contacts Section
        contacts_label = QLabel("Contact Information")
        contacts_label.setFont(section_heading_font(16))
        contacts_label.setStyleSheet(f"color: {Colors.DARK_BURNT_ORANGE};")
        form_layout.addWidget(contacts_label)
        
        self.contacts_layout = QVBoxLayout()
        self.contacts_layout.setSpacing(8)
        self.contact_widgets = []
        
        # Add default contact fields
        self._add_contact_row(ContactType.PHONE, True)
        self._add_contact_row(ContactType.EMAIL, False)
        
        add_contact_btn = QPushButton("+ Add Contact")
        add_contact_btn.setProperty("secondary", "true")
        add_contact_btn.clicked.connect(lambda: self._add_contact_row())
        
        self.contacts_layout.addWidget(add_contact_btn)
        form_layout.addLayout(self.contacts_layout)
        
        form_layout.addStretch()
        scroll.setWidget(form_widget)
        main_layout.addWidget(scroll)
        
        # Button box
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.setStyleSheet(f"""
            QDialogButtonBox {{
                background-color: {Colors.WHITE};
                border-top: 1px solid {Colors.CARD_BORDER};
                padding: 16px 24px;
            }}
        """)
        button_box.accepted.connect(self._validate_and_accept)
        button_box.rejected.connect(self.reject)

        confirm = button_box.button(QDialogButtonBox.StandardButton.Ok)
        if confirm is not None:
            confirm.setText("Save Changes")
        main_layout.addWidget(button_box)
    
    def _add_contact_row(self, contact_type: ContactType = ContactType.PHONE, is_primary: bool = False):
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        
        type_combo = QComboBox()
        type_combo.addItems([c.value for c in ContactType])
        type_combo.setCurrentText(contact_type.value)
        type_combo.setMinimumWidth(140)
        
        value_edit = QLineEdit()
        value_edit.setPlaceholderText("Contact value")
        
        primary_check = QPushButton("Primary" if is_primary else "Set Primary")
        primary_check.setCheckable(True)
        primary_check.setChecked(is_primary)
        primary_check.setProperty("secondary", "true")
        primary_check.setFixedWidth(100)
        
        remove_btn = QPushButton("✕")
        remove_btn.setProperty("ghost", "true")
        remove_btn.setProperty("danger", "true")
        remove_btn.setFixedSize(28, 28)
        remove_btn.clicked.connect(lambda: self._remove_contact_row(row_widget))
        
        row_layout.addWidget(type_combo)
        row_layout.addWidget(value_edit)
        row_layout.addWidget(primary_check)
        row_layout.addWidget(remove_btn)
        
        self.contacts_layout.insertWidget(self.contacts_layout.count() - 1, row_widget)
        self.contact_widgets.append((row_widget, type_combo, value_edit, primary_check))
    
    def _remove_contact_row(self, widget):
        for i, (w, _, _, _) in enumerate(self.contact_widgets):
            if w == widget:
                self.contact_widgets.pop(i)
                widget.deleteLater()
                break
    
    def _choose_photo(self):
        """Pick a photo and keep a copy inside the project.

        The chosen file is copied into ``assets/profile/`` and the *project
        relative* copy is stored. Recording the operator's absolute path instead
        (which is what this did) makes the record unresolvable on any other
        machine, and on any deployment that does not share that folder.
        """
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Profile Photo", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if not file_path:
            return

        source = Path(file_path)
        if not source.exists():
            QMessageBox.warning(
                self, "Cannot use that image",
                f"{source.name} could not be read.",
            )
            return

        try:
            settings.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            stem = source.stem.lower().replace(" ", "_")
            target = settings.PROFILE_DIR / f"{stem}{source.suffix.lower()}"
            if target.resolve() != source.resolve():
                shutil.copy2(source, target)
        except OSError as exc:
            QMessageBox.critical(
                self, "Cannot store the photo",
                f"The image was selected but could not be copied into "
                f"{settings.PROFILE_DIR}:\n\n{exc}",
            )
            return

        self.photo_path = settings.store_path(target)
        self._update_photo_display()

    def _remove_photo(self):
        """Detach the photo from the record.

        ``setText`` alone does not remove a pixmap: a QLabel paints whichever of
        the two it has, so the "removed" photograph stayed on screen.
        """
        self.photo_path = None
        self._update_photo_display()

    def _update_photo_display(self):
        resolved = settings.resolve_asset(self.photo_path) if self.photo_path else None
        if resolved is not None:
            pixmap = QPixmap(str(resolved))
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    100, 100,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                result = QPixmap(100, 100)
                result.fill(Qt.GlobalColor.transparent)
                from PySide6.QtGui import QPainter, QBrush
                painter = QPainter(result)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setBrush(QBrush(scaled))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(0, 0, 100, 100, 50, 50)
                painter.end()
                self.photo_label.setPixmap(result)
                self.photo_label.setText("")
                self.photo_label.setToolTip(resolved.name)
                return
            logger.warning("Selected photo could not be decoded: %s", resolved)

        self.photo_label.setPixmap(QPixmap())
        self.photo_label.setToolTip("No profile photo on file")
        self.photo_label.setText("—")
        self.photo_label.setFont(QFont(get_open_sauce_family(), 36))
        self.photo_label.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; background: {Colors.SURFACE_MUTED};"
            f"border-radius: 50%;"
        )
    
    def _populate_data(self):
        s = self.student
        self.student_id_edit.setText(s.student_id)
        self.reference_number_edit.setText(s.reference_number)
        self.first_name_edit.setText(s.first_name)
        self.middle_name_edit.setText(s.middle_name or "")
        self.last_name_edit.setText(s.last_name)
        self.suffix_edit.setText(s.suffix or "")
        
        if s.date_of_birth:
            self.dob_edit.setDate(QDate(s.date_of_birth.year, s.date_of_birth.month, s.date_of_birth.day))
        else:
            # A date edit is always "valid", so leaving it on its default wrote
            # today's date minus eighteen years into a student with no birth date
            # on file. It is made explicitly unset instead.
            self.dob_edit.setSpecialValueText("Not recorded")
            self.dob_edit.setDate(QDate(1900, 1, 1))
            self._dob_is_placeholder = True
        
        if s.gender:
            self.gender_combo.setCurrentText(s.gender.value if hasattr(s.gender, 'value') else str(s.gender))
        
        self.program_edit.setText(s.program)
        
        if s.year_level:
            self.year_level_combo.setCurrentText(s.year_level.value if hasattr(s.year_level, 'value') else str(s.year_level))
        
        self.section_edit.setText(s.section or "")
        
        if s.enrollment_status:
            self.status_combo.setCurrentText(s.enrollment_status.value if hasattr(s.enrollment_status, 'value') else str(s.enrollment_status))
        
        self.address_edit.setText(s.address or "")
        
        # Clear default contacts and add actual ones
        for w, _, _, _ in self.contact_widgets:
            w.deleteLater()
        self.contact_widgets.clear()
        
        for contact in s.contacts:
            self._add_contact_row(contact.contact_type, contact.is_primary)
            if self.contact_widgets:
                _, _, value_edit, primary_check = self.contact_widgets[-1]
                value_edit.setText(contact.contact_value)
                primary_check.setChecked(contact.is_primary)
                primary_check.setText("Primary" if contact.is_primary else "Set Primary")
        
        if not s.contacts:
            self._add_contact_row(ContactType.PHONE, True)
            self._add_contact_row(ContactType.EMAIL, False)
    
    def _validate_and_accept(self):
        if not self.first_name_edit.text().strip():
            QMessageBox.warning(self, "Validation Error", "First name is required")
            self.first_name_edit.setFocus()
            return
        
        if not self.last_name_edit.text().strip():
            QMessageBox.warning(self, "Validation Error", "Last name is required")
            self.last_name_edit.setFocus()
            return
        
        if not self.reference_number_edit.text().strip():
            QMessageBox.warning(self, "Validation Error", "Reference number is required")
            self.reference_number_edit.setFocus()
            return
        
        if not self.program_edit.text().strip():
            QMessageBox.warning(self, "Validation Error", "Program is required")
            self.program_edit.setFocus()
            return
        
        self.accept()
    
    def get_student(self) -> Student:
        """Build the edited record.

        Fields the form does not show are carried over from the record that was
        opened. Building a brand new object dropped ``date_enrolled``,
        ``created_by``, ``updated_by`` and both timestamps, so every save from
        this dialog blanked those columns in the database.
        """
        contacts = []
        primary_set = False
        
        for _, type_combo, value_edit, primary_check in self.contact_widgets:
            if value_edit.text().strip():
                try:
                    ctype = ContactType(type_combo.currentText())
                except ValueError:
                    continue
                is_primary = primary_check.isChecked()
                if is_primary:
                    primary_set = True
                contacts.append(StudentContact(
                    contact_type=ctype,
                    contact_value=value_edit.text().strip(),
                    is_primary=is_primary,
                ))
        
        # Ensure at least one primary
        if contacts and not primary_set:
            contacts[0].is_primary = True
        
        original = self.student

        dob = self.dob_edit.date().toPython()
        if getattr(self, "_dob_is_placeholder", False):
            dob = None

        def enum_value(combo, enum_cls, fallback):
            text = combo.currentText()
            try:
                return enum_cls(text)
            except ValueError:
                # A value that no longer maps to the enum (an older record, or a
                # renamed member) must not raise out of an already-closed dialog.
                logger.warning("Unrecognised %s value %r; keeping %r",
                                enum_cls.__name__, text, fallback)
                return fallback

        year_level = enum_value(
            self.year_level_combo, YearLevel,
            getattr(original, "year_level", None) or YearLevel.FIRST_YEAR,
        )
        gender = enum_value(
            self.gender_combo, Gender,
            getattr(original, "gender", None) or Gender.MALE,
        )
        status = enum_value(
            self.status_combo, EnrollmentStatus,
            getattr(original, "enrollment_status", None) or EnrollmentStatus.ACTIVE,
        )

        return Student(
            id=original.id if self.is_edit else None,
            student_id=self.student_id_edit.text().strip() or None,
            reference_number=self.reference_number_edit.text().strip(),
            first_name=self.first_name_edit.text().strip(),
            middle_name=self.middle_name_edit.text().strip() or None,
            last_name=self.last_name_edit.text().strip(),
            suffix=self.suffix_edit.text().strip() or None,
            program=self.program_edit.text().strip(),
            year_level=year_level,
            section=self.section_edit.text().strip() or None,
            date_of_birth=dob,
            gender=gender,
            address=self.address_edit.text().strip() or None,
            profile_image_path=self.photo_path,
            enrollment_status=status,
            barcode_payload=getattr(original, "barcode_payload", None),
            date_enrolled=getattr(original, "date_enrolled", None),
            created_by=getattr(original, "created_by", None),
            updated_by=getattr(original, "updated_by", None),
            created_at=getattr(original, "created_at", None),
            updated_at=getattr(original, "updated_at", None),
            contacts=contacts,
        )