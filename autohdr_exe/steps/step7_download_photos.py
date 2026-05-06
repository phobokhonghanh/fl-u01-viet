"""
Step 7: Download Processed Photos (Local) — Optimized.

Downloads processed photos directly to the user's chosen download directory.
Optimizations:
  - Concurrent download: ThreadPoolExecutor with 5 workers.
  - Large chunk size: 64KB for fewer I/O calls.
  - Checkpoint: saves progress so interrupted downloads can resume.

Output structure: {download_dir}/{date}/{folder_name}/
"""

import os
import time
import json
import logging
import datetime
from typing import List, Optional, Callable
from urllib.parse import urlparse, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.http_client import HttpClient
from core.logger import log

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_WORKERS = 5
CHUNK_SIZE = 65536  # 64KB — fewer I/O calls than 8KB


def _get_checkpoint_path(output_dir: str) -> str:
    """Get the checkpoint file path for a download session."""
    return os.path.join(output_dir, ".checkpoint.json")


def _load_checkpoint(output_dir: str) -> set:
    """Load already-downloaded filenames from checkpoint."""
    cp_path = _get_checkpoint_path(output_dir)
    if os.path.exists(cp_path):
        try:
            with open(cp_path, "r") as f:
                data = json.load(f)
                return set(data.get("completed", []))
        except Exception:
            pass
    return set()


def _save_checkpoint(output_dir: str, completed_files: set):
    """Save checkpoint with list of completed filenames."""
    cp_path = _get_checkpoint_path(output_dir)
    try:
        with open(cp_path, "w") as f:
            json.dump({"completed": list(completed_files)}, f)
    except Exception:
        pass


def _download_single_file(
    client: HttpClient,
    url: str,
    output_path: str,
    max_retries: int = 3,
    on_log: Optional[Callable] = None,
) -> bool:
    """Download a single file with streaming write and retry logic."""
    for attempt in range(max_retries):
        try:
            # S3 presigned URLs often fail if you send extra cookies or site-specific headers.
            # Setting a header to None in requests removes it from the session headers for this call.
            s3_headers = {
                "Cookie": None,
                "Origin": None,
                "Referer": None,
                "Content-Type": None,
            }
            response = client.get(url, stream=True, headers=s3_headers)
            response.raise_for_status()

            # Write in chunks — never hold entire file in RAM
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
            return True
        except Exception as e:
            msg = f"Download retry {attempt + 1}/{max_retries}: {e}"
            log(logger, "WARNING", 7, msg)
            if on_log:
                try:
                    on_log("WARNING", 7, msg)
                except Exception:
                    pass
            # Clean up partial file on failure
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            time.sleep(2 * (attempt + 1))
    return False


def execute(
    client: HttpClient,
    cleaned_urls: List[str],
    unique_str: str,
    download_dir: str,
    check_cancelled: Optional[Callable] = None,
    folder_name: Optional[str] = None,
    on_log: Optional[Callable] = None,
) -> List[str]:
    """
    Execute Step 7: Download processed photos (concurrent + checkpoint).

    Returns list of downloaded file paths.
    """
    step = 7
    downloaded_paths = []

    def _log(level: str, msg: str):
        """Log through both the module logger and the pipeline callback."""
        log(logger, level, step, msg)
        if on_log:
            try:
                on_log(level, step, msg)
            except Exception:
                pass

    if not cleaned_urls:
        _log("INFO", "Không có ảnh để tải")
        return []

    # Create output directory
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    final_folder = folder_name if folder_name else unique_str
    output_dir = os.path.join(download_dir, date_str, final_folder)
    os.makedirs(output_dir, exist_ok=True)

    file_count = len(cleaned_urls)
    _log("INFO", f"Thư mục lưu trữ: {output_dir}")

    # Load checkpoint — skip already-downloaded files
    completed_set = _load_checkpoint(output_dir)
    if completed_set:
        _log("INFO", f"Checkpoint: bỏ qua {len(completed_set)} ảnh đã tải trước đó")

    # Build download tasks
    tasks = []
    for i, url in enumerate(cleaned_urls):
        filename = unquote(os.path.basename(urlparse(url).path))
        if not filename:
            filename = f"photo_{i:03d}.jpg"

        # Skip if already in checkpoint
        if filename in completed_set:
            output_path = os.path.join(output_dir, filename)
            if os.path.exists(output_path):
                downloaded_paths.append(output_path)
                continue

        output_path = os.path.join(output_dir, filename)
        tasks.append((url, output_path, filename))

    if not tasks:
        _log("INFO", f"Tất cả {file_count} ảnh đã được tải trước đó (checkpoint)")
        return downloaded_paths

    # Determine log frequency
    log_every = 10 if len(tasks) >= 50 else 1

    # Execute concurrent downloads
    completed_count = 0
    failed_count = 0

    with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as executor:
        future_to_info = {}

        for i, (url, output_path, filename) in enumerate(tasks):
            if check_cancelled and check_cancelled():
                log(logger, "WARNING", step, "Download bị hủy bởi người dùng")
                break

            # Small stagger to avoid AWS throttling
            if i > 0:
                time.sleep(0.2)

            future = executor.submit(
                _download_single_file,
                client, url, output_path,
                3, on_log
            )
            future_to_info[future] = (output_path, filename)

        # Collect results
        for future in as_completed(future_to_info):
            output_path, filename = future_to_info[future]

            if check_cancelled and check_cancelled():
                log(logger, "WARNING", step, "Download bị hủy bởi người dùng")
                break

            try:
                success = future.result()
            except Exception as e:
                _log("ERROR", f"Download crash {filename}: {e}")
                success = False

            if success:
                completed_count += 1
                downloaded_paths.append(output_path)
                completed_set.add(filename)
                # Save checkpoint after each successful download
                _save_checkpoint(output_dir, completed_set)
                # Log progress
                if completed_count % log_every == 0 or completed_count == len(tasks):
                    _log("INFO", f"Tải OK: {completed_count}/{len(tasks)}")
            else:
                failed_count += 1
                _log("ERROR", f"Tải thất bại: {filename}")

    _log("INFO",
        f"Hoàn tất: {len(downloaded_paths)}/{file_count} ảnh "
        f"({failed_count} lỗi)")

    # Kiểm tra xem đã tải đủ số lượng URL đầu vào chưa
    if len(downloaded_paths) < file_count:
        raise Exception(f"Tải thiếu ảnh: {len(downloaded_paths)}/{file_count}. Hãy thử tải lại.")

    return downloaded_paths
