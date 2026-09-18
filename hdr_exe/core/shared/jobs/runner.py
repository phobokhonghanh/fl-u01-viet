"""Shared Jobs Sequential Runner.

Executes planned jobs sequentially with checkpointing, cancellation propagation,
best-effort batch failure handling, and step-aware manual job restarts.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.shared.events import StepEvent
from core.shared.jobs.models import (
    BatchResult,
    JobAttempt,
    JobError,
    JobPlan,
    JobResult,
    JobSpec,
)
from core.shared.jobs.store import JobStore
from core.shared.licensing import require_access
from core.shared.licensing.models import LicensingError


@dataclass
class JobContext:
    """Ngữ cảnh thực thi truyền cho executor của từng job."""
    run_id: str
    job_id: str
    attempt_no: int
    engine: str
    store: JobStore
    stop_event: Any | None = None
    event_fn: Callable[[StepEvent], None] | None = None
    log_fn: Callable[[str, str], None] | None = None
    previous_attempt: JobAttempt | None = None
    server_resources: dict[str, Any] = field(default_factory=dict)
    latest_step: str = ""
    resume_step: str = ""
    credentials: dict[str, Any] = field(default_factory=dict)

    def is_cancelled(self) -> bool:
        """Kiểm tra tín hiệu dừng từ người dùng."""
        return bool(self.stop_event and hasattr(self.stop_event, "is_set") and self.stop_event.is_set())

    def log(self, message: str, level: str = "info") -> None:
        """Ghi log qua callback nếu có."""
        if self.log_fn:
            prefix = f"[{self.engine.capitalize()}][{self.job_id}]"
            self.log_fn(f"{prefix} {message}", level)

    def record_checkpoint(
        self,
        step: str,
        status: str,
        *,
        server_resources: dict[str, Any] | None = None,
        error_message: str | None = None,
        cancel_reason: str | None = None,
    ) -> None:
        """Lưu checkpoint tiến trình của step hiện tại vào store."""
        if server_resources:
            self.server_resources.update(server_resources)
        self.latest_step = step
        self.store.record_step_progress(
            self.run_id,
            self.job_id,
            step,
            status,
            server_resources=self.server_resources,
            error_message=error_message,
            cancel_reason=cancel_reason,
        )


def run_jobs(
    *,
    plan: JobPlan,
    execute_job: Callable[[JobSpec, JobContext], JobResult],
    credentials: dict[str, Any] | None = None,
    stop_event: Any | None = None,
    event_fn: Callable[[StepEvent], None] | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    store: JobStore | None = None,
) -> BatchResult:
    """Thực thi tuần tự các job trong JobPlan.

    Quy tắc:
    - Chạy tuần tự: job sau chỉ bắt đầu khi job trước kết thúc.
    - Dừng (stop_event): Đánh dấu job đang chạy là 'cancelled' (với step bị ngắt),
      các job chưa chạy đánh dấu là 'cancelled' (lý do: skipped_due_to_batch_cancellation).
    - Lỗi job (failed): Best-effort, không làm dừng các job tiếp theo trong batch.
    - Checkpoint: Khởi tạo và cập nhật trạng thái liên tục vào store.
    """
    job_store = store or JobStore()
    job_store.init_run(plan)
    job_store.update_run_status(plan.run_id, "running")

    batch_start = time.time()
    job_results: list[JobResult] = []
    stopped_by_user = False

    for idx, spec in enumerate(plan.jobs):
        # Kiểm tra nếu người dùng đã yêu cầu dừng trước khi job này bắt đầu
        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            stopped_by_user = True
            # Đánh dấu job hiện tại và tất cả các job còn lại là cancelled (chưa chạy)
            for skip_spec in plan.jobs[idx:]:
                skip_result = JobResult(
                    job_id=skip_spec.job_id,
                    status="cancelled",
                    attempt_no=1,
                    step="queued",
                    cancel_reason="skipped_due_to_batch_cancellation",
                    error_message="Chưa chạy, do người dùng dừng batch",
                    started_at=None,
                    finished_at=time.time(),
                )
                job_results.append(skip_result)
                job_store.add_job_attempt(
                    plan.run_id,
                    skip_spec.job_id,
                    JobAttempt(
                        attempt_no=1,
                        status="cancelled",
                        step="queued",
                        started_at=time.time(),
                        finished_at=time.time(),
                        cancel_reason="skipped_due_to_batch_cancellation",
                        error_message="Chưa chạy, do người dùng dừng batch",
                    ),
                )
            break

        # Xác thực bản quyền trực tuyến trước khi chạy job
        act_event_start = StepEvent(
            run_id=plan.run_id,
            engine=spec.engine,
            step="activation",
            step_index=1,
            step_total=1,
            status="running",
            message=f"Đang xác thực bản quyền trực tuyến cho engine '{spec.engine}'...",
        )
        if event_fn:
            try:
                event_fn(act_event_start)
            except Exception:
                pass
        if log_fn:
            try:
                log_fn(f"[{spec.engine.capitalize()}][{spec.job_id}] Đang xác thực bản quyền...", "info")
            except Exception:
                pass

        job_start = time.time()
        try:
            lic_res = require_access(spec.engine, plan.mode)
            act_event_success = StepEvent(
                run_id=plan.run_id,
                engine=spec.engine,
                step="activation",
                step_index=1,
                step_total=1,
                status="success",
                message=f"Bản quyền hợp lệ (Cấp độ {lic_res.level.upper()}).",
            )
            if event_fn:
                try:
                    event_fn(act_event_success)
                except Exception:
                    pass
        except (LicensingError, Exception) as lic_exc:
            lic_err_msg = str(lic_exc)
            act_event_fail = StepEvent(
                run_id=plan.run_id,
                engine=spec.engine,
                step="activation",
                step_index=1,
                step_total=1,
                status="failed",
                message=f"Xác thực bản quyền thất bại: {lic_err_msg}",
            )
            if event_fn:
                try:
                    event_fn(act_event_fail)
                except Exception:
                    pass
            if log_fn:
                try:
                    log_fn(f"[{spec.engine.capitalize()}][{spec.job_id}] Lỗi bản quyền: {lic_err_msg}", "error")
                except Exception:
                    pass

            # Job hiện tại chưa chạy bị failed tại bước activation
            fail_job_result = JobResult(
                job_id=spec.job_id,
                status="failed",
                attempt_no=1,
                step="activation",
                error_message=lic_err_msg,
                started_at=job_start,
                finished_at=time.time(),
            )
            job_results.append(fail_job_result)
            job_store.record_step_progress(plan.run_id, spec.job_id, "activation", "failed", error_message=lic_err_msg)
            job_store.add_job_attempt(
                plan.run_id,
                spec.job_id,
                JobAttempt(
                    attempt_no=1,
                    status="failed",
                    step="activation",
                    started_at=job_start,
                    finished_at=time.time(),
                    error_message=lic_err_msg,
                ),
            )

            # Các job còn lại cancelled với lý do cụ thể
            for skip_spec in plan.jobs[idx + 1:]:
                skip_result = JobResult(
                    job_id=skip_spec.job_id,
                    status="cancelled",
                    attempt_no=1,
                    step="queued",
                    cancel_reason="cancelled_due_to_activation_failure",
                    error_message="Chưa chạy, do lỗi xác thực bản quyền ở job trước",
                    started_at=None,
                    finished_at=time.time(),
                )
                job_results.append(skip_result)
                job_store.add_job_attempt(
                    plan.run_id,
                    skip_spec.job_id,
                    JobAttempt(
                        attempt_no=1,
                        status="cancelled",
                        step="queued",
                        started_at=time.time(),
                        finished_at=time.time(),
                        cancel_reason="cancelled_due_to_activation_failure",
                        error_message="Chưa chạy, do lỗi xác thực bản quyền ở job trước",
                    ),
                )
            break

        # Khởi tạo ngữ cảnh thực thi cho job
        context = JobContext(
            run_id=plan.run_id,
            job_id=spec.job_id,
            attempt_no=1,
            engine=spec.engine,
            store=job_store,
            stop_event=stop_event,
            event_fn=event_fn,
            log_fn=log_fn,
            credentials=dict(credentials or {}),
        )

        job_store.record_step_progress(plan.run_id, spec.job_id, "start", "running")

        try:
            result = execute_job(spec, context)
        except Exception as exc:
            step_name = context.latest_step or "execution"
            err_msg = f"Ngoại lệ chưa bắt trong executor: {exc}"
            if event_fn:
                try:
                    event_fn(StepEvent(
                        run_id=plan.run_id,
                        engine=spec.engine,
                        step=step_name,
                        step_index=1,
                        step_total=1,
                        status="failed",
                        message=err_msg,
                    ))
                except Exception:
                    pass
            if log_fn:
                try:
                    log_fn(f"[{spec.engine.capitalize()}][{spec.job_id}] {err_msg}", "error")
                except Exception:
                    pass
            result = JobResult(
                job_id=spec.job_id,
                status="failed",
                attempt_no=1,
                step=step_name,
                error_message=str(exc),
                started_at=job_start,
                finished_at=time.time(),
            )

        if result.started_at is None:
            result.started_at = job_start
        if result.finished_at is None:
            result.finished_at = time.time()

        job_results.append(result)

        # Lưu attempt vào store
        job_store.add_job_attempt(
            plan.run_id,
            spec.job_id,
            JobAttempt(
                attempt_no=result.attempt_no,
                status=result.status,
                step=result.step,
                started_at=result.started_at,
                finished_at=result.finished_at,
                cancel_reason=result.cancel_reason,
                error_message=result.error_message,
                server_resources=result.server_resources,
                outputs_succeeded=result.outputs_succeeded,
                outputs_failed=result.outputs_failed,
            ),
        )

        # Nếu job vừa chạy bị huỷ bởi người dùng -> dừng batch ngay, huỷ các job tiếp theo
        if result.status == "cancelled" or (stop_event and hasattr(stop_event, "is_set") and stop_event.is_set()):
            stopped_by_user = True
            for skip_spec in plan.jobs[idx + 1:]:
                skip_result = JobResult(
                    job_id=skip_spec.job_id,
                    status="cancelled",
                    attempt_no=1,
                    step="queued",
                    cancel_reason="skipped_due_to_batch_cancellation",
                    error_message="Chưa chạy, do người dùng dừng batch",
                    started_at=None,
                    finished_at=time.time(),
                )
                job_results.append(skip_result)
                job_store.add_job_attempt(
                    plan.run_id,
                    skip_spec.job_id,
                    JobAttempt(
                        attempt_no=1,
                        status="cancelled",
                        step="queued",
                        started_at=time.time(),
                        finished_at=time.time(),
                        cancel_reason="skipped_due_to_batch_cancellation",
                        error_message="Chưa chạy, do người dùng dừng batch",
                    ),
                )
            break

    batch_finish = time.time()

    # Tổng hợp thống kê
    succeeded_jobs = sum(1 for j in job_results if j.status == "success")
    failed_jobs = sum(1 for j in job_results if j.status == "failed")
    cancelled_jobs = sum(1 for j in job_results if j.status == "cancelled")
    total_jobs = len(plan.jobs)

    succeeded_outputs = sum(len(j.outputs_succeeded) for j in job_results)
    failed_outputs = sum(len(j.outputs_failed) for j in job_results)
    cancelled_outputs = sum(
        len(spec.outputs)
        for spec, res in zip(plan.jobs, job_results)
        if res.status == "cancelled"
    )

    if stopped_by_user:
        batch_status = "cancelled"
    elif failed_jobs == 0 and cancelled_jobs == 0 and all(j.status == "success" for j in job_results):
        batch_status = "success"
    elif succeeded_jobs == 0 and (failed_jobs > 0 or cancelled_jobs > 0):
        batch_status = "failed"
    else:
        batch_status = "partial"

    job_store.update_run_status(plan.run_id, batch_status)

    return BatchResult(
        run_id=plan.run_id,
        engine=plan.engine,
        mode=plan.mode,
        status=batch_status,
        job_results=job_results,
        total_jobs=total_jobs,
        succeeded_jobs=succeeded_jobs,
        failed_jobs=failed_jobs,
        cancelled_jobs=cancelled_jobs,
        total_outputs=plan.total_outputs,
        succeeded_outputs=succeeded_outputs,
        failed_outputs=failed_outputs,
        cancelled_outputs=cancelled_outputs,
        started_at=batch_start,
        finished_at=batch_finish,
    )


def restart_job(
    *,
    run_id: str,
    job_id: str,
    execute_job: Callable[[JobSpec, JobContext], JobResult],
    credentials: dict[str, Any] | None = None,
    stop_event: Any | None = None,
    event_fn: Callable[[StepEvent], None] | None = None,
    log_fn: Callable[[str, str], None] | None = None,
    store: JobStore | None = None,
) -> JobResult:
    """Chạy lại một job đơn lẻ theo yêu cầu của người dùng (manual restart).

    Quy tắc:
    - Giữ nguyên job_id trên UI/store.
    - Tăng attempt_no cho lần chạy mới.
    - Truyền lịch sử attempt trước (step lỗi, ID tài nguyên server đã tạo)
      vào JobContext để executor quyết định điểm tiếp tục:
      * Trước polling: chạy lại từ đầu với 100% input, tạo listing mới.
      * Tại polling: tiếp tục polling với listing/enhance ID đã lưu.
      * Sau polling: tiếp tục download các output chưa hoàn tất.
    """
    job_store = store or JobStore()
    record = job_store.load_job_record(run_id, job_id)
    if not record:
        raise JobError(f"Không tìm thấy job '{job_id}' trong run '{run_id}' để chạy lại.")

    new_attempt_no = record.current_attempt + 1
    previous_attempt = record.attempts[-1] if record.attempts else None

    # Tính resume_step bằng cách bỏ qua các attempt lỗi ở bước activation
    resume_step = ""
    if record.attempts:
        for att in reversed(record.attempts):
            if att.step and att.step != "activation":
                resume_step = att.step
                break
    if not resume_step and record.latest_step and record.latest_step != "activation":
        resume_step = record.latest_step

    spec = JobSpec(
        job_id=record.job_id,
        run_id=record.run_id,
        job_index=record.job_index,
        job_total=record.job_total,
        engine=record.engine,
        outputs=record.outputs,
        preferences=record.preferences,
        output_dir=record.output_dir,
    )

    context = JobContext(
        run_id=run_id,
        job_id=job_id,
        attempt_no=new_attempt_no,
        engine=record.engine,
        store=job_store,
        stop_event=stop_event,
        event_fn=event_fn,
        log_fn=log_fn,
        previous_attempt=previous_attempt,
        server_resources=dict(record.server_resources),
        latest_step=record.latest_step,
        resume_step=resume_step,
        credentials=dict(credentials or {}),
    )

    # Xác thực bản quyền trực tuyến trước khi restart
    run_doc = job_store.load_run(run_id)
    mode = str(run_doc.get("mode", "single")) if run_doc else "single"
    job_start = time.time()

    act_start_event = StepEvent(
        run_id=run_id,
        engine=record.engine,
        step="activation",
        step_index=1,
        step_total=1,
        status="running",
        message=f"Đang xác thực bản quyền trực tuyến cho engine '{record.engine}' khi restart...",
    )
    if event_fn:
        try:
            event_fn(act_start_event)
        except Exception:
            pass

    try:
        lic_res = require_access(record.engine, mode)
        act_ok_event = StepEvent(
            run_id=run_id,
            engine=record.engine,
            step="activation",
            step_index=1,
            step_total=1,
            status="success",
            message=f"Bản quyền hợp lệ (Cấp độ {lic_res.level.upper()}).",
        )
        if event_fn:
            try:
                event_fn(act_ok_event)
            except Exception:
                pass
    except (LicensingError, Exception) as lic_exc:
        err_msg = str(lic_exc)
        act_fail_event = StepEvent(
            run_id=run_id,
            engine=record.engine,
            step="activation",
            step_index=1,
            step_total=1,
            status="failed",
            message=f"Xác thực bản quyền thất bại: {err_msg}",
        )
        if event_fn:
            try:
                event_fn(act_fail_event)
            except Exception:
                pass
        if log_fn:
            try:
                log_fn(f"[{record.engine.capitalize()}][{job_id}] Lỗi bản quyền khi restart: {err_msg}", "error")
            except Exception:
                pass

        # Giữ nguyên checkpoint xử lý khi xác thực restart thất bại
        fail_restart_result = JobResult(
            job_id=job_id,
            status="failed",
            attempt_no=new_attempt_no,
            step="activation",
            error_message=err_msg,
            started_at=job_start,
            finished_at=time.time(),
            server_resources=dict(record.server_resources),
        )
        job_store.add_job_attempt(
            run_id,
            job_id,
            JobAttempt(
                attempt_no=new_attempt_no,
                status="failed",
                step="activation",
                started_at=job_start,
                finished_at=time.time(),
                error_message=err_msg,
                server_resources=dict(record.server_resources),
            ),
        )
        return fail_restart_result

    job_store.record_step_progress(run_id, job_id, record.latest_step or "restart", "running")

    try:
        result = execute_job(spec, context)
    except Exception as exc:
        step_name = context.latest_step or "restart"
        err_msg = f"Ngoại lệ chưa bắt khi restart executor: {exc}"
        if event_fn:
            try:
                event_fn(StepEvent(
                    run_id=run_id,
                    engine=record.engine,
                    step=step_name,
                    step_index=1,
                    step_total=1,
                    status="failed",
                    message=err_msg,
                ))
            except Exception:
                pass
        if log_fn:
            try:
                log_fn(f"[{record.engine.capitalize()}][{job_id}] {err_msg}", "error")
            except Exception:
                pass
        result = JobResult(
            job_id=job_id,
            status="failed",
            attempt_no=new_attempt_no,
            step=step_name,
            error_message=str(exc),
            started_at=job_start,
            finished_at=time.time(),
        )

    if result.started_at is None:
        result.started_at = job_start
    if result.finished_at is None:
        result.finished_at = time.time()

    job_store.add_job_attempt(
        run_id,
        job_id,
        JobAttempt(
            attempt_no=result.attempt_no,
            status=result.status,
            step=result.step,
            started_at=result.started_at,
            finished_at=result.finished_at,
            cancel_reason=result.cancel_reason,
            error_message=result.error_message,
            server_resources=result.server_resources,
            outputs_succeeded=result.outputs_succeeded,
            outputs_failed=result.outputs_failed,
        ),
    )

    # Cập nhật trạng thái tổng quan của run sau khi restart hoàn tất
    run_doc = job_store.load_run(run_id)
    if run_doc and "jobs" in run_doc:
        all_records = [job_store.load_job_record(run_id, jid) for jid in run_doc["jobs"]]
        all_statuses = [r.status for r in all_records if r is not None]
        if all_statuses:
            if all(s == "success" for s in all_statuses):
                new_status = "success"
            elif any(s == "running" for s in all_statuses):
                new_status = "running"
            elif any(s == "success" for s in all_statuses):
                new_status = "partial"
            elif all(s == "cancelled" for s in all_statuses):
                new_status = "cancelled"
            else:
                new_status = "failed"
            job_store.update_run_status(run_id, new_status)

    return result
