"""
Job Executor cho Autoenhance Engine v3.
Kế thừa BaseExecutor, triển khai toàn bộ quy trình xử lý của Autoenhance:
1. Pre-transform: Chuẩn bị ảnh, đo kích thước ảnh gốc lưu vào meta_dims.
2. Create Order: Tạo đơn hàng mới trên API Autoenhance v3 (POST /orders/).
3. Upload to S3: Xin link upload cho từng ảnh (POST /images/) và PUT S3 đa luồng (run_parallel).
4. Execute Process: Kích hoạt xử lý AI với các tùy chọn preset (POST /orders/{id}/process).
5. Polling: Thăm dò trạng thái cho đến khi order hoàn tất (poll_until).
6. Download & Post-process: Tải ảnh PNG, xử lý nền trắng, resize Lanczos, DPI 300 (run_parallel).
"""
from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

import requests

from libs.constants.autoenhance import (
    API_BASE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_TIMEOUT,
    DOWNLOAD_CHUNK_SIZE,
    ENDPOINT_BRACKETS,
    ENDPOINT_IMAGES,
    ENDPOINT_ORDER_DETAIL,
    ENDPOINT_ORDERS,
    ENDPOINT_PROCESS,
    ENHANCED_IMAGE_QUERY,
    MAX_API_WORKERS,
    MAX_DOWNLOAD_WORKERS,
    MAX_UPLOAD_FILES,
    USER_AGENT,
)
from libs.executor.base import BaseExecutor, ExecutionContext
from libs.auth import get_active_session
from libs.utils import run_parallel, poll_until, PollStatus, make_progress_reporter
from libs.utils.autoenhance import (
    fetch_order_status,
    poll_order_completion,
    apply_pil_postprocess,
    download_order_images,
)


