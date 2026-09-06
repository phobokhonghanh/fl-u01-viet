"""Corner comparison, difference analysis, and clean source detection."""

from __future__ import annotations
import math
from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageChops, ImageFilter, ImageStat

from backend.watermark_cleaner.config import WatermarkCleanerConfig
from backend.watermark_cleaner.exceptions import (
    DuplicateWatermarkError,
    InsufficientCleanCoverageError,
)
from backend.watermark_cleaner.models import (
    CornerAnalysis,
    CornerName,
    CornerRegion,
)


@dataclass
class CaseDetectionPlan:
    """Complete detection plan specifying clean sources and regions to replace."""
    corner_analyses: dict[CornerName, CornerAnalysis]
    base_image_index: int
    base_image_path: Path
    corners_to_replace: list[CornerName]
    clean_sources: dict[CornerName, Path]
    warnings: list[str]


def compute_corner_regions(
    width: int,
    height: int,
    config: WatermarkCleanerConfig,
) -> dict[CornerName, CornerRegion]:
    """Calculate edge-anchored safe ROIs for all four corners."""
    cw = max(1, int(width * config.corner_width_fraction))
    # ceil(.52 * H) makes the bottom edge start at int(.48 * H), matching
    # the measured Bottom-Left bounding-box contract.
    ch = max(1, min(height, math.ceil(height * config.corner_height_fraction)))
    right_x = width - cw
    bottom_y = height - ch

    return {
        CornerName.TL: CornerRegion(CornerName.TL, (0, 0, cw, ch)),
        CornerName.TR: CornerRegion(CornerName.TR, (right_x, 0, width, ch)),
        CornerName.BL: CornerRegion(CornerName.BL, (0, bottom_y, cw, height)),
        CornerName.BR: CornerRegion(CornerName.BR, (right_x, bottom_y, width, height)),
    }



def analyze_corner_roi(
    corner_region: CornerRegion,
    images: list[Image.Image],
    image_paths: list[Path],
    config: WatermarkCleanerConfig,
) -> tuple[CornerAnalysis, set[int]]:
    """Analyze a single corner ROI across all input images.

    Returns:
        tuple of (CornerAnalysis, set of watermarked image indices for this corner)
    """
    n = len(images)
    box = corner_region.box
    corner_crops = [img.crop(box) for img in images]
    corner_area = corner_region.area

    # Pairwise difference analysis
    max_sig_pixels = 0
    max_sig_ratio = 0.0
    pairwise_diffs: dict[tuple[int, int], tuple[int, float, float]] = {}

    for i in range(n):
        for j in range(i + 1, n):
            diff_img = ImageChops.difference(corner_crops[i], corner_crops[j]).convert("L")
            hist = diff_img.histogram()
            threshold_int = int(config.jpeg_noise_threshold)
            sig_pixels = sum(hist[threshold_int:])
            sig_ratio = sig_pixels / corner_area
            stat = ImageStat.Stat(diff_img)
            mean_diff = stat.mean[0]

            pairwise_diffs[(i, j)] = (sig_pixels, sig_ratio, mean_diff)
            if sig_pixels > max_sig_pixels:
                max_sig_pixels = sig_pixels
                max_sig_ratio = sig_ratio

    # Check if this corner has no significant difference among any pair
    if max_sig_pixels < config.min_diff_pixel_count or max_sig_ratio < config.min_diff_pixel_ratio:
        analysis = CornerAnalysis(
            corner=corner_region.name,
            box=box,
            status="clean_all",
            clean_source=str(image_paths[0]),
            watermarked_sources=[],
            confidence=1.0,
            metrics={
                "significant_pixels": max_sig_pixels,
                "significant_ratio": round(max_sig_ratio, 6),
                "threshold": config.jpeg_noise_threshold,
            },
        )
        return analysis, set()

    # Watermark detected: distinguish watermarked vs clean image(s)
    watermarked_indices: set[int] = set()
    clean_indices: set[int] = set()

    # Build union difference mask for each image
    edge_scores: list[float] = []
    for i in range(n):
        # Union mask where image i differs from any other image beyond noise threshold
        union_mask = Image.new("L", (corner_region.width, corner_region.height), 0)
        for j in range(n):
            if i == j:
                continue
            pair_diff = ImageChops.difference(corner_crops[i], corner_crops[j]).convert("L")
            pair_mask = pair_diff.point(lambda p, t=config.jpeg_noise_threshold: 255 if p > t else 0)
            union_mask = ImageChops.lighter(union_mask, pair_mask)

        # High-frequency edge energy within the watermark difference mask
        mask_pixels = sum(union_mask.histogram()[1:])
        if mask_pixels == 0:
            edge_scores.append(0.0)
        else:
            gray_crop = corner_crops[i].convert("L")
            edge_img = gray_crop.filter(ImageFilter.FIND_EDGES)
            stat = ImageStat.Stat(edge_img, mask=union_mask)
            edge_scores.append(stat.mean[0])

    if n == 2:
        score_0, score_1 = edge_scores[0], edge_scores[1]
        conf = abs(score_0 - score_1) / (score_0 + score_1 + 1e-6)

        if score_0 > score_1:
            watermarked_indices.add(0)
            clean_indices.add(1)
        else:
            watermarked_indices.add(1)
            clean_indices.add(0)

        clean_idx = next(iter(clean_indices))
        analysis = CornerAnalysis(
            corner=corner_region.name,
            box=box,
            status="resolved" if conf >= config.min_confidence_threshold else "resolved_low_confidence",
            clean_source=str(image_paths[clean_idx]),
            watermarked_sources=[str(image_paths[idx]) for idx in sorted(watermarked_indices)],
            confidence=conf,
            metrics={
                "edge_scores": {
                    image_paths[0].name: round(score_0, 2),
                    image_paths[1].name: round(score_1, 2),
                },
                "confidence": round(conf, 4),
                "significant_pixels": max_sig_pixels,
                "significant_ratio": round(max_sig_ratio, 6),
            },
        )
        return analysis, watermarked_indices

    # General case for N >= 3:
    # Mutual consistency: clean images agree with each other (diff ~ 0)
    # The image with watermark is an outlier and has significantly higher edge score in the mask
    avg_score = sum(edge_scores) / n
    for i in range(n):
        if edge_scores[i] > avg_score:
            watermarked_indices.add(i)
        else:
            clean_indices.add(i)

    if not clean_indices:
        raise InsufficientCleanCoverageError(
            f"No clean source found for corner {corner_region.name.value}.",
            missing_corners=[corner_region.name.value],
        )

    # Calculate confidence as gap between lowest watermarked score and highest clean score
    min_wm_score = min(edge_scores[idx] for idx in watermarked_indices) if watermarked_indices else 0.0
    max_clean_score = max(edge_scores[idx] for idx in clean_indices)
    denom = min_wm_score + max_clean_score + 1e-6
    conf = max(0.0, (min_wm_score - max_clean_score) / denom) if watermarked_indices else 1.0

    clean_idx = min(clean_indices, key=lambda idx: edge_scores[idx])
    analysis = CornerAnalysis(
        corner=corner_region.name,
        box=box,
        status="resolved" if conf >= config.min_confidence_threshold else "resolved_low_confidence",
        clean_source=str(image_paths[clean_idx]),
        watermarked_sources=[str(image_paths[idx]) for idx in sorted(watermarked_indices)],
        confidence=conf,
        metrics={
            "edge_scores": {image_paths[i].name: round(edge_scores[i], 2) for i in range(n)},
            "confidence": round(conf, 4),
            "significant_pixels": max_sig_pixels,
            "significant_ratio": round(max_sig_ratio, 6),
        },
    )
    return analysis, watermarked_indices


