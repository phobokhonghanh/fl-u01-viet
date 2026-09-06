"""Models and pure helpers for watermark workflow coordination.

The workflow is intentionally represented by ordinary dictionaries. The
coordinator can therefore checkpoint a manifest directly as JSON while the
grouping and naming rules remain usable by the manual-download path.
"""

from __future__ import annotations

import copy
import os
import re
import time
import unicodedata
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
    VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception:
    VN_TZ = timezone(timedelta(hours=7), name="ICT")


def _to_vn_datetime(dt: datetime) -> datetime:
    """Convert a datetime to Vietnam local time (UTC+7)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=VN_TZ)
    return dt.astimezone(VN_TZ)


VALID_BRACKET_SIZES = (1, 3, 5)
MANIFEST_VERSION = 1
_ATTEMPT_MARKER = re.compile(
    r"^(?:\[Part\s+(?P<part_chunk>\d+)\]\s*-\s*)?"
    r"(?P<prefix>.*?)\s*"
    r"\[wm:(?P<family_id>[^:\]\s]+):"
    r"(?P<attempt>[1-9]\d*):(?P<chunk>[1-9]\d*)\]"
    r"(?:\s*-\s*(?P<timestamp>\d{1,2}\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{1,2}))?\s*$"
)

_LEGACY_LISTING_MARKER = re.compile(
    r"^(?:\[Part\s+(?P<part_chunk>\d+)\]\s*-\s*)?"
    r"(?P<prefix>.*?)"
    r"(?:\s*-\s*(?P<timestamp>\d{1,2}\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{1,2}))\s*$"
)


class WorkflowValidationError(ValueError):
    """Raised when input cannot be represented by a workflow manifest."""


def _natural_sort_key(path: Path) -> tuple[tuple[int, object], ...]:
    """Return a stable, case-insensitive natural-sort key for a path."""

    parts = re.split(r"(\d+)", path.name)
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in parts
    )


def _as_path(value: Path | str | os.PathLike[str]) -> Path:
    if isinstance(value, Path):
        return value
    if isinstance(value, (str, os.PathLike)):
        return Path(value)
    raise TypeError(f"image path must be a PathLike value, got {type(value).__name__}")


def _sanitize_component(value: str, *, fallback: str) -> str:
    """Make a filename component safe without losing normal Unicode names."""

    normalized = unicodedata.normalize("NFKC", value).strip()
    result: list[str] = []
    previous_separator = False
    for char in normalized:
        if char.isalnum() or char in "._-":
            result.append(char)
            previous_separator = False
        else:
            if not previous_separator:
                result.append("_")
            previous_separator = True
    component = "".join(result).strip(" ._-")
    return component or fallback


def sanitize_output_stem(stem: str) -> str:
    """Return a safe output stem while preserving ordinary user characters.

    Spaces, parentheses, commas, and other valid filename characters are
    retained. Only control characters and platform-reserved filename
    characters are replaced; trailing dots/spaces and Windows device names
    receive the minimal adjustment needed for a portable PNG path.
    """

    normalized = unicodedata.normalize("NFKC", str(stem))
    safe_chars = []
    for char in normalized:
        if unicodedata.category(char) == "Cc" or char in '<>:"/\\|?*':
            safe_chars.append("_")
        else:
            safe_chars.append(char)
    component = "".join(safe_chars).rstrip(" .")
    if not component:
        return "image"

    windows_base = component.split(".", 1)[0].casefold()
    reserved = {"con", "prn", "aux", "nul"}
    reserved.update(f"com{number}" for number in range(1, 10))
    reserved.update(f"lpt{number}" for number in range(1, 10))
    if windows_base in reserved:
        component = f"_{component}"
    return component


def sanitize_prefix(prefix: str | None) -> str:
    """Return the prefix used in remote names and raw-attempt directories."""

    return _sanitize_component(str(prefix or ""), fallback="workflow")


def _validate_bracket_size(bracket_size: int) -> int:
    if isinstance(bracket_size, bool) or not isinstance(bracket_size, int):
        raise WorkflowValidationError("bracket_size must be one of 1, 3, or 5")
    if bracket_size not in VALID_BRACKET_SIZES:
        raise WorkflowValidationError(
            f"Unsupported bracket_size={bracket_size}; expected one of {VALID_BRACKET_SIZES}"
        )
    return bracket_size


def _fingerprint(path: Path) -> dict[str, Any]:
    """Capture the cheap local identity needed before a retry upload."""

    try:
        stat = path.stat()
    except OSError:
        return {"exists": False}
    return {
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def build_groups(images: list[Path], bracket_size: int) -> list[dict[str, Any]]:
    """Build immutable output groups from naturally sorted input images.

    A group is the unit that is retried and cleaned. Its input paths are never
    re-chunked from a filtered retry list, which keeps bracket 3/5 mappings
    stable across all attempts.
    """

    size = _validate_bracket_size(bracket_size)
    if not isinstance(images, list):
        raise TypeError("images must be a list of paths")
    if not images:
        raise WorkflowValidationError("at least one input image is required")

    paths = [_as_path(image) for image in images]
    paths.sort(key=lambda path: (_natural_sort_key(path), str(path).casefold(), str(path)))
    if len(paths) % size:
        raise WorkflowValidationError(
            f"{len(paths)} input images cannot be divided into complete bracket-{size} groups"
        )

    groups: list[dict[str, Any]] = []
    seen_output_names: dict[str, Path] = {}
    for offset in range(0, len(paths), size):
        group_paths = paths[offset : offset + size]
        first = group_paths[0]
        output_stem = sanitize_output_stem(first.stem)
        output_name = f"{output_stem}.png"
        duplicate_key = unicodedata.normalize("NFKC", output_name).casefold()
        previous = seen_output_names.get(duplicate_key)
        if previous is not None:
            raise WorkflowValidationError(
                "duplicate sanitized output name "
                f"{output_name!r} from {previous.name!r} and {first.name!r}"
            )
        seen_output_names[duplicate_key] = first
        resolved_paths = [path.resolve() for path in group_paths]
        group_fingerprints = {
            str(path.resolve()): _fingerprint(path)
            for path in group_paths
        }

        groups.append(
            {
                "output_id": f"img{len(groups) + 1:04d}",
                "output_name": output_name,
                "input_paths": [str(path) for path in resolved_paths],
                "input_filenames": [path.name for path in group_paths],
                "input_fingerprints": group_fingerprints,
                "status": "need_variant",
                "variants": [],
            }
        )
    return groups


def _validate_groups(groups: list[dict[str, Any]]) -> int:
    if not isinstance(groups, list) or not groups:
        raise WorkflowValidationError("groups must contain at least one output group")

    bracket_size: int | None = None
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for index, group in enumerate(groups, 1):
        if not isinstance(group, Mapping):
            raise WorkflowValidationError(f"group {index} must be a mapping")
        input_paths = group.get("input_paths")
        input_filenames = group.get("input_filenames")
        if not isinstance(input_paths, list) or not input_paths:
            raise WorkflowValidationError(f"group {index} must contain input_paths")
        if not isinstance(input_filenames, list) or len(input_filenames) != len(input_paths):
            raise WorkflowValidationError(
                f"group {index} input_filenames must match input_paths"
            )
        current_size = len(input_paths)
        if bracket_size is None:
            bracket_size = current_size
        if current_size != bracket_size:
            raise WorkflowValidationError("all groups must use the same bracket size")
        _validate_bracket_size(current_size)

        output_id = str(group.get("output_id") or "")
        output_name = str(group.get("output_name") or "")
        if not output_id or output_id in seen_ids:
            raise WorkflowValidationError(f"duplicate or missing output_id in group {index}")
        seen_ids.add(output_id)
        if not output_name:
            raise WorkflowValidationError(f"group {index} must contain output_name")
        name_key = unicodedata.normalize("NFKC", output_name).casefold()
        if name_key in seen_names:
            raise WorkflowValidationError(f"duplicate output_name {output_name!r}")
        seen_names.add(name_key)
        variants = group.get("variants", [])
        if not isinstance(variants, list):
            raise WorkflowValidationError(f"group {index} variants must be a list")
    assert bracket_size is not None
    return bracket_size


def new_manifest(
    groups: list[dict[str, Any]],
    preferences: Mapping[str, Any] | None,
    team_id: str | None,
    prefix: str | None,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Create a JSON-ready workflow manifest with a new family identifier."""

    group_list = copy.deepcopy(groups)
    bracket_size = _validate_groups(group_list)
    if preferences is not None and not isinstance(preferences, Mapping):
        raise TypeError("preferences must be a mapping or None")
    if not isinstance(output_dir, (str, os.PathLike, Path)):
        raise TypeError("output_dir must be a path-like value")

    requested_prefix = str(prefix or "").strip()
    base_prefix = requested_prefix
    # Callers often pass the first displayed name (abc01) as the prefix. The
    # attempt number is added by attempt_name, so remove only the conventional
    # first-attempt suffix. Other numeric suffixes can be meaningful names.
    if len(base_prefix) > 2 and base_prefix.endswith("01"):
        base_prefix = base_prefix[:-2].rstrip()
    now_dt = datetime.now(timezone.utc)
    return {
        "family_id": str(uuid.uuid4()),
        "prefix": sanitize_prefix(base_prefix),
        "requested_prefix": requested_prefix,
        "bracket_size": bracket_size,
        "preferences": copy.deepcopy(dict(preferences or {})),
        "team_id": str(team_id or "").strip(),
        "groups": group_list,
        "attempts": [],
        "output_dir": str(Path(output_dir).expanduser().resolve()),
        "created_at": now_dt.isoformat(),
        "created_time": _to_vn_datetime(now_dt).strftime("%d %m, %Y %H:%M"),
        "version": MANIFEST_VERSION,
    }


