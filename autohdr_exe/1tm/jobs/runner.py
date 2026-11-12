"""
Bộ điều phối Job chạy nền (JobRunner).
Cung cấp cầu nối giữa giao diện UI và các Job Executor:
- Khởi chạy Job trên Background Thread để UI không bị đơ/treo.
- Đăng ký các callback thread-safe để UI cập nhật log và thanh tiến trình.
- Hỗ trợ dừng tác vụ an toàn tức thì qua stop_job(job_id).
- Quản lý danh sách các job đang chạy và đã hoàn thành.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

from libs.executor.base import ExecutionContext, ExecutorRegistry


class JobRunner:
    """Điều phối và quản lý vòng đời thực thi các Job trên Background Thread."""

    def __init__(self):
        self._jobs: Dict[str, ExecutionContext] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._engine_map: Dict[str, str] = {}
        self._lock = threading.Lock()

    def submit_job(
        self,
        engine: str,
        file_paths: List[str],
        output_dir: str,
        credentials: Optional[Dict[str, Any]] = None,
        preferences: Optional[Dict[str, Any]] = None,
        ui_callback_log: Optional[Callable[[str, str], None]] = None,
        ui_callback_progress: Optional[Callable[[str, str, int], None]] = None,
        ui_callback_finished: Optional[Callable[[ExecutionContext], None]] = None,
    ) -> ExecutionContext:
        """
        Kích hoạt một job mới trên luồng nền (Background Thread).

        Args:
            engine: Tên định danh engine ("autohdr", "autoenhance").
            file_paths: Danh sách đường dẫn ảnh nguồn.
            output_dir: Thư mục lưu ảnh thành phẩm.
            credentials: Thông tin xác thực (cookie, api_key...).
            preferences: Tùy chọn xử lý (preset, address, models...).
            ui_callback_log: Callback nhận log (level, message).
            ui_callback_progress: Callback nhận tiến độ (step_name, description, progress_pct).
            ui_callback_finished: Callback nhận thông báo khi job kết thúc.

        Returns:
            ExecutionContext của job vừa được khởi tạo.
        """
        engine_key = engine.strip().lower()
        job_cls = ExecutorRegistry.get(engine_key)
        if not job_cls:
            available = ", ".join(ExecutorRegistry.list_available())
            raise ValueError(f"Engine '{engine}' chưa được đăng ký trong hệ thống! Các engine sẵn có: {available}")

        job_id = f"job_{uuid.uuid4().hex[:8]}"
        stop_event = threading.Event()

        # Đảm bảo đường dẫn output_dir luôn trỏ vào thư mục con chứa job_id: <output_dir>/<job_id>/
        base_out = Path(output_dir).resolve()
        if base_out.name != job_id:
            job_output_dir = str(base_out / job_id)
        else:
            job_output_dir = str(base_out)

        context = ExecutionContext(
            job_id=job_id,
            file_paths=list(file_paths),
            output_dir=job_output_dir,
            credentials=dict(credentials or {}),
            preferences=dict(preferences or {}),
            stop_event=stop_event,
            on_log=ui_callback_log,
            on_step_change=ui_callback_progress,
            engine=engine_key,
        )

        with self._lock:
            self._jobs[job_id] = context
            self._engine_map[job_id] = engine_key

        def _worker_target():
            try:
                job_instance = job_cls()
                job_instance.execute(context)
            finally:
                if ui_callback_finished:
                    try:
                        ui_callback_finished(context)
                    except Exception as e:
                        context.log("ERROR", f"Lỗi trong ui_callback_finished: {e}")

        thread = threading.Thread(
            target=_worker_target,
            daemon=True,
            name=f"JobThread-{engine_key}-{job_id}",
        )

        with self._lock:
            self._threads[job_id] = thread

        thread.start()
        return context

    def stop_job(self, job_id: str) -> bool:
        """
        Phát tín hiệu dừng tác vụ khẩn cấp cho job đang chạy.
        Trả về True nếu job tồn tại và đã phát tín hiệu dừng.
        """
        with self._lock:
            context = self._jobs.get(job_id)
            if context and context.stop_event:
                context.stop_event.set()
                context.log("WARN", f"Đã gửi yêu cầu dừng job: {job_id}")
                return True
        return False

    def get_job(self, job_id: str) -> Optional[ExecutionContext]:
        """Lấy thông tin và trạng thái hiện tại của một job."""
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> List[ExecutionContext]:
        """Lấy danh sách toàn bộ các job đã từng được submit."""
        with self._lock:
            return list(self._jobs.values())

    def resume_job(
        self,
        job_id: str,
        ui_callback_log: Optional[Callable[[str, str], None]] = None,
        ui_callback_progress: Optional[Callable[[str, str, int], None]] = None,
        ui_callback_finished: Optional[Callable[[ExecutionContext], None]] = None,
    ) -> Optional[ExecutionContext]:
        """
        Khôi phục và chạy tiếp một job đã bị lỗi hoặc bị dừng (Resume from failed step).

        Args:
            job_id: ID của job cần chạy lại.
            ui_callback_log: Callback nhận log (level, message).
            ui_callback_progress: Callback nhận tiến độ (step_name, description, progress_pct).
            ui_callback_finished: Callback thông báo khi job kết thúc.

        Returns:
            ExecutionContext của job hoặc None nếu job không tồn tại.
        """
        with self._lock:
            context = self._jobs.get(job_id)
            if not context:
                return None

            engine_key = context.engine or self._engine_map.get(job_id, "")
            job_cls = ExecutorRegistry.get(engine_key)
            if not job_cls:
                raise ValueError(f"Không tìm thấy engine executor tương ứng với job {job_id} (engine: '{engine_key}')")

            old_thread = self._threads.get(job_id)
            if old_thread and old_thread.is_alive():
                context.log("WARN", f"Job {job_id} vẫn đang hoạt động trên luồng cũ. Không thể kích hoạt resume song song.")
                return None

            context.stop_event = threading.Event()
            context.status = "running"
            context.error_message = ""
            if ui_callback_log is not None:
                context.on_log = ui_callback_log
            if ui_callback_progress is not None:
                context.on_step_change = ui_callback_progress

        def _worker_target():
            try:
                job_instance = job_cls()
                job_instance.execute(context)
            finally:
                if ui_callback_finished:
                    try:
                        ui_callback_finished(context)
                    except Exception as e:
                        context.log("ERROR", f"Lỗi trong ui_callback_finished khi resume: {e}")

        thread = threading.Thread(
            target=_worker_target,
            daemon=True,
            name=f"JobThread-Resume-{engine_key}-{job_id}",
        )

        with self._lock:
            self._threads[job_id] = thread

        thread.start()
        return context

    def clear_finished_jobs(self) -> int:
        """Xóa bỏ các job đã kết thúc khỏi bộ nhớ, trả về số lượng job đã dọn."""
        with self._lock:
            to_remove = [
                jid for jid, ctx in self._jobs.items()
                if ctx.status in ("completed", "failed", "cancelled")
            ]
            for jid in to_remove:
                self._jobs.pop(jid, None)
                self._threads.pop(jid, None)
                self._engine_map.pop(jid, None)
            return len(to_remove)

