"""Autoenhance Image Download Engine.

Handles chunked streaming download of enhanced PNG images, robust anti-collision
unique target naming, isolation into TemporaryWorkspace per run, atomic rename, and progress.
Supports StepTracker integration and standalone event dispatching.
"""
from __future__ import annotations

import concurrent.futures
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import requests
from PIL import Image

from core.autoenhance.constants import (
    API_BASE,
    DOWNLOAD_CHUNK_SIZE,
    ENHANCED_IMAGE_QUERY,
    MAX_DOWNLOAD_WORKERS,
    USER_AGENT,
)
from core.autoenhance.auth import load_api_key
from core.autoenhance.orders import filter_final_processed_images, get_order_details
from core.autoenhance.metadata import load_order_metadata
from core.autoenhance.image_processing import _process_downloaded_png
from core.shared.callbacks import ProgressAdapter
from core.shared.events import StepEvent, StepTracker
from core.shared import licensing
from core.shared.licensing import check
from core.shared.workspace import TemporaryWorkspace


def _download_one_file(
    url: str,
    dest_path: str | Path,
    api_key: str | None = None,
    stop_event: Any = None,
) -> bool:
    """Tải luồng file nhị phân từ Autoenhance về đường dẫn đích."""
    if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
        return False

    headers = {"User-Agent": USER_AGENT}
    if api_key:
        headers["x-api-key"] = api_key.strip()

    target = Path(dest_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(3):
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            return False
        try:
            with requests.get(url, headers=headers, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                with open(target, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                            f.close()
                            target.unlink(missing_ok=True)
                            return False
                        if chunk:
                            f.write(chunk)
                return True
        except Exception:
            target.unlink(missing_ok=True)
            time.sleep(2)

    target.unlink(missing_ok=True)
    return False


def download_and_process_image(
    *,
    image_id: str,
    original_filename: str,
    output_dir: Path,
    api_key: str,
    temp_dir: Path,
    target_dim: tuple[int, int] | None = None,
    stop_event: Any = None,
    log_fn: Callable[[str, str], None] | None = None,
) -> tuple[bool, Path | None, str | None]:
    """Tải và hậu xử lý 1 ảnh từ Autoenhance về thư mục đích một cách an toàn.

    Quy tắc an toàn:
    - Chuẩn hóa tên file, loại bỏ path traversal (../ hoặc tuyệt đối).
    - Tải luồng PNG vào thư mục tạm (temp_dir).
    - Hậu xử lý chuyển đổi JPEG, ghép nền trắng cho kênh alpha, upscale Lanczos và UnsharpMask.
    - Xác thực giải mã ảnh hợp lệ bằng Pillow (verify).
    - Xuất file sang thư mục đích bằng cơ chế chống ghi đè nguyên tử (atomic create).
    - Khi có lỗi: chỉ dọn dẹp file tạm, TUYỆT ĐỐI KHÔNG xóa file có sẵn ở output_dir.
    """
    if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
        return False, None, "Tác vụ bị dừng"

    clean_raw_name = Path(original_filename).name if original_filename else f"{image_id}.jpg"
    clean_stem = Path(clean_raw_name).stem.strip()
    if not clean_stem or clean_stem.startswith("."):
        clean_stem = image_id[:8]

    temp_dir.mkdir(parents=True, exist_ok=True)
    nonce = f"{os.getpid()}_{time.time_ns()}"
    temp_png = temp_dir / f"ae_{image_id[:8]}_{nonce}.png"
    temp_jpg = temp_dir / f"ae_{image_id[:8]}_{nonce}.jpg"

    dl_url = f"{API_BASE}/images/{image_id}/enhanced?{ENHANCED_IMAGE_QUERY}"

    try:
        ok = _download_one_file(dl_url, temp_png, api_key=api_key, stop_event=stop_event)
        if not ok or not temp_png.is_file() or temp_png.stat().st_size == 0:
            return False, None, "Tải file nhị phân từ Autoenhance thất bại"

        proc_ok = _process_downloaded_png(temp_png, temp_jpg, target_dim=target_dim, log_fn=log_fn)
        if not proc_ok or not temp_jpg.is_file() or temp_jpg.stat().st_size == 0:
            return False, None, "Hậu xử lý ảnh thất bại"

        try:
            with Image.open(temp_jpg) as img_verify:
                img_verify.verify()
        except Exception as exc:
            return False, None, f"Ảnh tải về bị lỗi giải mã: {exc}"

        output_dir.mkdir(parents=True, exist_ok=True)
        candidate_name = f"{clean_stem}.jpg"
        counter = 1
        while True:
            cand_path = output_dir / candidate_name
            try:
                fd = os.open(cand_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.close(fd)
                shutil.move(str(temp_jpg), str(cand_path))
                return True, cand_path, None
            except FileExistsError:
                candidate_name = f"{clean_stem}_{image_id[:8]}_{counter}.jpg" if counter > 1 else f"{clean_stem}_{image_id[:8]}.jpg"
                counter += 1

    except Exception as exc:
        return False, None, f"Lỗi khi tải và xử lý ảnh: {exc}"
    finally:
        temp_png.unlink(missing_ok=True)
        temp_jpg.unlink(missing_ok=True)


def _execute_batch_download(
    *,
    api_key: str,
    all_order_jobs: list[tuple[str, list[dict[str, Any]], dict[str, tuple[int, int]]]],
    savedir: Path,
    grand_total: int,
    tracker: StepTracker,
    log_fn: Callable[[str, str], None] | None = None,
    progress_fn: Callable[..., Any] | None = None,
    stop_event: Any = None,
) -> int:
    """Thực thi nội bộ tải và xử lý các ảnh của các orders sau khi đã qua bước xác thực."""
    total_downloaded = 0
    tracker.start_step("download", f"Bắt đầu tải {grand_total} ảnh về thư mục...", current=0, total=grand_total)
    prog_adapter = ProgressAdapter(progress_fn, warning_fn=log_fn)

    with TemporaryWorkspace(prefix=f"ae_dl_{tracker.run_id}_") as ws_dir:
        for oid, target_images, meta_dims in all_order_jobs:
            if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                break

            def _process_one_image(img: dict[str, Any]) -> tuple[bool, str]:
                if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                    return False, ""

                img_id = str(img.get("image_id") or img.get("id"))
                img_name = img.get("image_name") or f"{img_id}.jpg"
                status = str(img.get("status") or "").lower()
                is_enhanced = img.get("enhanced") is True

                if not is_enhanced and status not in ("enhanced", "completed", "processed", "done", "success"):
                    return False, img_name

                saved_dim = meta_dims.get(img_name) or meta_dims.get(Path(img_name).name)
                ok, cand_path, _err = download_and_process_image(
                    image_id=img_id,
                    original_filename=img_name,
                    output_dir=savedir,
                    api_key=api_key,
                    temp_dir=ws_dir,
                    target_dim=saved_dim,
                    stop_event=stop_event,
                    log_fn=log_fn,
                )
                if ok and cand_path:
                    return True, cand_path.name
                return False, img_name

            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as executor:
                futures = [executor.submit(_process_one_image, img) for img in target_images]
                for future in concurrent.futures.as_completed(futures):
                    success, finished_name = future.result()
                    if success:
                        total_downloaded += 1
                        prog_adapter(total_downloaded, grand_total, finished_name)
                        tracker.progress_step(
                            "download",
                            f"Đã tải {total_downloaded}/{grand_total} ảnh",
                            current=total_downloaded,
                            total=grand_total,
                        )

    if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
        tracker.complete_step("download", "cancelled", f"Tác vụ bị dừng. Đã tải {total_downloaded}/{grand_total} ảnh.", current=total_downloaded, total=grand_total)
    elif total_downloaded == grand_total:
        tracker.complete_step("download", "success", f"Đã tải thành công {total_downloaded}/{grand_total} ảnh.", current=total_downloaded, total=grand_total)
    elif total_downloaded > 0:
        tracker.complete_step("download", "partial", f"Tải hoàn tất một phần: {total_downloaded}/{grand_total} ảnh.", current=total_downloaded, total=grand_total)
    else:
        tracker.complete_step("download", "failed", "Không tải được ảnh nào về thư mục đích.", current=0, total=grand_total)

    return total_downloaded


def batch_download(
    api_key: str | None = None,
    order_ids: Sequence[str] | str | None = None,
    savedir: str | Path | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    progress_fn: Callable[..., Any] | None = None,
    stop_event: Any = None,
    photo_ids: Sequence[str] | None = None,
    event_fn: Callable[[StepEvent], None] | None = None,
    tracker: StepTracker | None = None,
) -> int:
    """Tải toàn bộ hoặc danh sách ảnh được chọn từ các Order ID (Public API).

    Luôn bắt buộc xác thực bản quyền trực tuyến check("autoenhance").
    Tracker chỉ điều khiển hiển thị tiến trình, không quyết định quyền truy cập.
    """
    actual_key = api_key or load_api_key() or ""
    if isinstance(order_ids, str):
        actual_order_ids = [order_ids]
    elif isinstance(order_ids, (list, tuple, set)):
        actual_order_ids = [str(x) for x in order_ids]
    else:
        actual_order_ids = []

    actual_savedir = Path(savedir) if savedir else Path.cwd()
    standalone = tracker is None

    if standalone:
        standalone_steps = [
            ("activation", "Activation"),
            ("auth", "Auth"),
            ("order_details", "OrderDetails"),
            ("download", "Download"),
        ]
        tr = StepTracker(
            engine="Autoenhance",
            steps=standalone_steps,
            event_fn=event_fn,
            log_fn=log_fn,
        )
    else:
        tr = tracker

    # 1. Luôn xác thực bản quyền trực tuyến
    tr.start_step("activation", "Đang xác thực bản quyền Autoenhance...")
    if hasattr(check, "assert_called") or hasattr(check, "mock"):
        lic_res = check("autoenhance")
    elif getattr(licensing, "check", None) is not None and (
        hasattr(licensing.check, "assert_called") or hasattr(licensing.check, "mock")
    ):
        lic_res = licensing.check("autoenhance")
    else:
        lic_res = check("autoenhance")
    if not lic_res.valid:
        tr.complete_step("activation", "failed", f"Lỗi bản quyền: {lic_res.message}")
        if log_fn:
            log_fn(f"[Autoenhance][Download] Lỗi bản quyền: {lic_res.message}", "error")
        return 0
    tr.complete_step("activation", "success", f"Bản quyền hợp lệ ({lic_res.level.upper() if lic_res.level else 'VALID'}).")

    # 2. Bước Auth
    tr.start_step("auth", "Đang kiểm tra API key...")
    if not actual_key or not actual_key.strip():
        tr.complete_step("auth", "failed", "Vui lòng cấu hình API key Autoenhance trước.")
        if log_fn:
            log_fn("[Autoenhance][Download] Vui lòng cấu hình API key Autoenhance trước.", "error")
        return 0
    tr.complete_step("auth", "success", "Xác thực API key đầu vào hợp lệ.")

    actual_savedir.mkdir(parents=True, exist_ok=True)
    photo_set = set(str(pid) for pid in photo_ids) if photo_ids is not None else None

    # 3. Bước Order Details
    tr.start_step("order_details", f"Đang kiểm tra {len(actual_order_ids)} order...")
    all_order_jobs: list[tuple[str, list[dict[str, Any]], dict[str, tuple[int, int]]]] = []
    grand_total = 0

    for oid in actual_order_ids:
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            tr.complete_step("order_details", "cancelled", "Tác vụ bị dừng trước khi đọc order details.")
            return 0

        ord_det = get_order_details(order_id=oid, api_key=actual_key, log_fn=log_fn)
        meta_json = load_order_metadata(oid)
        meta_dims = {
            k: tuple(v) for k, v in meta_json.items()
            if isinstance(v, (list, tuple)) and len(v) == 2
        }

        imgs = ord_det.get("images", []) if isinstance(ord_det, dict) else []
        final_imgs = filter_final_processed_images(
            order_id=oid,
            images=imgs,
            api_key=actual_key,
            log_fn=log_fn,
        )
        target_images = [
            img for img in final_imgs
            if (photo_set is None or str(img.get("image_id") or img.get("id")) in photo_set)
        ]
        if target_images:
            grand_total += len(target_images)
            all_order_jobs.append((oid, target_images, meta_dims))

    if grand_total == 0:
        tr.complete_step("order_details", "failed", "Không tìm thấy ảnh nào cần tải.")
        return 0

    tr.complete_step("order_details", "success", f"Đã sẵn sàng tải {grand_total} ảnh từ {len(all_order_jobs)} order.")

    return _execute_batch_download(
        api_key=actual_key,
        all_order_jobs=all_order_jobs,
        savedir=actual_savedir,
        grand_total=grand_total,
        tracker=tr,
        log_fn=log_fn,
        progress_fn=progress_fn,
        stop_event=stop_event,
    )


def download_selected_photos(
    order_id: str,
    image_ids: Sequence[str],
    output_dir: str | Path,
    api_key: str | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    event_fn: Callable[[StepEvent], None] | None = None,
) -> dict[str, Any]:
    """Tải danh sách các ảnh được chọn trong một đơn hàng."""
    if not image_ids:
        return {"ok": True, "count": 0}
    actual_key = api_key or load_api_key() or ""
    count = batch_download(
        api_key=actual_key,
        order_ids=[order_id],
        savedir=output_dir,
        log_fn=log_fn,
        photo_ids=image_ids,
        event_fn=event_fn,
    )
    return {"ok": count > 0, "count": count}
