from __future__ import annotations

from datetime import datetime, timezone, timedelta
import io
import json
import os
import re
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .auth import FOTELLO_STATE, fotello_get_tokens, fotello_reconnect_saved
from .client import LogFn, json_request, noop_log, open_checked, print_system_exception, retry
from .constants import (
    FLD_BV,
    FLD_EDITED,
    FLD_EDITED_UPSIZED,
    FLD_ENHANCES,
    FLD_IS_WM,
    FLD_STATUS,
    FLD_SV,
    PREPARE_DOWNLOAD_URL,
)
from .firestore import firestore_get, firestore_patch, firestore_run_query, storage_download


def _normalize_firestore_timestamp(value: str) -> str:
    normalized = value.replace("Z", "+00:00")
    if "." not in normalized:
        return normalized
    prefix, suffix = normalized.split(".", 1)
    fraction = suffix
    timezone_part = ""
    for marker in ("+", "-"):
        if marker in suffix:
            fraction, timezone_part = suffix.split(marker, 1)
            timezone_part = marker + timezone_part
            break
    return f"{prefix}.{fraction[:6]}{timezone_part}"


def _parse_timestamp_sort_value(value: str) -> float:
    if not value:
        return 0.0
    try:
        normalized = _normalize_firestore_timestamp(value)
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError as exc:
        print_system_exception("downloads._parse_timestamp_sort_value", exc)
        return 0.0


try:
    from zoneinfo import ZoneInfo
    VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except Exception:
    VN_TZ = timezone(timedelta(hours=7), name="ICT")


def _to_local_datetime(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=VN_TZ)
    return dt.astimezone(VN_TZ)


def _format_listing_timestamp(value: str) -> str:
    if not value:
        return "-"
    try:
        normalized = _normalize_firestore_timestamp(value)
        created_at = datetime.fromisoformat(normalized)
        created_at = _to_local_datetime(created_at)
        return created_at.strftime("%Y-%m-%d %H:%M")
    except ValueError as exc:
        print_system_exception("downloads._format_listing_timestamp", exc)
        return "-"


def _parse_listing_name_datetime(name: str) -> datetime | None:
    separator = " - "
    if separator not in name:
        return None
    raw_value = name.rsplit(separator, 1)[-1].strip()
    try:
        dt = datetime.strptime(raw_value, "%d %m, %Y %H:%M")
        return _to_local_datetime(dt)
    except ValueError as exc:
        print_system_exception("downloads._parse_listing_name_datetime", exc)
        return None


