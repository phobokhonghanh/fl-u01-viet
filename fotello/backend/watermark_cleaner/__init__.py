"""Watermark Cleaner: lossless reconstruction of clean images from multiple watermarked copies."""

from backend.watermark_cleaner.config import WatermarkCleanerConfig
from backend.watermark_cleaner.exceptions import (
    AmbiguousCornerError,
    ConfigValidationError,
    CorruptedImageError,
    DimensionMismatchError,
    DuplicateWatermarkError,
    FileSizeDeltaExceededError,
    ImageSaveError,
    InputValidationError,
    InsufficientCleanCoverageError,
    InsufficientInputsError,
    InvalidWatermarkDistributionError,
    ModeMismatchError,
    SeamDiscontinuityError,
    SourceImageMismatchError,
    WatermarkCleanerError,
    WatermarkDetectionError,
)
from backend.watermark_cleaner.models import (
    BatchResult,
    CleaningResult,
    CornerAnalysis,
    CornerName,
    CornerRegion,
    ReplacedRegion,
)
from backend.watermark_cleaner.pipeline import (
    batch_clean,
    clean_case,
    clean_directory,
)

__all__ = [
    "WatermarkCleanerConfig",
    "WatermarkCleanerError",
    "ConfigValidationError",
    "InputValidationError",
    "InsufficientInputsError",
    "DimensionMismatchError",
    "ModeMismatchError",
    "FileSizeDeltaExceededError",
    "CorruptedImageError",
    "SourceImageMismatchError",
    "WatermarkDetectionError",
    "DuplicateWatermarkError",
    "InsufficientCleanCoverageError",
    "InvalidWatermarkDistributionError",
    "AmbiguousCornerError",
    "SeamDiscontinuityError",
    "ImageSaveError",
    "CornerName",
    "CornerRegion",
    "CornerAnalysis",
    "ReplacedRegion",
    "CleaningResult",
    "BatchResult",
    "clean_case",
    "clean_directory",
    "batch_clean",
]
