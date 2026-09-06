"""Thread-safe atomic persistence for watermark workflow manifests."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any


_REGISTRY_FILENAME = "manifest_registry.json"
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.expanduser().resolve())
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _state_dir() -> Path:
    override = os.environ.get("FOTELLO_WORKFLOW_STATE_DIR", "").strip()
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override)))
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "FotelloWorkflows"
        return Path.home() / "AppData" / "Local" / "FotelloWorkflows"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FotelloWorkflows"
    return Path.home() / ".local" / "share" / "FotelloWorkflows"


def _json_ready(value: Any) -> Any:
    """Convert path-like values in optional preferences to JSON values."""

    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, set):
        converted = [_json_ready(item) for item in value]
        return sorted(converted, key=lambda item: str(item))
    return value


def _atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(_json_ready(value), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def _registry_manifests(data: Any) -> list[dict[str, Any]]:
    """Read current and a couple of simple legacy registry shapes."""

    if isinstance(data, Mapping):
        entries = data.get("manifests")
        if isinstance(entries, Mapping):
            values = entries.values()
        elif isinstance(entries, list):
            values = entries
        else:
            values = data.values()
        result: list[dict[str, Any]] = []
        for entry in values:
            if not isinstance(entry, Mapping):
                continue
            manifest = entry.get("manifest")
            if isinstance(manifest, Mapping):
                result.append(dict(manifest))
            elif "groups" in entry and "family_id" in entry:
                result.append(dict(entry))
        return result
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, Mapping) and "groups" in item]
    return []


def _re_key(value: Any) -> str:
    return "".join(char for char in str(value).casefold() if char.isalnum())


def _has_listing(value: Any, listing_id: str, context: str = "") -> bool:
    if isinstance(value, Mapping):
        normalized_context = _re_key(context)
        for key, item in value.items():
            normalized_key = _re_key(key)
            if normalized_key in {"listingid", "listingids", "listing", "listings"}:
                if isinstance(item, (list, tuple, set)):
                    if any(str(candidate) == listing_id for candidate in item):
                        return True
                elif str(item) == listing_id:
                    return True
            if (
                normalized_context in {"listing", "listings"}
                and normalized_key == "id"
                and str(item) == listing_id
            ):
                return True
            child_context = (
                normalized_key
                if normalized_key in {"listing", "listings"}
                else normalized_context
            )
            if _has_listing(item, listing_id, child_context):
                return True
    elif isinstance(value, (list, tuple, set)):
        return any(_has_listing(item, listing_id, context) for item in value)
    return False


def _identity(value: Any, context: str) -> tuple[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    normalized_context = _re_key(context)
    keys: tuple[str, ...]
    if normalized_context in {"groups", "group"}:
        keys = ("output_id", "outputId", "output_name", "outputName")
    elif normalized_context in {"attempts", "attempt"}:
        keys = ("number", "attempt")
    elif normalized_context in {"listings", "listing"}:
        # A listing is checkpointed before its remote ID exists. Chunk is
        # stable within an attempt and therefore must be preferred.
        keys = ("chunk", "listing_id", "listingId", "id")
    elif normalized_context in {"enhances", "enhance"}:
        # The output group is known before createEnhance returns its ID.
        keys = ("output_id", "outputId", "enhance_id", "enhanceId", "id")
    elif normalized_context in {"variants", "variant"}:
        keys = ("enhance_id", "enhanceId", "path", "local_path", "id")
    else:
        keys = ()
    for key in keys:
        value_item = value.get(key)
        if value_item is not None and str(value_item) != "":
            return _re_key(key), str(value_item)
    return None


_STATUS_RANK = {
    "": 0,
    "need_variant": 1,
    "pending": 1,
    "uploading": 1,
    "submission_unknown": 1,
    "submitted": 2,
    "created": 2,
    "download_pending": 2,
    "downloaded": 3,
    "collected": 3,
    "preview": 4,
    "needs_review": 4,
    "need_review": 4,
    "blocked": 4,
    "failed": 4,
    "error": 4,
    "stopped": 5,
    "cleaned": 6,
    "complete": 6,
    "success": 6,
}


def _merge_lists(existing: list[Any], incoming: list[Any], context: str) -> list[Any]:
    """Merge checkpoint lists by their stable IDs, retaining history."""

    normalized_context = _re_key(context)
    mergeable = {
        "groups",
        "group",
        "attempts",
        "attempt",
        "listings",
        "listing",
        "enhances",
        "enhance",
        "variants",
        "variant",
    }
    if normalized_context not in mergeable:
        result = copy.deepcopy(existing)
        for item in incoming:
            if item not in result:
                result.append(copy.deepcopy(item))
        return result

    result = copy.deepcopy(existing)
    indices: dict[tuple[str, str], int] = {}
    for index, item in enumerate(result):
        key = _identity(item, normalized_context)
        if key is not None:
            indices[key] = index
    for item in incoming:
        key = _identity(item, normalized_context)
        if key is not None and key in indices:
            index = indices[key]
            if isinstance(result[index], Mapping) and isinstance(item, Mapping):
                result[index] = _merge_mapping(result[index], item, normalized_context)
            else:
                result[index] = copy.deepcopy(item)
        else:
            if key is not None:
                indices[key] = len(result)
            result.append(copy.deepcopy(item))
    return result


def _merge_mapping(existing: Mapping[str, Any], incoming: Mapping[str, Any], context: str = "") -> dict[str, Any]:
    """Merge stale and current snapshots without dropping attempt history."""

    result = copy.deepcopy(dict(existing))
    for key, incoming_value in incoming.items():
        if key not in result:
            result[key] = copy.deepcopy(incoming_value)
            continue
        existing_value = result[key]
        if isinstance(existing_value, Mapping) and isinstance(incoming_value, Mapping):
            result[key] = _merge_mapping(existing_value, incoming_value, str(key))
        elif isinstance(existing_value, list) and isinstance(incoming_value, list):
            result[key] = _merge_lists(existing_value, incoming_value, str(key))
        elif _re_key(key) == "status":
            old_status = str(existing_value or "").casefold()
            new_status = str(incoming_value or "").casefold()
            if not context or _STATUS_RANK.get(new_status, 0) >= _STATUS_RANK.get(old_status, 0):
                result[key] = copy.deepcopy(incoming_value)
        elif incoming_value is not None and incoming_value != "":
            result[key] = copy.deepcopy(incoming_value)
    return result


class ManifestStore:
    """Persist a workflow manifest and index it for manual listing lookup."""

    registry_filename = _REGISTRY_FILENAME

    def __init__(self, output_dir: Path | str) -> None:
        self.output_dir = Path(os.path.expandvars(os.path.expanduser(str(output_dir))))
        self.manifest_path = self.output_dir / "manifest.json"
        self.state_dir = _state_dir()
        self.registry_path = self.state_dir / self.registry_filename
        self._lock = _lock_for(self.manifest_path)

    @staticmethod
    def _registry_location() -> tuple[Path, threading.RLock]:
        path = _state_dir() / _REGISTRY_FILENAME
        return path, _lock_for(path)

    def save(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically write the output manifest and update the persistent index."""

        if not isinstance(manifest, Mapping):
            raise TypeError("manifest must be a mapping")
        family_id = str(manifest.get("family_id") or "").strip()
        if not family_id:
            raise ValueError("manifest must contain family_id")
        snapshot = copy.deepcopy(dict(manifest))
        with self._lock:
            _atomic_json_write(self.manifest_path, snapshot)
            registry_path, registry_lock = self._registry_location()
            with registry_lock:
                registry_data = _read_json(registry_path)
                if not isinstance(registry_data, Mapping):
                    registry_data = {"version": 1, "manifests": {}}
                entries = registry_data.get("manifests")
                if not isinstance(entries, Mapping):
                    entries = {}
                else:
                    entries = dict(entries)
                previous_entry = entries.get(family_id)
                previous_manifest = (
                    previous_entry.get("manifest")
                    if isinstance(previous_entry, Mapping)
                    else None
                )
                if isinstance(previous_manifest, Mapping):
                    registry_manifest = _merge_mapping(previous_manifest, snapshot)
                else:
                    registry_manifest = snapshot
                entries[family_id] = {
                    "manifest_path": str(self.manifest_path),
                    "manifest": registry_manifest,
                }
                registry = {"version": 1, "manifests": entries}
                _atomic_json_write(registry_path, registry)
        return copy.deepcopy(snapshot)

    def load(self) -> dict[str, Any] | None:
        """Load the output manifest, returning None when it does not exist."""

        with self._lock:
            data = _read_json(self.manifest_path)
        if data is None:
            return None
        if not isinstance(data, dict):
            raise ValueError(f"manifest at {self.manifest_path} must contain a JSON object")
        return data

    @staticmethod
    def find_by_listing(listing_id: str, team_id: str | None = None) -> dict[str, Any] | None:
        """Find a saved family containing listing_id."""

        needle = str(listing_id or "").strip()
        if not needle:
            return None
        registry_path, registry_lock = ManifestStore._registry_location()
        with registry_lock:
            data = _read_json(registry_path)
        candidates = _registry_manifests(data)
        requested_team = None if team_id is None else str(team_id).strip()
        for manifest in reversed(candidates):
            if requested_team is not None and str(manifest.get("team_id") or "").strip() != requested_team:
                continue
            if _has_listing(manifest.get("attempts", []), needle):
                return copy.deepcopy(manifest)
        return None

    @staticmethod
    def find_by_family(family_id: str, team_id: str | None = None) -> dict[str, Any] | None:
        """Find a saved family by its family_id."""

        needle = str(family_id or "").strip()
        if not needle:
            return None
        registry_path, registry_lock = ManifestStore._registry_location()
        with registry_lock:
            data = _read_json(registry_path)
        candidates = _registry_manifests(data)
        requested_team = None if team_id is None else str(team_id).strip()
        for manifest in reversed(candidates):
            if requested_team is not None and str(manifest.get("team_id") or "").strip() != requested_team:
                continue
            if str(manifest.get("family_id") or "").strip() == needle:
                return copy.deepcopy(manifest)
        return None


__all__ = ["ManifestStore"]
