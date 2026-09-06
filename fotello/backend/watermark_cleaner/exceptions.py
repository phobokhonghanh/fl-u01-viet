"""Domain exceptions for the watermark cleaner pipeline."""

from __future__ import annotations
from typing import Any


class WatermarkCleanerError(Exception):
    """Base domain exception for watermark cleaner pipeline."""
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InputValidationError(WatermarkCleanerError):
    """Base exception for input validation failures."""
    pass


class InsufficientInputsError(InputValidationError):
    """Raised when fewer than the minimum required images are provided."""
    def __init__(self, message: str, count: int, minimum_required: int = 2) -> None:
        super().__init__(message, {"count": count, "minimum_required": minimum_required})
        self.count = count
        self.minimum_required = minimum_required


class DimensionMismatchError(InputValidationError):
    """Raised when input images have conflicting pixel dimensions."""
    def __init__(self, message: str, dimensions: dict[str, tuple[int, int]]) -> None:
        super().__init__(message, {"dimensions": dimensions})
        self.dimensions = dimensions


class ModeMismatchError(InputValidationError):
    """Raised when input images have conflicting color modes."""
    def __init__(self, message: str, modes: dict[str, str]) -> None:
        super().__init__(message, {"modes": modes})
        self.modes = modes


class FileSizeDeltaExceededError(InputValidationError):
    """Raised when byte-size delta between images exceeds the allowed tolerance."""
    def __init__(self, message: str, delta: int, max_allowed: int, min_size: int, max_size: int) -> None:
        super().__init__(
            message,
            {
                "delta_bytes": delta,
                "max_allowed_bytes": max_allowed,
                "min_size_bytes": min_size,
                "max_size_bytes": max_size,
            },
        )
        self.delta = delta
        self.max_allowed = max_allowed
        self.min_size = min_size
        self.max_size = max_size


class CorruptedImageError(InputValidationError):
    """Raised when an image file cannot be opened or decoded."""
    def __init__(self, message: str, file_path: str, cause: str) -> None:
        super().__init__(message, {"file_path": file_path, "cause": cause})
        self.file_path = file_path
        self.cause = cause


class WatermarkDetectionError(WatermarkCleanerError):
    """Base exception for watermark detection and analysis errors."""
    pass


class DuplicateWatermarkError(WatermarkDetectionError):
    """Raised when images share identical watermarks or identical decoded pixels with no clean source."""
    def __init__(self, message: str, corner: str | None = None) -> None:
        super().__init__(message, {"corner": corner})
        self.corner = corner


class InsufficientCleanCoverageError(WatermarkDetectionError):
    """Raised when at least one corner cannot be covered by a verified clean source."""
    def __init__(self, message: str, missing_corners: list[str]) -> None:
        super().__init__(message, {"missing_corners": missing_corners})
        self.missing_corners = missing_corners


class AmbiguousCornerError(WatermarkDetectionError):
    """Raised when confidence in identifying the watermarked vs clean image is below threshold."""
    def __init__(self, message: str, corner: str, confidence: float, threshold: float) -> None:
        super().__init__(
            message,
            {"corner": corner, "confidence": confidence, "threshold": threshold},
        )
        self.corner = corner
        self.confidence = confidence
        self.threshold = threshold


class SeamDiscontinuityError(WatermarkCleanerError):
    """Raised when discontinuity along the quadrant internal seam exceeds allowed threshold."""
    def __init__(
        self,
        message: str,
        corner: str,
        seam_metrics: dict[str, Any],
        threshold: float,
    ) -> None:
        super().__init__(
            message,
            {"corner": corner, "seam_metrics": seam_metrics, "threshold": threshold},
        )
        self.corner = corner
        self.seam_metrics = seam_metrics
        self.threshold = threshold


class ConfigValidationError(WatermarkCleanerError):
    """Raised when configuration parameters are invalid or out of allowed bounds."""
    pass


class SourceImageMismatchError(InputValidationError):
    """Raised when input images are not genuine copies of the same original image."""
    def __init__(
        self,
        message: str,
        mean_diff: float,
        diff_ratio: float,
        thresholds: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            {"mean_diff": mean_diff, "diff_ratio": diff_ratio, "thresholds": thresholds or {}},
        )
        self.mean_diff = mean_diff
        self.diff_ratio = diff_ratio
        self.thresholds = thresholds or {}


class InvalidWatermarkDistributionError(WatermarkDetectionError):
    """Raised when detected watermark distribution violates the business invariant (e.g. not exactly 1 corner per image)."""
    def __init__(self, message: str, distributions: dict[str, list[str]]) -> None:
        super().__init__(message, {"distributions": distributions})
        self.distributions = distributions


class ImageSaveError(WatermarkCleanerError):
    """Raised when saving the clean output image fails."""
    def __init__(self, message: str, output_path: str, cause: str) -> None:
        super().__init__(message, {"output_path": output_path, "cause": cause})
        self.output_path = output_path
        self.cause = cause
