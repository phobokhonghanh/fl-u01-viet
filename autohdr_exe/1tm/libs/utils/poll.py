"""
Tiện ích quản lý vòng lặp thăm dò (Polling Loop).
Hỗ trợ dừng lập tức (0ms latency qua stop_event.wait) và cả 2 chế độ timeout (max_seconds, max_retries).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import Any, Callable, Optional, Tuple


class PollStatus(Enum):
    """Trạng thái thăm dò."""
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class PollResult:
    """Kết quả trả về sau phiên polling."""
    status: PollStatus
    data: Any = None
    error_message: str = ""
    attempts: int = 0
    duration_seconds: float = 0.0

    @property
    def is_success(self) -> bool:
        return self.status == PollStatus.SUCCESS


def poll_until(
    check_fn: Callable[[int], Tuple[PollStatus, Any, str]],
    poll_interval: float = 5.0,
    max_seconds: Optional[float] = None,
    max_retries: Optional[int] = None,
    stop_event: Optional[threading.Event] = None,
) -> PollResult:
    """
    Vòng lặp thăm dò định kỳ cho đến khi check_fn báo SUCCESS, FAILED hoặc quá thời gian.

    Args:
        check_fn: Hàm kiểm tra từng lần thử check_fn(attempt) -> (PollStatus, data, message).
        poll_interval: Thời gian chờ giữa 2 lần thăm dò (giây).
        max_seconds: Giới hạn tổng thời gian thăm dò tối đa (giây).
        max_retries: Giới hạn tổng số lần gọi API tối đa.
        stop_event: Cờ báo dừng từ UI (thoát tức thì mà không cần chờ hết poll_interval).

    Returns:
        PollResult chứa trạng thái cuối cùng, data thành phẩm và thông điệp lỗi nếu có.
    """
    start_time = time.time()
    attempt = 0

    while True:
        attempt += 1
        elapsed = time.time() - start_time

        # 1. Kiểm tra cờ dừng trước mỗi lần gọi
        if stop_event is not None and stop_event.is_set():
            return PollResult(
                status=PollStatus.CANCELLED,
                error_message="Tác vụ bị dừng bởi người dùng.",
                attempts=attempt,
                duration_seconds=elapsed,
            )

        # 2. Kiểm tra điều kiện Timeout
        if max_seconds is not None and elapsed >= max_seconds:
            return PollResult(
                status=PollStatus.TIMEOUT,
                error_message=f"Quá thời gian chờ xử lý ({int(max_seconds)}s).",
                attempts=attempt,
                duration_seconds=elapsed,
            )
        if max_retries is not None and attempt > max_retries:
            return PollResult(
                status=PollStatus.TIMEOUT,
                error_message=f"Vượt quá số lần kiểm tra tối đa ({max_retries} lần).",
                attempts=attempt,
                duration_seconds=elapsed,
            )

        # 3. Kích hoạt hàm kiểm tra đơn lẻ
        try:
            status, data, msg = check_fn(attempt)
            if status in (PollStatus.SUCCESS, PollStatus.FAILED):
                return PollResult(
                    status=status,
                    data=data,
                    error_message=msg,
                    attempts=attempt,
                    duration_seconds=time.time() - start_time,
                )
        except Exception as exc:
            # Lỗi mạng đột ngột: không dừng hẳn mà để vòng lặp thử lại tiếp
            pass

        # 4. Ngủ có điều kiện: nhả khóa ngay lập tức nếu người dùng bấm Hủy
        if stop_event is not None:
            if stop_event.wait(poll_interval):
                return PollResult(
                    status=PollStatus.CANCELLED,
                    error_message="Tác vụ bị dừng bởi người dùng.",
                    attempts=attempt,
                    duration_seconds=time.time() - start_time,
                )
        else:
            time.sleep(poll_interval)