def fotello_list_listings(log: LogFn = None) -> list[dict[str, object]]:
    log = log or noop_log
    if not FOTELLO_STATE.get("connected"):
        fotello_reconnect_saved(log)
    if not FOTELLO_STATE.get("connected"):
        raise RuntimeError("Chưa kết nối Fotello")

    tokens = fotello_get_tokens()
    access_token = tokens["access_token"]
    team_id = FOTELLO_STATE.get("team_id")
    query: dict[str, object] = {"from": [{"collectionId": "listings"}], "limit": 100}
    if team_id:
        query["where"] = {
            "fieldFilter": {
                "field": {"fieldPath": "teamId"},
                "op": "EQUAL",
                "value": {FLD_SV: team_id},
            }
        }

    # log("Đang tải danh sách listings...", "info")
    log("Step 01: Đang tải danh sách listings...", "info")
    rows = firestore_run_query(access_token, query, log=log)
    listings: list[dict[str, object]] = []
    for row in rows:
        doc = row.get("document", {})
        if not doc:
            continue
        doc_name = doc.get("name", "")
        doc_id = doc_name.split("/")[-1] if doc_name else ""
        fields = doc.get("fields", {})
        name = fields.get("address", {}).get(FLD_SV, "") or fields.get("name", {}).get(FLD_SV, "")
        created = fields.get("createdAt", {}).get("timestampValue", "")
        num_brackets = fields.get("num_total_brackets", {}).get("integerValue", "0")
        created_at = _format_listing_timestamp(created)
        created_sort = _parse_timestamp_sort_value(created)
        if not created_sort:
            created_from_name = _parse_listing_name_datetime(name)
            if created_from_name:
                created_sort = created_from_name.timestamp()
                created_at = created_from_name.strftime("%Y-%m-%d %H:%M")
        # New watermark workflow listings carry a machine-readable marker in
        # their remote name.  Keep the original display name while exposing the
        # parsed fields to the manual picker.  Parsing is intentionally local to
        # this call so downloads.py remains usable by legacy callers even when
        # the workflow package is not imported.
        marker = _parse_remote_attempt_marker(name)
        display_name = marker.get("display_name") or name or f"Listing {doc_id[:8]}"
        # Fallback to local manifest store if created_sort is still 0 (e.g. marker-only listing without createdAt)
        # Prioritize attempt.created_at/created_time for the specific attempt round, then family created_at/created_time.
        if not created_sort and marker.get("family_id"):
            try:
                from .watermark_workflow.store import ManifestStore
                manifest_data = ManifestStore.find_by_family(marker["family_id"], str(team_id) if team_id else None)
                if not manifest_data:
                    manifest_data = ManifestStore.find_by_listing(doc_id, str(team_id) if team_id else None)
                if manifest_data:
                    attempt_num = marker.get("attempt")
                    target_attempt = None
                    if attempt_num is not None and isinstance(manifest_data.get("attempts"), list):
                        for a in manifest_data["attempts"]:
                            if isinstance(a, dict) and a.get("number") == attempt_num:
                                target_attempt = a
                                break

                    fallback_dt_val = None
                    if target_attempt and target_attempt.get("created_at"):
                        fallback_dt_val = target_attempt.get("created_at")
                    elif target_attempt and target_attempt.get("created_time"):
                        fallback_dt_val = target_attempt.get("created_time")
                    elif manifest_data.get("created_at"):
                        fallback_dt_val = manifest_data.get("created_at")
                    elif manifest_data.get("created_time"):
                        fallback_dt_val = manifest_data.get("created_time")

                    if fallback_dt_val:
                        raw_val = str(fallback_dt_val).strip()
                        if "T" in raw_val:
                            dt_val = datetime.fromisoformat(raw_val.replace("Z", "+00:00"))
                            dt_local = _to_local_datetime(dt_val)
                            created_sort = dt_local.timestamp()
                            created_at = dt_local.strftime("%Y-%m-%d %H:%M")
                        else:
                            dt_parsed = _parse_listing_name_datetime(f"prefix - {raw_val}")
                            if dt_parsed:
                                created_sort = dt_parsed.timestamp()
                                created_at = dt_parsed.strftime("%Y-%m-%d %H:%M")
            except Exception as exc:
                print_system_exception("downloads.fotello_list_listings manifest fallback", exc)
        listings.append(
            {
                "id": doc_id,
                "name": name or f"Listing {doc_id[:8]}",
                "display_name": display_name,
                "created_at": created_at,
                "_created_sort": created_sort,
                "brackets": int(num_brackets or 0),
                "family_id": marker["family_id"],
                "attempt": marker["attempt"],
                "chunk": marker["chunk"],
                "prefix": marker["prefix"],
            }
        )
    listings.sort(key=lambda x: float(x.get("_created_sort") or 0), reverse=True)
    for listing in listings:
        listing.pop("_created_sort", None)
    log(f"✔ Tìm thấy {len(listings)} listings", "success")
    return listings


_REMOTE_ATTEMPT_RE = re.compile(
    r"\[wm:(?P<family_id>[^:\]\s]+):(?P<attempt>\d+):(?P<chunk>\d+)\]"
    r"",
    re.IGNORECASE,
)