class AutoenhanceJob(BaseExecutor):
    """Executor chuyên trách xử lý job cho engine Autoenhance."""

    max_upload_files: int = MAX_UPLOAD_FILES

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        max_upload_workers: int = MAX_API_WORKERS,
        max_download_workers: int = MAX_DOWNLOAD_WORKERS,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        max_poll_seconds: int = 1800,
    ):
        self.session = session or requests.Session()
        self.max_upload_workers = max_upload_workers
        self.max_download_workers = max_download_workers
        self.poll_interval = poll_interval
        self.max_poll_seconds = max_poll_seconds

    def _get_api_key(self, context: ExecutionContext) -> str:
        """Lấy API Key từ context hoặc session active."""
        key = context.credentials.get("api_key", "").strip()
        if not key:
            active = get_active_session("autoenhance")
            if active and hasattr(active, "api_key"):
                key = getattr(active, "api_key", "").strip()
        return key

    def _get_headers(self, api_key: str) -> Dict[str, str]:
        return {
            "x-api-key": api_key,
            # "x-dev-mode": "true",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def pre_transform(self, context: ExecutionContext) -> bool:
        """
        Bước chuẩn bị riêng của Autoenhance:
        1. Gọi logic của cha để kiểm tra file tồn tại & tạo thư mục output.
        2. Đo kích thước ảnh gốc (dimensions) lưu vào context.meta_dims phục vụ hậu kỳ.
        """
        if not super().pre_transform(context):
            return False

        context.set_step("prepare", "Autoenhance: Đang ghi nhận thông số kích thước ảnh...", progress_pct=8)
        for f in context.ready_files:
            dims = self._read_image_dimensions(f)
            if dims != (0, 0):
                context.meta_dims[f.name] = dims

        context.log("INFO", f"Đã ghi nhận metadata kích thước cho {len(context.meta_dims)} ảnh.")
        return True

    def _read_image_dimensions(self, file_path: Path) -> Tuple[int, int]:
        """Đọc kích thước (width, height) an toàn bằng PIL nếu có."""
        try:
            from PIL import Image
            with Image.open(file_path) as img:
                return img.size
        except Exception:
            return (0, 0)

    def transform(self, context: ExecutionContext) -> bool:
        """
        Hiện thực phương thức abstract transform() của BaseExecutor:
        Thực thi quy trình độc lập của Autoenhance có hỗ trợ Smart Resume.
        """
        api_key = self._get_api_key(context)
        if not api_key:
            context.error_message = "Chưa có API key Autoenhance. Vui lòng đăng nhập trước."
            return False

        headers = self._get_headers(api_key)

        # Smart Resume: Nếu đã có order_id từ lần chạy trước
        existing_order_id = context.remote_job_id or context.extra_data.get("order_id")
        if existing_order_id:
            context.log("INFO", f"[RESUME] Kiểm tra tiến độ đơn hàng cũ: {existing_order_id}...")
            status, completed, total_imgs, raw_data = fetch_order_status(self.session, headers, existing_order_id)
            if status not in ("not_found", "error") and total_imgs > 0:
                failed = [
                    img for img in raw_data.get("images", [])
                    if str(img.get("status", "")).lower() in ("failed", "error")
                    or img.get("error") is True
                ]
                if len(completed) + len(failed) == total_imgs and len(completed) > 0:
                    context.log(
                        "INFO",
                        f"[RESUME] Order {existing_order_id} đã hoàn tất AI trên server ({len(completed)}/{total_imgs} ảnh thành công)."
                    )
                    context.log("INFO", "[RESUME] Bỏ qua tạo order, upload S3 và trigger AI -> Nhảy thẳng đến bước tải ảnh thành phẩm.")
                    return self._step_download_and_process(context, headers, completed)
                else:
                    context.log(
                        "INFO",
                        f"[RESUME] Order {existing_order_id} đang xử lý dở dang ({len(completed)}/{total_imgs} hoàn tất). Chuyển sang theo dõi tiến trình..."
                    )
                    completed_images = self._step_poll_completion(context, headers, existing_order_id)
                    if not completed_images:
                        return False
                    return self._step_download_and_process(context, headers, completed_images)
            else:
                context.log("WARN", f"[RESUME] Không thể kiểm tra order cũ {existing_order_id}. Sẽ tạo đơn hàng mới.")

        # Bước 1: Tạo Order
        order_id = self._step_create_order(context, headers)
        if not order_id:
            return False
        context.remote_job_id = order_id
        context.extra_data["order_id"] = order_id

        # Bước 2: Upload ảnh lên S3 (run_parallel)
        if not self._step_upload_files(context, headers, order_id):
            return False

        # Bước 3: Kích hoạt xử lý AI
        if not self._step_trigger_process(context, headers, order_id):
            return False

        # Bước 4: Polling chờ server xử lý hoàn tất (poll_until)
        completed_images = self._step_poll_completion(context, headers, order_id)
        if not completed_images:
            return False

        # Bước 5: Download và xử lý hậu kỳ Local PIL (run_parallel)
        return self._step_download_and_process(context, headers, completed_images)

    # --- Chi tiết các bước nghiệp vụ của Autoenhance ---

    def _step_create_order(self, context: ExecutionContext, headers: Dict[str, str]) -> Optional[str]:
        """Bước 1: Tạo Order mới trên Autoenhance v3 (POST /orders/)."""
        if context.is_cancelled():
            return None

        context.set_step("create_order", "Đang tạo đơn hàng mới trên Autoenhance...", progress_pct=15)
        client_order_id = str(uuid.uuid4())
        order_name = context.preferences.get("order_name") or f"Job_{context.job_id}_{int(time.time())}"
        payload = {"order_id": client_order_id, "name": order_name}

        try:
            url = f"{API_BASE}{ENDPOINT_ORDERS}"
            # PLUS
            time.sleep(2)  # Nghỉ 2 - 3s chờ S3 ổn định hoàn tất 100%
            res = self.session.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
            if res.status_code not in (200, 201):
                context.error_message = f"Tạo Order thất bại (HTTP {res.status_code}): {res.text}"
                return None

            data = res.json()
            order_id = str(data.get("order_id") or data.get("id") or client_order_id)
            if not order_id:
                context.error_message = "Không nhận được order_id từ API Autoenhance."
                return None

            context.log("INFO", f"Tạo Order thành công: {order_id}")
            return order_id
        except Exception as exc:
            context.error_message = f"Ngoại lệ khi tạo order: {exc}"
            return None

    def _step_upload_files(
        self, context: ExecutionContext, headers: Dict[str, str], order_id: str
    ) -> bool:
        """Bước 2: Xin link S3 (POST /brackets/ hoặc POST /images/) đa luồng và tải ảnh lên AWS S3 đa luồng."""
        if context.is_cancelled():
            return False

        # Luôn sắp xếp file theo tên (DSC001, DSC002...) để đảm bảo các frame HDR cùng một cảnh chụp liền kề nhau
        sorted_files = sorted(context.ready_files, key=lambda f: f.name)
        total_files = len(sorted_files)
        context.set_step("upload_prepare", f"Đang chuẩn bị link upload cho {total_files} ảnh...", progress_pct=22)

        # Kiểm tra chế độ Bracket: Nếu > 1 thì dùng Endpoint /brackets/ chuyên dụng cho HDR
        brackets_val = context.preferences.get("number_of_brackets_per_image")
        bracket_int = 0
        if brackets_val is not None:
            try:
                bracket_int = int(brackets_val)
            except (ValueError, TypeError):
                bracket_int = 0
        is_bracket_mode = bracket_int > 1

        # 1. Xin link presigned S3 song song đa luồng qua run_parallel (có Smart Retry & Backoff)
        def _fetch_presigned_one(file_path: Path) -> Dict[str, Any]:
            if is_bracket_mode:
                img_url = f"{API_BASE}{ENDPOINT_BRACKETS}"
                payload = {"order_id": order_id, "name": file_path.name}
            else:
                img_url = f"{API_BASE}{ENDPOINT_IMAGES}"
                payload = {"order_id": order_id, "image_name": file_path.name}
            last_err = ""

            for attempt in range(3):
                if context.is_cancelled():
                    raise RuntimeError("Tác vụ bị dừng bởi người dùng.")
                try:
                    # PLUS
                    time.sleep(1)  # Nghỉ 0.5s giữa các lần xin link
                    res = self.session.post(img_url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
                    if res.status_code in (200, 201):
                        return res.json()

                    # Xử lý Rate Limit (429) hoặc Lỗi Server tạm thời (5xx)
                    if res.status_code == 429:
                        wait_sec = int(res.headers.get("Retry-After", attempt + 2))
                        last_err = f"HTTP 429 Rate limit, chờ {wait_sec}s"
                        time.sleep(wait_sec)
                        continue
                    elif res.status_code >= 500:
                        last_err = f"HTTP {res.status_code}: {res.text[:150]}"
                    else:
                        # Lỗi client (400, 401, 403, 404): Dừng ngay lập tức, không retry
                        raise ValueError(f"HTTP {res.status_code}: {res.text[:150]}")

                except (requests.RequestException, IOError) as exc:
                    last_err = str(exc)

                if attempt < 2:
                    time.sleep(attempt + 1)

            raise IOError(f"Lỗi xin link upload cho '{file_path.name}': {last_err}")

        batch_presigned = run_parallel(
            items=sorted_files,
            worker_fn=_fetch_presigned_one,
            max_workers=self.max_upload_workers,
            stop_event=context.stop_event,
            on_progress=make_progress_reporter(
                context, "upload_prepare", "lấy link", 22, 28, lambda f: f.name
            ),
        )

        if not batch_presigned.all_succeeded:
            if batch_presigned.is_cancelled or context.is_cancelled():
                context.error_message = "Tác vụ bị dừng bởi người dùng."
            elif batch_presigned.failed:
                context.error_message = f"Không lấy được link upload: {batch_presigned.failed[0][1]}"
            else:
                context.error_message = "Không lấy đủ link upload từ Autoenhance."
            return False

        # Khôi phục thứ tự chuẩn ban đầu từ sorted_files (tránh xáo trộn do đa luồng as_completed)
        res_by_path = {f_path: res for f_path, res in batch_presigned.succeeded}
        upload_tasks: List[Tuple[Path, Dict[str, Any]]] = [
            (f, res_by_path[f]) for f in sorted_files if f in res_by_path
        ]

        # Ở chế độ bracket, lưu danh sách packet (mỗi packet đúng bracket_int ảnh) vào context.extra_data
        if is_bracket_mode:
            ordered_bids = [
                res_by_path[f].get("bracket_id")
                for f in sorted_files
                if f in res_by_path and res_by_path[f].get("bracket_id")
            ]
            packets = [
                ordered_bids[i:i + bracket_int]
                for i in range(0, len(ordered_bids), bracket_int)
            ]
            context.extra_data["bracket_packets"] = packets
            context.log("INFO", f"Đã chia {len(ordered_bids)} ảnh bracket thành {len(packets)} packet (kích thước {bracket_int}).")

        # 2. Upload song song đa luồng lên AWS S3 qua run_parallel (có Retry & Delay)
        context.set_step("upload", f"Bắt đầu tải {total_files} ảnh lên S3...", progress_pct=28)

        def _upload_one(task: Tuple[Path, Dict[str, Any]]) -> bool:
            f_path, s3_info = task
            url = s3_info.get("upload_url") or s3_info.get("s3PutObjectUrl") or s3_info.get("url")
            if not url:
                raise ValueError(f"Thiếu S3 URL cho file {f_path.name}")

            raw_headers = s3_info.get("headers", {})
            req_headers = {str(k): str(v) for k, v in raw_headers.items() if str(k).lower() != "host"}
            if "content-type" not in [k.lower() for k in req_headers]:
                if is_bracket_mode:
                    req_headers["Content-Type"] = "application/octet-stream"
                else:
                    mime = s3_info.get("metadata", {}).get("MIMEType")
                    if not mime:
                        mime = "image/png" if f_path.suffix.lower() == ".png" else "image/jpeg"
                    req_headers["Content-Type"] = mime

            last_err = ""
            for attempt in range(3):
                if context.is_cancelled():
                    return False
                try:
                    with open(f_path, "rb") as fp:
                        r = requests.put(url, data=fp, headers=req_headers, timeout=60)
                        if r.status_code in (200, 204):
                            return True
                        last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                except Exception as exc:
                    last_err = str(exc)

                if attempt < 2:
                    time.sleep(attempt + 1)

            raise IOError(f"Upload S3 thất bại cho file {f_path.name}: {last_err}")

        batch_res = run_parallel(
            items=upload_tasks,
            worker_fn=_upload_one,
            max_workers=self.max_upload_workers,
            stop_event=context.stop_event,
            on_progress=make_progress_reporter(
                context, "upload", "upload", 28, 45, lambda task: task[0].name
            ),
        )

        if not batch_res.all_succeeded:
            if batch_res.is_cancelled or context.is_cancelled():
                context.error_message = "Tác vụ upload bị dừng bởi người dùng."
            elif batch_res.failed:
                context.error_message = f"Lỗi upload S3: {batch_res.failed[0][1]}"
            else:
                context.error_message = "Không upload được đầy đủ ảnh lên S3."
            return False

        context.log("INFO", f"Upload hoàn tất {len(batch_res.succeeded)} ảnh lên Autoenhance.")
        return True

    def _step_trigger_process(
        self, context: ExecutionContext, headers: Dict[str, str], order_id: str
    ) -> bool:
        """Bước 3: Gửi yêu cầu kích hoạt xử lý AI trên Autoenhance theo OpenAPI Spec."""
        if context.is_cancelled():
            return False

        context.set_step("trigger_process", "Đang gửi tùy chọn xử lý AI...", progress_pct=48)
        prefs = context.preferences

        # 1. Cloud & Sky Replacement
        cloud_type = prefs.get("cloud_type")
        sky_replacement = (cloud_type is not None)

        # 2. Perspective Correction
        persp_mode = prefs.get("perspective_correction_mode")
        persp_correction = (persp_mode is not None)

        # 3. Window Pull
        win_type = prefs.get("window_pull_type")

        # 4. Preset & Preset Version
        preset_id = prefs.get("preset_id")
        preset_version = 1 if preset_id is not None else None

        # 5. Brackets
        brackets_val = prefs.get("number_of_brackets_per_image")
        bracket_int: Optional[int] = None
        if brackets_val is not None:
            try:
                bracket_int = int(brackets_val)
            except (ValueError, TypeError):
                bracket_int = 0

        payload: Dict[str, Any] = {
            "ai_version": prefs.get("ai_version", "latest"),
            "enhancement_style": prefs.get("enhancement_style"),
            "sky_replacement": sky_replacement,
            "cloud_type": cloud_type,
            "perspective_correction": persp_correction,
            "perspective_correction_mode": persp_mode,
            "privacy": bool(prefs.get("privacy", False)),
            "lens_correction": bool(prefs.get("lens_correction", False)),
            "window_pull": bool(prefs.get("window_pull", False)),
            "window_pull_type": win_type or "ONLY_WINDOWS",
            "contrast_boost": prefs.get("contrast_boost", None),
            "saturation_level": prefs.get("saturation_level", None),
            "upscale": bool(prefs.get("upscale", False)),
            "restage": {
                "tvs": prefs.get("restage.tvs", "AS_SHOT"),
                "fire_in_fireplaces": prefs.get("restage.fire_in_fireplaces", "AS_SHOT"),
                "photographer": prefs.get("restage.photographer", "AS_SHOT"),
                "environment": "AS_SHOT",
                "grass": prefs.get("restage.grass", "AS_SHOT"),
            },
        }
        if preset_id is not None:
            payload["preset_id"] = preset_id
            payload["preset_version"] = preset_version

        # 5. Brackets: Nếu có bracket_packets thì push theo packet (Explicit Grouping)
        packets = context.extra_data.get("bracket_packets", [])
        if packets:
            payload["images"] = [{"bracket_ids": pkt} for pkt in packets]
        elif bracket_int is not None and bracket_int > 0:
            payload["number_of_brackets_per_image"] = bracket_int

        try:
            url = f"{API_BASE}{ENDPOINT_PROCESS.format(order_id=order_id)}"
            res = self.session.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
            if res.status_code not in (200, 201, 202):
                context.error_message = f"Không thể kích hoạt xử lý Autoenhance (HTTP {res.status_code}): {res.text}"
                return False

            context.log("INFO", "Đã kích hoạt xử lý AI trên Autoenhance thành công.")
            return True
        except Exception as exc:
            context.error_message = f"Ngoại lệ khi kích hoạt AI: {exc}"
            return False

    def _step_poll_completion(
        self, context: ExecutionContext, headers: Dict[str, str], order_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Bước 4: Thăm dò trạng thái qua poll_order_completion."""
        return poll_order_completion(
            session=self.session,
            headers=headers,
            order_id=order_id,
            context=context,
            poll_interval=self.poll_interval,
            max_seconds=self.max_poll_seconds,
        )

    def _step_download_and_process(
        self, context: ExecutionContext, headers: Dict[str, str], completed_images: List[Dict[str, Any]]
    ) -> bool:
        """Bước 5: Tải ảnh PNG thành phẩm và hậu kỳ (PIL) đa luồng qua run_parallel."""
        return download_order_images(
            context=context,
            session=None,
            headers=headers,
            completed_images=completed_images,
            max_workers=self.max_download_workers,
        )

    def _apply_pil_postprocess(
        self, src_path: Path, dest_path: Path, target_dims: Optional[Tuple[int, int]]
    ) -> None:
        """Xử lý hậu kỳ ảnh bằng PIL: kênh Alpha -> nền trắng, resize Lanczos, DPI 300."""
        apply_pil_postprocess(src_path, dest_path, target_dims)
