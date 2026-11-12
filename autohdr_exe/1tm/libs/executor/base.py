"""
Lớp trừu tượng BaseExecutor, ExecutionContext và ExecutorRegistry.

Cung cấp kiến trúc Template Method Pattern tối giản và linh hoạt:
- pre_transform (Hook/Base method): Chuẩn hóa ảnh, kiểm tra file, chuẩn bị thư mục.
- transform (Abstract method): Engine tự do thực hiện toàn bộ quy trình AI của riêng mình.
- post_transform (Hook/Base method): Hậu kỳ, dọn dẹp tài nguyên.
- execute (Template method): Điểm kích hoạt duy nhất từ bên ngoài.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Type


@dataclass
class ExecutionContext:
    """
    Chứa toàn bộ trạng thái thực thi, dữ liệu trung gian và cơ chế điều khiển
    xuyên suốt vòng đời của một Job.
    """
    job_id: str
    file_paths: List[str]
    output_dir: str
    credentials: Dict[str, Any] = field(default_factory=dict)
    preferences: Dict[str, Any] = field(default_factory=dict)

    # Dữ liệu ảnh và kết quả sau các giai đoạn
    ready_files: List[Path] = field(default_factory=list)
    meta_dims: Dict[str, tuple[int, int]] = field(default_factory=dict)
    downloaded_files: List[str] = field(default_factory=list)
    extra_data: Dict[str, Any] = field(default_factory=dict)

    # Trạng thái thực thi
    status: str = "pending"  # pending, running, completed, failed, cancelled
    current_step: str = ""
    error_message: str = ""
    duration_seconds: float = 0.0
    remote_job_id: Optional[str] = None
    engine: str = ""

    # Cơ chế điều khiển dừng và giao tiếp UI
    stop_event: Optional[threading.Event] = None
    on_log: Optional[Callable[[str, str], None]] = None  # (level, message)
    on_step_change: Optional[Callable[[str, str, int], None]] = None  # (step_name, description, progress_pct)

    def is_cancelled(self) -> bool:
        """Kiểm tra xem tác vụ có nhận được tín hiệu dừng hay không."""
        return self.stop_event is not None and self.stop_event.is_set()

    def set_step(self, step_name: str, description: str = "", progress_pct: int = -1) -> None:
        """Cập nhật bước hiện tại và thông báo đến giao diện UI."""
        self.current_step = step_name
        if description:
            self.log("INFO", description)
        if self.on_step_change:
            self.on_step_change(step_name, description, progress_pct)

    def log(self, level: str, message: str) -> None:
        """Ghi log có tiền tố bước hiện tại gửi ra callback."""
        if self.on_log:
            prefix = f"[{self.current_step.upper()}] " if self.current_step else ""
            self.on_log(level, f"{prefix}{message}")


class BaseExecutor(ABC):
    """
    Lớp cơ sở điều phối xử lý ảnh cho toàn bộ các engine (Template Method Pattern).
    Chỉ có duy nhất phương thức 'transform' là abstract để bảo đảm tính tự do tối đa cho từng Provider.
    """

    def pre_transform(self, context: ExecutionContext) -> bool:
        """
        Giai đoạn chuẩn bị (Base implementation):
        - Kiểm tra các file đầu vào có tồn tại hay không.
        - Khởi tạo thư mục đích lưu trữ output_dir.
        - Thu thập danh sách ready_files.
        Provider có thể override nếu muốn bổ sung logic chuyển đổi RAW/DNG riêng.
        """
        context.set_step("prepare", "Đang kiểm tra và chuẩn bị danh sách ảnh đầu vào...", progress_pct=5)

        # Đảm bảo thư mục đích tồn tại
        out_path = Path(context.output_dir)
        try:
            out_path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            context.error_message = f"Không thể tạo thư mục đầu ra '{context.output_dir}': {exc}"
            context.log("ERROR", context.error_message)
            return False

        # Thu thập các file hợp lệ
        valid_files: List[Path] = []
        for fp in context.file_paths:
            p = Path(fp).resolve()
            if p.is_file():
                valid_files.append(p)
            else:
                context.log("WARN", f"File không tồn tại hoặc không hợp lệ: {fp}")

        if not valid_files:
            context.error_message = f"Không tìm thấy file ảnh hợp lệ nào trong {len(context.file_paths)} đường dẫn đầu vào."
            context.log("ERROR", context.error_message)
            return False

        context.ready_files = valid_files
        context.log("INFO", f"Đã sẵn sàng {len(valid_files)} ảnh hợp lệ để xử lý.")
        return True

    def _validate_pre_transform(self, context: ExecutionContext) -> bool:
        """
        Lớp cha kiểm tra hậu điều kiện (Post-condition Validation / Gatekeeper):
        Đảm bảo lớp con sau khi xử lý pre_transform phải để lại trạng thái hợp lệ 100%:
        1. context.ready_files không được rỗng.
        2. Mọi file trong ready_files phải thực sự tồn tại trên ổ cứng.
        3. File không bị 0 byte (tránh trường hợp convert ảnh RAW hỏng).
        4. Thư mục context.output_dir phải tồn tại trên ổ cứng.
        """
        # 1. Danh sách ready_files không được rỗng
        if not context.ready_files:
            context.error_message = "Lỗi hậu kiểm: 'ready_files' bị rỗng sau bước pre_transform."
            context.log("ERROR", context.error_message)
            return False

        # 2. Từng file phải tồn tại và có kích thước > 0
        for f in context.ready_files:
            p = Path(f).resolve()
            if not p.is_file():
                context.error_message = f"Lỗi hậu kiểm: File không tồn tại trên ổ cứng: {p}"
                context.log("ERROR", context.error_message)
                return False

            try:
                if p.stat().st_size == 0:
                    context.error_message = f"Lỗi hậu kiểm: File bị lỗi 0 byte: {p.name}"
                    context.log("ERROR", context.error_message)
                    return False
            except Exception as e:
                context.error_message = f"Lỗi hậu kiểm: Không thể đọc thông tin file '{p.name}': {e}"
                context.log("ERROR", context.error_message)
                return False

        # 3. Thư mục đầu ra phải tồn tại
        out_p = Path(context.output_dir).resolve()
        if not out_p.is_dir():
            context.error_message = f"Lỗi hậu kiểm: Thư mục đầu ra không tồn tại: {context.output_dir}"
            context.log("ERROR", context.error_message)
            return False

        context.log("INFO", f"Hậu kiểm pre_transform thành công: Đã xác thực an toàn {len(context.ready_files)} file hợp lệ.")
        return True

    @abstractmethod
    def transform(self, context: ExecutionContext) -> bool:
        """
        Trọng tâm xử lý AI của từng Provider:
        - AutoHDR: presigned_urls -> upload -> finalize -> associate_and_run -> poll -> unwatermark -> download
        - Autoenhance: create_order -> upload_s3 -> trigger_process -> poll -> download -> PIL postprocess
        - Topaz / Offline: chạy CLI trực tiếp trên máy
        """
        pass

    def post_transform(self, context: ExecutionContext) -> bool:
        """
        Giai đoạn dọn dẹp và kết thúc (Base implementation):
        - Dọn dẹp các tài nguyên tạm (nếu có).
        - Có thể override bởi Provider để lưu file kiểm toán (manifest) hoặc thống kê.
        """
        context.set_step("cleanup", "Hoàn tất và dọn dẹp tài nguyên...", progress_pct=100)
        return True

    def execute(self, context: ExecutionContext) -> ExecutionContext:
        """
        Template Method: Điểm kích hoạt duy nhất từ bên ngoài.
        Điều phối tuần tự: pre_transform -> transform -> post_transform.
        """
        start_time = time.time()
        context.status = "running"
        context.log("INFO", f"Bắt đầu thực thi Job: {context.job_id}")

        phases = [
            ("pre_transform", self.pre_transform),
            ("transform", self.transform),
            ("post_transform", self.post_transform),
        ]

        for phase_name, phase_fn in phases:
            if context.is_cancelled():
                context.status = "cancelled"
                context.set_step(phase_name, "Tác vụ bị dừng bởi người dùng.")
                context.log("WARN", f"Tác vụ bị hủy trước/trong giai đoạn '{phase_name}'.")
                context.duration_seconds = round(time.time() - start_time, 2)
                return context

            try:
                success = phase_fn(context)
                if not success:
                    if context.is_cancelled():
                        context.status = "cancelled"
                        if not context.error_message:
                            context.error_message = "Tác vụ bị dừng bởi người dùng."
                        context.log("WARN", f"Tác vụ bị hủy trong giai đoạn '{phase_name}'.")
                    else:
                        context.status = "failed"
                        if not context.error_message:
                            context.error_message = f"Giai đoạn '{phase_name}' trả về thất bại."
                        context.log("ERROR", context.error_message)
                    context.duration_seconds = round(time.time() - start_time, 2)
                    return context

                # Chốt chặn hậu kiểm (Gatekeeper) ngay sau pre_transform
                if phase_name == "pre_transform":
                    if not self._validate_pre_transform(context):
                        context.status = "failed"
                        context.duration_seconds = round(time.time() - start_time, 2)
                        return context

            except Exception as exc:
                if context.is_cancelled():
                    context.status = "cancelled"
                    context.log("WARN", f"Tác vụ bị hủy: {str(exc)}")
                else:
                    context.status = "failed"
                    context.error_message = f"Ngoại lệ tại giai đoạn '{phase_name}': {str(exc)}"
                    context.log("ERROR", context.error_message)
                context.duration_seconds = round(time.time() - start_time, 2)
                return context

        context.status = "completed"
        context.duration_seconds = round(time.time() - start_time, 2)
        context.log("INFO", f"Job '{context.job_id}' hoàn tất thành công trong {context.duration_seconds}s.")
        return context


class ExecutorRegistry:
    """Quản lý và ánh xạ động các Executor theo tên engine (Strategy/Registry Pattern)."""
    _registry: Dict[str, Type[BaseExecutor]] = {}
    _lock = threading.Lock()

    @classmethod
    def register(cls, engine_name: str, executor_cls: Type[BaseExecutor]) -> None:
        """Đăng ký một class executor với tên engine định danh (chuẩn hóa chữ thường)."""
        key = engine_name.strip().lower()
        with cls._lock:
            cls._registry[key] = executor_cls

    @classmethod
    def get(cls, engine_name: str) -> Optional[Type[BaseExecutor]]:
        """Lấy class executor theo tên engine."""
        key = engine_name.strip().lower()
        with cls._lock:
            return cls._registry.get(key)

    @classmethod
    def list_available(cls) -> List[str]:
        """Danh sách các engine đã đăng ký."""
        with cls._lock:
            return sorted(list(cls._registry.keys()))

    @classmethod
    def clear(cls) -> None:
        """Dọn dẹp registry (phục vụ testing)."""
        with cls._lock:
            cls._registry.clear()