def _parse_remote_attempt_marker(name: str) -> dict[str, object]:
    """Parse the workflow marker embedded in a listing name.

    The marker is deliberately conservative.  A legacy listing with a similar
    looking name must not be assigned to a workflow family by guessing from its
    position or filename order.
    """

    raw_name = str(name or "").strip()
    try:
        from .watermark_workflow.models import parse_attempt_name

        parsed = parse_attempt_name(raw_name, full=True)
        if parsed is not None:
            return {
                "family_id": parsed.get("family_id"),
                "attempt": parsed.get("attempt"),
                "chunk": parsed.get("chunk"),
                "prefix": parsed.get("prefix", raw_name),
                "display_name": parsed.get("display_name", raw_name),
            }
    except Exception:
        pass
    match = _REMOTE_ATTEMPT_RE.search(raw_name)
    if not match:
        return {
            "family_id": None,
            "attempt": None,
            "chunk": None,
            "prefix": raw_name,
            "display_name": raw_name,
        }
    clean_display = _REMOTE_ATTEMPT_RE.sub("", raw_name).strip(" -")
    separator = " - "
    if separator in clean_display:
        parts = clean_display.split(separator)
        if len(parts) > 1 and len(parts[-1].strip()) >= 10:
            clean_display = separator.join(parts[:-1]).strip()
    prefix = (raw_name[: match.start()] + " " + raw_name[match.end() :]).strip(" -")
    if separator in prefix:
        parts = prefix.split(separator)
        if len(parts) > 1 and len(parts[-1].strip()) >= 10:
            prefix = separator.join(parts[:-1]).strip()
    return {
        "family_id": match.group("family_id"),
        "attempt": int(match.group("attempt")),
        "chunk": int(match.group("chunk")),
        "prefix": prefix,
        "display_name": clean_display or prefix or raw_name,
    }


def fotello_list_enhances_for_listing(listing_id: str, log: LogFn = None) -> list[dict[str, object]]:
    log = log or noop_log
    tokens = fotello_get_tokens()
    access_token = tokens["access_token"]
    query = {
        "from": [{"collectionId": FLD_ENHANCES}],
        "where": {
            "fieldFilter": {
                "field": {"fieldPath": "listingId"},
                "op": "EQUAL",
                "value": {FLD_SV: listing_id},
            }
        },
        "limit": 200,
    }
    rows = firestore_run_query(access_token, query, log=log)
    enhances: list[dict[str, object]] = []
    for row in rows:
        doc = row.get("document", {})
        if not doc:
            continue
        doc_name = doc.get("name", "")
        enhance_id = doc_name.split("/")[-1] if doc_name else ""
        fields = doc.get("fields", {})
        status = fields.get(FLD_STATUS, {}).get(FLD_SV, "unknown")
        has_upsized = bool(fields.get(FLD_EDITED_UPSIZED, {}).get(FLD_SV, ""))
        has_edited = bool(fields.get(FLD_EDITED, {}).get(FLD_SV, ""))
        input_filenames_val = fields.get("inputFilenames", {}).get("arrayValue", {}).get("values", [])
        input_filenames: list[str] = []
        if input_filenames_val and isinstance(input_filenames_val, list):
            for filename_val in input_filenames_val:
                if isinstance(filename_val, dict):
                    filename = filename_val.get("stringValue") or filename_val.get(FLD_SV) or ""
                else:
                    filename = str(filename_val or "")
                if filename:
                    input_filenames.append(str(filename))
        original_filename = input_filenames[0] if input_filenames else ""

        enhances.append(
            {
                "id": enhance_id,
                "enhance_id": enhance_id,
                "listing_id": listing_id,
                "status": status,
                "has_image": has_edited or has_upsized,
                "upsized": has_upsized,
                "filename": original_filename,
                "input_filenames": input_filenames,
                "inputFilenames": input_filenames,
            }
        )
    return enhances


def prepare_download(listing_id: str, id_token: str, log: LogFn = None) -> dict[str, object]:
    if log:
        # log(f"[DL-05] Gọi prepareDownload ZIP listing={listing_id}", "info")
        log(f"Step 05: ZIP listing={listing_id}", "info")
    body = json.dumps({"listing_id": listing_id, "sections": ["photos"], "photo_formats": ["original"]}).encode()

    def _do() -> dict[str, object]:
        req = urllib.request.Request(
            PREPARE_DOWNLOAD_URL,
            data=body,
            method="POST",
            headers={
                "accept": "*/*",
                "authorization": id_token,
                "content-type": "application/json",
                "origin": "https://app.fotello.co",
                "referer": "https://app.fotello.co/",
            },
        )
        return json_request(req, 30)

    return retry(_do)


