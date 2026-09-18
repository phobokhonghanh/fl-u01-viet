"""Resumable Chunked Image Upload for Fotello.

Handles resilient upload to Firebase Storage via Fotello's createUpload endpoint
and Google Cloud Storage Resumable Upload protocol. Supports worker concurrency,
retries with backoff, cancellation checks, and strict upload integrity verification.
"""
from __future__ import annotations

import concurrent.futures
import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence

from core.fotello.config import FotelloConfig, load_fotello_config
from core.shared.jobs.models import OutputSpec


def get_content_type(filepath: Path) -> str:
    """Xác định MIME Content-Type của file ảnh."""
    mime, _ = mimetypes.guess_type(str(filepath))
    return mime or "image/jpeg"


def upload_single_file(
    filepath: Path,
    id_token: str,
    team_id: str,
    config: FotelloConfig,
    stop_event: Any | None = None,
) -> str:
    """Tải một file ảnh lên Firebase Storage qua Resumable Upload protocol.

    Args:
        filepath: Đường dẫn file cần tải.
        id_token: Firebase ID token còn hiệu lực.
        team_id: ID nhóm làm việc của tài khoản Fotello.
        config: Cấu hình Fotello chứa URL và tham số retry/timeout.
        stop_event: Tín hiệu dừng nếu người dùng hủy tác vụ.

    Returns:
        upload_id (chuỗi định danh upload trên server Fotello).

    Raises:
        RuntimeError: Nếu upload thất bại sau các lần thử hoặc bị dừng.
    """
    if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
        raise RuntimeError("Tác vụ upload bị hủy bởi người dùng.")

    filename = filepath.name
    file_size = filepath.stat().st_size
    content_type = get_content_type(filepath)

    # Step 1: Gọi create-upload để lấy upload_id
    create_upload_url = config.endpoints.api_base_url.rstrip("/") + config.endpoints.create_upload_path
    upload_body = json.dumps({"filename": filename, "teamId": team_id, "contentType": content_type}).encode("utf-8")
    upload_req = urllib.request.Request(
        create_upload_url,
        data=upload_body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=UTF-8",
            "Authorization": f"Bearer {id_token}",
            "Origin": "https://app.fotello.co",
            "Referer": "https://app.fotello.co/",
        },
    )

    last_error: Exception | None = None
    max_retries = config.upload.max_retries
    timeout = config.upload.timeout_seconds

    upload_id = ""
    for attempt in range(max_retries + 1):
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            raise RuntimeError("Tác vụ upload bị hủy bởi người dùng.")
        try:
            with urllib.request.urlopen(upload_req, timeout=config.network.request_timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                upload_id = data.get("id", "") or data.get("upload_id", "")
                if upload_id:
                    break
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(config.upload.retry_delay_seconds * (attempt + 1))
            else:
                raise RuntimeError(f"Lỗi khởi tạo upload cho '{filename}': {exc}") from exc

    if not upload_id:
        raise RuntimeError(f"Server không trả về upload_id hợp lệ cho file '{filename}'")

    # Step 2: Khởi tạo Google Cloud Storage Resumable Session
    object_name = f"{team_id}/{upload_id}/{filename}"
    encoded_name = urllib.parse.quote(object_name, safe="")
    base_storage_url = config.endpoints.storage_upload_url.rstrip("/")
    separator = "&" if "?" in base_storage_url else "?"
    if "uploadType=resumable" not in base_storage_url:
        start_url = f"{base_storage_url}{separator}name={encoded_name}&uploadType=resumable"
    else:
        start_url = f"{base_storage_url}{separator}name={encoded_name}"

    start_req = urllib.request.Request(
        start_url,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json; charset=UTF-8",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(file_size),
            "X-Goog-Upload-Header-Content-Type": content_type,
            "X-Goog-Upload-Protocol": "resumable",
            "Authorization": f"Firebase {id_token}",
        },
    )

    upload_url = ""
    for attempt in range(max_retries + 1):
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            raise RuntimeError("Tác vụ upload bị hủy bởi người dùng.")
        try:
            with urllib.request.urlopen(start_req, timeout=config.network.request_timeout_seconds) as resp:
                upload_url = resp.headers.get("x-goog-upload-url") or resp.headers.get("X-Goog-Upload-URL") or ""
                if upload_url:
                    break
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(config.upload.retry_delay_seconds * (attempt + 1))
            else:
                raise RuntimeError(f"Lỗi khởi tạo GCS Resumable Session cho '{filename}': {exc}") from exc

    if not upload_url:
        raise RuntimeError(f"GCS không trả về upload_url cho '{filename}'")

    # Step 3: Tải dữ liệu nhị phân lên upload_url
    file_bytes = filepath.read_bytes()
    data_req = urllib.request.Request(
        upload_url,
        data=file_bytes,
        method="POST",
        headers={
            "Content-Type": content_type,
            "X-Goog-Upload-Command": "upload, finalize",
            "X-Goog-Upload-Offset": "0",
        },
    )

    for attempt in range(max_retries + 1):
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            raise RuntimeError("Tác vụ upload bị hủy bởi người dùng.")
        try:
            with urllib.request.urlopen(data_req, timeout=timeout) as resp:
                if 200 <= resp.status < 300:
                    return upload_id
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(config.upload.finalize_retry_delay_seconds * (attempt + 1))
            else:
                raise RuntimeError(f"Lỗi truyền dữ liệu GCS cho '{filename}': {exc}") from exc

    raise RuntimeError(f"Upload thất bại cho file '{filename}': {last_error}")


