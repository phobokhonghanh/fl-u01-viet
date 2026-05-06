"""
Step 2: Upload Files to S3 (Local) — Optimized.

Uses presigned URLs from Step 1 to upload files to S3.
Optimizations:
  - Streaming upload: files are streamed directly, NOT loaded into RAM.
  - Concurrent upload: ThreadPoolExecutor with 5 workers.
  - Batch progress: log every 10 files when count > 50.
"""

import os
import time
import logging
from typing import List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.http_client import HttpClient
from core.logger import log
from models.schemas import PipelineContext, PresignedUrl

logger = logging.getLogger(__name__)

# Concurrency config
MAX_UPLOAD_WORKERS = 5
STAGGER_DELAY = 0.3  # seconds between starting each upload


def _find_file_path(filename: str, file_paths: List[str]) -> Optional[str]:
    """Find the local file path matching a filename."""
    for fp in file_paths:
        if os.path.basename(fp) == filename:
            return fp
    return None


def _upload_single_file_streaming(
    client: HttpClient,
    presigned_url: PresignedUrl,
    file_path: str,
    max_retries: int = 3,
) -> bool:
    """
    Upload a single file to S3 using streaming (low RAM).
    The file is read from disk in chunks — never fully loaded into memory.
    """
    file_size = os.path.getsize(file_path)

    for attempt in range(max_retries):
        try:
            with open(file_path, "rb") as f:
                response = client.put_stream(presigned_url.url, f, file_size)
                if response.status_code == 200:
                    return True
                log(logger, "WARNING", 2,
                    f"Upload {presigned_url.filename} status {response.status_code}, "
                    f"retry {attempt + 1}/{max_retries}")
        except Exception as e:
            log(logger, "WARNING", 2,
                f"Upload {presigned_url.filename} exception: {e}, "
                f"retry {attempt + 1}/{max_retries}")
        time.sleep(2 * (attempt + 1))
    return False


def execute(client: HttpClient, context: PipelineContext, check_cancelled: Optional[Callable] = None) -> bool:
    """
    Execute Step 2: Upload all files to S3 (concurrent + streaming).

    Returns True if ALL uploads succeed, False if any fails.
    """
    step = 2

    if not context.presigned_urls:
        log(logger, "ERROR", step, "Không có presigned URLs để upload")
        return False

    total = len(context.presigned_urls)
    log(logger, "INFO", step, f"Upload {total} ảnh (streaming, {MAX_UPLOAD_WORKERS} workers song song)")

    # Determine log frequency: every file if < 50, every 10 if >= 50
    log_every = 10 if total >= 50 else 1

    # Build upload tasks
    tasks = []
    for presigned_url in context.presigned_urls:
        file_path = _find_file_path(presigned_url.filename, context.file_paths)
        if file_path is None:
            log(logger, "ERROR", step, f"Không tìm thấy file: {presigned_url.filename}")
            return False
        if not os.path.exists(file_path):
            log(logger, "ERROR", step, f"File không tồn tại: {file_path}")
            return False
        tasks.append((presigned_url, file_path))

    # Execute concurrent uploads
    completed_count = 0
    failed_count = 0

    with ThreadPoolExecutor(max_workers=MAX_UPLOAD_WORKERS) as executor:
        future_to_info = {}

        for i, (presigned_url, file_path) in enumerate(tasks):
            if check_cancelled and check_cancelled():
                log(logger, "WARNING", step, "Upload bị hủy bởi người dùng")
                return False

            # Small stagger to avoid thundering herd on S3
            if i > 0:
                time.sleep(STAGGER_DELAY)

            future = executor.submit(
                _upload_single_file_streaming,
                client, presigned_url, file_path,
            )
            future_to_info[future] = (i, presigned_url.filename)

        # Collect results
        for future in as_completed(future_to_info):
            idx, filename = future_to_info[future]

            if check_cancelled and check_cancelled():
                log(logger, "WARNING", step, "Upload bị hủy bởi người dùng")
                return False

            try:
                success = future.result()
            except Exception as e:
                log(logger, "ERROR", step, f"Upload crash {filename}: {e}")
                success = False

            if success:
                completed_count += 1
                # Log progress
                if completed_count % log_every == 0 or completed_count == total:
                    log(logger, "INFO", step, f"Upload OK: {completed_count}/{total}")
            else:
                failed_count += 1
                log(logger, "ERROR", step, f"Upload FAILED: {filename}")

    if failed_count > 0:
        log(logger, "ERROR", step, f"Upload hoàn tất với {failed_count} lỗi")
        return False

    log(logger, "INFO", step, f"Upload hoàn tất: {completed_count}/{total} ảnh")
    return True