def download_single_enhance(
    enhance_id: str,
    access_token: str,
    output_dir: Path,
    src_name: str | None = None,
    log: LogFn = None,
    is_cancelled=None,
) -> Path | None:
    log = log or noop_log
    is_cancelled = is_cancelled or (lambda: False)
    if is_cancelled():
        return None

    # log(f"[DL-09] Đọc Firestore doc enhance={enhance_id}", "info")
    doc = firestore_get(f"{FLD_ENHANCES}/{enhance_id}", access_token)
    fields = doc.get("fields", {})
    candidates = ("mergedImageUpsized", FLD_EDITED_UPSIZED, "mergedImage", FLD_EDITED, "outputImage")
    gs_uri = ""
    for key in candidates:
        gs_uri = fields.get(key, {}).get(FLD_SV, "")
        if gs_uri:
            break
    if not gs_uri:
        # log(f"Enhance chưa có ảnh: {enhance_id[:8]}", "warn")
        # log(f"Step 09: Dữ liệu ảnh chưa sẵn sàng. enhance={enhance_id[:8]}", "warn")
        return None

    data = storage_download(gs_uri, access_token)
    name = src_name or f"{enhance_id}.jpg"
    if not Path(name).suffix:
        name += ".jpg"
    out_path = output_dir / name
    out_path.write_bytes(data)
    return out_path


# The values returned by the API have changed names over time, but the
# workflow needs one stable rendition choice for every variant of an output.
# ``edited`` is the standard edited image and is intentionally preferred over
# every merged rendition.  The aliases below make the public argument useful
# to callers that use the Firestore field names from older API responses.
_RENDITION_FIELDS: dict[str, tuple[str, ...]] = {
    "edited": (FLD_EDITED,),
    "edited_standard": (FLD_EDITED,),
    "editedimage": (FLD_EDITED,),
    "edited_upsized": (FLD_EDITED_UPSIZED,),
    "editedupsized": (FLD_EDITED_UPSIZED,),
    "editedimageupsized": (FLD_EDITED_UPSIZED,),
    "upsized": (FLD_EDITED_UPSIZED,),
    "merged": ("mergedImage",),
    "merged_standard": ("mergedImage",),
    "mergedimage": ("mergedImage",),
    "merged_upsized": ("mergedImageUpsized",),
    "mergedupsized": ("mergedImageUpsized",),
    "mergedimageupsized": ("mergedImageUpsized",),
    "output": ("outputImage",),
    "outputimage": ("outputImage",),
}

_RENDITION_CANONICAL: dict[str, str] = {
    "edited": "edited",
    "edited_standard": "edited",
    "editedimage": "edited",
    "edited_upsized": "edited_upsized",
    "editedupsized": "edited_upsized",
    "editedimageupsized": "edited_upsized",
    "upsized": "edited_upsized",
    "merged": "merged",
    "merged_standard": "merged",
    "mergedimage": "merged",
    "merged_upsized": "merged_upsized",
    "mergedupsized": "merged_upsized",
    "mergedimageupsized": "merged_upsized",
    "output": "output",
    "outputimage": "output",
}

_DEFAULT_RENDITIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("edited", (FLD_EDITED,)),
    ("edited_upsized", (FLD_EDITED_UPSIZED,)),
    ("merged", ("mergedImage",)),
    ("merged_upsized", ("mergedImageUpsized",)),
    ("output", ("outputImage",)),
)


def _field_string(fields: dict[str, object], field_name: str) -> str:
    value = fields.get(field_name, "")
    if isinstance(value, dict):
        return str(value.get(FLD_SV) or value.get("stringValue") or "")
    return str(value or "")


