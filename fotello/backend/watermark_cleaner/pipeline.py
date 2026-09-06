"""Orchestration pipeline for single and batch watermark cleaning."""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image

from backend.watermark_cleaner.compositor import composite_clean_image
from backend.watermark_cleaner.config import WatermarkCleanerConfig
from backend.watermark_cleaner.detector import detect_watermarks_and_plan
from backend.watermark_cleaner.exceptions import (
    ImageSaveError,
    WatermarkCleanerError,
)
from backend.watermark_cleaner.models import (
    BatchResult,
    CleaningResult,
)
from backend.watermark_cleaner.validator import (
    ValidatedCase,
    discover_case_images,
    validate_case_inputs,
    validate_source_image_consistency,
)

logger = logging.getLogger("watermark_cleaner")


def _bounded_component(value: float, maximum: float) -> float:
    """Return a 0..1 quality component where zero measured risk is best."""
    if maximum <= 0:
        return 1.0 if value <= 0 else 0.0
    return max(0.0, 1.0 - min(value / maximum, 1.0))


def _quality_summary(plan, replaced_regions, config: WatermarkCleanerConfig):
    replacement_count = len(replaced_regions)
    required_count = len(plan.corners_to_replace)
    completion = 100.0 if required_count == 0 else 100.0 * replacement_count / required_count

    detection = (
        100.0 * sum(region.confidence for region in replaced_regions) / replacement_count
        if replacement_count else 0.0
    )
    seam_parts: list[float] = []
    warnings = list(plan.warnings)
    for region in replaced_regions:
        metrics = region.seam_metrics
        seam_parts.append(
            (
                _bounded_component(
                    metrics["overall_mean_discontinuity"],
                    config.max_seam_mean_discontinuity,
                )
                + _bounded_component(
                    metrics["overall_risk_score"],
                    config.max_seam_risk_score,
                )
            )
            / 2.0
        )
        if not metrics["is_acceptable"]:
            warnings.append(
                f"Seam risk at {region.corner.value} exceeds the configured threshold; "
                "the preview was still saved for visual review."
            )

    seam = 100.0 * sum(seam_parts) / len(seam_parts) if seam_parts else 0.0
    total_weight = config.detection_quality_weight + config.seam_quality_weight
    quality = (
        detection * config.detection_quality_weight
        + seam * config.seam_quality_weight
    ) / total_weight
    status = "complete" if completion == 100.0 and not warnings else "preview"
    return status, completion, quality, {
        "detection_confidence_percentage": round(detection, 2),
        "seam_safety_percentage": round(seam, 2),
        "score_weights": {
            "detection": config.detection_quality_weight,
            "seam": config.seam_quality_weight,
        },
        "note": "Quality is an automated estimate, not ground-truth verification.",
    }, warnings


