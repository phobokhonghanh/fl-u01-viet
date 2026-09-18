"""Download Engine for Fotello with streaming, atomic export, and run manifests."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.fotello.auth import get_tokens
from core.fotello.client import redact_sensitive, stream_download_image
from core.fotello.constants import DOWNLOAD_STEPS
from core.fotello.listings import list_enhances
from core.fotello.models import DownloadResult, RenditionInfo
from core.shared.callbacks import ProgressAdapter
from core.shared.events import StepEvent, StepTracker
from core.shared.licensing import check
from core.shared.workspace import TemporaryWorkspace


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
    """Định dạng tên file chuẩn hóa cho từng rendition."""
    stem = Path(original_filename).stem if original_filename else enhance_id
    # Sanitize stem to avoid path injection
    clean_stem = Path(stem).name
    ext = ext.lstrip(".")
    if variant_id and render_id:
        return f"{clean_stem}__{enhance_id}__{variant_id}__{render_id}__{rendition}__{width}x{height}.{ext}"
    elif variant_id:
        return f"{clean_stem}__{enhance_id}__{variant_id}__{rendition}__{width}x{height}.{ext}"
    else:
        return f"{clean_stem}__{enhance_id}__{rendition}__{width}x{height}.{ext}"


def export_unique_file(temp_file: Path, dest_dir: Path, preferred_filename: str) -> Path:
    """Xuất file sang thư mục đích bảo đảm không ghi đè kể cả trong môi trường đa tiến trình."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(preferred_filename).name
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix

    candidate = safe_name
    idx = 1
    while True:
        target = dest_dir / candidate
        try:
            # Atomic hard-link: thất bại với FileExistsError nếu file đã tồn tại
            os.link(temp_file, target)
            temp_file.unlink()
            return target
        except FileExistsError:
            candidate = f"{stem}_{idx}{suffix}"
            idx += 1
        except OSError:
            # Fallback khi liên kết qua khác filesystem: tạo file atomic bằng O_CREAT | O_EXCL
            try:
                fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "wb") as f_out, open(temp_file, "rb") as f_in:
                    shutil.copyfileobj(f_in, f_out)
                temp_file.unlink()
                return target
            except FileExistsError:
                candidate = f"{stem}_{idx}{suffix}"
                idx += 1


