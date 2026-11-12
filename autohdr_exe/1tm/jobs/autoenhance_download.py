"""
Job Executor chuyên trách tải ảnh từ Autoenhance Cloud qua Order ID / URL.
Độc lập, minh bạch và kế thừa từ BaseExecutor.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import requests

from libs.constants.autoenhance import (
    DEFAULT_POLL_INTERVAL,
    MAX_DOWNLOAD_WORKERS,
    USER_AGENT,
)
from libs.executor.base import BaseExecutor, ExecutionContext
from libs.auth import get_active_session
from libs.utils.autoenhance import (
    fetch_order_status,
    poll_order_completion,
    download_order_images,
)


class AutoenhanceDownloadJob(BaseExecutor):
    """
    Executor chuyên trách tải ảnh từ Order ID hoặc URL trên Cloud cho Autoenhance.
    Không yêu cầu file nguồn trên máy tính, chỉ cần Order ID hợp lệ.
    """

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        max_download_workers: int = MAX_DOWNLOAD_WORKERS,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        max_poll_seconds: int = 1800,
    ):
        self.session = session or requests.Session()
        self.max_download_workers = max_download_workers
        self.poll_interval = poll_interval
        self.max_poll_seconds = max_poll_seconds

    def _get_api_key(self, context: ExecutionContext) -> str:
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
        """Chuẩn bị thư mục đầu ra mà không yêu cầu file nguồn trên máy tính."""
        context.set_step("prepare", "Đang khởi tạo thư mục lưu trữ...", progress_pct=5)
        out_path = Path(context.output_dir)
        try:
            out_path.mkdir(parents=True, exist_ok=True)
            context.log("INFO", f"Thư mục lưu trữ: {out_path}")
            return True
        except Exception as exc:
            context.error_message = f"Không thể tạo thư mục đầu ra '{context.output_dir}': {exc}"
            context.log("ERROR", context.error_message)
            return False

    def _validate_pre_transform(self, context: ExecutionContext) -> bool:
        """Hậu kiểm riêng cho DownloadJob: chỉ cần thư mục đầu ra tồn tại trên ổ cứng."""
        out_p = Path(context.output_dir).resolve()
        if not out_p.is_dir():
            context.error_message = f"Lỗi hậu kiểm: Thư mục đầu ra không tồn tại: {context.output_dir}"
            context.log("ERROR", context.error_message)
            return False
        return True

    def transform(self, context: ExecutionContext) -> bool:
        """Thực thi kiểm tra đơn hàng và tải toàn bộ ảnh."""
        api_key = self._get_api_key(context)
        if not api_key:
            context.error_message = "Chưa có API key Autoenhance. Vui lòng đăng nhập trước."
            return False

        order_id = (
            context.remote_job_id
            or context.preferences.get("order_id")
            or context.extra_data.get("order_id")
        )
        if not order_id:
            context.error_message = "Không tìm thấy Order ID để tải ảnh từ Autoenhance Cloud."
            return False

        headers = self._get_headers(api_key)
        context.remote_job_id = order_id
        context.extra_data["order_id"] = order_id

        context.log("INFO", f"Đang tra cứu thông tin đơn hàng {order_id} trên Autoenhance Cloud...")
        status, completed, total_imgs, raw_data = fetch_order_status(self.session, headers, order_id)

        if status == "not_found":
            context.error_message = f"Không tìm thấy đơn hàng {order_id} trên Autoenhance (HTTP 404)."
            return False
        if status == "error":
            context.error_message = f"Lỗi kết nối khi tra cứu đơn hàng {order_id}."
            return False

        order_name = raw_data.get("name", "")
        if order_name:
            context.log("INFO", f"Tên đơn hàng: '{order_name}' | Tổng số ảnh: {total_imgs}")

        failed = [
            img for img in raw_data.get("images", [])
            if str(img.get("status", "")).lower() in ("failed", "error")
            or img.get("error") is True
        ]

        if len(completed) + len(failed) == total_imgs and total_imgs > 0:
            if not completed:
                context.error_message = f"Đơn hàng {order_id} xử lý thất bại toàn bộ ({len(failed)} ảnh lỗi)."
                return False
            context.log("INFO", f"Đơn hàng đã hoàn tất AI ({len(completed)}/{total_imgs} ảnh). Bắt đầu tải...")
            return download_order_images(
                context, None, headers, completed, self.max_download_workers
            )

        # Nếu order đang xử lý dở trên cloud -> polling
        context.log("INFO", f"Đơn hàng {order_id} đang được xử lý trên Cloud ({len(completed)}/{total_imgs} hoàn tất). Chờ AI hoàn tất...")
        completed_images = poll_order_completion(
            self.session, headers, order_id, context, self.poll_interval, self.max_poll_seconds
        )
        if not completed_images:
            return False

        return download_order_images(
            context, None, headers, completed_images, self.max_download_workers
        )

