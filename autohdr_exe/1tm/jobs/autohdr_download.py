"""
Job Executor chuyên trách tải ảnh từ AutoHDR Shared URL / Photoshoot UUID.
Kế thừa từ AutoHDRJob, tích hợp thống nhất vào hệ sinh thái JobRunner.
Không có fallback, độc lập 100%, bảo toàn toàn bộ nghiệp vụ từ core/pipeline.py.
"""
from __future__ import annotations

import gc
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from core.stats_client import stats_client
from libs.constants.autohdr import (
    API_BASE,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    ENDPOINT_FINALIZE_ADJUSTMENT,
    ENDPOINT_GENERATE_ADJUSTMENT_PRESIGNED,
    ENDPOINT_PHOTO_ADJUSTMENTS,
    ENDPOINT_PHOTOSHOOT_PROCESSED,
    ENDPOINT_PHOTOSHOOT_UUID_PROCESSED,
)
from libs.executor.base import ExecutionContext
from .autohdr import AutoHDRJob

logger = logging.getLogger(__name__)


class AutoHDRDownloadJob(AutoHDRJob):
    """
    Executor chuyên trách tải ảnh từ Photoshoot URL / UUID của AutoHDR.
    Không yêu cầu file nguồn trên máy tính, thực thi toàn bộ quy trình:
    1. Kiểm tra xác thực (Cookie) & cấu hình Proxy nếu có.
    2. Tra cứu Photoshoot ID từ UUID đã chia sẻ.
    3. Lấy danh sách ảnh đã xử lý, xin presigned URL để lấy s3_key và gỡ watermark (Step 6 đầy đủ).
    4. Tải toàn bộ ảnh thành phẩm về output_dir với đa luồng, smart resume (Step 7 đầy đủ).
    5. Ghi nhận thời gian hoàn tất qua stats_client telemetry.
    """

    def pre_transform(self, context: ExecutionContext) -> bool:
        """Khởi tạo và xác nhận thư mục lưu trữ đầu ra."""
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
        """Hậu kiểm riêng cho DownloadJob: đảm bảo thư mục đầu ra tồn tại trên ổ cứng."""
        out_p = Path(context.output_dir).resolve()
        if not out_p.is_dir():
            context.error_message = f"Lỗi hậu kiểm: Thư mục đầu ra không tồn tại: {context.output_dir}"
            context.log("ERROR", context.error_message)
            return False
        return True

    def _apply_proxy(self, context: ExecutionContext) -> None:
        """Cấu hình proxy cho session nếu được cung cấp qua context."""
        proxy_config = (
            context.preferences.get("proxy_config")
            or context.extra_data.get("proxy_config")
        )
        if not proxy_config or not proxy_config.get("ip"):
            return

        ip = proxy_config["ip"]
        port = proxy_config.get("port", "")
        user = proxy_config.get("user", "")
        password = proxy_config.get("password", "")

        if user and password:
            proxy_url = f"http://{user}:{password}@{ip}:{port}"
        else:
            proxy_url = f"http://{ip}:{port}"

        self.session.proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }
        context.log("INFO", f"Đã cấu hình proxy: {ip}:{port}")

    def _extract_photoshoot_uuid(self, context: ExecutionContext) -> str:
        """Trích xuất và chuẩn hóa photoshoot UUID/ID từ preferences hoặc URL."""
        raw = (
            context.preferences.get("photoshoot_uuid")
            or context.extra_data.get("photoshoot_uuid")
            or context.remote_job_id
            or ""
        ).strip()

        # Nếu người dùng dán URL thay vì UUID thuần túy, bóc tách lấy phần ID cuối
        if raw.startswith("http://") or raw.startswith("https://"):
            parts = [p for p in urlparse(raw).path.strip("/").split("/") if p]
            if len(parts) >= 2 and parts[0].lower() in ("photoshoots", "share"):
                return parts[1]
            if parts:
                return parts[-1]

        return raw

    def _step_get_processed_urls(
        self, context: ExecutionContext, headers: Dict[str, str], photoshoot_id: int
    ) -> List[Dict[str, str]]:
        """
        Hiện thực đầy đủ Bước 6:
        1. Lấy danh sách ảnh đã xử lý từ /processed_photos.
        2. Với từng ảnh: gọi /adjustments để lấy base_url.
        3. Nếu ảnh dính watermark: xin presigned URL để lấy s3_key, sau đó finalize unwatermark.
        4. Hỗ trợ retry 5 lần/ảnh kèm backoff.
        """
        if context.is_cancelled():
            return []

        context.set_step("unwatermark", "Đang lấy danh sách ảnh và gỡ watermark thành phẩm...", progress_pct=40)

        # Lấy tối đa 100 ảnh để tránh bị cắt bớt danh sách
        endpoint = ENDPOINT_PHOTOSHOOT_PROCESSED.format(photoshoot_id=photoshoot_id)
        url = f"{API_BASE}{endpoint}?page=1&page_size=100"

        try:
            res = self.session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
            if res.status_code != 200:
                context.error_message = f"Không lấy được danh sách ảnh thành phẩm (HTTP {res.status_code})"
                return []
            data = res.json()
        except Exception as exc:
            context.error_message = f"Lỗi kết nối khi lấy danh sách ảnh đã xử lý: {exc}"
            return []

        if not isinstance(data, list):
            context.error_message = f"Dữ liệu danh sách ảnh không đúng định dạng (kiểu: {type(data).__name__})"
            return []

        if len(data) == 0:
            context.error_message = "Không có ảnh đã xử lý nào trong Photoshoot này."
            return []

        results: List[Dict[str, str]] = []

        for item in data:
            if context.is_cancelled():
                return []

            photo_id = item.get("id")
            if not photo_id:
                continue

            filename = item.get("filename") or f"{photo_id}.jpg"
            success_url = None

            for attempt in range(5):
                if context.is_cancelled():
                    return []

                try:
                    # 1. Lấy base_url từ /adjustments
                    adj_endpoint = ENDPOINT_PHOTO_ADJUSTMENTS.format(photo_id=photo_id)
                    adj_url = f"{API_BASE}{adj_endpoint}"
                    adj_res = self.session.get(adj_url, headers=headers, timeout=DEFAULT_TIMEOUT)
                    adj_res.raise_for_status()
                    adjustments = adj_res.json()

                    base_url = adjustments.get("base_url") if isinstance(adjustments, dict) else None
                    if not base_url:
                        raise ValueError(f"Không tìm thấy base_url cho ảnh {photo_id}")

                    parsed_path = urlparse(base_url).path

                    # 2. Nếu có watermark -> xin s3_key, tải ảnh watermark, upload S3 presigned URL, và finalize
                    if "/watermarked/" in parsed_path:
                        # a. Tải dữ liệu ảnh watermark hiện tại
                        wm_res = requests.get(
                            base_url,
                            headers={"User-Agent": headers.get("User-Agent", DEFAULT_USER_AGENT)},
                            proxies=self.session.proxies,
                            timeout=DEFAULT_TIMEOUT,
                        )
                        wm_res.raise_for_status()
                        wm_bytes = wm_res.content

                        # b. Xin presigned upload URL và s3_key qua generate-adjustment-presigned-url
                        presign_url = f"{API_BASE}{ENDPOINT_GENERATE_ADJUSTMENT_PRESIGNED}"
                        presign_payload = {
                            "photo_id": str(photo_id),
                            "add_clouds": False,
                        }
                        p_res = self.session.post(presign_url, json=presign_payload, headers=headers, timeout=DEFAULT_TIMEOUT)
                        p_res.raise_for_status()
                        p_data = p_res.json()

                        if not isinstance(p_data, dict) or not p_data.get("success") or not p_data.get("s3_key"):
                            msg = p_data.get("message") if isinstance(p_data, dict) else None
                            raise ValueError(msg or f"Không lấy được s3_key cho ảnh {photo_id}")

                        s3_key = p_data["s3_key"]
                        # presigned_url = p_data.get("presigned_url")

                        # c. Upload (PUT) binary ảnh lên S3 presigned URL
                        # if presigned_url:
                        #     put_res = requests.put(
                        #         presigned_url,
                        #         data=wm_bytes,
                        #         headers={"Content-Type": "image/jpeg"},
                        #         proxies=self.session.proxies,
                        #         timeout=DEFAULT_TIMEOUT,
                        #     )
                        #     put_res.raise_for_status()

                        # d. Finalize unwatermark
                        time.sleep(3)
                        fin_url = f"{API_BASE}{ENDPOINT_FINALIZE_ADJUSTMENT}"
                        fin_payload = {
                            "photo_id": str(photo_id),
                            "add_clouds": False,
                            "s3_key": s3_key,
                            "preserve_photo": True,
                        }
                        f_res = self.session.post(fin_url, json=fin_payload, headers=headers, timeout=DEFAULT_TIMEOUT)
                        f_res.raise_for_status()
                        f_data = f_res.json()

                        if not isinstance(f_data, dict) or not f_data.get("success") or not f_data.get("url"):
                            raise ValueError(f"Không lấy được URL unwatermarked cho ảnh {photo_id}")

                        success_url = f_data["url"]
                    else:
                        success_url = base_url

                    break  # Thành công -> thoát retry loop

                except Exception as exc:
                    if attempt == 4:
                        context.log("ERROR", f"Lỗi lấy URL ảnh {photo_id} sau 5 lần thử: {exc}")
                        context.error_message = f"Thất bại khi lấy link ảnh {photo_id}: {exc}"
                        return []
                    context.log("WARN", f"Thử lại lấy link ảnh {photo_id} ({attempt + 1}/5)...")
                    time.sleep(1.0 * (attempt + 1))

            if success_url:
                results.append({"filename": filename, "url": success_url, "photo_id": str(photo_id)})

        context.log("INFO", f"Đã chuẩn bị xong {len(results)} link ảnh thành phẩm sạch watermark.")
        return results

    def transform(self, context: ExecutionContext) -> bool:
        """Thực thi toàn bộ luồng tải ảnh từ shared UUID."""
        # 1. Xác thực
        headers = self._get_auth_headers(context)
        if not headers.get("Cookie"):
            context.error_message = "Chưa có Cookie xác thực AutoHDR. Vui lòng đăng nhập trước."
            return False

        # 2. Cấu hình Proxy (nếu có)
        self._apply_proxy(context)

        # 3. Lấy UUID
        photoshoot_uuid = self._extract_photoshoot_uuid(context)
        if not photoshoot_uuid:
            context.error_message = "Thiếu Photoshoot UUID để tải ảnh từ liên kết chia sẻ."
            return False

        # 4. Tra cứu thông tin Photoshoot từ UUID
        context.set_step("fetch_share", "Đang kiểm tra link chia sẻ Photoshoot...", progress_pct=15)
        endpoint = ENDPOINT_PHOTOSHOOT_UUID_PROCESSED.format(uuid=photoshoot_uuid)
        url = f"{API_BASE}{endpoint}?page=1&page_size=10"

        try:
            res = self.session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
            if res.status_code != 200:
                context.error_message = f"Không lấy được thông tin Photoshoot từ UUID (HTTP {res.status_code})"
                return False
            data = res.json()
        except Exception as exc:
            context.error_message = f"Lỗi kết nối tới liên kết chia sẻ Photoshoot: {exc}"
            return False

        if not data or (isinstance(data, list) and len(data) == 0):
            context.error_message = "Photoshoot này không có ảnh nào."
            return False

        photoshoot_id = data.get("id") if isinstance(data, dict) else None
        if not photoshoot_id and isinstance(data, list) and len(data) > 0:
            photoshoot_id = data[0].get("photoshoot_id") or data[0].get("id")

        if not photoshoot_id:
            context.error_message = "Không tìm thấy Photoshoot ID từ liên kết đã chia sẻ."
            return False

        context.remote_job_id = str(photoshoot_id)
        context.extra_data["photoshoot_id"] = photoshoot_id
        context.log("INFO", f"Tìm thấy Photoshoot ID: {photoshoot_id}. Bắt đầu gỡ watermark và chuẩn bị link tải...")

        # 5. Lấy danh sách ảnh thành phẩm & gỡ watermark (Step 6)
        photo_urls = self._step_get_processed_urls(context, headers, int(photoshoot_id))
        if not photo_urls:
            if not context.error_message:
                context.error_message = "Không tìm thấy ảnh thành phẩm hợp lệ trong dự án này."
            return False

        # 6. Tải ảnh thành phẩm (Step 7)
        context.log("INFO", f"Bắt đầu tải {len(photo_urls)} ảnh thành phẩm...")
        download_ok = self._step_download(context, photo_urls)

        # 7. Telemetry & Dọn dẹp
        if download_ok:
            try:
                stats_client.append_runtime(photoshoot_uuid)
            except Exception:
                pass
            context.log("INFO", f"Đã tải {len(context.downloaded_files)} ảnh từ link chia sẻ thành công.")

        gc.collect()
        return download_ok
