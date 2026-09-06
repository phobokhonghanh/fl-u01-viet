"""Lossless coordinate-exact pixel compositing with seam discontinuity measurement."""

from __future__ import annotations
import math
from pathlib import Path
from typing import Any
from PIL import Image

from backend.watermark_cleaner.config import WatermarkCleanerConfig
from backend.watermark_cleaner.detector import CaseDetectionPlan
from backend.watermark_cleaner.models import CornerName, ReplacedRegion


def measure_corner_seam_discontinuity(
    base_img: Image.Image,
    clean_img: Image.Image,
    box: tuple[int, int, int, int],
    corner: CornerName,
    config: WatermarkCleanerConfig,
) -> dict[str, Any]:
    """Measure source mismatch along the two internal borders of a replaced ROI.

    Each corner ROI has 2 internal borders that separate it from the rest of the image:
      - TL: horizontal border at y=y2, vertical border at x=x2
      - TR: horizontal border at y=y2, vertical border at x=x1
      - BL: horizontal border at y=y1, vertical border at x=x2
      - BR: horizontal border at y=y1, vertical border at x=x1
    """
    x1, y1, x2, y2 = box
    w, h = base_img.size

    if corner == CornerName.TL:
        h_pairs = [(x, y2 - 1, x, y2) for x in range(x1, min(x2, w))]
        v_pairs = [(x2 - 1, y, x2, y) for y in range(y1, min(y2, h))]
    elif corner == CornerName.TR:
        h_pairs = [(x, y2 - 1, x, y2) for x in range(max(0, x1), x2)]
        v_pairs = [(x1, y, x1 - 1, y) for y in range(y1, min(y2, h))]
    elif corner == CornerName.BL:
        h_pairs = [(x, y1, x, y1 - 1) for x in range(x1, min(x2, w))]
        v_pairs = [(x2 - 1, y, x2, y) for y in range(max(0, y1), y2)]
    elif corner == CornerName.BR:
        h_pairs = [(x, y1, x, y1 - 1) for x in range(max(0, x1), x2)]
        v_pairs = [(x1, y, x1 - 1, y) for y in range(max(0, y1), y2)]
    else:
        raise ValueError(f"Unknown corner: {corner}")

    base_rgb = base_img.convert("RGB")
    clean_rgb = clean_img.convert("RGB")

    def evaluate_border(pairs: list[tuple[int, int, int, int]], border_name: str) -> dict[str, Any]:
        if not pairs:
            return {
                "border": border_name,
                "length": 0,
                "mean_discontinuity": 0.0,
                "rmse": 0.0,
                "max_discontinuity": 0.0,
                "high_risk_ratio": 0.0,
            }

        diffs: list[float] = []
        steps_comp: list[float] = []
        noise_thresh = config.jpeg_noise_threshold

        for xi, yi, xo, yo in pairs:
            p_out_clean = clean_rgb.getpixel((xo, yo))
            p_out_base = base_rgb.getpixel((xo, yo))
            p_in_clean = clean_rgb.getpixel((xi, yi))

            # Discrepancy between clean and base along the seam border
            diff = sum(abs(a - b) for a, b in zip(p_out_clean, p_out_base)) / 3.0
            diffs.append(diff)

            # Composite cross-seam step transition
            step_comp = sum(abs(a - b) for a, b in zip(p_in_clean, p_out_base)) / 3.0
            steps_comp.append(step_comp)

        mean_diff = sum(diffs) / len(diffs)
        rmse = math.sqrt(sum(d**2 for d in diffs) / len(diffs))
        max_diff = max(diffs) if diffs else 0.0
        high_risk_count = sum(1 for d in diffs if d > noise_thresh)
        high_risk_ratio = high_risk_count / len(diffs) if diffs else 0.0

        return {
            "border": border_name,
            "length": len(pairs),
            "mean_discontinuity": round(mean_diff, 4),
            "rmse": round(rmse, 4),
            "max_discontinuity": round(max_diff, 4),
            "high_risk_ratio": round(high_risk_ratio, 4),
            "mean_composite_step": round(sum(steps_comp) / len(steps_comp), 4),
        }

    h_metrics = evaluate_border(h_pairs, "horizontal")
    v_metrics = evaluate_border(v_pairs, "vertical")

    total_len = h_metrics["length"] + v_metrics["length"]
    if total_len > 0:
        overall_mean = (
            h_metrics["mean_discontinuity"] * h_metrics["length"]
            + v_metrics["mean_discontinuity"] * v_metrics["length"]
        ) / total_len
        overall_risk = (
            h_metrics["high_risk_ratio"] * h_metrics["length"]
            + v_metrics["high_risk_ratio"] * v_metrics["length"]
        ) / total_len
    else:
        overall_mean = 0.0
        overall_risk = 0.0

    is_acceptable = bool(
        overall_mean <= config.max_seam_mean_discontinuity
        and overall_risk <= config.max_seam_risk_score
    )

    return {
        "horizontal_border": h_metrics,
        "vertical_border": v_metrics,
        "overall_mean_discontinuity": round(overall_mean, 4),
        "overall_risk_score": round(overall_risk, 4),
        "max_allowed_mean_discontinuity": config.max_seam_mean_discontinuity,
        "max_allowed_risk_score": config.max_seam_risk_score,
        "is_acceptable": is_acceptable,
    }


def composite_clean_image(
    images: list[Image.Image],
    image_paths: list[Path],
    plan: CaseDetectionPlan,
    config: WatermarkCleanerConfig | None = None,
) -> tuple[Image.Image, list[ReplacedRegion]]:
    """Composite clean image strictly by copying decoded pixels of the entire corner ROI at exact coordinates.

    - Replaces the full configured edge-anchored corner ROI.
    - No resizing, no cropping (final dimensions preserved), no feathering, no blurring into watermark regions, no inpainting.
    - Measures seam discontinuity across the two internal borders of each replaced ROI.
    - Seam risk is reported as a warning/quality signal; it does not discard a usable preview.
    """
    cfg = config or WatermarkCleanerConfig()
    path_to_image = {image_paths[i]: images[i] for i in range(len(images))}

    base_img_orig = images[plan.base_image_index]
    base_img = base_img_orig.copy()
    replaced_regions: list[ReplacedRegion] = []

    for corner in plan.corners_to_replace:
        analysis = plan.corner_analyses[corner]
        # The FULL corner ROI box
        full_roi_box = analysis.box
        clean_path = plan.clean_sources[corner]
        clean_img = path_to_image[clean_path]

        # Measure seam discontinuity on the two internal borders before/during composite
        seam_metrics = measure_corner_seam_discontinuity(
            base_img=base_img_orig,
            clean_img=clean_img,
            box=full_roi_box,
            corner=corner,
            config=cfg,
        )

        # Replace ENTIRE corner ROI at exact coordinates (lossless decoded pixel copy)
        clean_crop = clean_img.crop(full_roi_box)
        base_img.paste(clean_crop, full_roi_box)

        replaced_regions.append(
            ReplacedRegion(
                corner=corner,
                box=full_roi_box,
                full_roi=full_roi_box,
                source=str(clean_path),
                clean_source=str(clean_path),
                confidence=analysis.confidence,
                seam_metrics=seam_metrics,
            )
        )

    # Sanity check dimensions and mode
    assert base_img.size == base_img_orig.size, "Dimension mutated during composite!"
    assert base_img.mode == base_img_orig.mode, "Color mode mutated during composite!"

    return base_img, replaced_regions