def detect_watermarks_and_plan(
    images: list[Image.Image],
    image_paths: list[Path],
    config: WatermarkCleanerConfig,
) -> CaseDetectionPlan:
    """Analyze all 4 corners, detect watermarks, and construct an actionable cleaning plan."""
    width, height = images[0].size
    corner_regions = compute_corner_regions(width, height, config)

    corner_analyses: dict[CornerName, CornerAnalysis] = {}
    corner_wm_map: dict[CornerName, set[int]] = {}
    image_wm_corners: dict[int, set[CornerName]] = {i: set() for i in range(len(images))}

    differing_corners = 0

    for corner_name, region in corner_regions.items():
        analysis, wm_indices = analyze_corner_roi(region, images, image_paths, config)
        corner_analyses[corner_name] = analysis
        corner_wm_map[corner_name] = wm_indices

        if wm_indices:
            differing_corners += 1
            for idx in wm_indices:
                image_wm_corners[idx].add(corner_name)

    # If no corner differed at all, check if images are duplicate / identical
    if differing_corners == 0:
        raise DuplicateWatermarkError(
            "All input images have identical decoded pixels across all corner ROIs. "
            "Unable to isolate or clean watermarks without an alternative clean source."
        )

    warnings: list[str] = []
    # Distribution anomalies lower confidence but do not suppress a replaceable preview.
    invalid_dist: dict[str, list[str]] = {}
    for idx, wm_corners in image_wm_corners.items():
        if len(wm_corners) != 1:
            invalid_dist[image_paths[idx].name] = [c.value for c in wm_corners]

    if invalid_dist:
        warnings.append(
            "Expected exactly one watermark corner per image; detected distribution: "
            f"{invalid_dist}. Preview was generated from the best available plan."
        )

    for corner, analysis in corner_analyses.items():
        if analysis.status == "resolved_low_confidence":
            warnings.append(
                f"Low detection confidence at {corner.value}: {analysis.confidence * 100:.2f}%."
            )

    # Verify clean coverage for all 4 corners
    clean_sources: dict[CornerName, Path] = {}
    missing_corners: list[str] = []

    for corner_name in CornerName:
        analysis = corner_analyses[corner_name]
        if analysis.clean_source:
            clean_sources[corner_name] = Path(analysis.clean_source)
        else:
            missing_corners.append(corner_name.value)

    if missing_corners:
        raise InsufficientCleanCoverageError(
            f"Missing clean coverage for corners: {missing_corners}",
            missing_corners=missing_corners,
        )

    # Select base image: image with fewest watermarks (cleanest image)
    base_idx = min(range(len(images)), key=lambda i: len(image_wm_corners[i]))
    base_path = image_paths[base_idx]

    # Corners that must be replaced in the base image
    corners_to_replace = sorted(list(image_wm_corners[base_idx]), key=lambda c: c.value)

    return CaseDetectionPlan(
        corner_analyses=corner_analyses,
        base_image_index=base_idx,
        base_image_path=base_path,
        corners_to_replace=corners_to_replace,
        clean_sources=clean_sources,
        warnings=warnings,
    )