def _normalise_rendition_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _select_rendition(
    fields: dict[str, object],
    forced: str | None = None,
) -> tuple[str, str] | None:
    """Return ``(canonical_rendition, gs_uri)`` or ``None`` when unavailable."""

    if forced is not None:
        normalized = _normalise_rendition_name(forced)
        # Accept both compact aliases (editedstandard) and exact canonical
        # names.  Do not fall through to another rendition when a forced one is
        # absent: mixing standard and upsized files breaks pair comparison.
        forced_key = next(
            (key for key in _RENDITION_FIELDS if _normalise_rendition_name(key) == normalized),
            None,
        )
        if forced_key is None:
            raise ValueError(f"Không hỗ trợ rendition: {forced}")
        canonical = _RENDITION_CANONICAL[forced_key]
        for field_name in _RENDITION_FIELDS[forced_key]:
            gs_uri = _field_string(fields, field_name)
            if gs_uri:
                return canonical, gs_uri
        return None

    for canonical, field_names in _DEFAULT_RENDITIONS:
        for field_name in field_names:
            gs_uri = _field_string(fields, field_name)
            if gs_uri:
                return canonical, gs_uri
    return None


def _sanitize_output_name(output_name: str | Path | None, fallback: str) -> str:
    """Keep a download filename inside its requested output directory."""

    raw = str(output_name or "").replace("\\", "/")
    candidate = Path(raw).name
    # Remove NUL/control characters and platform-reserved separators.  Keep
    # unicode letters and the ordinary punctuation used by camera filenames.
    candidate = re.sub(r"[\x00-\x1f\x7f]", "", candidate).strip().strip(".")
    candidate = re.sub(r"[<>:\"|?*]", "_", candidate)
    if not candidate or candidate in {".", ".."}:
        candidate = str(fallback)
    if not Path(candidate).suffix:
        candidate += ".jpg"
    return candidate


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def download_variant(
    enhance_id: str,
    access_token: str,
    output_dir: str | Path,
    output_name: str,
    log: LogFn = None,
    is_cancelled=None,
    rendition: str | None = None,
) -> dict[str, object] | None:
    """Download one enhance using a deterministic rendition.

    This helper is the mapping-safe path used by the watermark workflow.  It
    reads exactly one enhance document and downloads exactly that document's
    chosen storage object.  ZIP entries are intentionally not involved, since
    ZIP order and basenames cannot prove which enhance produced a file.
    """

    log = log or noop_log
    is_cancelled = is_cancelled or (lambda: False)
    if is_cancelled():
        return None

    doc = firestore_get(f"{FLD_ENHANCES}/{enhance_id}", access_token)
    fields = doc.get("fields", {})
    if not isinstance(fields, dict):
        return None
    selected = _select_rendition(fields, forced=rendition)
    if selected is None:
        if rendition:
            log(f"Không tìm thấy rendition '{rendition}' cho enhance={enhance_id[:8]}", "warn")
        return None
    chosen_rendition, gs_uri = selected
    if is_cancelled():
        return None

    data = storage_download(gs_uri, access_token)
    if is_cancelled():
        return None
    safe_name = _sanitize_output_name(output_name, enhance_id)
    out_path = Path(output_dir) / safe_name
    _atomic_write_bytes(out_path, data)
    return {"path": out_path, "rendition": chosen_rendition}

def check_download_single_enhance(
    enhance_id: str,
    access_token: str,
    log: LogFn = None,
    is_cancelled=None,
) -> bool | False:
    log = log or noop_log
    is_cancelled = is_cancelled or (lambda: False)
    if is_cancelled():
        return False

    doc = firestore_get(f"{FLD_ENHANCES}/{enhance_id}", access_token)
    fields = doc.get("fields", {})
    status = fields.get('status', {}).get('stringValue', "")
    if status != 'enhance_success':
        return False
    # candidates = ("mergedImageUpsized", FLD_EDITED_UPSIZED, "mergedImage", FLD_EDITED, "outputImage")
    # gs_uri = ""
    # for key in candidates:
    #     gs_uri = fields.get(key, {}).get(FLD_SV, "")
    #     if gs_uri:
    #         break
    # if not gs_uri:
    #    return False
    return True


