"""Input validation for watermark cleaner pipeline."""

from __future__ import annotations
import math
import os
from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageStat

from backend.watermark_cleaner.config import WatermarkCleanerConfig
from backend.watermark_cleaner.exceptions import (
    CorruptedImageError,
    DimensionMismatchError,
    FileSizeDeltaExceededError,
    InputValidationError,
    InsufficientInputsError,
    ModeMismatchError,
    SourceImageMismatchError,
)


@dataclass
class ValidatedCase:
    """Encapsulates validated inputs and image metadata for a case."""
    case_name: str
    case_dir: Path
    image_paths: list[Path]
    dimensions: tuple[int, int]
    mode: str
    file_sizes: dict[str, int]


def discover_case_images(
    case_dir: Path | str,
    config: WatermarkCleanerConfig | None = None,
) -> list[Path]:
    """Find all eligible image files within a case directory, ignoring output/report files."""
    cfg = config or WatermarkCleanerConfig()
    dir_path = Path(case_dir)

    if not dir_path.exists() or not dir_path.is_dir():
        raise InputValidationError(f"Case directory does not exist or is not a directory: {dir_path}")

    # Dynamically ignore default and configured output/report filenames
    ignored_names = set(cfg.ignore_generated_filenames)
    ignored_names.add(cfg.output_filename)
    ignored_names.add(cfg.report_filename)

    image_files: list[Path] = []
    try:
        entries = sorted(dir_path.iterdir())
    except (PermissionError, OSError) as exc:
        raise InputValidationError(f"Cannot read case directory {dir_path}: {exc}") from exc

    for entry in entries:
        if not entry.is_file():
            continue
        if entry.name in ignored_names:
            continue
        if entry.suffix.lower() in cfg.supported_extensions:
            image_files.append(entry)

    return image_files


def validate_case_inputs(
    image_paths: list[Path | str],
    case_name: str = "",
    case_dir: Path | str | None = None,
    config: WatermarkCleanerConfig | None = None,
) -> ValidatedCase:
    """Validate directory, file counts, file-size delta, dimensions, modes, and stream integrity."""
    cfg = config or WatermarkCleanerConfig()
    paths = [Path(p) for p in image_paths]

    # Check minimum inputs
    if len(paths) < cfg.min_images_per_case:
        raise InsufficientInputsError(
            f"Case requires at least {cfg.min_images_per_case} images, but found {len(paths)}.",
            count=len(paths),
            minimum_required=cfg.min_images_per_case,
        )

    # Validate file existence and file size delta
    file_sizes: dict[str, int] = {}
    for p in paths:
        if not p.exists() or not p.is_file():
            raise InputValidationError(f"Image file not found: {p}")
        file_sizes[str(p.resolve())] = os.path.getsize(p)

    sizes = list(file_sizes.values())
    min_size = min(sizes)
    max_size = max(sizes)
    size_delta = max_size - min_size

    if size_delta > cfg.max_file_size_delta_bytes:
        raise FileSizeDeltaExceededError(
            f"File size delta ({size_delta} bytes) exceeds maximum allowed threshold "
            f"({cfg.max_file_size_delta_bytes} bytes).",
            delta=size_delta,
            max_allowed=cfg.max_file_size_delta_bytes,
            min_size=min_size,
            max_size=max_size,
        )

    # Validate dimensions, modes, and full pixel decoding integrity
    dimensions: dict[str, tuple[int, int]] = {}
    modes: dict[str, str] = {}

    for p in paths:
        try:
            with Image.open(p) as img:
                # Fully decode all compressed scans to catch truncated/corrupted streams
                img.load()
                dimensions[str(p.resolve())] = (img.width, img.height)
                modes[str(p.resolve())] = img.mode
        except Exception as exc:
            raise CorruptedImageError(
                f"Failed to decode image {p}: {exc}",
                file_path=str(p),
                cause=str(exc),
            ) from exc

    # Check dimension equality across all images
    unique_dims = set(dimensions.values())
    if len(unique_dims) > 1:
        raise DimensionMismatchError(
            f"Images have conflicting pixel dimensions: {dimensions}",
            dimensions=dimensions,
        )

    # Check mode equality across all images
    unique_modes = set(modes.values())
    if len(unique_modes) > 1:
        raise ModeMismatchError(
            f"Images have conflicting color modes: {modes}",
            modes=modes,
        )

    resolved_dim = next(iter(unique_dims))
    resolved_mode = next(iter(unique_modes))
    resolved_case_dir = Path(case_dir) if case_dir else paths[0].parent
    resolved_case_name = case_name or resolved_case_dir.name

    return ValidatedCase(
        case_name=resolved_case_name,
        case_dir=resolved_case_dir,
        image_paths=paths,
        dimensions=resolved_dim,
        mode=resolved_mode,
        file_sizes=file_sizes,
    )


def validate_source_image_consistency(
    images: list[Image.Image],
    image_paths: list[Path],
    config: WatermarkCleanerConfig,
) -> None:
    """Verify that all input images are genuine copies of the same original image.

    Evaluates the control region outside the 4 corner ROIs (central / cross-region).
    """
    w, h = images[0].size
    cw = max(1, int(w * config.corner_width_fraction))
    ch = max(1, min(h, math.ceil(h * config.corner_height_fraction)))

    # Construct mask covering everything EXCEPT the 4 corner ROIs
    control_mask = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(control_mask)
    # Pillow rectangles include the end coordinate; use -1 to match crop's
    # half-open boxes exactly.
    draw.rectangle([0, 0, cw - 1, ch - 1], fill=0)  # TL
    draw.rectangle([w - cw, 0, w - 1, ch - 1], fill=0)  # TR
    draw.rectangle([0, h - ch, cw - 1, h - 1], fill=0)  # BL
    draw.rectangle([w - cw, h - ch, w - 1, h - 1], fill=0)  # BR

    total_control_pixels = control_mask.histogram()[255]
    if total_control_pixels <= 0:
        return

    n = len(images)
    for i in range(n):
        for j in range(i + 1, n):
            diff = ImageChops.difference(images[i].convert("RGB"), images[j].convert("RGB")).convert("L")
            stat = ImageStat.Stat(diff, mask=control_mask)
            mean_diff = stat.mean[0]

            diff_masked = Image.composite(diff, Image.new("L", (w, h), 0), control_mask)
            hist = diff_masked.histogram()
            sig_thresh = int(config.control_noise_threshold)
            sig_count = sum(hist[sig_thresh:])
            diff_ratio = sig_count / total_control_pixels

            if mean_diff > config.max_control_mean_diff or diff_ratio > config.max_control_diff_ratio:
                raise SourceImageMismatchError(
                    f"Source image mismatch between '{image_paths[i].name}' and '{image_paths[j].name}': "
                    f"control region mean difference is {mean_diff:.2f} (max allowed {config.max_control_mean_diff}) "
                    f"and differing pixel ratio is {diff_ratio * 100:.2f}% (max allowed {config.max_control_diff_ratio * 100:.2f}%). "
                    "Inputs must be genuine copies of the same original image.",
                    mean_diff=round(mean_diff, 4),
                    diff_ratio=round(diff_ratio, 4),
                    thresholds={
                        "max_mean_diff": config.max_control_mean_diff,
                        "max_diff_ratio": config.max_control_diff_ratio,
                        "control_noise_threshold": config.control_noise_threshold,
                    },
                )
