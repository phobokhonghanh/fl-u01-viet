"""Download Engine for Fotello (from_debug)."""
from __future__ import annotations

import io
import json
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Sequence

from PIL import Image

from core.fotello.models import DownloadResult, RenditionInfo
from core.fotello.from_debug.auth import get_tokens
from core.fotello.from_debug.constants import DOWNLOAD_STEPS, FIREBASE_PROJECT_ID
from core.fotello.from_debug.client import download_media_uri
from core.fotello.from_debug.listings import list_enhances_for_listing
from core.shared.callbacks import ProgressAdapter
from core.shared.events import StepEvent, StepTracker
from core.shared.workspace import TemporaryWorkspace


def prepare_download_zip(listing_id: str, id_token: str, log_fn: Callable[[str, str], None] | None = None) -> str | None:
    url = f"https://us-central1-{FIREBASE_PROJECT_ID}.cloudfunctions.net/prepareDownload"
    body = json.dumps({
        "listingId": listing_id,
        "photos": True,
        "sections": ["original"],
        "photo_formats": ["original"],
    }).encode("utf-8")
    headers = {
        "authorization": f"Bearer {id_token}",
        "content-type": "application/json",
        "origin": "https://app.fotello.co",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("download_url")
    except Exception:
        return None


import hashlib
from datetime import datetime, timezone


def format_rendition_filename(
    original_filename: str,
    enhance_id: str,
    rendition: str,
    width: int,
    height: int,
    ext: str,
    variant_id: str | None = None,
    render_id: str | None = None,
) -> str:
    stem = Path(original_filename).stem if original_filename else enhance_id
    ext = ext.lstrip(".")
    if variant_id and render_id:
        return f"{stem}__{enhance_id}__{variant_id}__{render_id}__{rendition}__{width}x{height}.{ext}"
    elif variant_id:
        return f"{stem}__{enhance_id}__{variant_id}__{rendition}__{width}x{height}.{ext}"
    else:
        return f"{stem}__{enhance_id}__{rendition}__{width}x{height}.{ext}"


def download(
    listing_id: str,
    output_dir: str | Path,
    use_zip: bool = False,
    allow_local_resize: bool = False,
    all_renditions: bool = False,
    log_fn: Callable[[str, str], None] | None = None,
    progress_fn: Callable[..., Any] | None = None,
    stop_event: Any = None,
    event_fn: Callable[[StepEvent], None] | None = None,
    run_id: str | None = None,
) -> DownloadResult:
    """Download Fotello listing results using from_debug logic."""
    tr = StepTracker(
        engine="fotello.from_debug",
        steps=DOWNLOAD_STEPS,
        run_id=run_id,
        event_fn=event_fn,
        log_fn=log_fn,
    )
    out_p = Path(output_dir)
    out_p.mkdir(parents=True, exist_ok=True)
    prog_adapter = ProgressAdapter(progress_fn, warning_fn=log_fn)

    # 1. Auth
    tr.start_step("auth", "Đang kiểm tra phiên làm việc...")
    tokens = get_tokens()
    if not tokens or not (tokens.get("access_token") or tokens.get("id_token")):
        tr.complete_step("auth", "failed", "Chưa có token xác thực Fotello.")
        return DownloadResult(success=False, status="failed", count=0, message="Chưa có token xác thực")
    access_token = tokens.get("access_token") or tokens.get("id_token")
    id_token = tokens.get("id_token", "")
    tr.complete_step("auth", "success", "Xác thực phiên thành công.")

    if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
        return DownloadResult(success=False, status="cancelled", count=0, message="Tác vụ bị hủy")

    # 2. Listings
    tr.start_step("listings", f"Đang kiểm tra listing {listing_id}...")
    enhances = list_enhances_for_listing(listing_id, all_renditions=all_renditions, log_fn=log_fn)
    ready_items = [e for e in enhances if e.get("has_image")]
    if not ready_items and not use_zip:
        tr.complete_step("listings", "failed", f"Không tìm thấy ảnh đã xử lý trong listing {listing_id}")
        return DownloadResult(success=False, status="failed", count=0, message="Không có ảnh nào sẵn sàng")
    tr.complete_step("listings", "success", f"Tìm thấy {len(ready_items)} ảnh khả dụng.")

    # 3. Resolve outputs
    tr.start_step("resolve_outputs", "Đang phân tích các đường dẫn rendition...")
    resolved_jobs: list[tuple[dict[str, Any], RenditionInfo]] = []
    for item in ready_items:
        uri = item.get("image_uri", "")
        if all_renditions:
            rendition_name = item.get("rendition", "output")
            is_up = bool(item.get("upsized") or "upsized" in rendition_name)
        else:
            rendition_name = "edited_upsized" if item.get("upsized") else "edited"
            is_up = bool(item.get("upsized", False))

        r_info = RenditionInfo(
            enhance_id=item.get("enhance_id") or item.get("id", ""),
            rendition=rendition_name,
            gs_uri=uri,
            original_filename=item.get("name", ""),
            source_width=item.get("inputWidth", 0),
            source_height=item.get("inputHeight", 0),
            is_upsized=is_up,
            variant_id=item.get("variant_id"),
            render_id=item.get("render_id"),
            field_name=item.get("field_name"),
        )
        resolved_jobs.append((item, r_info))
    tr.complete_step("resolve_outputs", "success", f"Đã giải quyết xong {len(resolved_jobs)} rendition.")

    # 4. Download
    total_target = len(resolved_jobs)
    tr.start_step("download", f"Bắt đầu tải {total_target} ảnh...", current=0, total=total_target)
    downloaded_files: list[Path] = []
    audited_renditions: list[RenditionInfo] = []
    errors: list[dict[str, str]] = []

    with TemporaryWorkspace(prefix="fotello_debug_dl_") as ws_dir:
        for idx, (item, r_info) in enumerate(resolved_jobs):
            if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                tr.complete_step("download", "cancelled", "Tác vụ bị dừng khi tải ảnh.", current=len(downloaded_files), total=total_target)
                return DownloadResult(success=False, status="cancelled", count=len(downloaded_files), files=downloaded_files, renditions=audited_renditions)

            fname = item.get("name") or f"{item.get('id')}.jpg"
            temp_path = ws_dir / f"tmp_{idx}_{Path(fname).name}"
            try:
                data = download_media_uri(r_info.gs_uri, access_token)
                temp_path.write_bytes(data)
                r_info.file_bytes = len(data)
                r_info.sha256 = hashlib.sha256(data).hexdigest()

                with Image.open(temp_path) as im:
                    w, h = im.size
                    fmt = (im.format or "").lower()
                    ext = "jpg" if fmt in ("jpeg", "jpg") else (fmt if fmt else "jpg")
                    r_info.downloaded_width = w
                    r_info.downloaded_height = h
                    r_info.final_width = w
                    r_info.final_height = h

                # Detect resolution shortfall
                if r_info.source_width > 0 and (r_info.downloaded_width < r_info.source_width or r_info.downloaded_height < r_info.source_height):
                    r_info.resolution_shortfall = True

                if all_renditions:
                    # In all_renditions mode: NEVER local resize, re-encode, or clean watermark.
                    r_info.is_local_resized = False
                    cand_name = format_rendition_filename(
                        original_filename=r_info.original_filename,
                        enhance_id=r_info.enhance_id,
                        rendition=r_info.rendition,
                        width=w,
                        height=h,
                        ext=ext,
                        variant_id=r_info.variant_id,
                        render_id=r_info.render_id,
                    )
                else:
                    # Legacy debug branch local resize behavior check
                    if allow_local_resize and r_info.source_width > 0 and r_info.source_height > 0:
                        if r_info.downloaded_width < r_info.source_width or r_info.downloaded_height < r_info.source_height:
                            with Image.open(temp_path) as im:
                                resized = im.resize((r_info.source_width, r_info.source_height), Image.Resampling.LANCZOS)
                                resized.save(temp_path, "JPEG", quality=95)
                                r_info.final_width = r_info.source_width
                                r_info.final_height = r_info.source_height
                                r_info.is_local_resized = True
                    cand_name = fname

                # Collision protection (do not overwrite existing files)
                dest_file = out_p / cand_name
                if dest_file.exists():
                    c_stem = dest_file.stem
                    c_suffix = dest_file.suffix
                    c_idx = 1
                    while (out_p / f"{c_stem}_{c_idx}{c_suffix}").exists():
                        c_idx += 1
                    dest_file = out_p / f"{c_stem}_{c_idx}{c_suffix}"

                shutil.move(str(temp_path), str(dest_file))
                r_info.local_path = dest_file
                r_info.output_filename = dest_file.name
                downloaded_files.append(dest_file)
                audited_renditions.append(r_info)

                prog_adapter(len(downloaded_files), total_target, dest_file.name)
                tr.progress_step("download", f"Đã tải {len(downloaded_files)}/{total_target} ảnh", current=len(downloaded_files), total=total_target)
            except Exception as e:
                r_info.error = str(e)
                errors.append({"enhance_id": r_info.enhance_id, "rendition": r_info.rendition, "error": str(e)})
                audited_renditions.append(r_info)
                if log_fn:
                    log_fn(f"[Fotello][Error] Lỗi tải rendition {r_info.enhance_id} ({r_info.rendition}): {e}", "error")

        # 5. Validate
        tr.start_step("validate", "Đang kiểm tra tính toàn vẹn các file tải về...")
        valid_count = len(downloaded_files)
        tr.complete_step("validate", "success", f"Đã xác thực xong {valid_count} file hợp lệ.")

        # 6. Export
        tr.start_step("export", "Đang hoàn tất xuất dữ liệu...")
        if all_renditions:
            manifest_data = {
                "engine": "fotello.from_debug",
                "listing_id": listing_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total_renditions": total_target,
                "successful_downloads": valid_count,
                "failed_downloads": len(errors),
                "files": [
                    {
                        "filename": r.output_filename or (r.local_path.name if r.local_path else ""),
                        "enhance_id": r.enhance_id,
                        "variant_id": r.variant_id,
                        "render_id": r.render_id,
                        "rendition": r.rendition,
                        "field_name": r.field_name,
                        "gs_uri": r.gs_uri,
                        "width": r.final_width,
                        "height": r.final_height,
                        "file_bytes": r.file_bytes,
                        "sha256": r.sha256,
                        "status": "failed" if r.error else "success",
                        "error": r.error,
                        "source_width": r.source_width,
                        "source_height": r.source_height,
                        "resolution_shortfall": r.resolution_shortfall,
                    }
                    for r in audited_renditions
                ],
                "errors": errors,
            }
            manifest_path = out_p / "manifest.json"
            with open(manifest_path, "w", encoding="utf-8") as mf:
                json.dump(manifest_data, mf, indent=2)

        tr.complete_step("export", "success", f"Đã xuất {valid_count} ảnh về {out_p}")

        if valid_count == total_target and len(errors) == 0:
            tr.complete_step("download", "success", f"Đã tải đủ {valid_count}/{total_target} ảnh.", current=valid_count, total=total_target)
            return DownloadResult(success=True, status="success", count=valid_count, files=downloaded_files, renditions=audited_renditions)
        elif valid_count > 0:
            tr.complete_step("download", "partial", f"Tải hoàn tất một phần: {valid_count}/{total_target} ảnh.", current=valid_count, total=total_target)
            return DownloadResult(success=True, status="partial", count=valid_count, files=downloaded_files, renditions=audited_renditions, errors=errors)
        else:
            tr.complete_step("download", "failed", "Không tải được ảnh nào về thư mục đích.", current=0, total=total_target)
            return DownloadResult(success=False, status="failed", count=0, errors=errors)