def _positive_int(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def attempt_name(
    prefix: str,
    attempt: int,
    family_id: str,
    chunk: int = 1,
    total_chunks: int = 1,
    created_at: str | datetime | None = None,
) -> str:
    """Build a human-readable remote listing name with a machine marker."""

    attempt_number = _positive_int(attempt, "attempt")
    chunk_number = _positive_int(chunk, "chunk")
    clean_prefix = str(prefix or "").strip()
    clean_family = str(family_id or "").strip()
    if not clean_family or any(char in clean_family for char in ":]\r\n"):
        raise ValueError("family_id must be a non-empty marker-safe string")
    display_prefix = f"{clean_prefix}{attempt_number:02d}" if clean_prefix else f"{attempt_number:02d}"
    marker = f"[wm:{clean_family}:{attempt_number}:{chunk_number}]"

    time_str = ""
    if created_at is not None:
        if isinstance(created_at, datetime):
            time_str = _to_vn_datetime(created_at).strftime("%d %m, %Y %H:%M")
        elif isinstance(created_at, str) and created_at.strip():
            raw = created_at.strip()
            if "T" in raw:
                try:
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    time_str = _to_vn_datetime(dt).strftime("%d %m, %Y %H:%M")
                except Exception:
                    time_str = raw
            else:
                time_str = raw

    part_prefix = f"[Part {chunk_number}] - " if total_chunks > 1 else ""
    if time_str:
        return f"{part_prefix}{display_prefix} {marker} - {time_str}"
    return f"{part_prefix}{display_prefix} {marker}"


def parse_attempt_name(name: str, full: bool = False) -> dict[str, Any] | None:
    """Parse a name generated by attempt_name or legacy format, returning None on mismatch."""

    if not isinstance(name, str):
        return None
    raw_name = name.strip()
    match = _ATTEMPT_MARKER.fullmatch(raw_name)
    if match:
        family_id = match.group("family_id")
        attempt_number = int(match.group("attempt"))
        chunk_number = int(match.group("chunk"))
        parsed_prefix = match.group("prefix").strip()
        suffix = f"{attempt_number:02d}"
        if parsed_prefix.endswith(suffix):
            parsed_prefix = parsed_prefix[: -len(suffix)].rstrip()
        part_chunk = match.group("part_chunk")
        timestamp = match.group("timestamp")
        raw_prefix_val = match.group("prefix").strip()
        display_name = f"[Part {part_chunk}] - {raw_prefix_val}" if part_chunk else raw_prefix_val
        data: dict[str, Any] = {
            "family_id": family_id,
            "attempt": attempt_number,
            "chunk": chunk_number,
            "prefix": parsed_prefix,
        }
        if full:
            data.update({
                "display_name": display_name,
                "timestamp": timestamp,
                "part_chunk": int(part_chunk) if part_chunk else chunk_number,
            })
        return data

    legacy_match = _LEGACY_LISTING_MARKER.fullmatch(raw_name)
    if legacy_match:
        part_chunk = legacy_match.group("part_chunk")
        chunk_num = int(part_chunk) if part_chunk else 1
        prefix_val = legacy_match.group("prefix").strip()
        timestamp = legacy_match.group("timestamp")
        display_name = f"[Part {chunk_num}] - {prefix_val}" if part_chunk else prefix_val
        data = {
            "family_id": None,
            "attempt": None,
            "chunk": chunk_num,
            "prefix": prefix_val,
        }
        if full:
            data.update({
                "display_name": display_name,
                "timestamp": timestamp,
                "part_chunk": chunk_num,
            })
        return data

    return None


def _variant_is_downloaded(variant: Any) -> bool:
    if isinstance(variant, (str, os.PathLike)):
        return bool(str(variant))
    if not isinstance(variant, Mapping):
        return False
    status = str(variant.get("status") or "").casefold()
    if status in {"downloaded", "cleaned", "complete", "success", "preview"}:
        return True
    return bool(
        variant.get("downloaded")
        or variant.get("path")
        or variant.get("local_path")
        or variant.get("output_path")
    )


def _group_status(group: Mapping[str, Any]) -> str:
    return str(group.get("status") or "need_variant").casefold()


def _attempt_number(attempt: Any) -> int:
    if not isinstance(attempt, Mapping):
        return 0
    for key in ("number", "attempt"):
        value = attempt.get(key)
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return 0


def summary(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return progress counts and an aggregate workflow status.

    downloaded_count counts downloaded variants, while the other image counts
    describe output groups. A preview/needs-review result is kept visible and
    never upgrades the workflow to success.
    """

    if not isinstance(manifest, Mapping):
        raise TypeError("manifest must be a mapping")
    raw_groups = manifest.get("groups")
    groups = raw_groups if isinstance(raw_groups, list) else []
    target_count = len(groups)
    downloaded_count = 0
    cleaned_count = 0
    preview_count = 0
    failed_count = 0
    pending_count = 0
    for raw_group in groups:
        group = raw_group if isinstance(raw_group, Mapping) else {}
        variants = group.get("variants")
        if isinstance(variants, list):
            downloaded_count += sum(1 for variant in variants if _variant_is_downloaded(variant))
        status = _group_status(group)
        if status in {"cleaned", "complete", "success"}:
            cleaned_count += 1
        elif status in {"preview", "needs_review", "need_review"}:
            preview_count += 1
        elif status in {"failed", "blocked", "error"}:
            failed_count += 1
        else:
            pending_count += 1

    raw_attempts = manifest.get("attempts")
    attempts = raw_attempts if isinstance(raw_attempts, list) else []
    latest_attempt = max((_attempt_number(item) for item in attempts), default=0)
    output_dir = Path(str(manifest.get("output_dir") or ".")).expanduser()
    explicit_output_path = manifest.get("output_path")
    explicit_manifest_path = manifest.get("manifest_path")
    manifest_path = Path(str(explicit_manifest_path)) if explicit_manifest_path else output_dir / "manifest.json"
    output_path = Path(str(explicit_output_path)) if explicit_output_path else output_dir

    declared_status = str(manifest.get("status") or "").casefold()
    if declared_status == "stopped":
        workflow_status = "stopped"
    elif target_count == 0 or (failed_count == target_count and target_count > 0):
        workflow_status = "failed"
    elif cleaned_count == target_count and target_count > 0:
        workflow_status = "success"
    else:
        workflow_status = "partial"

    return {
        "target_count": target_count,
        "downloaded_count": downloaded_count,
        "cleaned_count": cleaned_count,
        "pending_count": pending_count,
        "preview_count": preview_count,
        "failed_count": failed_count,
        "attempt": latest_attempt,
        "family_id": manifest.get("family_id"),
        "manifest_path": str(manifest_path),
        "output_path": str(output_path),
        "status": workflow_status,
    }


__all__ = [
    "VALID_BRACKET_SIZES",
    "MANIFEST_VERSION",
    "WorkflowValidationError",
    "build_groups",
    "new_manifest",
    "sanitize_output_stem",
    "sanitize_prefix",
    "attempt_name",
    "parse_attempt_name",
    "summary",
    "VN_TZ",
    "_to_vn_datetime",
]
