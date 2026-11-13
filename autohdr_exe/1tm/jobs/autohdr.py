"""
Job Executor cho AutoHDR Engine.
Kế thừa BaseExecutor, triển khai toàn bộ quy trình xử lý của AutoHDR:
1. Xin Presigned URLs (S3)
2. Upload ảnh lên S3 (streaming, đa luồng qua run_parallel)
3. Finalize upload
4. Associate and run (kích hoạt AI process)
5. Poll photoshoot status (qua poll_until)
6. Get processed photo URLs (gỡ watermark)
7. Download ảnh thành phẩm về output_dir (qua run_parallel)
"""
from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

import requests

from libs.constants.autohdr import (
    API_BASE,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    ENDPOINT_APP_USER,
    ENDPOINT_ASSOCIATE_RUN,
    ENDPOINT_FINALIZE_ADJUSTMENT,
    ENDPOINT_FINALIZE_UPLOAD,
    ENDPOINT_PHOTOSHOOTS,
    ENDPOINT_PRESIGNED_URLS,
    MAX_UPLOAD_FILES,
)
from libs.executor.base import BaseExecutor, ExecutionContext
from libs.auth import get_active_session
from libs.utils import run_parallel, poll_until, PollStatus, make_progress_reporter


class AutoHDRJob(BaseExecutor):
    """Executor chuyên trách xử lý job cho engine AutoHDR."""

    max_upload_files: int = MAX_UPLOAD_FILES

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        max_upload_workers: int = 4,
        max_download_workers: int = 4,
        poll_interval: int = 5,
        max_poll_retries: int = 60,
    ):
        self.session = session or requests.Session()
        self.max_upload_workers = max_upload_workers
        self.max_download_workers = max_download_workers
        self.poll_interval = poll_interval
        self.max_poll_retries = max_poll_retries

    def _get_auth_headers(self, context: ExecutionContext) -> Dict[str, str]:
        """Lấy header và Cookie xác thực từ context hoặc session active."""
        cookie = context.credentials.get("cookie", "")
        if not cookie:
            active = get_active_session("autohdr")
            if active and hasattr(active, "cookie"):
                cookie = getattr(active, "cookie")

        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        }
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def transform(self, context: ExecutionContext) -> bool:
        """
        Hiện thực phương thức abstract transform() của BaseExecutor:
        Thực thi tuần tự các bước nghiệp vụ của AutoHDR có hỗ trợ Smart Resume.
        """
        headers = self._get_auth_headers(context)
        if not headers.get("Cookie"):
            context.error_message = "Chưa có Cookie xác thực AutoHDR. Vui lòng đăng nhập trước."
            return False

        # Smart Resume 1: Nếu đã có photoshoot_id từ lần chạy trước
        existing_ps_id = context.remote_job_id or context.extra_data.get("photoshoot_id")
        if existing_ps_id:
            context.log("INFO", f"[RESUME] Kiểm tra Photoshoot cũ trên server: {existing_ps_id}...")
            try:
                photo_urls = self._step_get_processed_urls(context, headers, int(existing_ps_id))
                if photo_urls:
                    context.log(
                        "INFO",
                        f"[RESUME] Lấy thành công link cho {len(photo_urls)} ảnh từ Photoshoot {existing_ps_id}."
                    )
                    context.log("INFO", "[RESUME] Bỏ qua S3 upload và AI process -> Nhảy thẳng đến bước tải ảnh thành phẩm.")
                    return self._step_download(context, photo_urls)
            except Exception as exc:
                context.log("WARN", f"[RESUME] Không khôi phục được Photoshoot cũ {existing_ps_id}: {exc}.")

        # Smart Resume 2: Nếu đã upload xong và có unique_str đang thăm dò dở dang
        unique_str = context.extra_data.get("unique_str")
        if not unique_str:
            unique_str = str(uuid.uuid4())
            context.extra_data["unique_str"] = unique_str
            context.log("INFO", f"Khởi tạo phiên xử lý AutoHDR (unique_str: {unique_str})")

            # Bước 1: Xin presigned URLs
            if not self._step_presigned_urls(context, headers, unique_str):
                return False

            # Bước 2: Upload files lên S3
            if not self._step_upload_files(context):
                return False

            # Bước 3: Finalize upload
            if not self._step_finalize_upload(context, headers, unique_str):
                return False

            # Bước 4: Associate and run
            if not self._step_associate_and_run(context, headers, unique_str):
                return False
        else:
            context.log("INFO", f"[RESUME] Tiếp tục theo dõi Photoshoot từ unique_str: {unique_str}")

        # Bước 5: Poll status
        photoshoot_id = self._step_poll_status(context, headers, unique_str)
        if not photoshoot_id:
            return False
        context.remote_job_id = str(photoshoot_id)
        context.extra_data["photoshoot_id"] = photoshoot_id

        # Bước 6: Lấy danh sách ảnh thành phẩm & gỡ watermark
        photo_urls = self._step_get_processed_urls(context, headers, photoshoot_id)
        if not photo_urls:
            return False

        # Bước 7: Tải ảnh về thư mục đầu ra
        return self._step_download(context, photo_urls)

    # --- Chi tiết các bước nghiệp vụ của AutoHDR ---

    def _step_presigned_urls(
        self, context: ExecutionContext, headers: Dict[str, str], unique_str: str
    ) -> bool:
        """Bước 1: Gửi danh sách tên file để lấy presigned URLs từ S3."""
        if context.is_cancelled():
            return False

        context.set_step("presigned_urls", "Đang xin presigned URLs từ AutoHDR...", progress_pct=10)
        filenames = [f.name for f in context.ready_files]
        payload = {
            "unique_str": unique_str,
            "files": [{"filename": fn, "md5": ""} for fn in filenames],
        }

        try:
            url = f"{API_BASE}{ENDPOINT_PRESIGNED_URLS}"
            res = self.session.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
            if res.status_code != 200:
                context.error_message = f"Lỗi lấy presigned URLs (HTTP {res.status_code}): {res.text}"
                return False

            data = res.json()
            urls_map: Dict[str, str] = {}
            for item in data.get("presignedUrls", []):
                urls_map[item["filename"]] = item["url"]

            if not urls_map:
                context.error_message = "Không nhận được presigned URLs nào từ máy chủ AutoHDR."
                return False

            context.extra_data["presigned_urls"] = urls_map
            context.log("INFO", f"Đã lấy thành công {len(urls_map)} presigned URLs.")
            return True

        except Exception as exc:
            context.error_message = f"Ngoại lệ khi lấy presigned URLs: {exc}"
            return False

    def _step_upload_files(self, context: ExecutionContext) -> bool:
        """Bước 2: Tải các file ảnh lên S3 đa luồng qua run_parallel."""
        if context.is_cancelled():
            return False

        urls_map: Dict[str, str] = context.extra_data.get("presigned_urls", {})
        total_files = len(context.ready_files)
        context.set_step("upload", f"Bắt đầu upload {total_files} ảnh lên S3...", progress_pct=20)

        def _upload_one(file_path: Path) -> bool:
            if context.is_cancelled():
                return False
            put_url = urls_map.get(file_path.name)
            if not put_url:
                raise ValueError(f"Không có URL upload cho file {file_path.name}")

            file_size = file_path.stat().st_size
            upload_headers = {
                "User-Agent": DEFAULT_USER_AGENT,
                "Content-Type": "application/octet-stream",
                "Content-Length": str(file_size),
                "x-amz-acl": "private",
                "Origin": API_BASE,
            }

            last_err = ""
            for _ in range(3):
                if context.is_cancelled():
                    return False
                try:
                    with open(file_path, "rb") as f:
                        resp = requests.put(put_url, data=f, headers=upload_headers, timeout=(15, 300))
                        if resp.status_code in (200, 204):
                            return True
                        last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                except Exception as exc:
                    last_err = str(exc)
                time.sleep(1)

            raise IOError(f"Upload S3 thất bại file {file_path.name}: {last_err}")

        batch_res = run_parallel(
            items=context.ready_files,
            worker_fn=_upload_one,
            max_workers=self.max_upload_workers,
            stop_event=context.stop_event,
            on_progress=make_progress_reporter(
                context, "upload", "upload", 20, 40, lambda fp: fp.name
            ),
        )

        if not batch_res.all_succeeded:
            if batch_res.is_cancelled:
                context.error_message = "Tác vụ upload bị dừng bởi người dùng."
            elif batch_res.failed:
                context.error_message = f"Upload thất bại: {batch_res.failed[0][1]}"
            else:
                context.error_message = "Upload S3 thất bại."
            return False

        context.log("INFO", f"Đã upload thành công toàn bộ {len(batch_res.succeeded)} ảnh.")
        return True

    def _step_finalize_upload(
        self, context: ExecutionContext, headers: Dict[str, str], unique_str: str
    ) -> bool:
        """Bước 3: Báo cho server AutoHDR biết upload đã hoàn tất."""
        if context.is_cancelled():
            return False

        context.set_step("finalize_upload", "Đang xác nhận hoàn tất upload với máy chủ...", progress_pct=42)
        try:
            url = f"{API_BASE}{ENDPOINT_FINALIZE_UPLOAD}"
            res = self.session.post(url, json={"unique_str": unique_str}, headers=headers, timeout=DEFAULT_TIMEOUT)
            if res.status_code != 200:
                context.error_message = f"Lỗi finalize upload (HTTP {res.status_code}): {res.text}"
                return False
            context.log("INFO", "Máy chủ đã xác nhận nhận đủ file upload.")
            return True
        except Exception as exc:
            context.error_message = f"Ngoại lệ khi finalize upload: {exc}"
            return False

    def _step_associate_and_run(
        self, context: ExecutionContext, headers: Dict[str, str], unique_str: str
    ) -> bool:
        """Bước 4: Gửi thông tin tùy chọn để kích hoạt xử lý AI."""
        if context.is_cancelled():
            return False

        context.set_step("associate_and_run", "Đang gửi yêu cầu kích hoạt xử lý AI...", progress_pct=45)
        prefs = context.preferences
        creds = context.credentials

        payload = {
            "unique_str": unique_str,
            "email": creds.get("email", ""),
            "firstname": creds.get("firstname", ""),
            "lastname": creds.get("lastname", ""),
            "address": prefs.get("address", unique_str),
            "files_count": len(context.ready_files),
            "indoor_model_id": prefs.get("indoor_model_id"),
            "outdoor_model_id": prefs.get("outdoor_model_id"),
            "grass_replacement": prefs.get("grass_replacement", False),
            "perspective_correction": prefs.get("perspective_correction", False),
            "special_attention": prefs.get("special_attention", False),
            "declutter": prefs.get("declutter", False),
        }

        try:
            url = f"{API_BASE}{ENDPOINT_ASSOCIATE_RUN}"
            res = self.session.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
            if res.status_code != 200:
                context.error_message = f"Kích hoạt xử lý thất bại (HTTP {res.status_code}): {res.text}"
                return False

            data = res.json()
            if not data.get("success", False):
                context.error_message = f"Máy chủ từ chối kích hoạt: {data}"
                return False

            context.log("INFO", "Đã kích hoạt xử lý AI trên AutoHDR thành công.")
            return True
        except Exception as exc:
            context.error_message = f"Ngoại lệ kích hoạt xử lý: {exc}"
            return False

    def _step_poll_status(
        self, context: ExecutionContext, headers: Dict[str, str], unique_str: str
    ) -> Optional[int]:
        """Bước 5: Thăm dò danh sách photoshoots qua tiện ích poll_until."""
        context.set_step("polling", "Đang theo dõi tiến trình xử lý trên máy chủ...", progress_pct=50)
        address = context.preferences.get("address", unique_str)

        def _check(attempt: int) -> Tuple[PollStatus, Any, str]:
            url = f"{API_BASE}{ENDPOINT_PHOTOSHOOTS}?limit=20&offset=0"
            res = self.session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
            if res.status_code == 200:
                data = res.json()
                photoshoots = data.get("photoshoots", [])
                match = next(
                    (ps for ps in photoshoots if ps.get("name") == unique_str or ps.get("address") == address),
                    None,
                )
                if match:
                    status = match.get("status", "").lower()
                    if status == "success":
                        return PollStatus.SUCCESS, match.get("id"), "Hoàn thành"
                    elif status == "ignore":
                        return PollStatus.FAILED, None, "Photoshoot bị máy chủ đánh dấu 'ignore'."
            return PollStatus.IN_PROGRESS, None, "Đang xử lý..."

        poll_res = poll_until(
            check_fn=_check,
            poll_interval=self.poll_interval,
            max_retries=self.max_poll_retries,
            stop_event=context.stop_event,
        )

        if not poll_res.is_success:
            context.error_message = poll_res.error_message or "Quá thời gian chờ xử lý (polling timeout)."
            return None

        context.log("INFO", f"Photoshoot đã hoàn thành! ID: {poll_res.data}")
        return poll_res.data

    def _step_get_processed_urls(
        self, context: ExecutionContext, headers: Dict[str, str], photoshoot_id: int
    ) -> List[Dict[str, str]]:
        """Bước 6: Lấy danh sách ảnh và gọi API gỡ watermark."""
        if context.is_cancelled():
            return []

        context.set_step("unwatermark", "Đang xử lý gỡ watermark thành phẩm...", progress_pct=75)
        try:
            url = f"{API_BASE}/api/photoshoots/{photoshoot_id}/photos"
            res = self.session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
            if res.status_code != 200:
                context.error_message = f"Không lấy được danh sách ảnh (HTTP {res.status_code})"
                return []

            photos = res.json().get("photos", [])
            results = []
            for p in photos:
                photo_id = p.get("id")
                fin_url = f"{API_BASE}{ENDPOINT_FINALIZE_ADJUSTMENT}"
                adj_res = self.session.post(
                    fin_url,
                    json={"photo_id": str(photo_id), "preserve_photo": True},
                    headers=headers,
                    timeout=DEFAULT_TIMEOUT,
                )
                if adj_res.status_code == 200:
                    data = adj_res.json()
                    dl_url = data.get("url") or p.get("url")
                    filename = p.get("filename") or f"{photo_id}.jpg"
                    results.append({"filename": filename, "url": dl_url})
                else:
                    results.append({"filename": p.get("filename", f"{photo_id}.jpg"), "url": p.get("url")})

            return results
        except Exception as exc:
            context.error_message = f"Ngoại lệ khi lấy URL ảnh: {exc}"
            return []

    def _step_download(
        self, context: ExecutionContext, photo_urls: List[Dict[str, str]]
    ) -> bool:
        """Bước 7: Tải ảnh thành phẩm về thư mục output_dir qua run_parallel."""
        if context.is_cancelled():
            return False

        out_dir = Path(context.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        total = len(photo_urls)
        context.set_step("download", f"Bắt đầu tải {total} ảnh về máy...", progress_pct=85)

        # Cơ chế chống trùng tên file / ghi đè (Anti-collision unique naming):
        download_map: Dict[str, str] = context.extra_data.setdefault("hdr_download_map", {})
        used_names: set[str] = set()
        download_tasks: List[Tuple[Dict[str, str], Path]] = []

        for item in photo_urls:
            raw_url = item.get("url", "")
            orig_name = item.get("filename") or "result.jpg"
            photo_key = raw_url or orig_name

            if photo_key in download_map:
                assigned_name = download_map[photo_key]
            else:
                stem = Path(orig_name).stem.strip() or "result"
                ext = Path(orig_name).suffix.lower() or ".jpg"
                candidate = f"{stem}{ext}"
                counter = 1
                while candidate in used_names or (
                    (out_dir / candidate).is_file() and candidate not in download_map.values()
                ):
                    candidate = f"{stem}_{counter}{ext}"
                    counter += 1
                assigned_name = candidate
                download_map[photo_key] = assigned_name

            used_names.add(assigned_name)
            download_tasks.append((item, out_dir / assigned_name))

        def _download_one(task: Tuple[Dict[str, str], Path]) -> str:
            item, dst = task
            filename = dst.name
            url = item.get("url", "")
            if not url:
                raise ValueError(f"Thiếu URL tải ảnh cho {filename}")

            # Smart Resume cho từng file: Bỏ qua nếu đã tải thành công trước đó
            if dst.is_file() and dst.stat().st_size > 0:
                context.log("INFO", f"[RESUME] Bỏ qua tải {filename} (đã tồn tại sẵn trên máy).")
                return str(dst)

            last_err = ""
            resp = None
            for attempt in range(3):
                if context.is_cancelled():
                    raise IOError("Tác vụ tải ảnh bị dừng bởi người dùng.")
                try:
                    r = requests.get(url, stream=True, timeout=60)
                    if r.status_code == 200:
                        resp = r
                        break
                    last_err = f"HTTP {r.status_code}"
                except Exception as exc:
                    last_err = str(exc)
                time.sleep(1.5 * (attempt + 1))

            if not resp or resp.status_code != 200:
                raise IOError(f"Lỗi tải ảnh {filename}: {last_err}")

            with open(dst, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
            return str(dst)

        batch_res = run_parallel(
            items=download_tasks,
            worker_fn=_download_one,
            max_workers=self.max_download_workers,
            stop_event=context.stop_event,
            on_progress=make_progress_reporter(
                context, "download", "tải", 85, 100, lambda t: t[1].name
            ),
        )

        downloaded = [res for _, res in batch_res.succeeded if res]
        context.downloaded_files = downloaded

        if not batch_res.all_succeeded:
            if batch_res.is_cancelled:
                context.error_message = "Tải ảnh bị dừng bởi người dùng."
            elif batch_res.failed:
                context.error_message = f"Lỗi tải ảnh: {batch_res.failed[0][1]}"
            else:
                context.error_message = "Không tải được đầy đủ ảnh thành phẩm."
            return False

        context.log("INFO", f"Hoàn tất tải về {len(downloaded)} ảnh thành phẩm.")
        return True