def fotello_download_listing(
    listing_id: str,
    output_dir: str | Path,
    log: LogFn = None,
    is_cancelled=None,
    enhance_ids: list[str] = None,
) -> list[str]:
    log = log or noop_log
    is_cancelled = is_cancelled or (lambda: False)
    
    if not FOTELLO_STATE.get("connected"):
        raise RuntimeError("Chưa kết nối Fotello")

    # log("[DL-01] Refresh token / kiểm tra kết nối", "info")
    log("Step 01: Kiểm tra kết nối.", "info")
    tokens = fotello_get_tokens()
    id_token = tokens["id_token"]
    access_token = tokens["access_token"]

    out_dir = Path(output_dir)
    log(f"Step 02: Tạo thư mục output: {out_dir}", "info")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    if enhance_ids is None:
        log(f"Step 03: Kiểm tra thông tin listing={listing_id}", "info")
        enhances = fotello_list_enhances_for_listing(listing_id, log)
        successful = [e for e in enhances if e["has_image"]]
        log(f"Step 03: Tìm thấy {len(successful)} enhances có ảnh.", "info")
    else:
        successful = [{"id": str(e), "filename": ""} if isinstance(e, str) else e for e in enhance_ids]

    if not successful:
        raise RuntimeError("Không tìm thấy enhances có ảnh")

    # for enh in successful:
    #     if is_cancelled():
    #         return []
    #     log(f"Step 04: Xử lý ảnh watermark: enhance={enh}", "info")
    #     firestore_patch(
    #         f"{FLD_ENHANCES}/{enh}",
    #         {FLD_IS_WM: {FLD_BV: False}},
    #         access_token,
    #         [FLD_IS_WM],
    #         log=log,
    #     )

    results: list[str] = []
    try:
        zip_resp = prepare_download(listing_id, id_token, log)
        download_url = zip_resp.get("download_url") or zip_resp.get("url")
        if download_url:
            # log(f"[DL-06] Tải ZIP URL: {download_url}", "info")
            log(f"Step 05: Bắt đầu tải gói dữ liệu. listing={listing_id}", "info")
            req = urllib.request.Request(str(download_url))
            with open_checked(req, 120) as resp:
                zip_data = resp.read()
            # log("[DL-07] Extract từng file ZIP", "info")
            log("Step 06: Giải nén dữ liệu đã tải.", "info")
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                for idx, fname in enumerate(sorted(zf.namelist()), 1):
                    if is_cancelled():
                        return results
                    if fname.endswith("/"):
                        continue
                    # suffix = Path(fname).suffix or ".jpg"
                    file_path = out_dir / Path(fname).name
                    file_path.write_bytes(zf.read(fname))
                    results.append(str(file_path))
            if results:
                return results
    except Exception as exc:
        print_system_exception(f"downloads.fotello_download_listing ZIP listing_id={listing_id}", exc)
        # log(f"ZIP download lỗi, fallback từng ảnh: {exc}", "warn")
        log("Chuyển sang chế độ tải dự phòng.", "warn")

    # log("[DL-08] Fallback tải từng enhance", "warn")
    log("Step 07: Tải dự phòng theo từng mục.", "warn")
    for item in successful:
        if is_cancelled():
            return results
        enh_id = item["id"] if isinstance(item, dict) else str(item)
        src_name = item.get("filename") if isinstance(item, dict) else None
        path = download_single_enhance(enh_id, access_token, out_dir, src_name=src_name, log=log, is_cancelled=is_cancelled)
        if path:
            results.append(str(path))
    return results


def fotello_batch_download(
    listing_ids: list[str],
    output_dir: str,
    log: LogFn = None,
    progress_fn=None,
    is_cancelled=None,
    summary_fn=None,
) -> dict[str, object]:
    """Download selected listings through the mapping-safe WF2 workflow.

    Importing lazily keeps the legacy download module importable on its own and
    avoids a package cycle: the manual workflow uses ``download_variant`` from
    this module.  The old ``fotello_download_listing`` helper remains available
    for callers that explicitly need the legacy ZIP/fallback behavior.
    """

    from .watermark_workflow.manual import download_manual_workflow

    if not FOTELLO_STATE.get("connected"):
        raise RuntimeError("Chưa kết nối Fotello")
    team_id = FOTELLO_STATE.get("team_id")
    if not team_id:
        raise RuntimeError("Không tìm thấy team_id. Hãy kết nối lại.")
    return download_manual_workflow(
        listing_ids,
        output_dir,
        log=log,
        progress_fn=progress_fn,
        is_cancelled=is_cancelled,
        summary_fn=summary_fn,
        team_id=str(team_id),
    )
