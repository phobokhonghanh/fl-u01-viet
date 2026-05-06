"""
Step 5: Poll Photoshoot Status (Local).

Polls AutoHDR API to find the photoshoot matching unique_str and address,
then waits for processing to complete with retry logic.

Status handling:
    - 'success' → return photoshoot_id
    - 'ignore' → return None
    - 'in_progress' → retry with backoff
"""

import time
import logging
import datetime
from typing import Optional, List, Callable

from core.http_client import HttpClient
from core.logger import log

logger = logging.getLogger(__name__)


def _find_matching_photoshoot(photoshoots: List[dict], unique_str: str, address: str) -> Optional[dict]:
    """Find a photoshoot matching unique_str and address."""
    for ps in photoshoots:
        if ps.get("name") == unique_str and ps.get("address") == address:
            return ps
    return None


def _fetch_photoshoots(client: HttpClient, user_id: str, limit: int) -> Optional[dict]:
    """Fetch photoshoots list from the API."""
    url = f"/api/users/{user_id}/photoshoots?limit={limit}&offset=0"
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        log(logger, "ERROR", 5, f"Lỗi lấy danh sách photoshoots: {e}")
        return None


def execute(
    client: HttpClient,
    user_id: str,
    unique_str: str,
    address: str,
    max_retries: int = 20,
    initial_delay: float = 60.0,
    backoff_factor: float = 1.2,
    photoshoot_limit: int = 20,
    check_cancelled: Optional[Callable] = None,
    on_log: Optional[Callable] = None,
) -> Optional[int]:
    """
    Execute Step 5: Poll for photoshoot status.

    Args:
        on_log: Callback(level, step, msg) to emit logs visible in job UI.

    Returns photoshoot_id on success, None otherwise.
    """
    step = 5
    delay = initial_delay

    def _log(level: str, msg: str):
        """Log through both the module logger and the pipeline callback."""
        log(logger, level, step, msg)
        if on_log:
            try:
                on_log(level, step, msg)
            except Exception:
                pass

    for attempt in range(max_retries + 1):
        if check_cancelled and check_cancelled():
            _log("WARNING", "Polling bị hủy bởi người dùng")
            return None

        # Fetch photoshoots
        _log("INFO", f"Đang kiểm tra trạng thái... (lần {attempt + 1}/{max_retries + 1})")
        data = _fetch_photoshoots(client, user_id, photoshoot_limit)
        
        status = None
        photoshoot_id = None
        match = None

        if data is None:
            _log("ERROR", "Không thể lấy danh sách photoshoots")
            status = "failure"
        else:
            photoshoots = data.get("photoshoots", [])
            match = _find_matching_photoshoot(photoshoots, unique_str, address)

            if match is None:
                _log("ERROR", f"Không tìm thấy photoshoot cho unique_str: {unique_str}")
                status = "failure"
            else:
                status = match.get("status", "")
                photoshoot_id = match.get("id")

        if status == "success":
            _log("INFO", f"Xử lý hoàn tất! Photoshoot ID: {photoshoot_id}")
            return photoshoot_id

        elif status == "ignore":
            _log("ERROR", f"Photoshoot bị bỏ qua (ignore). Response: {match}")
            return None

        elif status in ["in_progress", "failure"]:
            if attempt < max_retries:
                minutes_approx = delay / 60
                msg_prefix = "Server đang xử lý" if status == "in_progress" else "Lỗi hệ thống/Không tìm thấy"
                _log(
                    "INFO",
                    f"{msg_prefix}... Retry {attempt + 1}/{max_retries}, "
                    f"đợi {delay:.0f}s (~{minutes_approx:.1f} phút)"
                )
                # Sleep in small chunks to check cancellation
                sleep_start = time.time()
                while time.time() - sleep_start < delay:
                    if check_cancelled and check_cancelled():
                        _log("WARNING", "Polling bị hủy trong lúc chờ")
                        return None
                    time.sleep(1)
                delay *= backoff_factor
                if delay > 200:
                    delay = 120
            else:
                _log("ERROR", "Server quá tải, hãy thử lại sau.")
                return None
        else:
            _log("ERROR", f"Status không xác định: '{status}'. Response: {match}")
            return "failure"

    return None
