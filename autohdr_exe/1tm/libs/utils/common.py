"""
Các hàm tiện ích trợ giúp dùng chung (Common Utilities).
"""
from __future__ import annotations

from typing import Any, Callable, Optional


def make_progress_reporter(
    context: Any,
    step_name: str,
    action_verb: str,
    start_pct: int,
    end_pct: int,
    name_getter: Optional[Callable[[Any], str]] = None,
) -> Callable[..., None]:
    """
    Factory tạo callback báo tiến độ chuẩn hóa cho các tác vụ đa luồng (run_parallel).
    Tự động tính toán % nội suy giữa start_pct và end_pct và cập nhật context.set_step.
    Chỉ báo thành công khi is_success=True; nếu thất bại (is_success=False) thì ghi nhận cảnh báo.
    """
    def _reporter(cur: int, tot: int, item: Any, res: Any = None, is_success: bool = True) -> None:
        pct = start_pct + (int((cur / tot) * (end_pct - start_pct)) if tot > 0 else 0)
        item_info = f" ({name_getter(item)})" if name_getter else ""
        if hasattr(context, "set_step"):
            if is_success:
                context.set_step(
                    step_name,
                    f"Đã {action_verb} {cur}/{tot} ảnh{item_info}",
                    progress_pct=pct,
                )
            else:
                if hasattr(context, "log"):
                    context.log("WARN", f"Lỗi {action_verb} ảnh{item_info} ({cur}/{tot})")
                context.set_step(
                    step_name,
                    f"Xử lý {cur}/{tot} ảnh (có lỗi)",
                    progress_pct=pct,
                )

    return _reporter

