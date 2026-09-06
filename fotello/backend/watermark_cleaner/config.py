"""Configuration dataclass for the watermark cleaner pipeline."""

from __future__ import annotations
from dataclasses import dataclass
from backend.watermark_cleaner.exceptions import ConfigValidationError


@dataclass(frozen=True)
class WatermarkCleanerConfig:
    """Configurable parameters for validation, detection, and compositing.

    Each ROI is anchored to its actual image edge. The measured defaults cover
    35% of the width and 52% of the height. For a 4096×2726 Bottom-Left image this
    is exactly ``(0, int(H * .48), int(W * .35), H)``.
    """

    corner_width_fraction: float = 0.35
    corner_height_fraction: float = 0.52

    # File size validation threshold (default 1 MiB)
    max_file_size_delta_bytes: int = 1024 * 1024  # 1 MiB = 1,048,576 bytes

    # Input requirements
    min_images_per_case: int = 2
    supported_extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp")
    ignore_generated_filenames: tuple[str, ...] = (
        "clean_result.png",
        "clean_image.png",
        "report.json",
        "clean_report.json",
    )

    # Difference and JPEG noise filtering
    jpeg_noise_threshold: float = 20.0  # Pixel difference magnitude (0-255)
    min_diff_pixel_ratio: float = 0.005  # Ratio of corner ROI pixels that must differ (0.5%)
    min_diff_pixel_count: int = 500  # Minimum pixel count differing

    # Watermark edge energy confidence threshold
    min_confidence_threshold: float = 0.15  # |score_wm - score_clean| / (score_wm + score_clean)

    # Control region consistency thresholds (central / cross-region outside all 4 corner ROIs)
    control_noise_threshold: float = 20.0
    max_control_diff_ratio: float = 0.02  # At most 2% of control pixels can differ beyond JPEG noise
    max_control_mean_diff: float = 10.0  # Mean pixel difference across control region must be <= 10.0

    # Seam discontinuity and risk thresholds along ROI internal borders
    max_seam_mean_discontinuity: float = 15.0  # Max mean pixel difference across internal seam
    max_seam_risk_score: float = 0.20  # Max ratio of high-discontinuity pixels along seam

    # Quality score weights. They are report-only and never suppress a usable preview.
    detection_quality_weight: float = 0.70
    seam_quality_weight: float = 0.30

    # Output parameters (strictly lossless formats: PNG, TIFF)
    output_format: str = "PNG"
    output_filename: str = "clean_result.png"
    report_filename: str = "report.json"

    def __post_init__(self) -> None:
        """Validate all parameters, bounds, and lossless format contracts."""
        # 1. ROI bounds validation. Slight top/bottom overlap is intentional for the
        # measured 52%-high safe zone; every ROI remains anchored to one corner.
        if not (0.0 < self.corner_width_fraction <= 1.0):
            raise ConfigValidationError(
                f"corner_width_fraction ({self.corner_width_fraction}) must be in range (0.0, 1.0]."
            )
        if not (0.0 < self.corner_height_fraction <= 1.0):
            raise ConfigValidationError(
                f"corner_height_fraction ({self.corner_height_fraction}) must be in range (0.0, 1.0]."
            )

        # 2. Minimum inputs and size delta
        if self.min_images_per_case < 2:
            raise ConfigValidationError(
                f"min_images_per_case ({self.min_images_per_case}) must be at least 2."
            )
        if self.max_file_size_delta_bytes < 0:
            raise ConfigValidationError("max_file_size_delta_bytes cannot be negative.")

        # 3. Thresholds and ratios
        if self.jpeg_noise_threshold < 0:
            raise ConfigValidationError("jpeg_noise_threshold cannot be negative.")
        if not (0.0 <= self.min_diff_pixel_ratio <= 1.0):
            raise ConfigValidationError("min_diff_pixel_ratio must be between 0.0 and 1.0.")
        if self.min_diff_pixel_count < 0:
            raise ConfigValidationError("min_diff_pixel_count cannot be negative.")
        if not (0.0 <= self.min_confidence_threshold <= 1.0):
            raise ConfigValidationError("min_confidence_threshold must be between 0.0 and 1.0.")

        # 4. Control region consistency
        if self.control_noise_threshold < 0:
            raise ConfigValidationError("control_noise_threshold cannot be negative.")
        if not (0.0 <= self.max_control_diff_ratio <= 1.0):
            raise ConfigValidationError("max_control_diff_ratio must be between 0.0 and 1.0.")
        if self.max_control_mean_diff < 0:
            raise ConfigValidationError("max_control_mean_diff cannot be negative.")

        # 5. Seam discontinuity
        if self.max_seam_mean_discontinuity < 0:
            raise ConfigValidationError("max_seam_mean_discontinuity cannot be negative.")
        if not (0.0 <= self.max_seam_risk_score <= 1.0):
            raise ConfigValidationError("max_seam_risk_score must be between 0.0 and 1.0.")
        if self.detection_quality_weight < 0 or self.seam_quality_weight < 0:
            raise ConfigValidationError("Quality score weights cannot be negative.")
        if self.detection_quality_weight + self.seam_quality_weight <= 0:
            raise ConfigValidationError("At least one quality score weight must be positive.")

        # 6. Lossless output format contract
        fmt_upper = self.output_format.strip().upper()
        if fmt_upper not in {"PNG", "TIFF"}:
            raise ConfigValidationError(
                f"Output format '{self.output_format}' is not a supported lossless format. "
                "Only 'PNG' and 'TIFF' are permitted."
            )
        object.__setattr__(self, "output_format", fmt_upper)

        # 7. Extension consistency check
        ext = self.output_filename.lower()
        if fmt_upper == "PNG" and not ext.endswith(".png"):
            raise ConfigValidationError(
                f"Output filename '{self.output_filename}' must end with '.png' for format PNG."
            )
        if fmt_upper == "TIFF" and not (ext.endswith(".tiff") or ext.endswith(".tif")):
            raise ConfigValidationError(
                f"Output filename '{self.output_filename}' must end with '.tiff' or '.tif' for format TIFF."
            )