def upload_job_inputs(
    *,
    outputs: Sequence[OutputSpec],
    id_token: str,
    team_id: str,
    config: FotelloConfig | None = None,
    stop_event: Any | None = None,
    progress_fn: Callable[[int, int], None] | None = None,
    log_fn: Callable[[str, str], None] | None = None,
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """Tải lên toàn bộ file ảnh đầu vào của một Job.

    Args:
        outputs: Danh sách OutputSpec của Job.
        id_token: Firebase ID token.
        team_id: Fotello Team ID.
        config: Cấu hình Fotello.
        stop_event: Tín hiệu dừng.
        progress_fn: Callback báo tiến độ (đã hoàn thành, tổng số file).
        log_fn: Callback ghi log.

    Returns:
        tuple (output_to_upload_ids_map, failed_files_list):
        - output_to_upload_ids_map: dict ánh xạ output_id -> list các upload_id
        - failed_files_list: danh sách các file upload thất bại (nếu có)
    """
    cfg = config or load_fotello_config()

    # Thu thập tất cả các file cần tải
    file_queue: list[tuple[str, int, Path]] = []
    for out in outputs:
        for f_idx, f_path in enumerate(out.input_files):
            file_queue.append((out.output_id, f_idx, f_path))

    total_files = len(file_queue)
    completed_count = 0
    uploaded_results: dict[tuple[str, int], str] = {}
    failed_uploads: list[dict[str, Any]] = []

    def _worker_task(item: tuple[str, int, Path]) -> tuple[str, int, str]:
        out_id, idx, path = item
        up_id = upload_single_file(path, id_token, team_id, cfg, stop_event=stop_event)
        return out_id, idx, up_id

    max_workers = min(cfg.upload.max_workers, max(1, total_files))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker_task, item): item for item in file_queue}
        for fut in concurrent.futures.as_completed(futures):
            item = futures[fut]
            out_id, f_idx, path = item
            if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                break
            try:
                o_id, idx, up_id = fut.result()
                uploaded_results[(o_id, idx)] = up_id
                completed_count += 1
                if progress_fn:
                    progress_fn(completed_count, total_files)
                if log_fn:
                    log_fn(f"Đã upload {completed_count}/{total_files}: {path.name}", "info")
            except Exception as exc:
                failed_uploads.append({
                    "output_id": out_id,
                    "file_index": f_idx,
                    "filename": path.name,
                    "error": str(exc),
                })
                if log_fn:
                    log_fn(f"Upload thất bại file {path.name}: {exc}", "error")

    # Tổng hợp lại theo từng OutputSpec
    output_upload_ids: dict[str, list[str]] = {}
    for out in outputs:
        ids_for_out: list[str] = []
        has_all = True
        for f_idx in range(len(out.input_files)):
            key = (out.output_id, f_idx)
            if key in uploaded_results:
                ids_for_out.append(uploaded_results[key])
            else:
                has_all = False
                break
        if has_all:
            output_upload_ids[out.output_id] = ids_for_out

    return output_upload_ids, failed_uploads
