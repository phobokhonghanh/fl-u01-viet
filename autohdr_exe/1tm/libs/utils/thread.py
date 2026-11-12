"""
Tiện ích quản lý luồng song song (ThreadPoolExecutor).
Cung cấp hàm run_parallel gom kết quả, báo tiến độ và hỗ trợ dừng tức thì khi nhận stop_event.
"""
from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
import threading
from typing import Any, Callable, Generic, List, Optional, Sequence, Tuple, TypeVar

T = TypeVar("T")
R = TypeVar("R")


@dataclass
class BatchResult(Generic[T, R]):
    """Kết quả tổng hợp từ một lượt chạy song song."""
    succeeded: List[Tuple[T, R]] = field(default_factory=list)
    failed: List[Tuple[T, Exception]] = field(default_factory=list)
    is_cancelled: bool = False

    @property
    def total(self) -> int:
        return len(self.succeeded) + len(self.failed)

    @property
    def all_succeeded(self) -> bool:
        return len(self.failed) == 0 and not self.is_cancelled and len(self.succeeded) > 0


def run_parallel(
    items: Sequence[T],
    worker_fn: Callable[[T], R],
    max_workers: int = 4,
    stop_event: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[int, int, T, Optional[R]], None]] = None,
    fail_fast: bool = False,
) -> BatchResult[T, R]:
    """
    Thực thi worker_fn trên từng phần tử trong items song song qua ThreadPoolExecutor.

    Args:
        items: Danh sách phần tử đầu vào (file path, url, image spec...).
        worker_fn: Hàm xử lý đơn lẻ cho từng phần tử.
        max_workers: Số luồng chạy đồng thời tối đa.
        stop_event: Tín hiệu hủy tác vụ từ UI.
        on_progress: Callback(completed_count, total_count, item, result).
        fail_fast: Dừng nhận task tiếp theo ngay khi gặp 1 lỗi đầu tiên.

    Returns:
        BatchResult chứa danh sách thành công, thất bại và cờ is_cancelled.
    """
    result = BatchResult[T, R]()
    total = len(items)
    if total == 0:
        return result

    completed_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {executor.submit(worker_fn, item): item for item in items}

        for future in concurrent.futures.as_completed(future_to_item):
            item = future_to_item[future]

            if stop_event is not None and stop_event.is_set():
                result.is_cancelled = True
                break

            try:
                res = future.result()
                result.succeeded.append((item, res))
                completed_count += 1
                if on_progress:
                    try:
                        on_progress(completed_count, total, item, res, True)
                    except TypeError:
                        on_progress(completed_count, total, item, res)
            except Exception as exc:
                result.failed.append((item, exc))
                completed_count += 1
                if on_progress:
                    try:
                        on_progress(completed_count, total, item, None, False)
                    except TypeError:
                        on_progress(completed_count, total, item, None)
                if fail_fast:
                    break

    return result

