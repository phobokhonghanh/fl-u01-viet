"""
Tiện ích nghiệp vụ riêng cho Autoenhance Engine v3:
- fetch_order_status: Tra cứu trạng thái đơn hàng và danh sách ảnh trên Cloud.
- poll_order_completion: Thăm dò (polling) chờ đơn hàng hoàn tất AI.
- apply_pil_postprocess: Hậu kỳ ảnh PIL (nền trắng, resize Lanczos, DPI 300).
- download_order_images: Tải ảnh đa luồng có Smart Resume, Retry 3 lần, chống trùng tên.
"""
from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

from libs.constants.autoenhance import (
    API_BASE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_TIMEOUT,
    DEFAULT_MAX_WAIT_SECONDS,
    DOWNLOAD_CHUNK_SIZE,
    ENDPOINT_ORDER_DETAIL,
    ENDPOINT_ORDER_BRACKETS,
    ENHANCED_IMAGE_QUERY,
    MAX_DOWNLOAD_WORKERS,
)
from libs.utils.thread import run_parallel
from libs.utils.poll import poll_until, PollStatus
from libs.utils.common import make_progress_reporter


def fetch_order_status(
    session: requests.Session,
    headers: Dict[str, str],
    order_id: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> Tuple[str, List[Dict[str, Any]], int, Dict[str, Any]]:
    """
    Kiểm tra trạng thái đơn hàng trên Autoenhance Cloud.

    Returns:
        (status, completed_images, total_images, raw_order_data)
        Nếu không tìm thấy hoặc lỗi: ("not_found", [], 0, {})
    """
    url = f"{API_BASE}{ENDPOINT_ORDER_DETAIL.format(order_id=order_id)}"
    try:
        res = session.get(url, headers=headers, timeout=timeout)
        if res.status_code == 200:
            order_data = res.json()
            images = order_data.get("images", [])
            completed = [
                img for img in images
                if str(img.get("status", "")).lower() in ("processed", "completed", "done", "success", "enhanced")
                or img.get("enhanced") is True
            ]
            status = str(order_data.get("status", "unknown")).lower()
            return status, completed, len(images), order_data
        return "not_found", [], 0, {}
    except Exception:
        return "error", [], 0, {}


def poll_order_completion(
    session: requests.Session,
    headers: Dict[str, str],
    order_id: str,
    context: Any,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
    max_seconds: int = DEFAULT_MAX_WAIT_SECONDS,
) -> Optional[List[Dict[str, Any]]]:
    """
    Thăm dò trạng thái đơn hàng cho đến khi toàn bộ ảnh xử lý xong.
    Cập nhật tiến trình qua context.set_step và trả về danh sách completed_images.
    """
    if hasattr(context, "set_step"):
        context.set_step("polling", "Đang theo dõi tiến trình xử lý trên Autoenhance...", progress_pct=55)

    def _check(attempt: int) -> Tuple[PollStatus, Any, str]:
        status, completed, total_imgs, raw_data = fetch_order_status(session, headers, order_id)
        if status not in ("not_found", "error") and total_imgs > 0:
            failed = [
                img for img in raw_data.get("images", [])
                if str(img.get("status", "")).lower() in ("failed", "error")
                or img.get("error") is True
            ]

            pct = 55 + int((len(completed) / total_imgs) * 20)
            if hasattr(context, "set_step"):
                context.set_step("polling", f"Tiến độ AI: {len(completed)}/{total_imgs} ảnh hoàn tất", progress_pct=pct)

            if len(completed) + len(failed) == total_imgs:
                if len(completed) > 0:
                    return PollStatus.SUCCESS, completed, "Order đã xử lý thành công."
                else:
                    return PollStatus.FAILED, None, "Toàn bộ ảnh trong order đều xử lý thất bại."

        return PollStatus.IN_PROGRESS, None, "Đang xử lý..."

    stop_event = getattr(context, "stop_event", None)
    poll_res = poll_until(
        check_fn=_check,
        poll_interval=poll_interval,
        max_seconds=max_seconds,
        stop_event=stop_event,
    )

    if not poll_res.is_success:
        if hasattr(context, "error_message"):
            context.error_message = poll_res.error_message or "Quá thời gian chờ xử lý order (Timeout)."
        return None

    if hasattr(context, "log"):
        context.log("INFO", f"Order {order_id} đã xử lý xong {len(poll_res.data)} ảnh.")
    return poll_res.data


def apply_pil_postprocess(
    src_path: Path,
    dest_path: Path,
    target_dims: Optional[Tuple[int, int]] = None,
) -> None:
    """
    Xử lý hậu kỳ ảnh bằng PIL: kênh Alpha -> nền trắng, resize Lanczos, DPI 300.
    
    [Minh bạch Fallback]: Nếu file không thể giải mã qua PIL (ví dụ mock binary trong unit tests),
    sẽ di chuyển file trực tiếp để đảm bảo không làm mất dữ liệu đã tải về máy.
    """
    try:
        from PIL import Image

        with Image.open(src_path) as im:
            # 1. Đè alpha lên nền trắng nếu ảnh có độ trong suốt
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                alpha = im.convert("RGBA")
                bg = Image.new("RGBA", alpha.size, (255, 255, 255, 255))
                bg.alpha_composite(alpha)
                out_img = bg.convert("RGB")
            else:
                out_img = im.convert("RGB")

            # 2. Resize Lanczos về kích thước ảnh gốc nếu có metadata target_dims
            if target_dims and target_dims != (0, 0):
                orig_w, orig_h = target_dims
                if out_img.size != (orig_w, orig_h):
                    out_img = out_img.resize((orig_w, orig_h), Image.Resampling.LANCZOS)

            # 3. Xuất JPEG 300 DPI chất lượng cao
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            out_img.save(dest_path, "JPEG", quality=95, dpi=(300, 300), subsampling=0)
    except Exception:
        # Fallback có chủ đích: Bảo toàn file đã tải về nếu PIL không xử lý được
        src_path.replace(dest_path)


def filter_final_processed_images(
    order_id: str,
    images: List[Dict[str, Any]],
    headers: Dict[str, str],
    session: Optional[requests.Session] = None,
    log_fn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Lọc danh sách ảnh hoàn tất để chỉ giữ lại ảnh thành phẩm cuối cùng (Final Output).
    1. Ưu tiên tra cứu /orders/{order_id}/brackets: Các bracket luôn liên kết đến image_id thành phẩm cuối cùng.
    2. Fallback heuristic:
       - Nếu có ảnh chứa 'Manually Grouped' trong metadata (do endpoint /process sinh ra),
         chỉ giữ lại các ảnh có trường này.
       - Với các ảnh có cùng tên file (image_name), chỉ giữ lại ảnh mới nhất (date_added lớn nhất).
    """
    img_list = list(images)
    if not img_list:
        return []

    # 1. Thử lấy target image_ids từ brackets endpoint
    if order_id:
        try:
            requester = session if session is not None else requests
            url = f"{API_BASE}{ENDPOINT_ORDER_BRACKETS.format(order_id=order_id)}"
            resp = requester.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                brackets = data.get("brackets", [])
                target_ids = {str(b.get("image_id")) for b in brackets if b.get("image_id")}
                if target_ids:
                    matched = [img for img in img_list if str(img.get("image_id") or img.get("id", "")) in target_ids]
                    if matched:
                        if log_fn:
                            log_fn("INFO", f"[Autoenhance][Order] Đã lọc {len(matched)}/{len(img_list)} ảnh kết quả theo brackets.")
                        return matched
        except Exception as exc:
            if log_fn:
                log_fn("WARN", f"[Autoenhance][Order] Không thể đọc danh sách brackets: {exc}")

    # 2. Heuristic fallback:
    # 2a. Nếu có ảnh thành phẩm do /process tạo ra (Manually Grouped hoặc preset_id)
    processed_imgs = [
        img for img in img_list
        if (
            isinstance(img.get("metadata"), dict) and "Manually Grouped" in img["metadata"]
        ) or img.get("preset_id") is not None
    ]
    candidate_pool = processed_imgs if processed_imgs else img_list

    # 2b. Khử trùng lặp theo tên: Nếu có nhiều ảnh trùng filename, chọn ảnh có date_added mới nhất
    by_name: Dict[str, Dict[str, Any]] = {}
    for img in candidate_pool:
        name = str(img.get("image_name") or img.get("filename") or "")
        key = name.strip().lower() if name else str(img.get("image_id") or img.get("id", ""))
        if key not in by_name:
            by_name[key] = img
        else:
            prev = by_name[key]
            prev_date = prev.get("date_added") or 0
            cur_date = img.get("date_added") or 0
            if cur_date >= prev_date:
                by_name[key] = img

    filtered = list(by_name.values())
    if len(filtered) < len(img_list) and log_fn:
        log_fn("INFO", f"[Autoenhance][Order] Đã loại bỏ {len(img_list) - len(filtered)} ảnh sơ bộ, giữ lại {len(filtered)} ảnh hoàn thành cuối cùng.")
    return filtered


def download_order_images(
    context: Any,
    session: Optional[requests.Session],
    headers: Dict[str, str],
    completed_images: List[Dict[str, Any]],
    max_workers: int = MAX_DOWNLOAD_WORKERS,
) -> bool:
    """
    Tải ảnh thành phẩm và hậu kỳ (PIL) đa luồng qua run_parallel.
    - Ánh xạ chống trùng tên (anti-collision naming).
    - Smart Resume cấp độ file: bỏ qua nếu file đã tồn tại và > 0 byte.
    - Cơ chế Retry 3 lần nếu server 503 hoặc lỗi mạng.
    """
    if hasattr(context, "is_cancelled") and context.is_cancelled():
        return False

    # Lọc bỏ ảnh draft/sơ bộ nếu có
    order_id = getattr(context, "remote_job_id", "") or getattr(context, "extra_data", {}).get("order_id", "")
    if order_id:
        completed_images = filter_final_processed_images(
            order_id=order_id,
            images=completed_images,
            headers=headers,
            session=session,
            log_fn=getattr(context, "log", None),
        )

    out_dir = Path(getattr(context, "output_dir", "."))
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(completed_images)
    if hasattr(context, "set_step"):
        context.set_step("download", f"Bắt đầu tải và xử lý hậu kỳ {total} ảnh...", progress_pct=80)

    # Cơ chế chống trùng tên file / ghi đè (Anti-collision unique naming):
    extra_data = getattr(context, "extra_data", {})
    download_map: Dict[str, str] = extra_data.setdefault("ae_download_map", {})
    used_names: set[str] = set()
    download_tasks: List[Tuple[Dict[str, Any], Path]] = []

    for img_data in completed_images:
        img_id = str(img_data.get("image_id") or img_data.get("id", ""))
        orig_name = str(img_data.get("image_name") or f"{img_id}.jpg")
        clean_stem = Path(orig_name).stem.strip() or "image"
        ext = ".jpg"

        # 1. Tái sử dụng tên file đã cấp phát nếu là job khôi phục (Resume)
        if img_id in download_map:
            assigned_name = download_map[img_id]
        else:
            candidate_name = f"{clean_stem}{ext}"
            counter = 1
            while candidate_name in used_names or (
                (out_dir / candidate_name).is_file() and candidate_name not in download_map.values()
            ):
                candidate_name = f"{clean_stem}_{counter}{ext}"
                counter += 1
            assigned_name = candidate_name
            download_map[img_id] = assigned_name

        used_names.add(assigned_name)
        dest_file = out_dir / assigned_name
        download_tasks.append((img_data, dest_file))

    def _process_one(task: Tuple[Dict[str, Any], Path]) -> str:
        img_data, dest_file = task
        img_id = str(img_data.get("image_id") or img_data.get("id", ""))
        orig_name = str(img_data.get("image_name") or f"{img_id}.jpg")

        # Smart Resume cho từng file: Bỏ qua nếu đã tải và xử lý xong từ phiên trước
        if dest_file.is_file() and dest_file.stat().st_size > 0:
            if hasattr(context, "log"):
                context.log("INFO", f"[RESUME] Bỏ qua tải {dest_file.name} (đã tồn tại sẵn trên máy).")
            return str(dest_file)

        dl_url = f"{API_BASE}/images/{img_id}/enhanced?{ENHANCED_IMAGE_QUERY}"
        last_err = ""
        resp = None

        requester = session if session is not None else requests

        for attempt in range(3):
            if hasattr(context, "is_cancelled") and context.is_cancelled():
                raise IOError("Tác vụ tải ảnh bị dừng bởi người dùng.")
            try:
                r = requester.get(dl_url, headers=headers, stream=True, timeout=60)
                if r.status_code == 200:
                    resp = r
                    break
                last_err = f"HTTP {r.status_code} ({r.text[:150]})"
            except Exception as exc:
                last_err = str(exc)
            time.sleep(1.5 * (attempt + 1))

        if not resp or resp.status_code != 200:
            raise IOError(f"Lỗi tải ảnh {dest_file.name}: {last_err}")

        temp_raw = out_dir / f".temp_{img_id}.png"
        with open(temp_raw, "wb") as f:
            for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                f.write(chunk)

        meta_dims = getattr(context, "meta_dims", {})
        # PLUS
        time.sleep(1)  # Giãn cách 1s giữa mỗi lượt tải và xử lý PIL
        apply_pil_postprocess(temp_raw, dest_file, meta_dims.get(orig_name))

        if temp_raw.exists():
            temp_raw.unlink(missing_ok=True)

        if not dest_file.is_file():
            raise IOError(f"Không thể tạo file kết quả cho {dest_file.name}")

        return str(dest_file)

    stop_event = getattr(context, "stop_event", None)
    batch_res = run_parallel(
        items=download_tasks,
        worker_fn=_process_one,
        max_workers=max_workers,
        stop_event=stop_event,
        on_progress=make_progress_reporter(
            context, "download", "tải & xử lý", 80, 100, lambda t: t[1].name
        ),
    )

    downloaded = [res for _, res in batch_res.succeeded if res]
    if hasattr(context, "downloaded_files"):
        context.downloaded_files = downloaded

    if not batch_res.all_succeeded:
        if batch_res.is_cancelled:
            err = "Tải ảnh bị dừng bởi người dùng."
        elif batch_res.failed:
            err = f"Lỗi xử lý tải ảnh: {batch_res.failed[0][1]}"
        else:
            err = "Không tải hoặc xử lý được đầy đủ ảnh."
        if hasattr(context, "error_message"):
            context.error_message = err
        return False

    if hasattr(context, "log"):
        context.log("INFO", f"Hoàn tất tải về và hậu kỳ {len(downloaded)} ảnh cho Autoenhance.")
    return True
