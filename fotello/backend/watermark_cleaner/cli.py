"""Command-line interface for watermark cleaner pipeline."""

from __future__ import annotations
import argparse
import json
import logging
import sys
from pathlib import Path

from backend.watermark_cleaner.config import WatermarkCleanerConfig
from backend.watermark_cleaner.pipeline import (
    batch_clean,
    clean_directory,
)


def setup_logging(verbose: bool = False, quiet: bool = False) -> None:
    """Configure console logging level and format."""
    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(message)s",
    )


def create_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser with comprehensive overrides."""
    parser = argparse.ArgumentParser(
        prog="watermark-cleaner",
        description="Lossless watermark removal and clean image composite from multiple watermarked copies.",
    )

    parser.add_argument(
        "path",
        type=str,
        help="Path to case directory or root directory containing case subdirectories.",
    )

    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default=None,
        help="Optional base directory to save output clean images and JSON reports.",
    )

    # Safe ROI overrides (each rectangle is anchored to its actual corner)
    parser.add_argument(
        "--corner-width-fraction",
        type=float,
        default=0.35,
        help=(
            "Fraction of image width covered by each corner ROI (default: 0.35). "
            "Valid range: (0.0, 1.0]."
        ),
    )
    parser.add_argument(
        "--corner-height-fraction",
        type=float,
        default=0.52,
        help=(
            "Fraction of image height covered by each corner ROI (default: 0.52). "
            "Valid range: (0.0, 1.0]."
        ),
    )

    # Tolerance & Threshold Overrides
    parser.add_argument(
        "--max-file-size-delta",
        type=int,
        default=1024 * 1024,
        help="Maximum allowed file size difference in bytes (default: 1048576 / 1 MiB).",
    )
    parser.add_argument(
        "--max-seam-discontinuity-mean",
        type=float,
        default=15.0,
        help="Maximum mean pixel difference allowed across ROI internal seams (default: 15.0).",
    )
    parser.add_argument(
        "--max-seam-risk-score",
        type=float,
        default=0.20,
        help="Maximum ratio of high-discontinuity pixels along internal seams (default: 0.20).",
    )
    parser.add_argument(
        "--jpeg-noise-threshold",
        type=float,
        default=20.0,
        help="Pixel difference threshold to distinguish JPEG noise from watermark (default: 20.0).",
    )
    parser.add_argument(
        "--min-diff-pixel-ratio",
        type=float,
        default=0.005,
        help="Minimum ratio of differing pixels in corner ROI to detect watermark (default: 0.005).",
    )
    parser.add_argument(
        "--min-diff-pixel-count",
        type=int,
        default=500,
        help="Minimum absolute count of differing pixels in corner ROI (default: 500).",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.15,
        help="Minimum confidence threshold for clean vs watermarked decision (default: 0.15).",
    )
    parser.add_argument(
        "--detection-quality-weight",
        type=float,
        default=0.70,
        help="Weight of detection confidence in the reported quality percentage (default: 0.70).",
    )
    parser.add_argument(
        "--seam-quality-weight",
        type=float,
        default=0.30,
        help="Weight of seam safety in the reported quality percentage (default: 0.30).",
    )

    # Output Names & Formats
    parser.add_argument(
        "--output-format",
        type=str,
        default="PNG",
        help="Lossless output format (default: PNG).",
    )
    parser.add_argument(
        "--output-filename",
        type=str,
        default="clean_result.png",
        help="Filename for clean output image (default: clean_result.png).",
    )
    parser.add_argument(
        "--report-filename",
        type=str,
        default="report.json",
        help="Filename for JSON report (default: report.json).",
    )

    # Execution Flow Controls
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Force batch processing across subdirectories.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop batch execution immediately when a case fails (default is to continue).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print final summary as JSON to stdout.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Quiet mode; only print errors and final summary.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main CLI entrypoint returning status code."""
    parser = create_parser()
    args = parser.parse_args(argv)

    setup_logging(verbose=args.verbose, quiet=args.quiet)
    logger = logging.getLogger("watermark_cleaner")

    input_path = Path(args.path)
    if not input_path.exists():
        logger.error(f"Input path does not exist: {input_path}")
        return 2

    if input_path.is_file():
        logger.error(f"Input path '{input_path}' is a file, expected a directory.")
        return 2

    try:
        config = WatermarkCleanerConfig(
            corner_width_fraction=args.corner_width_fraction,
            corner_height_fraction=args.corner_height_fraction,
            max_file_size_delta_bytes=args.max_file_size_delta,
            max_seam_mean_discontinuity=args.max_seam_discontinuity_mean,
            max_seam_risk_score=args.max_seam_risk_score,
            jpeg_noise_threshold=args.jpeg_noise_threshold,
            min_diff_pixel_ratio=args.min_diff_pixel_ratio,
            min_diff_pixel_count=args.min_diff_pixel_count,
            min_confidence_threshold=args.confidence_threshold,
            detection_quality_weight=args.detection_quality_weight,
            seam_quality_weight=args.seam_quality_weight,
            output_format=args.output_format,
            output_filename=args.output_filename,
            report_filename=args.report_filename,
        )
    except Exception as exc:
        logger.error(f"Invalid configuration: {exc}")
        return 2

    # Determine if input path is a batch directory (has subdirectories) or a single case directory
    try:
        subdirs = [d for d in sorted(input_path.iterdir()) if d.is_dir()]
    except (PermissionError, OSError) as exc:
        logger.error(f"Cannot access directory '{input_path}': {exc}")
        return 1

    is_batch = args.batch or (len(subdirs) > 0)

    if is_batch:
        logger.info(f"Running batch clean on {input_path}...")
        batch_res = batch_clean(
            root_dir=input_path,
            output_dir=args.output_dir,
            config=config,
            continue_on_error=not args.stop_on_error,
        )

        if batch_res.total_cases == 0:
            logger.error(f"No valid case directories or images found in '{input_path}'.")
            return 1

        if args.json:
            print(json.dumps(batch_res.to_dict(), indent=2, ensure_ascii=False))
        else:
            print("\n" + "=" * 60)
            print("BATCH PROCESSING SUMMARY")
            print("=" * 60)
            print(f"Total cases:     {batch_res.total_cases}")
            print(f"Succeeded cases: {batch_res.succeeded_cases}")
            print(f"Failed cases:    {batch_res.failed_cases}")
            print("-" * 60)
            for r in batch_res.results:
                status_str = "SUCCESS" if r.success else f"FAILED ({r.error_type}: {r.error_message})"
                print(f"  Case [{r.case_name}]: {status_str}")
                if r.output_path:
                    print(f"    Clean image: {r.output_path}")
                    print(f"    Completion: {r.completion_percentage:.2f}%")
                    print(f"    Quality:    {r.quality_score_percentage:.2f}% ({r.status})")
                if r.report_path:
                    print(f"    Report JSON: {r.report_path}")
            print("=" * 60)

        # Return exit code 0 if all succeeded, 1 if any failed
        return 0 if batch_res.failed_cases == 0 else 1

    else:
        logger.info(f"Running single case clean on {input_path}...")
        result = clean_directory(
            case_dir=input_path,
            output_dir=args.output_dir,
            config=config,
        )

        if args.json:
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        else:
            print("\n" + "=" * 60)
            print(f"CASE RESULT: {result.case_name}")
            print("=" * 60)
            print(f"Success:      {result.success}")
            if result.success:
                print(f"Dimensions:   {result.dimensions}")
                print(f"Color Mode:   {result.mode}")
                print(f"Clean image:  {result.output_path}")
                print(f"Report:       {result.report_path}")
                print(f"Replaced:     {len(result.regions_replaced)} region(s)")
                print(f"Completion:   {result.completion_percentage:.2f}%")
                print(f"Quality:      {result.quality_score_percentage:.2f}% ({result.status})")
                for warning in result.warnings:
                    print(f"Warning:      {warning}")
                for reg in result.regions_replaced:
                    print(f"  Corner {reg['corner']}: box={reg['box']} from {Path(reg['source']).name}")
            else:
                print(f"Error type:   {result.error_type}")
                print(f"Error message:{result.error_message}")
                print(f"Report:       {result.report_path}")
            print("=" * 60)

        return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
