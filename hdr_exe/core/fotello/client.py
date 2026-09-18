"""Network client, streaming downloader, and credential security for Fotello Engine."""
from __future__ import annotations

import hashlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from core.fotello.config import load_fotello_config


_BEARER_PATTERN = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", re.IGNORECASE)
_API_KEY_PATTERN = re.compile(r"(key=)[A-Za-z0-9_\-]+", re.IGNORECASE)
_SIGNATURE_PATTERN = re.compile(r"((?:X-Goog-Signature|Signature)=)[^&]+", re.IGNORECASE)


def redact_sensitive(text: str) -> str:
    """Che các thông tin nhạy cảm (Bearer token, API key, chữ ký) trong thông báo và URL."""
    if not text:
        return ""
    s = _BEARER_PATTERN.sub(r"\1[REDACTED]", text)
    s = _API_KEY_PATTERN.sub(r"\1[REDACTED]", s)
    s = _SIGNATURE_PATTERN.sub(r"\1[REDACTED]", s)
    return s


def is_presigned_url(url: str) -> bool:
    """Kiểm tra URL có chứa tham số chữ ký tải sẵn (presigned) hay không."""
    lower = url.lower()
    return any(p in lower for p in ("signature=", "x-goog-signature=", "alt=media&token="))


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Trình xử lý chuyển hướng bảo mật: tự động gỡ Authorization nếu redirect sang host khác."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        orig_host = urllib.parse.urlparse(req.get_full_url()).hostname
        new_host = urllib.parse.urlparse(newurl).hostname
        if orig_host and new_host and orig_host.lower() != new_host.lower():
            if "Authorization" in new_req.headers:
                del new_req.headers["Authorization"]
            if "authorization" in new_req.headers:
                del new_req.headers["authorization"]
        return new_req


_OPENER = urllib.request.build_opener(SafeRedirectHandler)


def retry_request(
    fn: Callable[[], Any],
    max_retries: int | None = None,
    stop_event: Any | None = None,
) -> Any:
    """Thực thi hàm HTTP có retry với backoff lũy tiến; hết lượt trả lỗi rõ ràng."""
    config = load_fotello_config()
    retries = max_retries if max_retries is not None else config.network.max_retries
    delay = config.network.retry_delay_seconds

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            raise RuntimeError("Tác vụ mạng bị hủy bởi người dùng")

        try:
            return fn()
        except urllib.error.HTTPError as exc:
            last_exc = exc
            # Retry trên lỗi tạm thời hoặc rate-limit
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(delay)
                delay *= config.network.retry_backoff
                continue
            # Lỗi client 4xx khác (400, 401, 403, 404): không retry
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(delay)
                delay *= config.network.retry_backoff
                continue
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError("Vượt quá số lần thử lại yêu cầu mạng")


def stream_download_image(
    uri: str,
    access_token: str,
    dest_temp_path: Path,
    max_bytes: int | None = None,
    max_dimension: int | None = None,
    timeout: float | None = None,
    stop_event: Any | None = None,
) -> tuple[int, str, tuple[int, int], str]:
    """Tải ảnh dạng stream vào file tạm, tính hash theo chunk và kiểm tra giải mã ảnh.
    
    Trả về: (file_bytes, sha256_hex, (width, height), extension)
    """
    config = load_fotello_config()
    limit_bytes = config.download.max_file_bytes if max_bytes is None else max_bytes
    limit_dim = config.download.max_image_dimension if max_dimension is None else max_dimension
    timeout_sec = config.network.request_timeout_seconds if timeout is None else timeout

    # Phân giải link
    if uri.startswith("gs://"):
        parts = uri[5:].split("/", 1)
        bucket = parts[0]
        obj = urllib.parse.quote(parts[1], safe="")
        primary_url = f"{config.endpoints.storage_download_base_url}/b/{bucket}/o/{obj}?alt=media"
        fallback_url = f"{config.endpoints.storage_fallback_base_url}/b/{bucket}/o/{obj}?alt=media"
    else:
        primary_url = uri
        fallback_url = None

    headers: dict[str, str] = {}
    # Không gắn Bearer token nếu là presigned URL có chữ ký sẵn
    if not is_presigned_url(primary_url) and access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    def _execute_download() -> None:
        def _fetch(url: str):
            req = urllib.request.Request(url, headers=headers)
            with _OPENER.open(req, timeout=timeout_sec) as resp:
                sha = hashlib.sha256()
                total = 0
                with open(dest_temp_path, "wb") as out_f:
                    while True:
                        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                            raise RuntimeError("Tải ảnh bị hủy bởi người dùng")
                        chunk = resp.read(config.download.chunk_size_bytes)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > limit_bytes:
                            raise ValueError(
                                f"Dung lượng tải ({total} bytes) vượt quá giới hạn cho phép {limit_bytes} bytes"
                            )
                        sha.update(chunk)
                        out_f.write(chunk)
                return total, sha.hexdigest()

        try:
            return _fetch(primary_url)
        except urllib.error.HTTPError as err:
            if fallback_url and err.code in (401, 403):
                return _fetch(fallback_url)
            raise

    total_bytes, sha256_hex = retry_request(_execute_download, stop_event=stop_event)

    # Kiểm tra giải mã ảnh thực sự (không chỉ đọc header)
    try:
        with Image.open(dest_temp_path) as im:
            im.verify()
        with Image.open(dest_temp_path) as im:
            im.load()
            w, h = im.size
            fmt = (im.format or "JPEG").lower()
            ext = "jpg" if fmt in ("jpeg", "jpg") else fmt
    except Exception as img_err:
        raise ValueError(f"Dữ liệu tải về không phải ảnh hợp lệ hoặc bị hỏng: {img_err}") from img_err

    if w > limit_dim or h > limit_dim:
        raise ValueError(
            f"Kích thước ảnh ({w}x{h}) vượt quá giới hạn tối đa cho phép ({limit_dim}px)"
        )

    return total_bytes, sha256_hex, (w, h), ext


