"""Firestore Status Polling for Fotello Enhances.

Polls enhance status documents in Firestore until completion, failure, timeout,
or user cancellation. Supports fine-grained per-enhance tracking and step callbacks.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Sequence

from core.fotello.config import FotelloConfig, load_fotello_config
from core.fotello.firestore import decode_firestore_value, get_document


def poll_enhances_completion(
    *,
    enhance_ids: Sequence[str],
    access_token: str,
    config: FotelloConfig | None = None,
    stop_event: Any | None = None,
    progress_fn: Callable[[int, int], None] | None = None,
    log_fn: Callable[[str, str], None] | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """Theo dõi tiến độ xử lý của danh sách enhances trên Firestore.

    Args:
        enhance_ids: Danh sách ID các enhance cần theo dõi.
        access_token: Token xác thực Firestore.
        config: Cấu hình Fotello chứa interval và timeout polling.
        stop_event: Tín hiệu dừng từ người dùng.
        progress_fn: Callback báo tiến độ (số enhance đã xong, tổng số enhance).
        log_fn: Callback ghi log.

    Returns:
        tuple (succeeded_ids, failed_ids, timed_out_ids):
        - succeeded_ids: tập ID đã xử lý thành công (status == "enhance_success")
        - failed_ids: tập ID server báo lỗi (status == "enhance_failed")
        - timed_out_ids: tập ID chưa hoàn tất khi hết thời gian chờ hoặc bị dừng
    """
    cfg = config or load_fotello_config()
    all_targets = set(enhance_ids)
    total = len(all_targets)

    succeeded: set[str] = set()
    failed: set[str] = set()
    pending: set[str] = set(all_targets)

    poll_interval = cfg.polling.interval_seconds
    timeout = cfg.polling.timeout_seconds
    start_time = time.time()

    while pending:
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            if log_fn:
                log_fn("Polling bị dừng bởi người dùng.", "warn")
            break

        if time.time() - start_time > timeout:
            if log_fn:
                log_fn(f"Hết thời gian chờ polling ({timeout}s) cho {len(pending)} enhances.", "error")
            break

        # Kiểm tra từng enhance còn pending
        just_completed: list[str] = []
        for enh_id in list(pending):
            if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                break

            try:
                doc = get_document(f"enhances/{enh_id}", access_token)
                fields = decode_firestore_value(doc.get("fields", {}))
                status = fields.get("status", "unknown")

                if status == "enhance_success":
                    succeeded.add(enh_id)
                    just_completed.append(enh_id)
                elif "fail" in status.lower() or "error" in status.lower():
                    failed.add(enh_id)
                    just_completed.append(enh_id)
            except Exception:
                # Lỗi mạng tạm thời khi đọc 1 doc, thử lại ở chu kỳ tiếp theo
                pass

        for c_id in just_completed:
            pending.remove(c_id)

        if progress_fn:
            progress_fn(len(succeeded) + len(failed), total)

        if not pending:
            break

        # Ngủ giữa các lần thăm dò
        sleep_elapsed = 0.0
        while sleep_elapsed < poll_interval:
            if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
                break
            time.sleep(min(cfg.polling.cancellation_check_seconds, poll_interval - sleep_elapsed))
            sleep_elapsed += cfg.polling.cancellation_check_seconds

    timed_out = pending
    return succeeded, failed, timed_out
