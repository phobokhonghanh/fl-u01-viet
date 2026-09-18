"""Autoenhance Order Polling Loop.

Periodically queries order details until all images complete AI enhancement
or fail, handling stop event cancellation and 30-minute timeout.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from core.autoenhance.constants import DEFAULT_MAX_WAIT_SECONDS, DEFAULT_POLL_INTERVAL
from core.autoenhance.orders import get_order_details


def poll_order_completion(
    api_key: str,
    order_id: str,
    max_wait_seconds: int = DEFAULT_MAX_WAIT_SECONDS,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
    log_fn: Callable[[str, str], None] | None = None,
    stop_event: Any = None,
    session: Any = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """Thăm dò trạng thái xử lý của Order cho tới khi toàn bộ ảnh kết thúc.

    - Dùng time.monotonic() để đo lường thời gian trôi qua chính xác, không bị ảnh hưởng bởi đồng hồ hệ thống.
    - Dùng stop_event.wait(interval) để ngắt lập tức khi nhận tín hiệu hủy.
    - Tái sử dụng HTTP Session qua các lần polling để tối ưu socket pool.
    - Trả về tuple: (order_details, successful_images, failed_images).
    - Nếu bị hủy hoặc quá thời gian, trả về (None, [], []).
    """
    start_time = time.monotonic()

    while time.monotonic() - start_time < max_wait_seconds:
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            if log_fn:
                log_fn("[Autoenhance][Polling] Đã hủy thăm dò tiến trình theo yêu cầu dừng.", "info")
            return None, [], []

        # Chờ theo khoảng thời gian hoặc thức dậy ngay lập tức khi stop_event kích hoạt
        if stop_event and hasattr(stop_event, "wait"):
            stopped = stop_event.wait(poll_interval)
            if stopped:
                if log_fn:
                    log_fn("[Autoenhance][Polling] Đã hủy thăm dò tiến trình theo yêu cầu dừng.", "info")
                return None, [], []
        else:
            time.sleep(poll_interval)

        try:
            ord_det = get_order_details(order_id=order_id, api_key=api_key, session=session)
            imgs = ord_det.get("images", [])
            if not imgs:
                continue

            pending = [i for i in imgs if i.get("status") in ("processing", "queued", "pending", "waiting", "") and not i.get("enhanced")]
            successful = [i for i in imgs if i.get("status") in ("processed", "completed", "done", "success") or i.get("enhanced") is True]
            failed = [i for i in imgs if i.get("status") in ("failed", "error") or i.get("error") is True]
            completed_total = len(successful) + len(failed)

            if log_fn:
                log_fn(
                    f"Tiến trình AI: {completed_total}/{len(imgs)} ảnh (Thành công: {len(successful)}, Lỗi: {len(failed)})",
                    "info",
                )

            if len(pending) == 0:
                # Toàn bộ ảnh đã kết thúc xử lý trên server
                return ord_det, successful, failed
        except Exception as e:
            if log_fn:
                log_fn(f"Cảnh báo lỗi tạm thời khi thăm dò: {e}", "warn")

    if log_fn:
        elapsed = int(time.monotonic() - start_time)
        log_fn(f"Quá thời gian chờ xử lý AI trên Autoenhance ({elapsed}s >= {max_wait_seconds}s).", "error")
    return None, [], []

