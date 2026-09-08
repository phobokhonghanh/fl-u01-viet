"""Variant pair comparison and changed-region analysis for watermark workflow."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageChops, ImageFilter

from backend.watermark_cleaner.config import WatermarkCleanerConfig
from backend.watermark_cleaner.detector import detect_watermarks_and_plan
from backend.watermark_cleaner.exceptions import (
    ConfigValidationError,
    CorruptedImageError,
    DimensionMismatchError,
    FileSizeDeltaExceededError,
    InputValidationError,
    ModeMismatchError,
    SourceImageMismatchError,
    WatermarkCleanerError,
)
from backend.watermark_cleaner.validator import (
    validate_case_inputs,
    validate_source_image_consistency,
)

logger = logging.getLogger("watermark_workflow.comparison")

VariantPairComparison = dict[str, Any]


def _file_fingerprint(path: Path) -> tuple[int, int] | None:
    """Return the cheap identity used to reuse a persisted pair comparison."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return int(stat.st_size), int(stat.st_mtime_ns)


def _comparison(
    first: Path,
    second: Path,
    *,
    status: str,
    reason: str,
    distinct: bool = False,
    changed_corners: Sequence[str] = (),
    groups: Sequence[dict[str, Any]] = (),
    metrics: dict[str, Any] | None = None,
    error_type: str | None = None,
    detector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable JSON-compatible comparison payload."""
    changed = list(changed_corners)
    first_fingerprint = _file_fingerprint(first)
    second_fingerprint = _file_fingerprint(second)
    return {
        "first": str(first),
        "second": str(second),
        "fingerprints": [
            list(first_fingerprint) if first_fingerprint else None,
            list(second_fingerprint) if second_fingerprint else None,
        ],
        "status": status,
        "distinct": bool(distinct),
        "changed_corners": changed,
        "groups": list(groups),
        "metrics": metrics or {},
        "reason": reason,
        "error_type": error_type,
        "detector": detector or {},
    }


def _resize_for_comparison(image: Image.Image, max_dimension: int = 768) -> Image.Image:
    """Create a bounded RGB analysis image.

    The workflow may process 4K images. Comparison only needs the location of
    changed watermark regions, so a bounded copy keeps the adapter's working
    memory close to one pair of images while leaving the lossless cleaner to do
    the actual composition.
    """
    rgb = image.convert("RGB")
    if max(rgb.size) <= max_dimension:
        return rgb
    scale = max_dimension / max(rgb.size)
    size = (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale)))
    return rgb.resize(size, Image.Resampling.BILINEAR)


def _threshold_difference(
    first: Image.Image,
    second: Image.Image,
    threshold: float,
) -> Image.Image:
    """Return a binary mask of meaningful differences between two images."""
    diff = ImageChops.difference(first, second).convert("L")
    threshold_int = max(0, min(255, int(threshold)))
    # ``> threshold`` mirrors the detector's pixel threshold contract.
    return diff.point(lambda value: 255 if value > threshold_int else 0, mode="L")


def _connected_components(mask: Image.Image) -> list[dict[str, int]]:
    """Find coarse connected regions in a bounded binary mask.

    Pillow does not expose connected components. The mask is capped at 768px
    on its longest edge, making this small scan predictable for large source
    photos. Regions are intentionally coarse; later grouping joins separated
    letters/lines from one watermark.
    """
    width, height = mask.size
    pixels = mask.load()
    visited = bytearray(width * height)
    components: list[dict[str, int]] = []

    for y in range(height):
        row_offset = y * width
        for x in range(width):
            position = row_offset + x
            if visited[position] or not pixels[x, y]:
                continue

            visited[position] = 1
            stack = [position]
            min_x = max_x = x
            min_y = max_y = y
            area = 0

            while stack:
                current = stack.pop()
                cy, cx = divmod(current, width)
                area += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)

                for ny in range(max(0, cy - 1), min(height, cy + 2)):
                    start = max(0, cx - 1)
                    stop = min(width, cx + 2)
                    for nx in range(start, stop):
                        neighbour = ny * width + nx
                        if not visited[neighbour] and pixels[nx, ny]:
                            visited[neighbour] = 1
                            stack.append(neighbour)

            components.append(
                {
                    "left": min_x,
                    "top": min_y,
                    "right": max_x + 1,
                    "bottom": max_y + 1,
                    "area": area,
                }
            )

    return components


def _bbox_gap(first: dict[str, int], second: dict[str, int]) -> tuple[int, int]:
    horizontal = max(
        0,
        first["left"] - second["right"],
        second["left"] - first["right"],
    )
    vertical = max(
        0,
        first["top"] - second["bottom"],
        second["top"] - first["bottom"],
    )
    return horizontal, vertical


def _group_components(
    components: list[dict[str, int]],
    width: int,
    height: int,
) -> list[dict[str, int]]:
    """Join nearby connected pieces which belong to one watermark."""
    if not components:
        return []

    # A JPEG with a broad source mismatch can produce thousands of isolated
    # speckles. Pair grouping is intentionally bounded; such a pair should be
    # reported as uncertain and allow the coordinator to try another variant,
    # rather than spending quadratic time on noise.
    if len(components) > 512:
        return []

    # Hatches and text in the same watermark are commonly separated by a few
    # pixels after downsampling. This gap is deliberately small compared to
    # the distance between two corner watermarks.
    gap_x = max(2, round(width * 0.045))
    gap_y = max(2, round(height * 0.045))
    parent = list(range(len(components)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parent[second_root] = first_root

    for index, first in enumerate(components):
        for other_index in range(index + 1, len(components)):
            second = components[other_index]
            horizontal, vertical = _bbox_gap(first, second)
            if horizontal <= gap_x and vertical <= gap_y:
                union(index, other_index)

    grouped: dict[int, dict[str, int]] = {}
    for index, component in enumerate(components):
        root = find(index)
        target = grouped.setdefault(
            root,
            {
                "left": component["left"],
                "top": component["top"],
                "right": component["right"],
                "bottom": component["bottom"],
                "area": 0,
            },
        )
        target["left"] = min(target["left"], component["left"])
        target["top"] = min(target["top"], component["top"])
        target["right"] = max(target["right"], component["right"])
        target["bottom"] = max(target["bottom"], component["bottom"])
        target["area"] += component["area"]

    return list(grouped.values())


def _corner_for_group(
    group: dict[str, int],
    width: int,
    height: int,
) -> str | None:
    """Assign one coarse changed region to a physical image corner.

    The detector's top and bottom ROIs intentionally overlap at 52% image
    height. This assignment uses the image midpoint and edge distance instead
    of counting ROI hits, so one watermark crossing that overlap remains one
    changed region. A region spanning most of an axis is left uncertain.
    """
    left, top, right, bottom = (
        group["left"],
        group["top"],
        group["right"],
        group["bottom"],
    )
    if right - left > width * 0.70 or bottom - top > height * 0.70:
        return None

    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    horizontal = "L" if center_x <= width / 2.0 else "R"

    # A watermark near the top/bottom midpoint can be present in both 0.52
    # ROIs. Pick the nearer edge; ties use the center so a single region is
    # still represented by one corner. Distinct regions remain separate.
    distance_top = top
    distance_bottom = height - bottom
    if distance_top < distance_bottom:
        vertical = "T"
    elif distance_bottom < distance_top:
        vertical = "B"
    else:
        vertical = "T" if center_y <= height / 2.0 else "B"

    return f"{vertical}{horizontal}"


def _changed_region_summary(
    first: Image.Image,
    second: Image.Image,
    config: WatermarkCleanerConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build coarse watermark region evidence for one pair."""
    bounded_first = _resize_for_comparison(first)
    bounded_second = _resize_for_comparison(second)
    try:
        mask = _threshold_difference(
            bounded_first,
            bounded_second,
            config.jpeg_noise_threshold,
        )
        significant_histogram = mask.histogram()
        significant_pixels = sum(significant_histogram[1:])
        total_pixels = mask.width * mask.height

        # Dilation joins the separated strokes of a single watermark. It is
        # only used for region topology; significant-pixel metrics come from
        # the undilated mask and therefore do not inflate evidence.
        kernel = max(3, min(31, int(round(min(mask.size) * 0.025)) | 1))
        dilated = mask.filter(ImageFilter.MaxFilter(kernel))
        components = [
            component
            for component in _connected_components(dilated)
            if component["area"] >= 2
        ]
        topology_overloaded = len(components) > 512
        groups = _group_components(components, mask.width, mask.height)

        scale_x = mask.width / max(1, first.width)
        scale_y = mask.height / max(1, first.height)
        corner_width = max(1, round(mask.width * config.corner_width_fraction))
        corner_height = max(1, round(mask.height * config.corner_height_fraction))
        corner_area = max(1, corner_width * corner_height)
        # Keep the same spirit as detector thresholds while scaling the pixel
        # count for downsampled analysis images.
        scaled_min_count = max(
            4,
            round(config.min_diff_pixel_count * scale_x * scale_y),
        )
        required_count = max(
            scaled_min_count,
            round(config.min_diff_pixel_ratio * corner_area),
        )

        mask_pixels = mask.load()
        useful_groups: list[dict[str, Any]] = []
        for group in groups:
            left = max(0, group["left"])
            top = max(0, group["top"])
            right = min(mask.width, group["right"])
            bottom = min(mask.height, group["bottom"])
            original_count = 0
            for y in range(top, bottom):
                for x in range(left, right):
                    if mask_pixels[x, y]:
                        original_count += 1

            corner = _corner_for_group(group, mask.width, mask.height)
            # A group can be split into many tiny pieces. Keep the piece for
            # topology, but only promote it once the aggregate has enough
            # evidence. Tiny noise is discarded here.
            if original_count < max(2, round(required_count * 0.10)):
                continue
            useful_groups.append(
                {
                    "box": [left, top, right, bottom],
                    "corner": corner,
                    "significant_pixels": original_count,
                    "dilated_pixels": group["area"],
                    "ratio_of_corner_roi": round(original_count / corner_area, 6),
                    "ratio_of_image": round(original_count / max(1, total_pixels), 6),
                }
            )

        # For evidence, combine all groups assigned to a corner. A watermark
        # spanning the top/bottom ROI overlap is one group after dilation and
        # therefore cannot be counted twice.
        corner_totals: dict[str, int] = {}
        for group in useful_groups:
            corner = group.get("corner")
            if corner:
                corner_totals[corner] = corner_totals.get(corner, 0) + int(
                    group["significant_pixels"]
                )

        active_corners = sorted(
            corner
            for corner, count in corner_totals.items()
            if count >= required_count
            and count / corner_area >= config.min_diff_pixel_ratio
        )

        metrics = {
            "analysis_size": [mask.width, mask.height],
            "significant_pixels": significant_pixels,
            "significant_ratio": round(significant_pixels / max(1, total_pixels), 6),
            "required_group_pixels": required_count,
            "component_count": len(components),
            "group_count": len(useful_groups),
            "topology_overloaded": topology_overloaded,
            "corner_totals": corner_totals,
            "active_corners": active_corners,
            "roi_height_fraction": config.corner_height_fraction,
            "roi_overlap_avoided": bool(config.corner_height_fraction > 0.5),
        }
        return useful_groups, metrics
    finally:
        # ``bounded_first`` and ``bounded_second`` are local references; closing
        # them releases resized copies promptly for large batches.
        bounded_first.close()
        bounded_second.close()


def _detector_summary(plan: Any) -> dict[str, Any]:
    """Extract small source-to-corner metadata from the legacy detector."""
    summary: dict[str, Any] = {}
    analyses = getattr(plan, "corner_analyses", {})
    for corner, analysis in analyses.items():
        corner_name = getattr(corner, "value", str(corner))
        box = getattr(analysis, "box", None)
        summary[corner_name] = {
            "status": getattr(analysis, "status", None),
            "clean_source": getattr(analysis, "clean_source", None),
            "watermarked_sources": list(
                getattr(analysis, "watermarked_sources", []) or []
            ),
            "confidence": getattr(analysis, "confidence", None),
            "box": list(box) if box is not None else None,
            "metrics": getattr(analysis, "metrics", {}) or {},
        }
    return summary


def compare_variant_pair(
    first: str | Path,
    second: str | Path,
    config: WatermarkCleanerConfig | None = None,
) -> VariantPairComparison:
    """Compare two downloaded variants for genuinely different WM corners.

    A byte or pixel difference in one corner is insufficient: two attempts can
    put a differently rendered watermark at the same corner. The comparison
    first validates that the pair comes from the same source, then groups a
    bounded difference mask into physical corner regions. Only two distinct
    corner regions qualify as a usable pair. The legacy detector is run as a
    secondary source of per-corner provenance and remains the authority used
    by the actual composition step.

    The function never raises for ordinary pair problems. It returns a
    JSON-compatible mapping with ``status`` set to ``distinct``, ``duplicate``,
    ``uncertain`` or ``blocked`` so a coordinator can continue trying other
    pairs when one download is bad.
    """
    cfg = config or WatermarkCleanerConfig()
    first_path = Path(first)
    second_path = Path(second)

    if first_path.resolve() == second_path.resolve():
        return _comparison(
            first_path,
            second_path,
            status="duplicate",
            reason="The two variants point to the same file.",
        )

    try:
        validate_case_inputs(
            [first_path, second_path],
            case_name="variant_pair",
            case_dir=first_path.parent,
            config=cfg,
        )
        with Image.open(first_path) as first_open, Image.open(second_path) as second_open:
            first_open.load()
            second_open.load()
            first_image = first_open.copy()
            second_image = second_open.copy()

        try:
            validate_source_image_consistency(
                [first_image, second_image],
                [first_path, second_path],
                cfg,
            )
            groups, region_metrics = _changed_region_summary(
                first_image,
                second_image,
                cfg,
            )

            detector_details: dict[str, Any] = {}
            detector_error: str | None = None
            try:
                plan = detect_watermarks_and_plan(
                    [first_image, second_image],
                    [first_path, second_path],
                    cfg,
                )
                detector_details = _detector_summary(plan)
            except Exception as exc:
                # The strong topology gate is deliberately independent of the
                # detector's overlapping ROI count. The actual clean attempt
                # will still exercise the detector and report its exact error.
                detector_error = f"{type(exc).__name__}: {exc}"

            active_corners = list(region_metrics.get("active_corners", []))
            unknown_groups = sum(1 for group in groups if not group.get("corner"))
            detector_sources: list[str] = []
            detector_corners = 0
            detector_corner_names: set[str] = set()
            for corner_name, info in detector_details.items():
                sources = list(info.get("watermarked_sources", []) or [])
                conf = float(info.get("confidence") or 0.0)
                status = info.get("status")
                # Filter out low-confidence noise from detector (< 10%)
                if sources and (status == "resolved" or conf >= 0.10):
                    detector_corners += 1
                    detector_corner_names.add(corner_name)
                    detector_sources.extend(sources)
            normalised_detector_sources = [
                str(Path(source).resolve()) for source in detector_sources
            ]
            expected_sources = {
                str(first_path.resolve()),
                str(second_path.resolve()),
            }
            source_counts = {
                source: normalised_detector_sources.count(source)
                for source in expected_sources
            }
            # Prune noise from active_corners when detector identified the primary highest-% watermark per source
            if (
                detector_corners == 2
                and set(normalised_detector_sources) == expected_sources
                and all(count == 1 for count in source_counts.values())
                and (detector_corner_names.issubset(set(active_corners)) or len(active_corners) >= 2)
            ):
                active_corners = sorted(detector_corner_names)
                detector_has_one_source_per_corner = True
            else:
                detector_has_one_source_per_corner = bool(
                    detector_details
                    and detector_corners == 2
                    and unknown_groups == 0
                    and set(active_corners) == detector_corner_names
                    and set(normalised_detector_sources) == expected_sources
                    and all(count == 1 for count in source_counts.values())
                )
            region_metrics["detector_corners"] = detector_corners
            region_metrics["detector_source_counts"] = source_counts

            if len(active_corners) == 2 and detector_has_one_source_per_corner:
                return _comparison(
                    first_path,
                    second_path,
                    status="distinct",
                    reason="The pair contains meaningful changes in exactly two distinct watermark corners.",
                    distinct=True,
                    changed_corners=active_corners,
                    groups=groups,
                    metrics=region_metrics,
                    detector=detector_details,
                )

            group_corners = {g.get("corner") for g in groups if g.get("corner")}

            if not active_corners and not groups:
                return _comparison(
                    first_path,
                    second_path,
                    status="duplicate",
                    reason="The variants are identical within the configured noise threshold.",
                    metrics=region_metrics,
                    detector=detector_details,
                )

            # If detector confirmed both variants have their primary watermark in the same corner
            if len(detector_corner_names) == 1 and not detector_has_one_source_per_corner:
                same_corner = next(iter(detector_corner_names))
                return _comparison(
                    first_path,
                    second_path,
                    status="duplicate",
                    reason=f"Both variants have their highest watermark evidence in the same corner ({same_corner}).",
                    changed_corners=[same_corner],
                    groups=groups,
                    metrics=region_metrics,
                    detector=detector_details,
                )

            # Differences confined to at most one physical corner -> duplicate watermark
            if len(active_corners) <= 1 and unknown_groups == 0 and len(group_corners) <= 1:
                corner = (list(active_corners) or list(group_corners) or ["same corner"])[0]
                return _comparison(
                    first_path,
                    second_path,
                    status="duplicate",
                    reason=f"The variants differ in one physical watermark corner ({corner}); no alternate corner was found.",
                    changed_corners=list(active_corners or group_corners),
                    groups=groups,
                    metrics=region_metrics,
                    detector=detector_details,
                )

            if len(active_corners) >= 2:
                return _comparison(
                    first_path,
                    second_path,
                    status="uncertain",
                    reason=(
                        "Meaningful differences were found in multiple regions, but the "
                        "legacy detector could not confirm exactly one watermark source "
                        "per distinct corner."
                    ),
                    changed_corners=active_corners,
                    groups=groups,
                    metrics=region_metrics,
                    detector=detector_details,
                )

            reason = (
                "The changed regions do not provide reliable evidence of two distinct "
                "watermark corners."
            )
            if detector_error:
                reason += f" Legacy detector: {detector_error}."
            return _comparison(
                first_path,
                second_path,
                status="uncertain",
                reason=reason,
                changed_corners=active_corners,
                groups=groups,
                metrics=region_metrics,
                detector=detector_details,
            )
        finally:
            first_image.close()
            second_image.close()
    except (
        ConfigValidationError,
        CorruptedImageError,
        DimensionMismatchError,
        FileSizeDeltaExceededError,
        InputValidationError,
        ModeMismatchError,
        SourceImageMismatchError,
        WatermarkCleanerError,
        OSError,
    ) as exc:
        return _comparison(
            first_path,
            second_path,
            status="blocked",
            reason=f"Cannot compare variants: {type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
        )
    except Exception as exc:  # Defensive boundary for a workflow worker.
        logger.exception("Unexpected variant comparison error")
        return _comparison(
            first_path,
            second_path,
            status="blocked",
            reason=f"Unexpected comparison error: {type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
        )


__all__ = [
    "VariantPairComparison",
    "_file_fingerprint",
    "compare_variant_pair",
]
