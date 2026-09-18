"""Tests for Shared Jobs Subsystem."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from core.shared.jobs import (
    BatchResult,
    JobAttempt,
    JobCapacityExceededError,
    JobContext,
    JobLimits,
    JobPlan,
    JobRecord,
    JobResult,
    JobSpec,
    JobStore,
    JobValidationError,
    OutputSpec,
    plan_jobs,
    restart_job,
    run_jobs,
)
from core.shared.licensing.models import LicenseResult


@pytest.fixture(autouse=True)
def mock_licensing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "core.shared.jobs.runner.require_access",
        lambda engine, mode: LicenseResult(valid=True, engine=engine, level="plus"),
    )



def _make_dummy_outputs(count: int) -> list[OutputSpec]:
    return [
        OutputSpec(
            output_id=f"out_{i:03d}",
            input_files=[Path(f"/tmp/input_{i:03d}.jpg")],
            metadata={"index": i},
        )
        for i in range(count)
    ]


def test_planner_single_mode_limits():
    limits = JobLimits(max_outputs_per_job=20, max_jobs_per_batch=3)

    # 20 outputs: allowed, exactly 1 job
    outputs_20 = _make_dummy_outputs(20)
    plan = plan_jobs(engine="mock", outputs=outputs_20, mode="single", limits=limits)
    assert len(plan.jobs) == 1
    assert len(plan.jobs[0].outputs) == 20
    assert plan.total_outputs == 20

    # 21 outputs: rejected in single mode
    outputs_21 = _make_dummy_outputs(21)
    with pytest.raises(JobCapacityExceededError) as exc_info:
        plan_jobs(engine="mock", outputs=outputs_21, mode="single", limits=limits)
    assert "chỉ cho phép tối đa 20" in str(exc_info.value)


def test_planner_batch_mode_limits():
    limits = JobLimits(max_outputs_per_job=20, max_jobs_per_batch=3)

    # 45 outputs: 3 jobs (20 + 20 + 5)
    outputs_45 = _make_dummy_outputs(45)
    plan_45 = plan_jobs(engine="mock", outputs=outputs_45, mode="batch", limits=limits)
    assert len(plan_45.jobs) == 3
    assert len(plan_45.jobs[0].outputs) == 20
    assert len(plan_45.jobs[1].outputs) == 20
    assert len(plan_45.jobs[2].outputs) == 5

    # 60 outputs: 3 jobs of 20 (max batch capacity)
    outputs_60 = _make_dummy_outputs(60)
    plan_60 = plan_jobs(engine="mock", outputs=outputs_60, mode="batch", limits=limits)
    assert len(plan_60.jobs) == 3

    # 61 outputs: rejected (would require 4 jobs > 3)
    outputs_61 = _make_dummy_outputs(61)
    with pytest.raises(JobCapacityExceededError) as exc_info:
        plan_jobs(engine="mock", outputs=outputs_61, mode="batch", limits=limits)
    assert "vượt quá giới hạn batch tối đa" in str(exc_info.value)


def test_planner_validation_errors():
    limits = JobLimits(max_outputs_per_job=10, max_jobs_per_batch=2)

    # Empty outputs
    with pytest.raises(JobValidationError):
        plan_jobs(engine="mock", outputs=[], mode="single", limits=limits)

    # Invalid mode
    with pytest.raises(JobValidationError):
        plan_jobs(engine="mock", outputs=_make_dummy_outputs(5), mode="unknown", limits=limits)


def test_runner_sequential_and_best_effort_batch(tmp_path: Path):
    limits = JobLimits(max_outputs_per_job=2, max_jobs_per_batch=5)
    outputs = _make_dummy_outputs(6)  # 3 jobs of 2 outputs
    plan = plan_jobs(engine="mock", outputs=outputs, mode="batch", limits=limits, output_dir=tmp_path)

    store = JobStore(jobs_dir=tmp_path / "jobs")

    executed_jobs = []

    def mock_executor(spec: JobSpec, ctx: JobContext) -> JobResult:
        executed_jobs.append(spec.job_id)
        if spec.job_id == plan.jobs[1].job_id:
            # Job 2 fails
            return JobResult(
                job_id=spec.job_id,
                status="failed",
                attempt_no=ctx.attempt_no,
                step="upload",
                error_message="Upload failed on job 2",
                outputs_failed=[{"output_id": o.output_id} for o in spec.outputs],
            )
        # Job 1 and 3 succeed
        return JobResult(
            job_id=spec.job_id,
            status="success",
            attempt_no=ctx.attempt_no,
            step="export",
            outputs_succeeded=[o.output_id for o in spec.outputs],
        )

    batch_result = run_jobs(plan=plan, execute_job=mock_executor, store=store)

    assert executed_jobs == [j.job_id for j in plan.jobs]
    assert batch_result.status == "partial"
    assert batch_result.succeeded_jobs == 2
    assert batch_result.failed_jobs == 1
    assert batch_result.succeeded_outputs == 4
    assert batch_result.failed_outputs == 2


def test_runner_stop_event_cancellation(tmp_path: Path):
    limits = JobLimits(max_outputs_per_job=2, max_jobs_per_batch=5)
    outputs = _make_dummy_outputs(6)  # 3 jobs
    plan = plan_jobs(engine="mock", outputs=outputs, mode="batch", limits=limits, output_dir=tmp_path)
    store = JobStore(jobs_dir=tmp_path / "jobs")

    stop_event = threading.Event()
    executed_jobs = []

    def mock_executor(spec: JobSpec, ctx: JobContext) -> JobResult:
        executed_jobs.append(spec.job_id)
        if spec.job_id == plan.jobs[1].job_id:
            # Set stop event during execution of job 2
            stop_event.set()
            return JobResult(
                job_id=spec.job_id,
                status="cancelled",
                attempt_no=ctx.attempt_no,
                step="download",
                cancel_reason="stopped_during_execution",
            )
        return JobResult(
            job_id=spec.job_id,
            status="success",
            attempt_no=ctx.attempt_no,
            step="export",
            outputs_succeeded=[o.output_id for o in spec.outputs],
        )

    batch_result = run_jobs(plan=plan, execute_job=mock_executor, stop_event=stop_event, store=store)

    # Job 1 completed, Job 2 stopped during execution, Job 3 skipped
    assert executed_jobs == [plan.jobs[0].job_id, plan.jobs[1].job_id]
    assert batch_result.status == "cancelled"
    assert batch_result.succeeded_jobs == 1
    assert batch_result.cancelled_jobs == 2
    assert batch_result.succeeded_outputs == 2

    # Verify unstarted job was marked skipped in results
    job_003_res = next(j for j in batch_result.job_results if j.job_id == plan.jobs[2].job_id)
    assert job_003_res.status == "cancelled"
    assert job_003_res.cancel_reason == "skipped_due_to_batch_cancellation"
    assert job_003_res.started_at is None


def test_restart_job_preserves_id_and_increments_attempt(tmp_path: Path):
    limits = JobLimits(max_outputs_per_job=2, max_jobs_per_batch=2)
    outputs = _make_dummy_outputs(2)
    plan = plan_jobs(engine="mock", outputs=outputs, mode="single", limits=limits, output_dir=tmp_path)
    target_job_id = plan.jobs[0].job_id
    store = JobStore(jobs_dir=tmp_path / "jobs")

    # Initial run: fail at step upload
    def failing_executor(spec: JobSpec, ctx: JobContext) -> JobResult:
        return JobResult(
            job_id=spec.job_id,
            status="failed",
            attempt_no=ctx.attempt_no,
            step="upload",
            error_message="Upload timeout",
            server_resources={"uploaded_temp_ids": ["up_123"]},
        )

    run_jobs(plan=plan, execute_job=failing_executor, store=store)

    # Now manual restart job
    restart_attempts = []

    def successful_restart_executor(spec: JobSpec, ctx: JobContext) -> JobResult:
        restart_attempts.append(ctx.attempt_no)
        assert ctx.previous_attempt is not None
        assert ctx.previous_attempt.step == "upload"
        assert ctx.previous_attempt.server_resources == {"uploaded_temp_ids": ["up_123"]}
        return JobResult(
            job_id=spec.job_id,
            status="success",
            attempt_no=ctx.attempt_no,
            step="export",
            outputs_succeeded=[o.output_id for o in spec.outputs],
        )

    restarted_result = restart_job(
        run_id=plan.run_id,
        job_id=target_job_id,
        execute_job=successful_restart_executor,
        store=store,
    )

    assert restarted_result.status == "success"
    assert restarted_result.attempt_no == 2
    assert restarted_result.job_id == target_job_id
    assert restart_attempts == [2]

    # Verify store record has 2 attempts
    record = store.load_job_record(plan.run_id, target_job_id)
    assert record is not None
    assert record.current_attempt == 2
    assert len(record.attempts) == 2
    assert record.attempts[0].status == "failed"
    assert record.attempts[1].status == "success"


def test_plan_jobs_generates_unique_8_char_job_ids(tmp_path: Path):
    """Kiểm tra plan_jobs sinh job_id gồm đúng 8 ký tự và phân tách thư mục riêng."""
    limits = JobLimits(max_outputs_per_job=2, max_jobs_per_batch=5)
    outputs = _make_dummy_outputs(4)
    plan1 = plan_jobs(engine="autoenhance", outputs=outputs, mode="batch", limits=limits, output_dir=tmp_path)
    plan2 = plan_jobs(engine="autoenhance", outputs=outputs, mode="single", limits=JobLimits(max_outputs_per_job=10, max_jobs_per_batch=1), output_dir=tmp_path)

    for job in plan1.jobs:
        assert len(job.job_id) == 8
        assert job.output_dir == tmp_path / job.job_id

    for job in plan2.jobs:
        assert len(job.job_id) == 8
        assert job.output_dir == tmp_path / job.job_id

    # Đảm bảo các job_id giữa các lần chạy khác nhau hoàn toàn
    all_ids = [j.job_id for j in plan1.jobs] + [j.job_id for j in plan2.jobs]
    assert len(all_ids) == len(set(all_ids))
