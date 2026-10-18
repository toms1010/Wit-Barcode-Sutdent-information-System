from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QFrame, QGroupBox,
    QFileDialog, QMessageBox, QProgressBar, QTabWidget, QTextEdit, QLineEdit,
    QComboBox, QSpinBox, QDoubleSpinBox, QFormLayout, QSplitter
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap, QFont
from styles.typography import get_open_sauce_family, get_agrandir_family, body_font, body_bold_font, section_heading_font, page_heading_font, small_label_font, button_font
from styles.colors import Colors
from database.connection import DatabaseUnavailableError
from database.repositories import YOLOModelRepository, DatasetImageRepository
from ui.db_error import show_db_error
from models.yolo import YOLOModel, DatasetImage, YOLOModelStatus, YOLOModelType, DatasetSplit
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class TrainingWorker(QThread):
    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)
    
    def __init__(self, data_yaml: str, epochs: int, imgsz: int, batch: int, device: str):
        super().__init__()
        self.data_yaml = data_yaml
        self.epochs = epochs
        self.imgsz = imgsz
        self.batch = batch
        self.device = device
    
    def run(self):
        try:
            from ultralytics import YOLO
            
            model = YOLO("yolov8n.pt")  # Start from base model
            
            def callback(trainer):
                epoch = trainer.epoch + 1
                total = trainer.epochs
                metrics = getattr(trainer, 'metrics', {})
                msg = f"Epoch {epoch}/{total} - "
                msg += ", ".join([f"{k}: {v:.4f}" for k, v in metrics.items() if isinstance(v, (int, float))])
                self.progress.emit(int(epoch / total * 100), msg)
            
            model.add_callback("on_train_epoch_end", callback)
            
            results = model.train(
                data=self.data_yaml,
                epochs=self.epochs,
                imgsz=self.imgsz,
                batch=self.batch,
                device=self.device,
                verbose=True,
            )
            
            # Get best model path
            best_model = Path(results.save_dir) / "weights" / "best.pt"
            
            self.finished.emit({
                "model_path": str(best_model),
                "metrics": results.results_dict if hasattr(results, 'results_dict') else {},
                "save_dir": str(results.save_dir),
            })
        except Exception as e:
            self.error.emit(str(e))


class TrainingCenterPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_repo = YOLOModelRepository()
        self.dataset_repo = DatasetImageRepository()
        self.training_worker: TrainingWorker = None
        
        self.setObjectName("trainingCenterPage")
        self._setup_ui()
        self._load_models()
        self._load_dataset_stats()
    
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
        
        title = QLabel("Training Center")
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
        
        # Dataset tab
        dataset_tab = self._create_dataset_tab()
        self.tabs.addTab(dataset_tab, "📁 Dataset")
        
        # Training tab
        training_tab = self._create_training_tab()
        self.tabs.addTab(training_tab, "🏋️ Training")
        
        # Models tab
        models_tab = self._create_models_tab()
        self.tabs.addTab(models_tab, "🤖 Models")
        
        main_layout.addWidget(self.tabs)
    
    def _create_dataset_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        
        # Stats
        stats_layout = QHBoxLayout()
        self.stats_cards = {}
        from ui.widgets.stat_card import StatCard
        
        self.stats_cards["train"] = StatCard("Training Images", "0", "🖼️")
        self.stats_cards["val"] = StatCard("Validation Images", "0", "✅")
        self.stats_cards["test"] = StatCard("Test Images", "0", "🧪")
        self.stats_cards["validated"] = StatCard("Validated", "0", "✓")
        
        stats_layout.addWidget(self.stats_cards["train"])
        stats_layout.addWidget(self.stats_cards["val"])
        stats_layout.addWidget(self.stats_cards["test"])
        stats_layout.addWidget(self.stats_cards["validated"])
        layout.addLayout(stats_layout)
        
        # Import section
        import_group = QGroupBox("Import Images")
        import_group.setFont(section_heading_font(14))
        import_layout = QVBoxLayout(import_group)
        
        import_btn_layout = QHBoxLayout()
        self.import_btn = QPushButton("Import Images")
        self.import_btn.clicked.connect(self._import_images)
        
        self.import_annotations_btn = QPushButton("Import Annotations")
        self.import_annotations_btn.setProperty("secondary", "true")
        self.import_annotations_btn.clicked.connect(self._import_annotations)
        
        self.validate_btn = QPushButton("Validate Dataset")
        self.validate_btn.setProperty("secondary", "true")
        self.validate_btn.clicked.connect(self._validate_dataset)
        
        import_btn_layout.addWidget(self.import_btn)
        import_btn_layout.addWidget(self.import_annotations_btn)
        import_btn_layout.addWidget(self.validate_btn)
        import_btn_layout.addStretch()
        
        import_layout.addLayout(import_btn_layout)
        layout.addWidget(import_group)
        
        # Dataset table
        table_group = QGroupBox("Dataset Images")
        table_group.setFont(section_heading_font(14))
        table_layout = QVBoxLayout(table_group)
        
        self.dataset_table = QTableWidget()
        self.dataset_table.setColumnCount(7)
        self.dataset_table.setHorizontalHeaderLabels(["File", "Split", "Size", "Annotations", "Validated", "Issues", "Actions"])
        self.dataset_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.dataset_table.setAlternatingRowColors(True)
        self.dataset_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        
        table_layout.addWidget(self.dataset_table)
        layout.addWidget(table_group)
        
        return tab
    
    def _create_training_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        
        # Configuration
        config_group = QGroupBox("Training Configuration")
        config_group.setFont(section_heading_font(14))
        config_layout = QFormLayout(config_group)
        config_layout.setSpacing(12)
        
        self.data_yaml_edit = QLineEdit("dataset/data.yaml")
        self.data_yaml_edit.setReadOnly(True)
        config_layout.addRow("Dataset Config:", self.data_yaml_edit)
        
        browse_btn = QPushButton("Browse")
        browse_btn.setProperty("secondary", "true")
        browse_btn.clicked.connect(lambda: self._browse_data_yaml())
        config_layout.addRow("", browse_btn)
        
        self.epochs_spin = QSpinBox()
        self.epochs_spin.setRange(1, 1000)
        self.epochs_spin.setValue(50)
        config_layout.addRow("Epochs:", self.epochs_spin)
        
        self.imgsz_spin = QSpinBox()
        self.imgsz_spin.setRange(320, 1280)
        self.imgsz_spin.setValue(640)
        self.imgsz_spin.setSingleStep(32)
        config_layout.addRow("Image Size:", self.imgsz_spin)
        
        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(1, 64)
        self.batch_spin.setValue(16)
        config_layout.addRow("Batch Size:", self.batch_spin)
        
        self.device_combo = QComboBox()
        self.device_combo.addItems(["cpu", "cuda:0", "cuda:1", "mps"])
        config_layout.addRow("Device:", self.device_combo)
        
        layout.addWidget(config_group)
        
        # Progress
        progress_group = QGroupBox("Training Progress")
        progress_group.setFont(section_heading_font(14))
        progress_layout = QVBoxLayout(progress_group)
        
        self.train_progress = QProgressBar()
        self.train_progress.setRange(0, 100)
        self.train_progress.setVisible(False)
        progress_layout.addWidget(self.train_progress)
        
        self.train_status = QLabel("Ready to train")
        self.train_status.setFont(small_label_font(11))
        self.train_status.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        progress_layout.addWidget(self.train_status)
        
        self.train_log = QTextEdit()
        self.train_log.setReadOnly(True)
        self.train_log.setMaximumHeight(150)
        self.train_log.setFont(QFont("Monospace", 10))
        progress_layout.addWidget(self.train_log)
        
        layout.addWidget(progress_group)
        
        # Actions
        actions_layout = QHBoxLayout()
        self.start_train_btn = QPushButton("Start Training")
        self.start_train_btn.setMinimumHeight(44)
        self.start_train_btn.clicked.connect(self._start_training)
        
        self.stop_train_btn = QPushButton("Stop Training")
        self.stop_train_btn.setProperty("danger", "true")
        self.stop_train_btn.setEnabled(False)
        self.stop_train_btn.clicked.connect(self._stop_training)
        
        actions_layout.addWidget(self.start_train_btn)
        actions_layout.addWidget(self.stop_train_btn)
        actions_layout.addStretch()
        
        layout.addLayout(actions_layout)
        layout.addStretch()
        
        return tab
    
    def _create_models_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        
        # Active model
        active_group = QGroupBox("Active Model")
        active_group.setFont(section_heading_font(14))
        active_layout = QVBoxLayout(active_group)
        
        self.active_model_info = QLabel("No active model")
        self.active_model_info.setFont(body_font(12))
        self.active_model_info.setStyleSheet(f"color: {Colors.MEDIUM_TEXT};")
        active_layout.addWidget(self.active_model_info)
        
        active_btn_layout = QHBoxLayout()
        self.deactivate_btn = QPushButton("Deactivate")
        self.deactivate_btn.setProperty("danger", "true")
        self.deactivate_btn.setEnabled(False)
        active_btn_layout.addWidget(self.deactivate_btn)
        active_btn_layout.addStretch()
        active_layout.addLayout(active_btn_layout)
        
        layout.addWidget(active_group)
        
        # Candidate models
        candidate_group = QGroupBox("Candidate Models")
        candidate_group.setFont(section_heading_font(14))
        candidate_layout = QVBoxLayout(candidate_group)
        
        self.models_table = QTableWidget()
        self.models_table.setColumnCount(7)
        self.models_table.setHorizontalHeaderLabels(["Name", "Version", "Type", "Status", "Metrics", "Created", "Actions"])
        self.models_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.models_table.setAlternatingRowColors(True)
        self.models_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        
        candidate_layout.addWidget(self.models_table)
        layout.addWidget(candidate_group)
        
        return tab
    
    def _load_models(self):
        try:
            models = self.model_repo.get_all()
        except DatabaseUnavailableError as exc:
            self.models_table.setRowCount(0)
            self.active_model_info.setText("Model registry unavailable — database not connected")
            self.active_model_info.setStyleSheet(f"color: {Colors.WARNING_ORANGE};")
            show_db_error(self, exc, retry_callback=self._load_models)
            return
        self.models_table.setRowCount(0)
        
        for model in models:
            row = self.models_table.rowCount()
            self.models_table.insertRow(row)
            
            self.models_table.setItem(row, 0, QTableWidgetItem(model.model_name))
            self.models_table.setItem(row, 1, QTableWidgetItem(model.version))
            self.models_table.setItem(row, 2, QTableWidgetItem(model.model_type.value if hasattr(model.model_type, 'value') else str(model.model_type)))
            
            status_item = QTableWidgetItem(model.status.value.title() if hasattr(model.status, 'value') else str(model.status))
            if model.status == YOLOModelStatus.ACTIVE:
                status_item.setForeground(Colors.qcolor(Colors.SUCCESS_GREEN))
            elif model.status == YOLOModelStatus.CANDIDATE:
                status_item.setForeground(Colors.qcolor(Colors.ORANGE))
            elif model.status == YOLOModelStatus.TRAINING:
                status_item.setForeground(Colors.qcolor(Colors.INFO_BLUE))
            self.models_table.setItem(row, 3, status_item)
            
            # Display key metrics
            metrics_text = ""
            if model.metrics:
                key_metrics = []
                for k in ['metrics/mAP50(B)', 'metrics/mAP50-95(B)', 'metrics/precision(B)', 'metrics/recall(B)']:
                    if k in model.metrics:
                        key_metrics.append(f"{k.split('/')[-1].split('(')[0]}: {model.metrics[k]:.4f}")
                metrics_text = ", ".join(key_metrics) if key_metrics else ", ".join([f"{k}: {v:.4f}" for k, v in model.metrics.items() if isinstance(v, (int, float))][:4])
            self.models_table.setItem(row, 4, QTableWidgetItem(metrics_text))
            
            self.models_table.setItem(row, 5, QTableWidgetItem(model.created_at.strftime("%Y-%m-%d %H:%M") if model.created_at else "N/A"))
            
            # Actions
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(4)
            
            if model.status == YOLOModelStatus.CANDIDATE:
                activate_btn = QPushButton("Activate")
                activate_btn.setProperty("secondary", "true")
                activate_btn.setFixedSize(80, 28)
                activate_btn.clicked.connect(lambda checked, mid=model.id: self._activate_model(mid))
                actions_layout.addWidget(activate_btn)
            
            test_btn = QPushButton("Test")
            test_btn.setProperty("secondary", "true")
            test_btn.setFixedSize(60, 28)
            test_btn.clicked.connect(lambda checked, mid=model.id: self._test_model(mid))
            actions_layout.addWidget(test_btn)
            
            self.models_table.setCellWidget(row, 6, actions_widget)
        
        # Update active model info with metrics
        active = self.model_repo.get_active()
        if active:
            metrics_info = ""
            if active.metrics:
                map50 = active.metrics.get('metrics/mAP50(B)', 0)
                map50_95 = active.metrics.get('metrics/mAP50-95(B)', 0)
                precision = active.metrics.get('metrics/precision(B)', 0)
                recall = active.metrics.get('metrics/recall(B)', 0)
                metrics_info = f"  |  mAP50: {map50:.3f}  mAP50-95: {map50_95:.3f}  P: {precision:.3f}  R: {recall:.3f}"
            
            self.active_model_info.setText(f"{active.model_name} v{active.version} ({active.model_type.value if hasattr(active.model_type, 'value') else active.model_type}){metrics_info}")
            self.active_model_info.setStyleSheet(f"color: {Colors.SUCCESS_GREEN}; font-weight: 600;")
            self.deactivate_btn.setEnabled(True)
    
    def _load_dataset_stats(self):
        try:
            counts = {split: len(self.dataset_repo.get_by_split(split)) for split in DatasetSplit}
        except DatabaseUnavailableError as exc:
            show_db_error(self, exc, retry_callback=self._load_dataset_stats)
            return
        for split, count in counts.items():
            key = split.value
            if key in self.stats_cards:
                self.stats_cards[key].update_value(str(count))
        self.stats_cards["validated"].update_value("0")
    
    def _import_images(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Images", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if files:
            QMessageBox.information(self, "Import", f"Selected {len(files)} images. Import functionality to be implemented.")
    
    def _import_annotations(self):
        QMessageBox.information(self, "Import Annotations", "Annotation import functionality to be implemented.")
    
    def _validate_dataset(self):
        QMessageBox.information(self, "Validate Dataset", "Dataset validation functionality to be implemented.")
    
    def _browse_data_yaml(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select data.yaml", "", "YAML Files (*.yaml *.yml)")
        if file_path:
            self.data_yaml_edit.setText(file_path)
    
    def _start_training(self):
        data_yaml = self.data_yaml_edit.text().strip()
        if not data_yaml or not Path(data_yaml).exists():
            QMessageBox.warning(self, "Invalid Config", "Please select a valid data.yaml file.")
            return
        
        self.start_train_btn.setEnabled(False)
        self.stop_train_btn.setEnabled(True)
        self.train_progress.setVisible(True)
        self.train_progress.setValue(0)
        self.train_log.clear()
        
        self.training_worker = TrainingWorker(
            data_yaml=data_yaml,
            epochs=self.epochs_spin.value(),
            imgsz=self.imgsz_spin.value(),
            batch=self.batch_spin.value(),
            device=self.device_combo.currentText(),
        )
        self.training_worker.progress.connect(self._on_training_progress)
        self.training_worker.finished.connect(self._on_training_finished)
        self.training_worker.error.connect(self._on_training_error)
        self.training_worker.start()
    
    def _stop_training(self):
        if self.training_worker and self.training_worker.isRunning():
            self.training_worker.terminate()
            self.training_worker.wait(3000)
        
        self.start_train_btn.setEnabled(True)
        self.stop_train_btn.setEnabled(False)
        self.train_progress.setVisible(False)
        self.train_status.setText("Training stopped")
    
    def _on_training_progress(self, progress: int, message: str):
        self.train_progress.setValue(progress)
        self.train_status.setText(message)
        self.train_log.append(message)
    
    def _on_training_finished(self, result: dict):
        self.start_train_btn.setEnabled(True)
        self.stop_train_btn.setEnabled(False)
        self.train_progress.setVisible(False)
        self.train_status.setText("Training completed!")
        
        # Create candidate model entry
        model = YOLOModel(
            model_name=f"barcode_detector_{len(self.model_repo.get_all()) + 1}",
            version="1.0",
            file_path=result["model_path"],
            model_type=YOLOModelType.DETECTION,
            classes=["barcode"],
            training_config={
                "epochs": self.epochs_spin.value(),
                "imgsz": self.imgsz_spin.value(),
                "batch": self.batch_spin.value(),
                "device": self.device_combo.currentText(),
            },
            metrics=result.get("metrics", {}),
            status=YOLOModelStatus.CANDIDATE,
        )
        
        model_id = self.model_repo.create(model)
        if model_id:
            QMessageBox.information(self, "Training Complete", f"Model trained and saved as candidate.\nPath: {result['model_path']}")
            self._load_models()
    
    def _on_training_error(self, error: str):
        self.start_train_btn.setEnabled(True)
        self.stop_train_btn.setEnabled(False)
        self.train_progress.setVisible(False)
        self.train_status.setText(f"Error: {error}")
        self.train_log.append(f"ERROR: {error}")
        QMessageBox.critical(self, "Training Error", f"Training failed: {error}")
    
    def _activate_model(self, model_id: int):
        if self.model_repo.update_status(model_id, YOLOModelStatus.ACTIVE, activated_by=1):
            QMessageBox.information(self, "Activated", "Model activated successfully!")
            self._load_models()
    
    def _test_model(self, model_id: int):
        QMessageBox.information(self, "Test Model", "Model testing functionality to be implemented.")