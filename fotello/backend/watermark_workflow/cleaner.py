"""Workflow-facing adapter for the existing watermark cleaner.

The standalone cleaner works on one directory and deliberately owns the
``clean_result.png`` file in that directory.  A workflow has a different
lifecycle: one logical output can have several downloaded variants, and a
successful result must survive later retry attempts.  This module keeps that
lifecycle outside :mod:`backend.watermark_cleaner` and only publishes a result
after a pair has been checked and the old cleaner reports a *complete* result.

The public entry point is :func:`clean_output`.  It accepts paths to variants
of one logical output and returns a small JSON-compatible dictionary with one
of ``cleaned``, ``need_variant``, ``needs_review`` or ``blocked`` statuses.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

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
from backend.watermark_cleaner.pipeline import clean_case
from backend.watermark_cleaner.validator import (
    validate_case_inputs,
    validate_source_image_consistency,
)


logger = logging.getLogger("watermark_workflow.cleaner")

_STATUS_CLEANED = "cleaned"
_STATUS_NEED_VARIANT = "need_variant"
_STATUS_NEEDS_REVIEW = "needs_review"
_STATUS_BLOCKED = "blocked"
def _normalise_output_name(output_name: str | Path) -> str:
    """Return a safe, lossless output filename.

    Workflow manifests normally carry ``img01`` or ``img01.png``.  Keeping a
    supplied PNG/TIFF suffix makes the function friendly to callers that have
    already normalised names; a bare stem receives the configured PNG suffix
    at the publication boundary.
    """

    name = Path(str(output_name)).name.strip()
    if not name or name in {".", ".."}:
        return ""

    suffix = Path(name).suffix.lower()
    if suffix in {".png", ".tif", ".tiff"}:
        return name
    # A workflow output is always lossless.  A remote input extension such as
    # .jpg is treated as part of the stem rather than used for the output.
    return f"{Path(name).stem or name}.png"


def _as_paths(variants: Iterable[str | Path]) -> list[Path]:
    paths: list[Path] = []
    for variant in variants:
        if variant is None:  # type: ignore[comparison-overlap]
            continue
        text = str(variant).strip()
        if text:
            paths.append(Path(text))
    return paths


def _cancelled(is_cancelled: Callable[[], bool] | None) -> tuple[bool, str | None]:
    if is_cancelled is None:
        return False, None
    try:
        return bool(is_cancelled()), None
    except Exception as exc:  # A broken cancellation hook must stop safely.
        return True, f"Cancellation callback failed: {type(exc).__name__}: {exc}"


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

    The workflow may process 4K images.  Comparison only needs the location of
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

    Pillow does not expose connected components.  The mask is capped at 768px
    on its longest edge, making this small scan predictable for large source
    photos.  Regions are intentionally coarse; later grouping joins separated
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
    # speckles.  Pair grouping is intentionally bounded; such a pair should be
    # reported as uncertain and allow the coordinator to try another variant,
    # rather than spending quadratic time on noise.
    if len(components) > 512:
        return []

    # Hatches and text in the same watermark are commonly separated by a few
    # pixels after downsampling.  This gap is deliberately small compared to
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
    height.  This assignment uses the image midpoint and edge distance instead
    of counting ROI hits, so one watermark crossing that overlap remains one
    changed region.  A region spanning most of an axis is left uncertain.
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
    # ROIs.  Pick the nearer edge; ties use the center so a single region is
    # still represented by one corner.  Distinct regions remain separate.
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

        # Dilation joins the separated strokes of a single watermark.  It is
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
            # A group can be split into many tiny pieces.  Keep the piece for
            # topology, but only promote it once the aggregate has enough
            # evidence.  Tiny noise is discarded here.
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

        # For evidence, combine all groups assigned to a corner.  A watermark
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
        summary[corner_name] = {
            "status": getattr(analysis, "status", None),
            "clean_source": getattr(analysis, "clean_source", None),
            "watermarked_sources": list(
                getattr(analysis, "watermarked_sources", []) or []
            ),
            "confidence": getattr(analysis, "confidence", None),
        }
    return summary


def compare_variant_pair(
    first: str | Path,
    second: str | Path,
    config: WatermarkCleanerConfig | None = None,
) -> VariantPairComparison:
    """Compare two downloaded variants for genuinely different WM corners.

    A byte or pixel difference in one corner is insufficient: two attempts can
    put a differently rendered watermark at the same corner.  The comparison
    first validates that the pair comes from the same source, then groups a
    bounded difference mask into physical corner regions.  Only two distinct
    corner regions qualify as a usable pair.  The legacy detector is run as a
    secondary source of per-corner provenance and remains the authority used
    by the actual composition step.

    The function never raises for ordinary pair problems.  It returns a
    a JSON-compatible mapping with ``status`` set to ``distinct``, ``duplicate``,
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
        validated = validate_case_inputs(
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
                # detector's overlapping ROI count.  The actual clean attempt
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
                # Filter out low-confidence noise from detector
                if sources and (status == "resolved" or conf >= 0.05):
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


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _file_fingerprint(path: Path) -> tuple[int, int] | None:
    """Return the cheap identity used to reuse a persisted pair comparison."""

    try:
        stat = path.stat()
    except OSError:
        return None
    return int(stat.st_size), int(stat.st_mtime_ns)


def _pair_cache_key(first: Path, second: Path) -> str:
    first_key = str(first.resolve())
    second_key = str(second.resolve())
    return "\n".join(sorted((first_key, second_key)))


def _load_comparison_cache(
    report_path: Path,
    output_id: str,
) -> dict[str, dict[str, Any]]:
    """Load comparisons from a prior incomplete run when their files persist."""

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(report, dict) or str(report.get("output_id", "")) != output_id:
        return {}
    cached: dict[str, dict[str, Any]] = {}
    comparisons = report.get("comparisons")
    if not isinstance(comparisons, list):
        return cached
    for item in comparisons:
        if not isinstance(item, dict):
            continue
        first = item.get("first")
        second = item.get("second")
        if not first or not second:
            continue
        first_path = Path(str(first))
        second_path = Path(str(second))
        first_fingerprint = _file_fingerprint(first_path)
        second_fingerprint = _file_fingerprint(second_path)
        fingerprints = item.get("fingerprints")
        if fingerprints != [list(first_fingerprint) if first_fingerprint else None,
                            list(second_fingerprint) if second_fingerprint else None]:
            continue
        cached[_pair_cache_key(first_path, second_path)] = dict(item)
    return cached


def _write_attempt_report(attempt_dir: Path, payload: dict[str, Any]) -> Path:
    report_path = attempt_dir / "report.json"
    _atomic_json(report_path, payload)
    return report_path


def _publish_clean_output(source: Path, destination: Path) -> None:
    """Copy a successful attempt into ``clean/`` with atomic replacement."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(source, temporary)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _existing_clean_result(
    output_path: Path,
    report_path: Path,
    output_id: str,
    output_name: str,
) -> dict[str, Any] | None:
    """Return a stable result when a prior clean output already exists."""

    if not output_path.is_file() or not report_path.is_file():
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(report, dict):
        return None
    if str(report.get("output_id", "")) != str(output_id):
        return None
    if report.get("status") != _STATUS_CLEANED:
        return None
    try:
        with Image.open(output_path) as image:
            image.verify()
    except Exception:
        # An incomplete/corrupt destination is not treated as a completed
        # result; an upcoming successful attempt may replace it atomically.
        return None
    return {
        "output_id": output_id,
        "output_name": output_name,
        "status": _STATUS_CLEANED,
        "reason": "A completed clean output already exists; it was preserved.",
        "output_path": str(output_path),
        "report_path": str(report_path),
        "cleaner_attempts": [],
    }


def _pair_order(paths: Sequence[Path]) -> list[tuple[int, int]]:
    """Prioritise newest-with-previous pairs, then remaining older pairs."""

    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for newest in range(len(paths) - 1, 0, -1):
        for previous in range(newest - 1, -1, -1):
            pair = (previous, newest)
            if pair not in seen:
                pairs.append(pair)
                seen.add(pair)
    return pairs


def _is_blocking_error(error_type: str | None) -> bool:
    return error_type in {
        "ConfigValidationError",
        "CorruptedImageError",
        "DimensionMismatchError",
        "FileSizeDeltaExceededError",
        "ImageSaveError",
        "InputValidationError",
        "ModeMismatchError",
        "OSError",
        "PermissionError",
        "SourceImageMismatchError",
    }


def _cleaner_result_values(result: Any) -> tuple[dict[str, Any], bool, str, Path | None, list[str], str | None, str | None]:
    """Read either the legacy ``CleaningResult`` or a mapping test double."""

    if isinstance(result, dict):
        payload = dict(result)
        success = bool(payload.get("success"))
        status = str(payload.get("status") or "failed")
        output_value = payload.get("output_path")
        warnings = list(payload.get("warnings") or [])
        error_type = payload.get("error_type")
        error_message = payload.get("error_message")
    else:
        payload = result.to_dict() if hasattr(result, "to_dict") else {}
        success = bool(getattr(result, "success", False))
        status = str(getattr(result, "status", "failed") or "failed")
        output_value = getattr(result, "output_path", None)
        warnings = list(getattr(result, "warnings", []) or [])
        error_type = getattr(result, "error_type", None)
        error_message = getattr(result, "error_message", None)
    output_path = Path(str(output_value)) if output_value else None
    return payload, success, status, output_path, warnings, error_type, error_message


def clean_output(
    output_id: str,
    output_name: str,
    variants: list[str],
    output_dir: str | Path,
    is_cancelled: Callable[[], bool] | None = None,
    *,
    config: WatermarkCleanerConfig | None = None,
) -> dict[str, Any]:
    """Clean one logical workflow output from downloaded variants.

    ``variants`` are ordered from oldest to newest.  Every candidate is kept
    in its own ``attempts/<output_id>/...`` directory.  The first complete,
    warning-free clean attempt is copied atomically to
    ``<output_dir>/clean/<output_name>``.  Existing clean files are returned as
    completed results and are never removed by a later retry.
    """

    cfg = config or WatermarkCleanerConfig()
    root = Path(output_dir)
    filename = _normalise_output_name(output_name)
    clean_path = root / "clean" / filename if filename else root / "clean" / ""
    report_path = (
        root / "reports" / f"{Path(filename).stem}.json"
        if filename
        else root / "reports" / "invalid-output.json"
    )

    if not str(output_id).strip() or not filename:
        result = {
            "output_id": str(output_id),
            "output_name": str(output_name),
            "status": _STATUS_BLOCKED,
            "reason": "output_id and output_name are required.",
            "report_path": str(report_path),
        }
        try:
            _atomic_json(report_path, result)
        except OSError:
            pass
        return result

    existing = _existing_clean_result(
        clean_path,
        report_path,
        str(output_id),
        filename,
    )
    if existing is not None:
        return existing

    cancelled, cancel_reason = _cancelled(is_cancelled)
    if cancelled:
        result = {
            "output_id": str(output_id),
            "output_name": filename,
            "status": _STATUS_BLOCKED,
            "reason": cancel_reason or "Cleaning was cancelled before starting.",
            "report_path": str(report_path),
            "cleaner_attempts": [],
        }
        try:
            _atomic_json(report_path, result)
        except OSError:
            pass
        return result

    paths = _as_paths(variants)
    if len(paths) < 2:
        result = {
            "output_id": str(output_id),
            "output_name": filename,
            "status": _STATUS_NEED_VARIANT,
            "reason": "At least two downloaded variants are required.",
            "report_path": str(report_path),
            "cleaner_attempts": [],
        }
        try:
            _atomic_json(report_path, result)
        except OSError:
            pass
        return result

    attempts_root = root / "attempts" / str(output_id)
    attempts_root.mkdir(parents=True, exist_ok=True)
    comparison_cache = _load_comparison_cache(report_path, str(output_id))
    comparisons: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    blocked_reasons: list[str] = []
    duplicate_reasons: list[str] = []
    uncertain_reasons: list[str] = []
    review_candidate: dict[str, Any] | None = None

    pair_indices = _pair_order(paths)
    for attempt_number, (first_index, second_index) in enumerate(pair_indices, start=1):
        cancelled, cancel_reason = _cancelled(is_cancelled)
        if cancelled:
            blocked_reasons.append(cancel_reason or "Cleaning was cancelled.")
            break

        first_path = paths[first_index]
        second_path = paths[second_index]
        cached_comparison = comparison_cache.get(_pair_cache_key(first_path, second_path))
        if cached_comparison is not None:
            comparison = dict(cached_comparison)
        else:
            comparison = compare_variant_pair(first_path, second_path, config=cfg)
        comparison_payload = dict(comparison)
        comparisons.append(comparison_payload)
        comparison_status = str(comparison_payload.get("status") or "uncertain")

        if comparison_status == "blocked":
            blocked_reasons.append(str(comparison_payload.get("reason", "Pair is blocked.")))
            continue
        if comparison_status == "duplicate":
            duplicate_reasons.append(str(comparison_payload.get("reason", "Same watermark corner.")))
            continue
        if comparison_status != "distinct":
            uncertain_reasons.append(str(comparison_payload.get("reason", "Pair is uncertain.")))
            continue

        attempt_name = f"attempt-{attempt_number:03d}-{uuid.uuid4().hex[:10]}"
        attempt_dir = attempts_root / attempt_name
        attempt_dir.mkdir(parents=True, exist_ok=False)
        attempt_record: dict[str, Any] = {
            "attempt": attempt_number,
            "variants": [str(first_path), str(second_path)],
            "comparison": comparison_payload,
            "path": str(attempt_dir),
        }

        try:
            cleaner_result = clean_case(
                [first_path, second_path],
                case_name=str(output_id),
                case_dir=first_path.parent,
                output_dir=attempt_dir,
                config=cfg,
            )
            (
                cleaner_payload,
                cleaner_success,
                cleaner_status,
                attempt_output,
                cleaner_warnings,
                error_type,
                error_message,
            ) = _cleaner_result_values(cleaner_result)
            attempt_record["cleaner"] = cleaner_payload
            attempt_record["output_path"] = str(attempt_output) if attempt_output else None
            attempt_record["report_path"] = cleaner_payload.get("report_path")
            attempts.append(attempt_record)
            _write_attempt_report(attempt_dir, attempt_record)

            cancelled, cancel_reason = _cancelled(is_cancelled)
            if cancelled:
                blocked_reasons.append(cancel_reason or "Cleaning was cancelled after an attempt.")
                break

            complete = bool(
                cleaner_success
                and cleaner_status == "complete"
                and float(cleaner_payload.get("completion_percentage", 0.0)) == 100.0
                and not cleaner_warnings
                and attempt_output is not None
                and attempt_output.is_file()
            )
            if complete and attempt_output is not None:
                # A concurrent worker may have published while this attempt
                # ran.  Preserve whichever completed output is already there.
                if clean_path.is_file():
                    existing = _existing_clean_result(
                        clean_path,
                        report_path,
                        str(output_id),
                        filename,
                    )
                    if existing is not None:
                        return existing
                _publish_clean_output(attempt_output, clean_path)
                result = {
                    "output_id": str(output_id),
                    "output_name": filename,
                    "status": _STATUS_CLEANED,
                    "reason": "A distinct watermark-corner pair was cleaned successfully.",
                    "output_path": str(clean_path),
                    "report_path": str(report_path),
                    "source_images": [str(path) for path in paths],
                    "comparisons": comparisons,
                    "cleaner_attempts": attempts,
                    "cleaner": cleaner_payload,
                }
                _atomic_json(report_path, result)
                return result

            if cleaner_success and attempt_output is not None and attempt_output.is_file():
                # A preview remains isolated in attempts and cannot become the
                # workflow's clean output.  Prefer a later complete pair if one
                # exists, while retaining this review candidate as a fallback.
                review_candidate = {
                    "output_id": str(output_id),
                    "output_name": filename,
                    "status": _STATUS_NEEDS_REVIEW,
                    "reason": (
                        "The cleaner produced a preview or quality warning; "
                        "the preview was kept for review."
                    ),
                    "preview_path": str(attempt_output),
                    "report_path": str(report_path),
                    "source_images": [str(path) for path in paths],
                    "comparisons": comparisons,
                    "cleaner_attempts": attempts,
                    "cleaner": cleaner_payload,
                }
                continue

            reason = (
                f"Cleaner rejected pair {first_path.name}, {second_path.name}: "
                f"{error_type or 'unknown error'}: {error_message or ''}"
            ).strip()
            if _is_blocking_error(error_type):
                blocked_reasons.append(reason)
            else:
                duplicate_reasons.append(reason)
        except Exception as exc:
            # Keep this pair isolated and continue to older pairs.  A bad pair
            # must not hide a valid pair that was downloaded in the same job.
            attempt_record["error_type"] = type(exc).__name__
            attempt_record["error"] = str(exc)
            attempts.append(attempt_record)
            try:
                _write_attempt_report(attempt_dir, attempt_record)
            except OSError:
                logger.exception("Could not write attempt report %s", attempt_dir)
            if _is_blocking_error(type(exc).__name__):
                blocked_reasons.append(str(exc))
            else:
                duplicate_reasons.append(str(exc))

    if review_candidate is not None:
        review_candidate["comparisons"] = comparisons
        review_candidate["cleaner_attempts"] = attempts
        try:
            _atomic_json(report_path, review_candidate)
        except OSError:
            pass
        return review_candidate

    if blocked_reasons and not duplicate_reasons and not uncertain_reasons:
        status = _STATUS_BLOCKED
        reason = " ".join(blocked_reasons)
    else:
        status = _STATUS_NEED_VARIANT
        if duplicate_reasons:
            reason = duplicate_reasons[0]
        elif uncertain_reasons:
            reason = uncertain_reasons[0]
        else:
            reason = "No pair with two distinct watermark corners was found."

    result = {
        "output_id": str(output_id),
        "output_name": filename,
        "status": status,
        "reason": reason,
        "report_path": str(report_path),
        "source_images": [str(path) for path in paths],
        "comparisons": comparisons,
        "cleaner_attempts": attempts,
    }
    try:
        _atomic_json(report_path, result)
    except OSError:
        pass
    return result


def format_cleaner_result_vn(
    output_name: str,
    result: Mapping[str, Any],
    *,
    with_step: bool = True,
) -> tuple[str, str]:
    """Format cleaner results into friendly, specific Vietnamese log messages.

    Returns (message, level) where level is 'success', 'info', 'warn', or 'error'.
    Eliminates raw English error prose or machine status strings from user logs.
    """
    clean_name = str(output_name or "ảnh").strip()
    st = str(result.get("status") or "")
    rs = str(result.get("reason") or "").strip()
    rs_lower = rs.lower()

    if st == _STATUS_CLEANED:
        msg = f"Step 10: Ghép thành công - {clean_name}" if with_step else f"{clean_name}: Ghép thành công, đã xóa sạch watermark."
        return msg, "success"

    if st == _STATUS_NEED_VARIANT:
        comparisons = result.get("comparisons", [])
        if "trùng góc" in rs_lower or "duplicate" in rs_lower or "same" in rs_lower or any(
            isinstance(c, Mapping) and c.get("status") == "duplicate" for c in comparisons
        ):
            msg = (
                f"Step 09: Watermark trùng góc - cần lấy thêm biến thể cho {clean_name}"
                if with_step
                else f"{clean_name}: Watermark trùng góc - cần thêm ảnh biến thể khác góc để ghép."
            )
            return msg, "info"
        if "at least two" in rs_lower or "chưa đủ 2" in rs_lower:
            msg = (
                f"Step 09: Chưa đủ 2 biến thể - cần tải thêm ảnh cho {clean_name}"
                if with_step
                else f"{clean_name}: Chưa đủ 2 biến thể để so sánh watermark."
            )
            return msg, "info"
        msg = (
            f"Step 09: Chưa đủ cặp góc sạch - cần lấy thêm biến thể cho {clean_name}"
            if with_step
            else f"{clean_name}: Chưa đủ cặp góc sạch - cần thêm ảnh biến thể khác để ghép."
        )
        return msg, "info"

    if st == _STATUS_NEEDS_REVIEW:
        msg = (
            f"Step 10: Cần kiểm tra đường nối (seam) - {clean_name}: Ảnh xem trước đã tạo nhưng có đường nối/chất lượng chưa tối ưu."
            if with_step
            else f"{clean_name}: Cần kiểm tra đường nối (seam) - Đã tạo ảnh xem trước nhưng chất lượng chưa tối ưu."
        )
        return msg, "warn"

    if st == _STATUS_BLOCKED or "error" in st:
        error_type = str(result.get("error_type") or "")
        all_text = f"{error_type} {rs} " + " ".join(
            str(c.get("reason") or "") for c in result.get("comparisons", []) if isinstance(c, Mapping)
        ) + " ".join(
            str(a.get("error") or "") for a in result.get("cleaner_attempts", []) if isinstance(a, Mapping)
        )
        all_text_lower = all_text.lower()

        # if "dimensionmismatch" in all_text_lower or "conflicting pixel dimensions" in all_text_lower or "kích thước" in all_text_lower:
        #     dims = re.findall(r"\((\d+),\s*(\d+)\)", all_text)
        #     if not dims:
        #         dims = re.findall(r"(\d{3,5})\s*[xX]\s*(\d{3,5})", all_text)
        #     if dims and len(dims) >= 2:
        #         dim_str = f"{dims[0][0]}x{dims[0][1]} vs {dims[1][0]}x{dims[1][1]}"
        #         detail = f"Kích thước ảnh không khớp ({dim_str})"
        #     else:
        #         detail = "Kích thước ảnh không khớp"
        #     msg = (
        #         f"Step 10: {detail} - {clean_name}: Không thể ghép ảnh."
        #         if with_step
        #         else f"{clean_name}: {detail} - Không thể ghép ảnh."
        #     )
        #     return msg, "error"

        if "sourcemismatch" in all_text_lower or "sourceimagemismatch" in all_text_lower:
            msg = (
                f"Step 10: Ảnh gốc không khớp nội dung - {clean_name}: Không thể ghép ảnh."
                if with_step
                else f"{clean_name}: Ảnh gốc không khớp nội dung - Không thể ghép ảnh."
            )
            return msg, "error"

        if "corrupted" in all_text_lower or "decode" in all_text_lower:
            msg = (
                f"Step 10: Tệp ảnh bị lỗi hoặc không đọc được - {clean_name}: Không thể xử lý."
                if with_step
                else f"{clean_name}: Tệp ảnh bị lỗi hoặc không đọc được - Không thể xử lý."
            )
            return msg, "error"

        if "cancel" in all_text_lower or "hủy" in all_text_lower:
            msg = f"Step 10: Quá trình ghép bị dừng - {clean_name}." if with_step else f"{clean_name}: Quá trình ghép bị dừng."
            return msg, "warn"

        msg = (
            f"Step 10: Không thể ghép ảnh - {clean_name} (lỗi dữ liệu đầu vào)."
            if with_step
            else f"{clean_name}: Không thể ghép ảnh (lỗi dữ liệu đầu vào)."
        )
        return msg, "error"

    msg = f"{clean_name}: Đang chờ xử lý..."
    return msg, "info"


__all__ = [
    "clean_output",
    "compare_variant_pair",
    "format_cleaner_result_vn",
]
