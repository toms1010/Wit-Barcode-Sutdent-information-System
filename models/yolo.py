from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict
from enum import Enum


class YOLOModelStatus(str, Enum):
    TRAINING = "training"
    CANDIDATE = "candidate"
    ACTIVE = "active"
    ARCHIVED = "archived"
    FAILED = "failed"


class YOLOModelType(str, Enum):
    DETECTION = "detection"
    CLASSIFICATION = "classification"
    SEGMENTATION = "segmentation"


class DatasetSplit(str, Enum):
    TRAIN = "train"
    VAL = "val"
    TEST = "test"


@dataclass
class YOLOModel:
    id: Optional[int] = None
    model_name: str = ""
    version: str = ""
    file_path: str = ""
    model_type: YOLOModelType = YOLOModelType.DETECTION
    classes: List[str] = field(default_factory=list)
    training_config: Optional[Dict] = None
    metrics: Optional[Dict] = None
    status: YOLOModelStatus = YOLOModelStatus.CANDIDATE
    trained_by: Optional[int] = None
    activated_by: Optional[int] = None
    activated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class DatasetImage:
    id: Optional[int] = None
    file_path: str = ""
    file_name: str = ""
    width: int = 0
    height: int = 0
    file_size: int = 0
    split: DatasetSplit = DatasetSplit.TRAIN
    has_annotations: bool = False
    annotation_file: Optional[str] = None
    is_validated: bool = False
    validated_by: Optional[int] = None
    validated_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None