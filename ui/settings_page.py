from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QFrame, QGroupBox,
    QFormLayout, QFileDialog, QMessageBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QDialog, QDialogButtonBox, QTextEdit
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from styles.typography import get_open_sauce_family, get_agrandir_family, body_font, body_bold_font, section_heading_font, page_heading_font, small_label_font, button_font
from styles.colors import Colors
from config.settings import settings
from database.connection import test_connection, initialize_database
import logging

logger = logging.getLogger(__name__)


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._setup_ui()
        self._load_settings()
    
    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Header
        header = QFrame()
        header.setFixedHeight(80)
        header.setStyleSheet(f"background-color: {Colors.ORANGE}; border-top-left-radius: 8px; border-top-right-radius: 8px;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 0, 24, 0)
        
        title = QLabel("Settings")
        title.setFont(page_heading_font(24))
        title.setStyleSheet("color: white;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        
        main_layout.addWidget(header)
        
        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {Colors.CARD_BORDER}; border-radius: 8px; background: white; }}
            QTabBar::tab {{ background: {Colors.CREAM}; padding: 10px 20px; border: 1px solid {Colors.CARD_BORDER}; border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; }}
            QTabBar::tab:selected {{ background: white; color: {Colors.ORANGE}; border-color: {Colors.CARD_BORDER}; border-bottom: 1px solid white; }}
        """)
        
        # General tab
        general_tab = self._create_general_tab()
        self.tabs.addTab(general_tab, "⚙️ General")
        
        # Database tab
        db_tab = self._create_database_tab()
        self.tabs.addTab(db_tab, "🗄️ Database")
        
        # Scanner tab
        scanner_tab = self._create_scanner_tab()
        self.tabs.addTab(scanner_tab, "📷 Scanner")
        
        # Barcode tab
        barcode_tab = self._create_barcode_tab()
        self.tabs.addTab(barcode_tab, "🏷️ Barcode")
        
        # Appearance tab
        appearance_tab = self._create_appearance_tab()
        self.tabs.addTab(appearance_tab, "🎨 Appearance")
        
        # Backup tab
        backup_tab = self._create_backup_tab()
        self.tabs.addTab(backup_tab, "💾 Backup")
        
        main_layout.addWidget(self.tabs)
        
        # Save button
        save_layout = QHBoxLayout()
        save_layout.addStretch()
        
        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setMinimumWidth(160)
        self.save_btn.setMinimumHeight(44)
        self.save_btn.clicked.connect(self._save_settings)
        save_layout.addWidget(self.save_btn)
        
        main_layout.addLayout(save_layout)
    
    def _create_general_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        
        # Application
        app_group = QGroupBox("Application")
        app_group.setFont(section_heading_font(14))
        app_layout = QFormLayout(app_group)
        app_layout.setSpacing(12)
        
        self.app_name_edit = QLineEdit()
        app_layout.addRow("Application Name:", self.app_name_edit)
        
        self.app_version_edit = QLineEdit()
        self.app_version_edit.setReadOnly(True)
        app_layout.addRow("Version:", self.app_version_edit)
        
        self.debug_check = QCheckBox("Enable Debug Mode")
        app_layout.addRow("", self.debug_check)
        
        layout.addWidget(app_group)
        
        # Academic
        academic_group = QGroupBox("Academic Defaults")
        academic_group.setFont(section_heading_font(14))
        academic_layout = QFormLayout(academic_group)
        academic_layout.setSpacing(12)
        
        self.default_academic_year = QLineEdit()
        academic_layout.addRow("Default Academic Year:", self.default_academic_year)
        
        self.default_semester = QComboBox()
        self.default_semester.addItems(["1st Semester", "2nd Semester", "Summer", "Midyear"])
        academic_layout.addRow("Default Semester:", self.default_semester)
        
        layout.addWidget(academic_group)
        
        layout.addStretch()
        return tab
    
    def _create_database_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        
        # Connection
        conn_group = QGroupBox("Database Connection")
        conn_group.setFont(section_heading_font(14))
        conn_layout = QFormLayout(conn_group)
        conn_layout.setSpacing(12)
        
        self.db_host = QLineEdit()
        conn_layout.addRow("Host:", self.db_host)
        
        self.db_port = QSpinBox()
        self.db_port.setRange(1, 65535)
        conn_layout.addRow("Port:", self.db_port)
        
        self.db_name = QLineEdit()
        conn_layout.addRow("Database:", self.db_name)
        
        self.db_user = QLineEdit()
        conn_layout.addRow("Username:", self.db_user)
        
        self.db_password = QLineEdit()
        self.db_password.setEchoMode(QLineEdit.EchoMode.Password)
        conn_layout.addRow("Password:", self.db_password)
        
        test_btn = QPushButton("Test Connection")
        test_btn.setProperty("secondary", "true")
        test_btn.clicked.connect(self._test_db_connection)
        conn_layout.addRow("", test_btn)
        
        layout.addWidget(conn_group)
        
        # Schema
        schema_group = QGroupBox("Schema Management")
        schema_group.setFont(section_heading_font(14))
        schema_layout = QVBoxLayout(schema_group)
        
        init_btn = QPushButton("Initialize Schema")
        init_btn.clicked.connect(self._init_schema)
        
        migrate_btn = QPushButton("Run Migrations")
        migrate_btn.setProperty("secondary", "true")
        
        schema_layout.addWidget(init_btn)
        schema_layout.addWidget(migrate_btn)
        
        layout.addWidget(schema_group)
        layout.addStretch()
        return tab
    
    def _create_scanner_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        
        # Camera
        cam_group = QGroupBox("Camera Settings")
        cam_group.setFont(section_heading_font(14))
        cam_layout = QFormLayout(cam_group)
        cam_layout.setSpacing(12)
        
        self.camera_width = QSpinBox()
        self.camera_width.setRange(320, 1920)
        self.camera_width.setSingleStep(160)
        cam_layout.addRow("Width:", self.camera_width)
        
        self.camera_height = QSpinBox()
        self.camera_height.setRange(240, 1080)
        self.camera_height.setSingleStep(120)
        cam_layout.addRow("Height:", self.camera_height)
        
        self.camera_fps = QSpinBox()
        self.camera_fps.setRange(10, 60)
        cam_layout.addRow("FPS:", self.camera_fps)
        
        layout.addWidget(cam_group)
        
        # YOLO
        yolo_group = QGroupBox("YOLO Detection")
        yolo_group.setFont(section_heading_font(14))
        yolo_layout = QFormLayout(yolo_group)
        yolo_layout.setSpacing(12)
        
        self.yolo_confidence = QDoubleSpinBox()
        self.yolo_confidence.setRange(0.1, 1.0)
        self.yolo_confidence.setSingleStep(0.05)
        self.yolo_confidence.setDecimals(2)
        yolo_layout.addRow("Confidence Threshold:", self.yolo_confidence)
        
        self.yolo_iou = QDoubleSpinBox()
        self.yolo_iou.setRange(0.1, 1.0)
        self.yolo_iou.setSingleStep(0.05)
        self.yolo_iou.setDecimals(2)
        yolo_layout.addRow("IoU Threshold:", self.yolo_iou)
        
        self.yolo_device = QComboBox()
        self.yolo_device.addItems(["cpu", "cuda:0", "cuda:1", "mps"])
        yolo_layout.addRow("Device:", self.yolo_device)
        
        layout.addWidget(yolo_group)
        
        # Scan
        scan_group = QGroupBox("Scan Settings")
        scan_group.setFont(section_heading_font(14))
        scan_layout = QFormLayout(scan_group)
        scan_layout.setSpacing(12)
        
        self.duplicate_suppression = QSpinBox()
        self.duplicate_suppression.setRange(1, 60)
        self.duplicate_suppression.setSuffix(" seconds")
        scan_layout.addRow("Duplicate Suppression:", self.duplicate_suppression)
        
        layout.addWidget(scan_group)
        layout.addStretch()
        return tab
    
    def _create_barcode_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        
        # Generation
        gen_group = QGroupBox("Barcode Generation")
        gen_group.setFont(section_heading_font(14))
        gen_layout = QFormLayout(gen_group)
        gen_layout.setSpacing(12)
        
        self.barcode_prefix = QLineEdit()
        gen_layout.addRow("Barcode Prefix:", self.barcode_prefix)
        
        self.module_width = QSpinBox()
        self.module_width.setRange(1, 5)
        gen_layout.addRow("Module Width:", self.module_width)
        
        self.module_height = QSpinBox()
        self.module_height.setRange(5, 50)
        gen_layout.addRow("Module Height:", self.module_height)
        
        self.output_dir = QLineEdit()
        self.output_dir.setReadOnly(True)
        browse_btn = QPushButton("Browse")
        browse_btn.setProperty("secondary", "true")
        browse_btn.clicked.connect(lambda: self._browse_output_dir())
        
        hbox = QHBoxLayout()
        hbox.addWidget(self.output_dir)
        hbox.addWidget(browse_btn)
        gen_layout.addRow("Output Directory:", hbox)
        
        layout.addWidget(gen_group)
        layout.addStretch()
        return tab
    
    def _create_appearance_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        
        # Theme info
        theme_group = QGroupBox("Theme")
        theme_group.setFont(section_heading_font(14))
        theme_layout = QVBoxLayout(theme_group)
        
        theme_info = QLabel(
            "WIT Barcode System uses a fixed light theme with the WIT brand colors.\n"
            "Dark mode is not supported per design requirements."
        )
        theme_info.setFont(body_font(12))
        theme_info.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        theme_info.setWordWrap(True)
        theme_layout.addWidget(theme_info)
        
        layout.addWidget(theme_group)
        
        # Fonts
        fonts_group = QGroupBox("Fonts")
        fonts_group.setFont(section_heading_font(14))
        fonts_layout = QVBoxLayout(fonts_group)
        
        font_info = QLabel(
            "Primary: Open Sauce (UI, body text, tables)\n"
            "Display: Agrandir (Titles, headings, prominent numbers)\n\n"
            "Place font files in assets/fonts/open-sauce/ and assets/fonts/agrandir/"
        )
        font_info.setFont(body_font(12))
        font_info.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        font_info.setWordWrap(True)
        fonts_layout.addWidget(font_info)
        
        layout.addWidget(fonts_group)
        layout.addStretch()
        return tab
    
    def _create_backup_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        
        backup_group = QGroupBox("Database Backup")
        backup_group.setFont(section_heading_font(14))
        backup_layout = QVBoxLayout(backup_group)
        
        backup_btn = QPushButton("Create Backup")
        backup_btn.clicked.connect(self._create_backup)
        
        restore_btn = QPushButton("Restore from Backup")
        restore_btn.setProperty("secondary", "true")
        restore_btn.clicked.connect(self._restore_backup)
        
        backup_layout.addWidget(backup_btn)
        backup_layout.addWidget(restore_btn)
        
        info = QLabel(
            "Backups are created as SQL dump files.\n"
            "Store backups in a secure location.\n"
            "Test restores periodically to ensure backup integrity."
        )
        info.setFont(small_label_font(11))
        info.setStyleSheet(f"color: {Colors.LIGHT_TEXT};")
        info.setWordWrap(True)
        backup_layout.addWidget(info)
        
        layout.addWidget(backup_group)
        layout.addStretch()
        return tab
    
    def _load_settings(self):
        self.app_name_edit.setText(settings.APP_NAME)
        self.app_version_edit.setText(settings.APP_VERSION)
        self.debug_check.setChecked(settings.DEBUG)
        
        self.default_academic_year.setText("2024-2025")
        self.default_semester.setCurrentText("1st Semester")
        
        self.db_host.setText(settings.DB_HOST)
        self.db_port.setValue(settings.DB_PORT)
        self.db_name.setText(settings.DB_NAME)
        self.db_user.setText(settings.DB_USER)
        self.db_password.setText(settings.DB_PASSWORD)
        
        self.camera_width.setValue(settings.CAMERA_WIDTH)
        self.camera_height.setValue(settings.CAMERA_HEIGHT)
        self.camera_fps.setValue(settings.CAMERA_FPS)
        
        self.yolo_confidence.setValue(settings.YOLO_CONFIDENCE_THRESHOLD)
        self.yolo_iou.setValue(settings.YOLO_IOU_THRESHOLD)
        self.yolo_device.setCurrentText(settings.YOLO_DEVICE)
        
        self.duplicate_suppression.setValue(settings.SCAN_DUPLICATE_SUPPRESSION_SECONDS)
        
        self.barcode_prefix.setText(settings.BARCODE_PREFIX)
        self.module_width.setValue(settings.BARCODE_MODULE_WIDTH)
        self.module_height.setValue(settings.BARCODE_MODULE_HEIGHT)
        self.output_dir.setText(str(settings.BARCODE_OUTPUT_DIR))
    
    def _test_db_connection(self):
        if test_connection():
            QMessageBox.information(self, "Success", "Database connection successful!")
        else:
            QMessageBox.critical(self, "Failed", "Database connection failed. Check your settings.")
    
    def _init_schema(self):
        reply = QMessageBox.question(
            self, "Initialize Schema",
            "This will create all database tables. Existing data will not be affected.\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            if initialize_database():
                QMessageBox.information(self, "Success", "Database schema initialized successfully!")
            else:
                QMessageBox.critical(self, "Failed", "Failed to initialize database schema.")
    
    def _browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Output Directory", str(settings.BARCODE_OUTPUT_DIR))
        if dir_path:
            self.output_dir.setText(dir_path)
    
    def _create_backup(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Backup", f"wit_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql",
            "SQL Files (*.sql)"
        )
        if file_path:
            QMessageBox.information(self, "Backup", f"Backup functionality to be implemented.\nWould save to: {file_path}")
    
    def _restore_backup(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Backup File", "", "SQL Files (*.sql)"
        )
        if file_path:
            reply = QMessageBox.warning(
                self, "Restore Backup",
                "This will overwrite the current database. Are you sure?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                QMessageBox.information(self, "Restore", "Restore functionality to be implemented.")
    
    def _save_settings(self):
        QMessageBox.information(self, "Settings", "Settings saved (persistent storage to be implemented).")
        logger.info("Settings saved")


from datetime import datetime