from __future__ import annotations

from datetime import datetime, timezone
import io
import json
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


def _format_listing_timestamp(value: str) -> str:
    if not value:
        return "-"
    try:
        normalized = _normalize_firestore_timestamp(value)
        created_at = datetime.fromisoformat(normalized)
        if created_at.tzinfo:
            created_at = created_at.astimezone(timezone.utc)
        return created_at.strftime("%Y-%m-%d %H:%M")
    except ValueError as exc:
        print_system_exception("downloads._format_listing_timestamp", exc)
        return "-"


def _parse_upload_name_datetime(name: str) -> datetime | None:
    prefix = "AutoHDR Upload - "
    if not name.startswith(prefix):
        return None
    raw_value = name[len(prefix) :].strip()
    try:
        return datetime.strptime(raw_value, "%d %m, %Y %H:%M").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        print_system_exception("downloads._parse_upload_name_datetime", exc)
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
            created_from_name = _parse_upload_name_datetime(name)
            if created_from_name:
                created_sort = created_from_name.timestamp()
                created_at = created_from_name.strftime("%Y-%m-%d %H:%M")
        listings.append(
            {
                "id": doc_id,
                "name": name or f"Listing {doc_id[:8]}",
                "created_at": created_at,
                "_created_sort": created_sort,
                "brackets": int(num_brackets or 0),
            }
        )
    listings.sort(key=lambda x: float(x.get("_created_sort") or 0), reverse=True)
    for listing in listings:
        listing.pop("_created_sort", None)
    log(f"✔ Tìm thấy {len(listings)} listings", "success")
    return listings


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
        enhances.append(
            {
                "id": enhance_id,
                "status": status,
                "has_image": has_edited or has_upsized,
                "upsized": has_upsized,
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
    candidates = ("mergedImageUpsized", FLD_EDITED_UPSIZED, "mergedImage", FLD_EDITED, "outputImage")
    gs_uri = ""
    for key in candidates:
        gs_uri = fields.get(key, {}).get(FLD_SV, "")
        if gs_uri:
            break
    if not gs_uri:
       return False
    return True


def fotello_download_listing(
    listing_id: str,
    output_dir: str | Path,
    log: LogFn = None,
    is_cancelled=None,
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

    log(f"Step 02: Tạo thư mục output: {out_dir}", "info")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"Step 03: Kiểm tra thông tin listing={listing_id}", "info")
    enhances = fotello_list_enhances_for_listing(listing_id, log)
    successful = [e for e in enhances if e["has_image"]]
    log(f"Step 03: Tìm thấy {len(successful)} enhances có ảnh.", "info")

    for enh in successful:
        if is_cancelled():
            return []
        # log(f"[DL-04] Patch watermark enhance={enh['id']}", "info")
        log(f"Step 04: Xử lý ảnh watermark: enhance={enh['id']}", "info")
        firestore_patch(
            f"{FLD_ENHANCES}/{enh['id']}",
            {FLD_IS_WM: {FLD_BV: False}},
            access_token,
            [FLD_IS_WM],
            log=log,
        )

    results: list[str] = []
    try:
        zip_resp = prepare_download(listing_id, id_token, log)
        download_url = zip_resp.get("download_url") or zip_resp.get("url")
        if download_url:
            # log(f"[DL-06] Tải ZIP URL: {download_url}", "info")
            log(f"Step 06: Bắt đầu tải gói dữ liệu. listing={listing_id}", "info")
            req = urllib.request.Request(str(download_url))
            with open_checked(req, 120) as resp:
                zip_data = resp.read()
            # log("[DL-07] Extract từng file ZIP", "info")
            log("Step 07: Giải nén dữ liệu đã tải.", "info")
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                for idx, fname in enumerate(sorted(zf.namelist()), 1):
                    if is_cancelled():
                        return results
                    if fname.endswith("/"):
                        continue
                    suffix = Path(fname).suffix or ".jpg"
                    file_path = out_dir / f"{idx:03d}{suffix}"
                    file_path.write_bytes(zf.read(fname))
                    results.append(str(file_path))
            if results:
                return results
    except Exception as exc:
        print_system_exception(f"downloads.fotello_download_listing ZIP listing_id={listing_id}", exc)
        # log(f"ZIP download lỗi, fallback từng ảnh: {exc}", "warn")
        log("Step 08: Chuyển sang chế độ tải dự phòng.", "warn")

    # log("[DL-08] Fallback tải từng enhance", "warn")
    log("Step 08: Tải dự phòng theo từng mục.", "warn")
    for enh in successful:
        path = download_single_enhance(str(enh["id"]), access_token, out_dir, log=log, is_cancelled=is_cancelled)
        if path:
            results.append(str(path))
    return results


def fotello_batch_download(
    listing_ids: list[str],
    output_dir: str,
    log: LogFn = None,
    progress_fn=None,
    is_cancelled=None,
) -> int:
    log = log or noop_log
    progress_fn = progress_fn or (lambda cur, total: None)
    is_cancelled = is_cancelled or (lambda: False)
    total_results: list[str] = []
    total = len(listing_ids)
    for idx, listing_id in enumerate(listing_ids, 1):
        if is_cancelled():
            break
        listing_dir = Path(output_dir) / f"listing_{listing_id[:8]}"
        # log(f"Fotello {idx}/{total}: {listing_id}", "info")
        log(f"Step 00: Thực thi tiến trình tải {idx}/{total}.", "info")
        total_results.extend(fotello_download_listing(listing_id, listing_dir, log, is_cancelled))
        progress_fn(idx, total)
    return len(total_results)