def clean_case(
    image_paths: list[Path | str],
    case_name: str = "",
    case_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    config: WatermarkCleanerConfig | None = None,
) -> CleaningResult:
    """Run full validation, detection, and compositing for a single case."""
    cfg = config or WatermarkCleanerConfig()
    timestamp = datetime.now(timezone.utc).isoformat()
    paths = [Path(p) for p in image_paths]
    resolved_case_dir = Path(case_dir) if case_dir else (paths[0].parent if paths else Path("."))
    resolved_case_name = case_name or resolved_case_dir.name

    # Determine destination paths
    if output_dir:
        dest_dir = Path(output_dir) / resolved_case_name
    else:
        dest_dir = resolved_case_dir
    dest_dir.mkdir(parents=True, exist_ok=True)

    report_path = dest_dir / cfg.report_filename
    output_img_path = dest_dir / cfg.output_filename
    tmp_output = dest_dir / f".{cfg.output_filename}.tmp"

    # Enforce clean lifecycle: delete any pre-existing output image so failure never leaves stale output
    output_img_path.unlink(missing_ok=True)
    tmp_output.unlink(missing_ok=True)

    # Step 1: Input Validation (file size, dimensions, modes, decoding integrity)
    try:
        validated = validate_case_inputs(
            image_paths=paths,
            case_name=resolved_case_name,
            case_dir=resolved_case_dir,
            config=cfg,
        )
    except Exception as exc:
        logger.error(f"Validation failed for case {resolved_case_name}: {exc}")
        output_img_path.unlink(missing_ok=True)
        result = CleaningResult(
            case_name=resolved_case_name,
            case_dir=str(resolved_case_dir),
            success=False,
            timestamp=timestamp,
            source_images=[str(p) for p in paths],
            report_path=str(report_path),
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        report_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return result

    # Step 2: Open images, verify source consistency, and run Detection & Planning
    images: list[Image.Image] = []
    try:
        for p in validated.image_paths:
            img = Image.open(p)
            img.load()
            images.append(img)

        # Verify all inputs are genuine copies of the same original photo (control region check)
        validate_source_image_consistency(images, validated.image_paths, cfg)

        # Detect watermarks and enforce business invariant
        plan = detect_watermarks_and_plan(images, validated.image_paths, cfg)

        # Step 3: Composite full safe ROIs. Quality warnings do not suppress previews.
        clean_img, replaced_regions = composite_clean_image(images, validated.image_paths, plan, config=cfg)

        status, completion, quality, quality_metrics, warnings = _quality_summary(
            plan, replaced_regions, cfg
        )

        # Save output image atomically losslessly
        try:
            clean_img.save(tmp_output, format=cfg.output_format)
            tmp_output.replace(output_img_path)
        except Exception as exc:
            tmp_output.unlink(missing_ok=True)
            raise ImageSaveError(
                f"Failed to save clean image to {output_img_path}: {exc}",
                output_path=str(output_img_path),
                cause=str(exc),
            ) from exc

        logger.info(f"Clean image successfully saved to {output_img_path}")

        corner_dict = {
            corner.value: analysis.to_dict()
            for corner, analysis in plan.corner_analyses.items()
        }

        result = CleaningResult(
            case_name=validated.case_name,
            case_dir=str(validated.case_dir),
            success=True,
            timestamp=timestamp,
            status=status,
            completion_percentage=completion,
            quality_score_percentage=quality,
            quality_metrics=quality_metrics,
            warnings=warnings,
            dimensions=validated.dimensions,
            mode=validated.mode,
            base_image=str(plan.base_image_path),
            source_images=[str(p) for p in validated.image_paths],
            output_path=str(output_img_path),
            report_path=str(report_path),
            corner_analyses=corner_dict,
            regions_replaced=[r.to_dict() for r in replaced_regions],
        )

        report_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return result

    except Exception as exc:
        logger.warning(f"Watermark cleaning failed for case {resolved_case_name}: {type(exc).__name__}: {exc}")
        # Always purge any partial / existing output on failure
        output_img_path.unlink(missing_ok=True)
        tmp_output.unlink(missing_ok=True)

        corner_dict = {}
        if "plan" in locals() and hasattr(plan, "corner_analyses"):
            corner_dict = {
                corner.value: analysis.to_dict()
                for corner, analysis in plan.corner_analyses.items()
            }

        result = CleaningResult(
            case_name=validated.case_name,
            case_dir=str(validated.case_dir),
            success=False,
            timestamp=timestamp,
            dimensions=validated.dimensions,
            mode=validated.mode,
            source_images=[str(p) for p in validated.image_paths],
            report_path=str(report_path),
            corner_analyses=corner_dict,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        report_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return result

    finally:
        tmp_output.unlink(missing_ok=True)
        for img in images:
            try:
                img.close()
            except Exception:
                pass


def clean_directory(
    case_dir: Path | str,
    output_dir: Path | str | None = None,
    config: WatermarkCleanerConfig | None = None,
) -> CleaningResult:
    """Clean watermarks from image copies in a single directory."""
    cfg = config or WatermarkCleanerConfig()
    dir_path = Path(case_dir)
    image_paths = discover_case_images(dir_path, cfg)

    return clean_case(
        image_paths=image_paths,
        case_name=dir_path.name,
        case_dir=dir_path,
        output_dir=output_dir,
        config=cfg,
    )


def batch_clean(
    root_dir: Path | str,
    output_dir: Path | str | None = None,
    config: WatermarkCleanerConfig | None = None,
    continue_on_error: bool = True,
) -> BatchResult:
    """Run watermark cleaning across all subdirectories of root_dir."""
    cfg = config or WatermarkCleanerConfig()
    root = Path(root_dir)

    if not root.exists():
        raise FileNotFoundError(f"Root path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Root path is not a directory: {root}")

    # Discover subdirectories that have images
    try:
        subdirs = sorted([d for d in root.iterdir() if d.is_dir()])
    except (PermissionError, OSError) as exc:
        logger.error(f"Cannot read directory {root}: {exc}")
        return BatchResult(
            total_cases=0,
            succeeded_cases=0,
            failed_cases=1,
            results=[
                CleaningResult(
                    case_name=root.name,
                    case_dir=str(root),
                    success=False,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            ],
        )

    case_dirs: list[Path] = []
    for d in subdirs:
        try:
            if len(discover_case_images(d, cfg)) > 0:
                case_dirs.append(d)
        except (PermissionError, OSError) as exc:
            logger.warning(f"Skipping inaccessible directory {d}: {exc}")
            if not continue_on_error:
                return BatchResult(
                    total_cases=1,
                    succeeded_cases=0,
                    failed_cases=1,
                    results=[
                        CleaningResult(
                            case_name=d.name,
                            case_dir=str(d),
                            success=False,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                        )
                    ],
                )

    # If root itself directly has images and no valid subdirs, treat root as single case
    if not case_dirs:
        try:
            if len(discover_case_images(root, cfg)) >= cfg.min_images_per_case:
                case_dirs = [root]
        except (PermissionError, OSError):
            pass

    results: list[CleaningResult] = []
    succeeded = 0
    failed = 0

    for c_dir in case_dirs:
        logger.info(f"Processing case: {c_dir.name} ({c_dir})")
        res = clean_directory(c_dir, output_dir=output_dir, config=cfg)
        results.append(res)
        if res.success:
            succeeded += 1
        else:
            failed += 1
            if not continue_on_error:
                break

    return BatchResult(
        total_cases=len(case_dirs),
        succeeded_cases=succeeded,
        failed_cases=failed,
        results=results,
    )