def download_listing(
    listing_id: str,
    output_dir: str | Path,
    prioritize_upsized: bool = True,
    all_renditions: bool = False,
    log_fn: Callable[[str, str], None] | None = None,
    progress_fn: Callable[..., Any] | None = None,
    stop_event: Any | None = None,
    event_fn: Callable[[StepEvent], None] | None = None,
    run_id: str | None = None,
) -> DownloadResult:
    """Tải các rendition của listing Fotello với tiến trình an toàn và thống nhất."""
    active_run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tr = StepTracker(
        engine="fotello",
        steps=DOWNLOAD_STEPS,
        run_id=active_run_id,
        event_fn=event_fn,
        log_fn=log_fn,
    )
    out_p = Path(output_dir).resolve()
    out_p.mkdir(parents=True, exist_ok=True)
    prog_adapter = ProgressAdapter(progress_fn, warning_fn=log_fn)

    # 1. Activation (Xác thực bản quyền key Fotello)
    tr.start_step("activation", "Đang xác thực bản quyền Fotello...")
    lic_res = check("fotello")
    if not lic_res.valid:
        tr.complete_step("activation", "failed", f"Lỗi bản quyền: {lic_res.message}")
        return DownloadResult(success=False, status="failed", count=0, message=f"Lỗi bản quyền: {lic_res.message}")
    tr.complete_step("activation", "success", f"Bản quyền hợp lệ ({lic_res.level.upper() if lic_res.level else 'VALID'}).")

    if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
        return DownloadResult(success=False, status="cancelled", count=0, message="Tác vụ bị hủy")

    # 2. Auth
    tr.start_step("auth", "Dang kiem tra phien lam viec...")
    tokens = get_tokens()
    if not tokens or not (tokens.get("access_token") or tokens.get("id_token")):
        tr.complete_step("auth", "failed", "Chua co token xac thuc Fotello.")
        return DownloadResult(success=False, status="failed", count=0, message="Chua co token xac thuc")
    access_token = tokens.get("access_token") or tokens.get("id_token")
    tr.complete_step("auth", "success", "Xac thuc phien thanh cong.")

    if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
        return DownloadResult(success=False, status="cancelled", count=0, message="Tac vu bi huy")

    # 2. Listings
    tr.start_step("listings", f"Dang kiem tra listing {listing_id}...")
    ready_items = list_enhances(
        listing_id,
        prioritize_upsized=prioritize_upsized,
        all_renditions=all_renditions,
        log_fn=log_fn,
        stop_event=stop_event,
    )
    ready_items = [e for e in ready_items if e.get("has_image")]
    if not ready_items:
        tr.complete_step("listings", "failed", f"Khong tim thay anh da xu ly trong listing {listing_id}")
        return DownloadResult(success=False, status="failed", count=0, message="Khong co anh nao san sang")
    tr.complete_step("listings", "success", f"Tim thay {len(ready_items)} anh kha dung.")

    # 3. Resolve outputs
    tr.start_step("resolve_outputs", "Dang phan tich cac duong dan rendition...")
    resolved_jobs: list[tuple[dict[str, Any], RenditionInfo]] = []
    for item in ready_items:
        uri = item.get("image_uri", "")
        rend_name = item.get("rendition", "")
        r_info = RenditionInfo(
            enhance_id=item.get("enhance_id") or item.get("id", ""),
            rendition=rend_name,
            gs_uri=uri,
            original_filename=item.get("filename", ""),
            source_width=item.get("inputWidth", 0),
            source_height=item.get("inputHeight", 0),
            is_upsized="upsized" in rend_name,
            variant_id=item.get("variant_id"),
            render_id=item.get("render_id"),
            field_name=item.get("field_name"),
            requested_rendition=item.get("requested_rendition", rend_name),
            fallback_reason=item.get("fallback_reason"),
        )
        resolved_jobs.append((item, r_info))
    tr.complete_step("resolve_outputs", "success", f"Da giai quyet xong {len(resolved_jobs)} rendition.")

    # 4. Download
    total_target = len(resolved_jobs)
    tr.start_step("download", f"Bat dau tai {total_target} anh...", current=0, total=total_target)
    downloaded_files: list[Path] = []
    audited_renditions: list[RenditionInfo] = []
    errors: list[dict[str, str]] = []

    with TemporaryWorkspace(prefix="fotello_dl_") as ws_dir:
        for idx, (item, r_info) in enumerate(resolved_jobs):
            if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                tr.complete_step(
                    "download",
                    "cancelled",
                    "Tac vu bi dung khi tai anh.",
                    current=len(downloaded_files),
                    total=total_target,
                )
                return DownloadResult(
                    success=len(downloaded_files) > 0,
                    status="cancelled",
                    count=len(downloaded_files),
                    files=downloaded_files,
                    renditions=audited_renditions,
                    message="Tac vu bi dung boi nguoi dung",
                )

            fname = item.get("filename") or f"{r_info.enhance_id}.jpg"
            temp_path = ws_dir / f"tmp_{idx}_{Path(fname).name}"
            try:
                # Streaming download with chunk hash and PIL verification
                f_bytes, sha256_hex, (w, h), ext = stream_download_image(
                    uri=r_info.gs_uri,
                    access_token=access_token,
                    dest_temp_path=temp_path,
                    stop_event=stop_event,
                )
                r_info.file_bytes = f_bytes
                r_info.sha256 = sha256_hex
                r_info.downloaded_width = w
                r_info.downloaded_height = h
                r_info.final_width = w
                r_info.final_height = h

                # Detect resolution shortfall
                if r_info.source_width > 0 and (w < r_info.source_width or h < r_info.source_height):
                    r_info.resolution_shortfall = True

                r_info.is_local_resized = False

                if all_renditions:
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
                    cand_name = fname if Path(fname).suffix else f"{fname}.{ext}"

                # Safe atomic non-overwriting export
                dest_file = export_unique_file(temp_path, out_p, cand_name)
                r_info.local_path = dest_file
                r_info.output_filename = dest_file.name
                downloaded_files.append(dest_file)
                audited_renditions.append(r_info)

                prog_adapter(len(downloaded_files), total_target, dest_file.name)
                tr.progress_step(
                    "download",
                    f"Da tai {len(downloaded_files)}/{total_target} anh",
                    current=len(downloaded_files),
                    total=total_target,
                )
            except Exception as e:
                err_msg = redact_sensitive(str(e))
                r_info.error = err_msg
                errors.append({
                    "enhance_id": r_info.enhance_id,
                    "rendition": r_info.rendition,
                    "error": err_msg,
                })
                audited_renditions.append(r_info)
                if log_fn:
                    log_fn(f"[Fotello][Error] Loi tai rendition {r_info.enhance_id} ({r_info.rendition}): {err_msg}", "error")

        # Hoàn tất bước download trước validate
        dl_status = "success" if len(errors) == 0 else ("failed" if len(downloaded_files) == 0 else "partial")
        tr.complete_step(
            "download",
            dl_status,
            f"Ket thuc tai anh: {len(downloaded_files)} thanh cong, {len(errors)} loi.",
            current=len(downloaded_files),
            total=total_target,
        )

    # 5. Validate
    tr.start_step("validate", "Dang kiem tra tinh toan ven cac file tai ve...")
    valid_count = len(downloaded_files)
    if valid_count == 0:
        tr.complete_step("validate", "failed", "Khong co file nao duoc tai thanh cong.")
    else:
        tr.complete_step("validate", "success", f"Da xac thuc xong {valid_count} file hop le.")

    # 6. Export manifest per run
    tr.start_step("export", "Dang hoan tat xuat du lieu...")
    manifest_data = {
        "engine": "fotello",
        "run_id": active_run_id,
        "listing_id": listing_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "all_renditions_mode": all_renditions,
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
                "requested_rendition": r.requested_rendition,
                "fallback_reason": r.fallback_reason,
                "field_name": r.field_name,
                "gs_uri": redact_sensitive(r.gs_uri),
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

    manifest_file = out_p / f"manifest_{active_run_id}.json"
    with open(manifest_file, "w", encoding="utf-8") as mf:
        json.dump(manifest_data, mf, indent=2)

    tr.complete_step("export", "success", f"Da xuat hoan tat {valid_count} file va manifest.")

    final_status = "success" if len(errors) == 0 and valid_count > 0 else ("failed" if valid_count == 0 else "partial")

    return DownloadResult(
        success=(valid_count > 0),
        status=final_status,
        count=valid_count,
        files=downloaded_files,
        renditions=audited_renditions,
        errors=errors,
        manifest_path=manifest_file,
        message=f"Tai hoan tat {valid_count}/{total_target} anh (Loi: {len(errors)})",
    )


def download_multiple_listings(
    listing_ids: list[str] | Sequence[str],
    output_dir: str | Path,
    prioritize_upsized: bool = True,
    all_renditions: bool = False,
    log_fn: Callable[[str, str], None] | None = None,
    progress_fn: Callable[..., Any] | None = None,
    stop_event: Any | None = None,
    event_fn: Callable[[StepEvent], None] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Tải tuần tự danh sách các listing đã chọn vào các thư mục con riêng biệt.

    Quy tắc:
    - Mỗi listing có kết quả, lỗi và thư mục con riêng (output_dir / listing_id).
    - Tải tuần tự: lỗi của một listing không làm dừng listing tiếp theo.
    - Lỗi xác thực chung (bản quyền, token) hoặc dừng bởi người dùng (stop_event)
      sẽ dừng ngay toàn bộ hàng đợi tải.
    - Không áp giới hạn output của luồng tạo ảnh mới lên luồng tải listing có sẵn.
    - Ghi nhận manifest tổng hợp và trả về đường dẫn thư mục để UI mở trực tiếp.
    """
    root_out = Path(output_dir).resolve()
    root_out.mkdir(parents=True, exist_ok=True)
    active_run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Xác thực bản quyền một lần trước khi bắt đầu hàng đợi
    lic_res = check("fotello")
    if not lic_res.valid:
        msg = f"Lỗi bản quyền Fotello: {lic_res.message}"
        if log_fn:
            log_fn(f"[Fotello][BatchListings] {msg}", "error")
        return {
            "success": False,
            "status": "failed",
            "message": msg,
            "total": len(listing_ids),
            "succeeded": 0,
            "failed": len(listing_ids),
            "results": {},
            "output_dir": str(root_out),
            "manifest_path": None,
        }

    tokens = get_tokens()
    if not tokens or not (tokens.get("access_token") or tokens.get("id_token")):
        msg = "Chưa có token xác thực phiên Fotello."
        if log_fn:
            log_fn(f"[Fotello][BatchListings] {msg}", "error")
        return {
            "success": False,
            "status": "failed",
            "message": msg,
            "total": len(listing_ids),
            "succeeded": 0,
            "failed": len(listing_ids),
            "results": {},
            "output_dir": str(root_out),
            "manifest_path": None,
        }

    results: dict[str, Any] = {}
    succeeded_count = 0
    failed_count = 0

    if log_fn:
        log_fn(f"[Fotello][BatchListings] Bắt đầu tải {len(listing_ids)} listing tuần tự...", "info")

    for idx, lid in enumerate(listing_ids, start=1):
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            if log_fn:
                log_fn(f"[Fotello][BatchListings] Người dùng dừng hàng đợi tại listing {idx}/{len(listing_ids)}.", "warn")
            break

        sub_dir = root_out / lid
        sub_dir.mkdir(parents=True, exist_ok=True)

        if log_fn:
            log_fn(f"[Fotello][BatchListings] [{idx}/{len(listing_ids)}] Bắt đầu tải listing '{lid}'...", "info")

        try:
            res = download_listing(
                listing_id=lid,
                output_dir=sub_dir,
                prioritize_upsized=prioritize_upsized,
                all_renditions=all_renditions,
                log_fn=log_fn,
                progress_fn=progress_fn,
                stop_event=stop_event,
                event_fn=event_fn,
                run_id=f"{active_run_id}_{lid}",
            )
            results[lid] = {
                "success": res.success,
                "status": res.status,
                "count": res.count,
                "errors": res.errors,
                "output_dir": str(sub_dir),
                "manifest_path": str(res.manifest_path) if res.manifest_path else None,
                "message": res.message,
            }
            if res.success:
                succeeded_count += 1
            else:
                failed_count += 1
        except Exception as exc:
            failed_count += 1
            err_str = str(exc)
            results[lid] = {
                "success": False,
                "status": "failed",
                "count": 0,
                "errors": [{"error": err_str}],
                "output_dir": str(sub_dir),
                "manifest_path": None,
                "message": err_str,
            }
            if log_fn:
                log_fn(f"[Fotello][BatchListings] Lỗi khi tải listing '{lid}': {err_str}", "error")

    # Ghi manifest tổng hợp
    summary_manifest = root_out / f"batch_listings_manifest_{active_run_id}.json"
    manifest_data = {
        "run_id": active_run_id,
        "engine": "fotello",
        "type": "batch_listings_download",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_listings": len(listing_ids),
        "succeeded": succeeded_count,
        "failed": failed_count,
        "results": results,
    }
    with open(summary_manifest, "w", encoding="utf-8") as mf:
        json.dump(manifest_data, mf, indent=2, ensure_ascii=False)

    final_status = "success" if failed_count == 0 else ("failed" if succeeded_count == 0 else "partial")
    return {
        "success": succeeded_count > 0,
        "status": final_status,
        "total": len(listing_ids),
        "succeeded": succeeded_count,
        "failed": failed_count,
        "results": results,
        "output_dir": str(root_out),
        "manifest_path": str(summary_manifest),
    }

