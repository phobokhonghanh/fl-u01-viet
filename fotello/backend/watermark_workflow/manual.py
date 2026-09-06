"""Workflow 2: download selected existing jobs and clean their variants.

WF2 never creates a listing or enhance. It resolves selected remote records to
the persisted WF1 manifest, downloads each enhance independently, and hands
only mapped variants to the shared cleaner adapter.
"""

from __future__ import annotations

import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from backend.auth import fotello_get_tokens
from backend.downloads import (
    download_variant,
    fotello_list_enhances_for_listing,
    fotello_list_listings,
)

from .cleaner import clean_output, format_cleaner_result_vn
from .models import parse_attempt_name, sanitize_output_stem, sanitize_prefix, summary
from .store import ManifestStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(log, message: str, level: str = "info") -> None:
    try:
        log(message, level)
    except TypeError:
        log(message)


def _listing_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("listing_id") or "")


def _listing_meta(listing_id: str, rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    for row in rows:
        if _listing_id(row) != str(listing_id):
            continue
        name = str(row.get("name") or row.get("address") or "")
        parsed = parse_attempt_name(name)
        data = dict(row)
        if parsed:
            data.update(parsed)
        else:
            data.update({"family_id": None, "attempt": None, "chunk": None, "prefix": name})
        return data
    return {"id": str(listing_id), "family_id": None, "attempt": None, "chunk": None, "prefix": ""}


def _find_attempt(manifest: Mapping[str, Any], listing_id: str) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    attempts = manifest.get("attempts", [])
    if not isinstance(attempts, list):
        return None, None
    for attempt in attempts:
        if not isinstance(attempt, Mapping):
            continue
        listings = attempt.get("listings", [])
        if not isinstance(listings, list):
            continue
        for listing in listings:
            if isinstance(listing, Mapping) and str(
                listing.get("listing_id") or listing.get("id") or ""
            ) == str(listing_id):
                return attempt, listing
    return None, None


def _manifest_enhances(manifest: Mapping[str, Any], listing_id: str) -> list[dict[str, Any]]:
    _, listing = _find_attempt(manifest, listing_id)
    if not listing:
        return []
    rows = listing.get("enhances", [])
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _groups(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = manifest.get("groups", [])
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("output_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("output_id")
    }


def _input_names(enhance: Mapping[str, Any]) -> tuple[str, ...]:
    values = enhance.get("input_filenames") or enhance.get("inputFilenames") or []
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(value) for value in values if value)


def _resolve_group_id(enhance: Mapping[str, Any], groups: Mapping[str, Mapping[str, Any]]) -> str | None:
    for key in ("output_id", "outputId", "group_id", "groupId"):
        value = enhance.get(key)
        if value is not None and str(value) in groups:
            return str(value)
    names = _input_names(enhance)
    if not names:
        return None
    matches = []
    for output_id, group in groups.items():
        expected = tuple(str(value) for value in group.get("input_filenames", []) if value)
        if expected and expected == names:
            matches.append(output_id)
    return matches[0] if len(matches) == 1 else None


def _new_manifest(
    output_dir: Path,
    family_id: str,
    prefix: str,
    team_id: str | None,
    bracket_size: int = 1,
) -> dict[str, Any]:
    return {
        "family_id": family_id,
        "prefix": sanitize_prefix(prefix),
        "team_id": str(team_id or ""),
        "bracket_size": bracket_size,
        "preferences": {},
        "groups": [],
        "attempts": [],
        "output_dir": str(output_dir),
        "version": 1,
    }


def _destination_for_manifest(
    manifest: Mapping[str, Any],
    requested_root: Path,
    multi_family: bool = False,
) -> Path:
    """Choose the manual output folder while retaining the full manifest."""

    family_id = str(manifest.get("family_id") or "family")
    source = Path(str(manifest.get("output_dir") or "")) if manifest.get("output_dir") else None
    try:
        requested = requested_root.expanduser().resolve()
        source_resolved = source.expanduser().resolve() if source else None
    except OSError:
        requested, source_resolved = requested_root, source
    if source_resolved and requested == source_resolved:
        return requested_root
    if requested_root.name == family_id:
        return requested_root
    if multi_family:
        return requested_root / sanitize_prefix(family_id)
    return requested_root


def _sync_raw_files_to_destination(manifest: dict[str, Any], destination: Path) -> None:
    """Ensure all existing downloaded variants are copied to destination/raw and paths updated."""
    prefix = str(manifest.get("prefix") or "listing")
    groups = manifest.get("groups", [])
    if not isinstance(groups, list):
        return
    for group in groups:
        if not isinstance(group, dict):
            continue
        output_name = str(group.get("output_name") or group.get("output_id") or "output")
        raw_name = Path(output_name).stem + ".jpg"
        variants = group.get("variants", [])
        if not isinstance(variants, list):
            continue
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            src_path_str = variant.get("path")
            if not src_path_str:
                continue
            src_path = Path(src_path_str)
            if not src_path.is_file():
                continue
            attempt_num = variant.get("attempt")
            if attempt_num is not None:
                try:
                    attempt_label = f"{prefix}{int(attempt_num):02d}"
                except (ValueError, TypeError):
                    attempt_label = f"{prefix}_{attempt_num}"
            else:
                listing_id = variant.get("listing_id")
                matched_attempt = None
                if listing_id:
                    for att in manifest.get("attempts", []):
                        if any(str(lst.get("listing_id")) == str(listing_id) for lst in att.get("listings", [])):
                            matched_attempt = att.get("number")
                            break
                if matched_attempt is not None:
                    attempt_label = f"{prefix}{int(matched_attempt):02d}"
                else:
                    p_name = src_path.parent.name
                    if p_name.startswith("part"):
                        attempt_label = src_path.parent.parent.name
                    else:
                        attempt_label = p_name if p_name != "raw" else f"{prefix}01"
            dest_raw_dir = destination / "raw" / attempt_label
            dest_file = dest_raw_dir / raw_name
            try:
                if src_path.resolve() != dest_file.resolve():
                    dest_raw_dir.mkdir(parents=True, exist_ok=True)
                    if not dest_file.is_file() or dest_file.stat().st_size == 0:
                        shutil.copy2(src_path, dest_file)
                    variant["path"] = str(dest_file)
            except OSError:
                pass


def _clone_for_destination(source: Mapping[str, Any], destination: Path) -> dict[str, Any]:
    """Clone the complete target universe and reset paths from another folder."""

    manifest = deepcopy(dict(source))
    manifest["output_dir"] = str(destination)
    for key in ("status", "output_path", "manifest_path", "errors", "unresolved"):
        manifest.pop(key, None)
    for group in manifest.get("groups", []) if isinstance(manifest.get("groups"), list) else []:
        if not isinstance(group, dict):
            continue
        if str(group.get("status") or "").casefold() in {"cleaned", "complete", "success"}:
            group["status"] = "need_variant"
        # The shared adapter validates an existing output's report before reuse.
        group["output_path"] = None
        group["report_path"] = None
    manifest["cloned_from"] = str(source.get("output_dir") or "")
    manifest["updated_at"] = _now()
    _sync_raw_files_to_destination(manifest, destination)
    return manifest


def _ensure_group_from_metadata(manifest: dict[str, Any], enhance: Mapping[str, Any]) -> str | None:
    groups = _groups(manifest)
    names = _input_names(enhance)
    if len(names) not in (1, 3, 5):
        return None
    existing = _resolve_group_id(enhance, groups)
    if existing:
        return existing
    output_stem = sanitize_output_stem(Path(names[0]).stem or f"img{len(groups) + 1:02d}")
    if any(str(g.get("output_name", "")).casefold() == f"{output_stem}.png".casefold()
           for g in groups.values()):
        return None  # Different brackets sharing an output name need explicit mapping.
    output_id = output_stem if output_stem not in groups else f"img{len(groups) + 1:04d}"
    manifest.setdefault("groups", []).append(
        {
            "output_id": output_id,
            "output_name": f"{output_stem}.png",
            "input_paths": [],
            "input_filenames": list(names),
            "status": "need_variant",
            "variants": [],
        }
    )
    return output_id


def _add_attempt_enhance(
    manifest: dict[str, Any],
    listing_id: str,
    attempt_number: int,
    chunk: int | None,
    enhances: list[Mapping[str, Any]],
    group_ids: Mapping[str, str],
) -> None:
    attempts = manifest.setdefault("attempts", [])
    if not isinstance(attempts, list):
        attempts = []
        manifest["attempts"] = attempts
    attempt = next(
        (
            row
            for row in attempts
            if isinstance(row, dict)
            and int(row.get("number") or row.get("attempt") or 0) == attempt_number
        ),
        None,
    )
    if attempt is None:
        attempt = {"number": attempt_number, "listings": []}
        attempts.append(attempt)
    listings = attempt.setdefault("listings", [])
    listing = next(
        (
            row
            for row in listings
            if isinstance(row, dict)
            and str(row.get("listing_id") or row.get("id") or "") == listing_id
        ),
        None,
    )
    if listing is None:
        listing = {"listing_id": listing_id, "chunk": chunk, "enhances": []}
        listings.append(listing)
    records = listing.setdefault("enhances", [])
    if not isinstance(records, list):
        records = []
        listing["enhances"] = records
    for enhance in enhances:
        enhance_id = str(enhance.get("enhance_id") or enhance.get("id") or "")
        if not enhance_id:
            continue
        payload = {
            "enhance_id": enhance_id,
            "output_id": group_ids.get(enhance_id) or enhance.get("output_id"),
            "input_filenames": list(_input_names(enhance)),
        }
        old = next(
            (
                row
                for row in records
                if isinstance(row, dict)
                and str(row.get("enhance_id") or row.get("id") or "") == enhance_id
            ),
            None,
        )
        if old is None:
            records.append(payload)
        else:
            old.update({key: value for key, value in payload.items() if value not in (None, [])})


def _merge_variant(group: dict[str, Any], payload: Mapping[str, Any]) -> None:
    variants = group.setdefault("variants", [])
    if not isinstance(variants, list):
        variants = []
        group["variants"] = variants
    enhance_id = str(payload.get("enhance_id") or "")
    for variant in variants:
        if isinstance(variant, dict) and enhance_id and str(variant.get("enhance_id") or "") == enhance_id:
            variant.update(dict(payload))
            return
    variants.append(dict(payload))


def _summary_many(manifests: list[Mapping[str, Any]], root: Path | None = None) -> dict[str, Any]:
    rows = [summary(manifest) for manifest in manifests]
    result = {
        "status": "partial",
        "manifests": rows,
        "target_count": sum(int(row.get("target_count") or 0) for row in rows),
        "downloaded_count": sum(int(row.get("downloaded_count") or 0) for row in rows),
        "cleaned_count": sum(int(row.get("cleaned_count") or 0) for row in rows),
        "pending_count": sum(int(row.get("pending_count") or 0) for row in rows),
        "preview_count": sum(int(row.get("preview_count") or 0) for row in rows),
        "failed_count": sum(int(row.get("failed_count") or 0) for row in rows),
        "attempt": max((int(row.get("attempt") or 0) for row in rows), default=0),
        "family_id": rows[0].get("family_id") if len(rows) == 1 else None,
        "output_path": rows[0].get("output_path") if len(rows) == 1 else str(root or Path(".")),
        "manifest_path": rows[0].get("manifest_path") if len(rows) == 1 else "",
    }
    if not rows:
        result["status"] = "failed"
    elif result["failed_count"] and result["failed_count"] == result["target_count"]:
        result["status"] = "failed"
    elif result["cleaned_count"] == result["target_count"] and result["target_count"]:
        result["status"] = "success"
    result["unresolved"] = [issue for manifest in manifests
                            for issue in list(manifest.get("unresolved", [])) + list(manifest.get("errors", []))]
    result["unresolved_count"] = len(result["unresolved"])
    if result["unresolved"]:
        result["status"] = "partial" if result["target_count"] else "failed"
    if any(manifest.get("status") == "stopped" for manifest in manifests):
        result["status"] = "stopped"
    return result


def download_manual_workflow(
    listing_ids: list[str],
    output_dir: str | Path,
    log=None,
    progress_fn=None,
    is_cancelled=None,
    summary_fn=None,
    team_id: str | None = None,
) -> dict[str, Any]:
    """Download and clean selected listings using manifest-backed mapping."""

    log = log or (lambda *_args: None)
    progress_fn = progress_fn or (lambda *_args: None)
    is_cancelled = is_cancelled or (lambda: False)
    selected = list(dict.fromkeys(str(value) for value in listing_ids or [] if value))
    root = Path(output_dir).expanduser().resolve()
    if not selected:
        result = _summary_many([])
        if summary_fn:
            summary_fn(result)
        return result

    source_by_listing: dict[str, dict[str, Any]] = {}
    for listing_id in selected:
        source = ManifestStore.find_by_listing(listing_id, team_id=team_id)
        if source:
            source_by_listing[listing_id] = source

    # Listing names are only needed when a selected listing has no manifest.
    listing_rows: list[Mapping[str, Any]] = []
    if len(source_by_listing) != len(selected):
        try:
            rows = fotello_list_listings(log=log)
        except Exception as exc:
            _log(log, f"Không thể đọc metadata listing: {exc}", "warn")
            rows = []
        listing_rows = [row for row in rows if isinstance(row, Mapping)]

    family_ids: set[str] = set()
    for listing_id in selected:
        source = source_by_listing.get(listing_id)
        if source:
            family_ids.add(str(source.get("family_id") or f"family-{listing_id}"))
        else:
            meta = _listing_meta(listing_id, listing_rows)
            family_ids.add(str(meta.get("family_id") or f"legacy-{listing_id}"))
    multi_family = len(family_ids) > 1

    manifests: dict[str, dict[str, Any]] = {}
    manifest_for_listing: dict[str, dict[str, Any]] = {}
    for listing_id in selected:
        source = source_by_listing.get(listing_id)
        if source:
            family_id = str(source.get("family_id") or f"family-{listing_id}")
            if family_id not in manifests:
                destination = _destination_for_manifest(source, root, multi_family=multi_family)
                manifests[family_id] = _clone_for_destination(source, destination)
            manifest_for_listing[listing_id] = manifests[family_id]
            continue
        meta = _listing_meta(listing_id, listing_rows)
        family_id = str(meta.get("family_id") or f"legacy-{listing_id}")
        if family_id not in manifests:
            dest_dir = root / sanitize_prefix(family_id) if multi_family else root
            manifests[family_id] = _new_manifest(
                dest_dir,
                family_id,
                str(meta.get("prefix") or "legacy"),
                team_id,
            )
        manifest_for_listing[listing_id] = manifests[family_id]

    access_token: str | None = None
    def checkpoint(manifest):
        ManifestStore(manifest["output_dir"]).save(manifest)
        if summary_fn:
            summary_fn(_summary_many(list(manifests.values()), root))

    for manifest in manifests.values():
        checkpoint(manifest)
    for index, listing_id in enumerate(selected, 1):
        manifest = manifest_for_listing[listing_id]
        if is_cancelled():
            manifest["status"] = "stopped"
            break
        metadata = _listing_meta(listing_id, listing_rows)
        attempt_record, listing_record = _find_attempt(manifest, listing_id)
        enhances = _manifest_enhances(manifest, listing_id)
        if not enhances:
            try:
                rows = fotello_list_enhances_for_listing(listing_id, log=log)
            except Exception as exc:
                _log(log, f"Không thể đọc enhances listing={listing_id}: {exc}", "error")
                manifest.setdefault("errors", []).append(
                    {"listing_id": listing_id, "reason": str(exc), "at": _now()}
                )
                checkpoint(manifest)
                progress_fn(index, len(selected))
                continue
            enhances = [dict(row) for row in rows if isinstance(row, Mapping)]

        if not enhances:
            manifest.setdefault("errors", []).append({
                "listing_id": listing_id, "reason": "Listing chưa có enhance để tải", "at": _now(),
            })
            checkpoint(manifest)
            progress_fn(index, len(selected))
            continue
        expected_count = int(metadata.get("brackets") or 0)
        if expected_count and len(enhances) < expected_count:
            manifest.setdefault("errors", []).append({
                "listing_id": listing_id,
                "reason": f"Chỉ nhận được {len(enhances)}/{expected_count} enhance", "at": _now(),
            })

        groups = _groups(manifest)
        group_ids: dict[str, str] = {}
        unresolved: list[str] = []
        for enhance in enhances:
            enhance_id = str(enhance.get("enhance_id") or enhance.get("id") or "")
            if not enhance_id:
                unresolved.append("<missing-enhance-id>")
                continue
            output_id = _resolve_group_id(enhance, groups) or _ensure_group_from_metadata(manifest, enhance)
            if output_id:
                group_ids[enhance_id] = output_id
            else:
                unresolved.append(enhance_id)
        if unresolved:
            manifest.setdefault("unresolved", []).extend(
                {"listing_id": listing_id, "enhance_id": value, "reason": "No exact output mapping"}
                for value in unresolved
            )
            _log(log, f"Listing {listing_id}: {len(unresolved)} mục chưa xác định được nhóm ảnh gốc.", "error")

        attempt_number = (
            int(metadata["attempt"])
            if metadata.get("attempt") is not None
            else int(attempt_record.get("number") or attempt_record.get("attempt") or 0) if attempt_record else 0
        )
        chunk_number = metadata.get("chunk")
        if chunk_number is None and listing_record:
            chunk_number = listing_record.get("chunk")
        _add_attempt_enhance(manifest, listing_id, attempt_number, chunk_number, enhances, group_ids)
        checkpoint(manifest)

        groups = _groups(manifest)
        needs_download = False
        for enhance_id, output_id in group_ids.items():
            group = groups.get(output_id)
            if not group:
                continue
            variants = group.get("variants", [])
            already = any(
                isinstance(variant, Mapping)
                and str(variant.get("enhance_id") or "") == enhance_id
                and variant.get("path")
                and Path(str(variant["path"])).is_file()
                for variant in variants if isinstance(variants, list)
            )
            needs_download = needs_download or not already
        if needs_download and access_token is None:
            try:
                access_token = str(fotello_get_tokens()["access_token"])
            except Exception as exc:
                _log(log, f"Không thể lấy access token: {exc}", "error")
                access_token = ""

        for enhance in enhances:
            if is_cancelled():
                break
            enhance_id = str(enhance.get("enhance_id") or enhance.get("id") or "")
            output_id = group_ids.get(enhance_id)
            group = groups.get(output_id or "")
            if not enhance_id or not group:
                continue
            variants = group.get("variants", [])
            existing = next(
                (
                    variant
                    for variant in variants if isinstance(variants, list)
                    if isinstance(variant, Mapping)
                    and str(variant.get("enhance_id") or "") == enhance_id
                    and variant.get("path")
                    and Path(str(variant["path"])).is_file()
                ),
                None,
            )
            attempt_label = (
                f"{manifest.get('prefix') or 'listing'}{attempt_number:02d}"
                if attempt_number
                else f"listing_{listing_id[:8]}"
            )
            raw_dir = (
                Path(str(manifest.get("output_dir") or root))
                / "raw"
                / attempt_label
            )
            raw_name = Path(str(group.get("output_name") or output_id)).stem + ".jpg"
            if existing:
                try:
                    dest_file = raw_dir / raw_name
                    if Path(str(existing["path"])).resolve() != dest_file.resolve():
                        raw_dir.mkdir(parents=True, exist_ok=True)
                        if not dest_file.is_file() or dest_file.stat().st_size == 0:
                            shutil.copy2(str(existing["path"]), dest_file)
                        existing["path"] = str(dest_file)
                except OSError:
                    pass
                continue
            if not access_token:
                group["status"] = "blocked"
                group["reason"] = "Không có access token để tải enhance"
                continue
            forced_rendition = next(
                (
                    str(variant.get("rendition"))
                    for variant in variants if isinstance(variants, list)
                    if isinstance(variant, Mapping) and variant.get("rendition")
                ),
                group.get("rendition"),
            )
            try:
                result = download_variant(
                    enhance_id,
                    access_token,
                    raw_dir,
                    raw_name,
                    log=log,
                    is_cancelled=is_cancelled,
                    rendition=forced_rendition,
                )
            except Exception as exc:
                _log(log, f"Không thể tải enhance={enhance_id[:8]}: {exc}", "error")
                result = None
            if result:
                group["rendition"] = result.get("rendition")
                _merge_variant(
                    group,
                    {
                        "enhance_id": enhance_id,
                        "listing_id": listing_id,
                        "attempt": attempt_number or None,
                        "path": str(result["path"]),
                        "rendition": result.get("rendition"),
                    },
                )
                checkpoint(manifest)
            elif not is_cancelled():
                group["status"] = "need_variant"
                group["reason"] = f"Chưa tải được enhance {enhance_id}; chọn lại listing để tải bổ sung."
                _log(log, group["reason"], "warn")

        family_root = Path(str(manifest.get("output_dir") or root))
        for group in groups.values():
            if is_cancelled():
                break
            if group.get("status") == "cleaned":
                continue  # Already validated/published in this manual run.
            variants = group.get("variants", [])
            paths = [
                str(variant.get("path"))
                for variant in variants if isinstance(variants, list)
                if isinstance(variant, Mapping)
                and variant.get("path")
                and Path(str(variant["path"])).is_file()
            ]
            if len(set(paths)) < 2 and group.get("status") == "blocked":
                continue
            try:
                result = clean_output(
                    str(group.get("output_id") or ""),
                    str(group.get("output_name") or group.get("output_id") or ""),
                    paths, family_root, is_cancelled=is_cancelled,
                )
            except Exception as exc:
                result = {"status": "blocked", "reason": str(exc)}
            msg, level = format_cleaner_result_vn(
                str(group.get("output_name") or group.get("output_id") or ""),
                result,
                with_step=False,
            )
            _log(log, msg, level)
            # Keep variants as mapping records; cleaner metadata lives beside
            # the group and cannot overwrite that list.
            for key in ("status", "reason", "output_path", "report_path", "preview_path"):
                if key in result:
                    group[key] = result[key]
            group["cleaning"] = {
                key: result[key]
                for key in ("status", "reason", "output_path", "report_path", "preview_path")
                if key in result
            }
            checkpoint(manifest)

        ManifestStore(family_root).save(manifest)
        progress_fn(index, len(selected))
        if summary_fn:
            summary_fn(_summary_many(list(manifests.values()), root))

    if is_cancelled():
        for manifest in manifests.values():
            manifest["status"] = "stopped"
            ManifestStore(Path(str(manifest.get("output_dir") or root))).save(manifest)

    for manifest in manifests.values():
        out_dir = Path(str(manifest.get("output_dir") or root))
        shutil.rmtree(out_dir / "attempts", ignore_errors=True)
        shutil.rmtree(out_dir / "reports", ignore_errors=True)
    shutil.rmtree(root / "attempts", ignore_errors=True)
    shutil.rmtree(root / "reports", ignore_errors=True)

    result = _summary_many(list(manifests.values()), root)
    if summary_fn:
        summary_fn(result)
    return result


manual_download = download_manual_workflow


__all__ = ["download_manual_workflow", "manual_download"]
