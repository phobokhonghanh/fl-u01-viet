"""Workflow-facing adapter for the existing watermark cleaner.

The standalone cleaner works on one directory and deliberately owns the
``clean_result.png`` file in that directory. A workflow has a different
lifecycle: one logical output can have several downloaded variants, and a
successful result must survive later retry attempts. This module keeps that
lifecycle outside :mod:`backend.watermark_cleaner` and only publishes a result
after a pair has been checked and the old cleaner reports a *complete* result.

The public entry point is :func:`clean_output`. It accepts paths to variants
of one logical output and returns a small JSON-compatible dictionary with one
of ``cleaned``, ``need_variant``, ``needs_review`` or ``blocked`` statuses.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from PIL import Image

from backend.watermark_cleaner.config import WatermarkCleanerConfig
from backend.watermark_cleaner.pipeline import clean_case
from .comparison import VariantPairComparison, _file_fingerprint, compare_variant_pair
from .formatting import format_cleaner_result_vn
from .manifest_sync import (
    _atomic_json,
    _extract_variant_watermarks,
    _normalise_output_name,
    apply_watermark_positions_to_manifest,
    update_manifest_watermark_positions,
)

logger = logging.getLogger("watermark_workflow.cleaner")

_STATUS_CLEANED = "cleaned"
_STATUS_NEED_VARIANT = "need_variant"
_STATUS_NEEDS_REVIEW = "needs_review"
_STATUS_BLOCKED = "blocked"


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
        if fingerprints != [
            list(first_fingerprint) if first_fingerprint else None,
            list(second_fingerprint) if second_fingerprint else None,
        ]:
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
        for attempt in range(5):
            try:
                os.replace(temporary, destination)
                break
            except PermissionError:
                if attempt == 4:
                    shutil.copyfile(temporary, destination)
                    break
                import time
                time.sleep(0.05)
    finally:
        temporary.unlink(missing_ok=True)


def _existing_clean_result(
    output_path: Path,
    report_path: Path,
    output_id: str,
    output_name: str,
) -> dict[str, Any] | None:
    """Return a stable result when a prior clean output already exists."""
    if not output_path.is_file():
        return None
    report_data: dict[str, Any] = {}
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(report, dict):
                if str(report.get("output_id", "")) != str(output_id) or report.get("status") != _STATUS_CLEANED:
                    return None
                report_data = report
        except Exception:
            return None
    try:
        with Image.open(output_path) as image:
            image.verify()
    except Exception:
        # An incomplete/corrupt destination is not treated as a completed
        # result; an upcoming successful attempt may replace it atomically.
        return None
    result: dict[str, Any] = {
        "output_id": output_id,
        "output_name": output_name,
        "status": _STATUS_CLEANED,
        "reason": "A completed clean output already exists; it was preserved.",
        "output_path": str(output_path),
        "report_path": str(report_path),
        "cleaner_attempts": report_data.get("cleaner_attempts", []),
    }
    if "variant_watermarks" in report_data:
        result["variant_watermarks"] = report_data["variant_watermarks"]
    if "watermark_positions" in report_data:
        result["watermark_positions"] = report_data["watermark_positions"]
    if "comparisons" in report_data:
        result["comparisons"] = report_data["comparisons"]
    if "cleaner" in report_data:
        result["cleaner"] = report_data["cleaner"]
    return result


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
    manifest: dict[str, Any] | None = None,
    group: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Clean one logical workflow output from downloaded variants.

    ``variants`` are ordered from oldest to newest. Every candidate is kept
    in its own ``attempts/<output_id>/...`` directory. The first complete,
    warning-free clean attempt is copied atomically to
    ``<output_dir>/clean/<output_name>``. Existing clean files are returned as
    completed results and are never removed by a later retry.
    """
    cfg = config or WatermarkCleanerConfig(allow_dimension_mismatch=True)
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
        if manifest is not None or group is not None:
            apply_watermark_positions_to_manifest(manifest, group, existing)
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
                # ran. Preserve whichever completed output is already there.
                if clean_path.is_file():
                    existing = _existing_clean_result(
                        clean_path,
                        report_path,
                        str(output_id),
                        filename,
                    )
                    if existing is not None:
                        if manifest is not None or group is not None:
                            apply_watermark_positions_to_manifest(manifest, group, existing)
                        return existing
                _publish_clean_output(attempt_output, clean_path)
                variant_watermarks, watermark_positions = _extract_variant_watermarks(
                    comparisons=comparisons,
                    attempts=attempts,
                    cleaner_payload=cleaner_payload,
                )
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
                    "variant_watermarks": variant_watermarks,
                    "watermark_positions": watermark_positions,
                }
                _atomic_json(report_path, result)
                if manifest is not None or group is not None:
                    apply_watermark_positions_to_manifest(manifest, group, result)
                return result

            if cleaner_success and attempt_output is not None and attempt_output.is_file():
                # A preview remains isolated in attempts and cannot become the
                # workflow's clean output. Prefer a later complete pair if one
                # exists, while retaining this review candidate as a fallback.
                variant_watermarks, watermark_positions = _extract_variant_watermarks(
                    comparisons=comparisons,
                    attempts=attempts,
                    cleaner_payload=cleaner_payload,
                )
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
                    "variant_watermarks": variant_watermarks,
                    "watermark_positions": watermark_positions,
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
            # Keep this pair isolated and continue to older pairs. A bad pair
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
        if "variant_watermarks" not in review_candidate:
            v_w, w_p = _extract_variant_watermarks(
                comparisons=comparisons,
                attempts=attempts,
                cleaner_payload=review_candidate.get("cleaner"),
            )
            review_candidate["variant_watermarks"] = v_w
            review_candidate["watermark_positions"] = w_p
        try:
            _atomic_json(report_path, review_candidate)
        except OSError:
            pass
        if manifest is not None or group is not None:
            apply_watermark_positions_to_manifest(manifest, group, review_candidate)
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

    variant_watermarks, watermark_positions = _extract_variant_watermarks(
        comparisons=comparisons,
        attempts=attempts,
    )
    result = {
        "output_id": str(output_id),
        "output_name": filename,
        "status": status,
        "reason": reason,
        "report_path": str(report_path),
        "source_images": [str(path) for path in paths],
        "comparisons": comparisons,
        "cleaner_attempts": attempts,
        "variant_watermarks": variant_watermarks,
        "watermark_positions": watermark_positions,
    }
    try:
        _atomic_json(report_path, result)
    except OSError:
        pass
    if manifest is not None or group is not None:
        apply_watermark_positions_to_manifest(manifest, group, result)
    return result


__all__ = [
    "VariantPairComparison",
    "_atomic_json",
    "_extract_variant_watermarks",
    "_normalise_output_name",
    "apply_watermark_positions_to_manifest",
    "clean_output",
    "compare_variant_pair",
    "format_cleaner_result_vn",
    "update_manifest_watermark_positions",
]
