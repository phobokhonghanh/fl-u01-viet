"""Safe Progress Callback Dispatcher for HDR Engines.

Safely handles 2-argument and 3-argument progress callbacks without risking
re-invoking the callback if an internal exception occurs.
"""
from __future__ import annotations

import inspect
import sys
from typing import Any, Callable


class ProgressAdapter:
    """Adapter chuẩn hóa progress callback chuẩn bị trước (bind 1 lần duy nhất).

    - Kiểm tra và bind cấu trúc tham số một lần khi khởi tạo.
    - Cảnh báo qua warning_fn hoặc sys.stderr khi callback phát sinh lỗi nội bộ.
    - Không nuốt lỗi âm thầm và không bao giờ gọi lại callback khi lỗi.
    """

    def __init__(
        self,
        callback: Callable[..., Any] | None,
        warning_fn: Callable[[str, str], None] | None = None,
    ) -> None:
        self.callback = callback
        self.warning_fn = warning_fn
        self._mode = "none"  # "none", "three", "two"
        self._failed = False

        if not callback or not callable(callback):
            self._mode = "none"
            return

        try:
            sig = inspect.signature(callback)
            # Thử bind với 3 tham số (current, total, filename)
            try:
                sig.bind(0, 0, "")
                self._mode = "three"
            except TypeError:
                # Nếu không bind được 3 tham số, thử 2 tham số (current, total)
                try:
                    sig.bind(0, 0)
                    self._mode = "two"
                except TypeError:
                    self._mode = "three"
        except (ValueError, TypeError):
            self._mode = "three"

    def __call__(self, current: int, total: int, filename: str = "") -> None:
        if self._mode == "none" or self._failed or not self.callback:
            return

        try:
            if self._mode == "three":
                self.callback(current, total, filename)
            else:
                self.callback(current, total)
        except Exception as e:
            self._failed = True
            msg = f"Progress callback failed with exception: {e}. Subsequent calls suppressed."
            if self.warning_fn and callable(self.warning_fn):
                try:
                    self.warning_fn(msg, "warn")
                except Exception:
                    pass
            else:
                sys.stderr.write(f"[Warning] {msg}\n")


def safe_call_progress(
    progress_fn: Callable[..., Any] | None,
    current: int,
    total: int,
    filename: str = "",
) -> None:
    """Gọi callback tiến trình an toàn, tự động chuẩn bị adapter."""
    if not progress_fn or not callable(progress_fn):
        return
    adapter = ProgressAdapter(progress_fn)
    adapter(current, total, filename)
