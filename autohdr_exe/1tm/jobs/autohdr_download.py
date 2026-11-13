"""
Job Executor chuyên trách tải ảnh từ AutoHDR Shared URL / Photoshoot UUID.
Kế thừa từ AutoHDRJob, tích hợp thống nhất vào hệ sinh thái JobRunner.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict, Any
import requests

from libs.constants.autohdr import (
    API_BASE,
    DEFAULT_TIMEOUT,
)
from libs.executor.base import ExecutionContext
from .autohdr import AutoHDRJob


class AutoHDRDownloadJob(AutoHDRJob):
    """
    Executor chuyên trách tải ảnh từ Photoshoot URL / UUID của AutoHDR.
    Không yêu cầu file nguồn trên máy tính.
    """

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
        """Lấy thông tin photoshoot từ UUID và tải ảnh thành phẩm."""
        headers = self._get_auth_headers(context)
        if not headers.get("Cookie"):
            context.error_message = "Chưa có Cookie xác thực AutoHDR. Vui lòng đăng nhập trước."
            return False

        photoshoot_uuid = (
            context.preferences.get("photoshoot_uuid")
            or context.extra_data.get("photoshoot_uuid")
            or context.remote_job_id
        )
        if not photoshoot_uuid:
            context.error_message = "Thiếu Photoshoot UUID để tải ảnh."
            return False

        context.set_step("fetch_info", "Đang kiểm tra link chia sẻ Photoshoot...", progress_pct=20)
        url = f"{API_BASE}/api/proxy/photoshoots/uuid/{photoshoot_uuid}/processed_photos?page=1&page_size=10"
        try:
            res = self.session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
            if res.status_code != 200:
                context.error_message = f"Không lấy được thông tin Photoshoot (HTTP {res.status_code})"
                return False
            data = res.json()
        except Exception as exc:
            context.error_message = f"Lỗi kết nối tới Photoshoot share: {exc}"
            return False

        photoshoot_id = data.get("id") if isinstance(data, dict) else None
        if not photoshoot_id and isinstance(data, list) and len(data) > 0:
            photoshoot_id = data[0].get("id")

        if not photoshoot_id:
            context.error_message = "Không tìm thấy Photoshoot ID từ liên kết đã chia sẻ."
            return False

        context.remote_job_id = str(photoshoot_id)
        context.extra_data["photoshoot_id"] = photoshoot_id
        context.log("INFO", f"Tìm thấy Photoshoot ID: {photoshoot_id}. Đang lấy link ảnh thành phẩm...")

        photo_urls = self._step_get_processed_urls(context, headers, int(photoshoot_id))
        if not photo_urls:
            if not context.error_message:
                context.error_message = "Không tìm thấy ảnh thành phẩm nào trong dự án này."
            return False

        context.log("INFO", f"Bắt đầu tải {len(photo_urls)} ảnh...")
        return self._step_download(context, photo_urls)

