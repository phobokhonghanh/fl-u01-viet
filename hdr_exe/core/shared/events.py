"""Step Events & Execution Tracking for HDR Engines.

Defines standardized StepEvent structure and StepTracker coordinator for
emitting structured events to event_fn and formatting step logs to log_fn.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class StepEvent:
    """Đại diện cho một sự kiện tiến trình tại một bước trong quy trình xử lý."""
    run_id: str
    engine: str
    step: str
    step_index: int
    step_total: int
    status: str  # 'running' | 'success' | 'partial' | 'failed' | 'cancelled'
    message: str
    current: int | None = None
    total: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Chuyển đổi đối tượng StepEvent thành dictionary an toàn cho JSON."""
        return asdict(self)


VALID_TERMINAL_STATUSES = {"success", "partial", "failed", "cancelled"}
VALID_STATUSES = {"running"} | VALID_TERMINAL_STATUSES

_LOG_LEVEL_MAP = {
    "running": "info",
    "success": "success",
    "partial": "warn",
    "cancelled": "warn",
    "failed": "error",
}


class StepTracker:
    """Bộ điều phối tập trung quản lý run_id, bước thực thi, sự kiện và log.

    - Đảm bảo mỗi lần chạy có một run_id riêng.
    - Phát StepEvent tới event_fn (nếu có).
    - Định dạng log gửi tới log_fn (nếu có): [Engine][Index/Total StepTitle][Status] Message.
    - Hai callback được bảo vệ độc lập: lỗi ở một callback không ảnh hưởng tới callback còn lại.
    - Không gọi lại callback nếu callback phát sinh lỗi nội bộ.
    - Bảo đảm nghiêm ngặt vòng đời bước: cấm bước chưa khai báo, chặn phát lặp sự kiện kết thúc.
    """

    def __init__(
        self,
        engine: str,
        steps: Sequence[tuple[str, str]],
        run_id: str | None = None,
        event_fn: Callable[[StepEvent], None] | None = None,
        log_fn: Callable[[str, str], None] | None = None,
    ) -> None:
        self.engine = engine
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._steps = list(steps)
        self._step_names = [s[0] for s in self._steps]
        self._step_titles = {s[0]: s[1] for s in self._steps}
        self.step_total = len(self._steps)

        self._event_fn = event_fn
        self._log_fn = log_fn
        self._event_fn_failed = False
        self._log_fn_failed = False

        # Theo dõi trạng thái của từng bước
        self._started_steps: set[str] = set()
        self._completed_steps: set[str] = set()
        self._last_events: dict[str, StepEvent] = {}
        self.active_step: str | None = None

    def _validate_step_name(self, step_name: str) -> None:
        """Kiểm tra tên bước có nằm trong danh sách khai báo ban đầu hay không."""
        if step_name not in self._step_names:
            raise ValueError(
                f"Bước '{step_name}' không tồn tại trong danh sách khai báo của engine {self.engine}: {self._step_names}"
            )

    def get_step_info(self, step_name: str) -> tuple[int, str]:
        """Lấy step_index (1-based) và step_title theo tên định danh bước."""
        self._validate_step_name(step_name)
        idx = self._step_names.index(step_name) + 1
        title = self._step_titles[step_name]
        return idx, title

    def emit(
        self,
        step_name: str,
        status: str,
        message: str,
        current: int | None = None,
        total: int | None = None,
    ) -> StepEvent:
        """Phát sự kiện và điều phối log tới log_fn và event_fn."""
        self._validate_step_name(step_name)
        if status not in VALID_STATUSES:
            raise ValueError(f"Trạng thái '{status}' không hợp lệ. Các trạng thái được phép: {VALID_STATUSES}")

        step_idx, step_title = self.get_step_info(step_name)

        event = StepEvent(
            run_id=self.run_id,
            engine=self.engine.lower(),
            step=step_name,
            step_index=step_idx,
            step_total=self.step_total,
            status=status,
            message=message,
            current=current,
            total=total,
        )
        self._last_events[step_name] = event

        # 1. Gửi StepEvent tới event_fn
        if self._event_fn and not self._event_fn_failed:
            try:
                self._event_fn(event)
            except Exception:
                self._event_fn_failed = True

        # 2. Định dạng chuỗi log gửi tới log_fn
        if self._log_fn and not self._log_fn_failed:
            level = _LOG_LEVEL_MAP.get(status, "info")
            engine_label = self.engine.capitalize()
            step_label = f"{step_idx}/{self.step_total} {step_title}"
            formatted_log = f"[{engine_label}][{step_label}][{status}] {message}"
            try:
                self._log_fn(formatted_log, level)
            except Exception:
                self._log_fn_failed = True

        return event

    def start_step(
        self,
        step_name: str,
        message: str,
        current: int | None = None,
        total: int | None = None,
    ) -> StepEvent:
        """Bắt đầu một bước thực thi mới với trạng thái running."""
        self._validate_step_name(step_name)
        if step_name in self._completed_steps:
            if self._log_fn and not self._log_fn_failed:
                try:
                    self._log_fn(f"[{self.engine.capitalize()}] Warning: Bước '{step_name}' đã kết thúc, không thể bắt đầu lại.", "warn")
                except Exception:
                    self._log_fn_failed = True
            return self._last_events[step_name]

        self._started_steps.add(step_name)
        self.active_step = step_name
        return self.emit(step_name, "running", message, current, total)

    def progress_step(
        self,
        step_name: str,
        message: str,
        current: int,
        total: int,
    ) -> StepEvent:
        """Cập nhật tiến độ trong khi một bước đang chạy."""
        self._validate_step_name(step_name)
        if step_name in self._completed_steps:
            if self._log_fn and not self._log_fn_failed:
                try:
                    self._log_fn(f"[{self.engine.capitalize()}] Warning: Bước '{step_name}' đã kết thúc, bỏ qua cập nhật tiến độ sau khi hoàn tất.", "warn")
                except Exception:
                    self._log_fn_failed = True
            return self._last_events[step_name]

        return self.emit(step_name, "running", message, current, total)

    def complete_step(
        self,
        step_name: str,
        status: str,
        message: str,
        current: int | None = None,
        total: int | None = None,
    ) -> StepEvent:
        """Đánh dấu kết thúc bước với một trong các trạng thái cuối: success, partial, failed, cancelled."""
        self._validate_step_name(step_name)
        if status not in VALID_TERMINAL_STATUSES:
            raise ValueError(f"Trạng thái kết thúc '{status}' không hợp lệ. Phải là một trong: {VALID_TERMINAL_STATUSES}")

        if step_name in self._completed_steps:
            if self._log_fn and not self._log_fn_failed:
                try:
                    self._log_fn(f"[{self.engine.capitalize()}] Warning: Bước '{step_name}' đã kết thúc trước đó, bỏ qua sự kiện hoàn tất trùng lặp.", "warn")
                except Exception:
                    self._log_fn_failed = True
            return self._last_events[step_name]

        self._completed_steps.add(step_name)
        if self.active_step == step_name:
            self.active_step = None
        return self.emit(step_name, status, message, current, total)

    def is_step_completed(self, step_name: str) -> bool:
        """Kiểm tra bước đã có trạng thái kết thúc hay chưa."""
        return step_name in self._completed_steps

    def fail_active_step(self, message: str) -> StepEvent | None:
        """Đóng bước đang hoạt động với trạng thái failed nếu chưa hoàn tất."""
        if self.active_step and not self.is_step_completed(self.active_step):
            step = self.active_step
            self.active_step = None
            return self.complete_step(step, "failed", message)
        return None

