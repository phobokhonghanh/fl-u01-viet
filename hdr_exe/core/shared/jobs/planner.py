"""Shared Jobs Planner.

Plans and validates job distribution based on output count and engine capacity limits.
Supports 'single' and 'batch' execution modes with strict pre-upload capacity validation.
"""
from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any, Sequence

from core.shared.jobs.models import (
    JobCapacityExceededError,
    JobLimits,
    JobPlan,
    JobSpec,
    JobValidationError,
    OutputSpec,
)


def generate_job_id() -> str:
    """Sinh định danh job duy nhất gồm đúng 8 ký tự hex để tránh trùng lặp thư mục output."""
    return uuid.uuid4().hex[:8]


def plan_jobs(
    *,
    engine: str,
    outputs: Sequence[OutputSpec],
    mode: str,
    limits: JobLimits,
    preferences: dict[str, Any] | None = None,
    output_dir: Path | str | None = None,
    run_id: str | None = None,
    job_ids: Sequence[str] | None = None,
) -> JobPlan:
    """Lập kế hoạch chia job cho danh sách outputs theo giới hạn sức chứa.

    Args:
        engine: Định danh engine (ví dụ: 'fotello', 'autoenhance').
        outputs: Danh sách OutputSpec logic đã chuẩn bị.
        mode: 'single' (tối đa 1 job) hoặc 'batch' (tự chia nhiều job tuần tự).
        limits: Giới hạn sức chứa (max_outputs_per_job, max_jobs_per_batch).
        preferences: Snapshot thông số cấu hình ảnh cho lần chạy này.
        output_dir: Thư mục gốc lưu kết quả output.
        run_id: ID phiên chạy (nếu không truyền sẽ tự sinh UUID hex).
        job_ids: Tùy chọn chỉ định trước danh sách job_id (nếu không truyền sẽ tự sinh 8 ký tự hex).

    Returns:
        JobPlan hoàn chỉnh chứa danh sách JobSpec.

    Raises:
        JobValidationError: Nếu mode hoặc dữ liệu đầu vào không hợp lệ.
        JobCapacityExceededError: Nếu số output vượt quá sức chứa của mode đã chọn.
    """
    if not engine or not isinstance(engine, str):
        raise JobValidationError("Định danh engine không hợp lệ")

    output_list = list(outputs)
    output_count = len(output_list)
    if output_count == 0:
        raise JobValidationError("Danh sách output không được để trống")

    mode_norm = str(mode).strip().lower()
    if mode_norm not in ("single", "batch"):
        raise JobValidationError(
            f"Chế độ chạy không hợp lệ: '{mode}'. Chỉ hỗ trợ 'single' hoặc 'batch'."
        )

    run_id = str(run_id or uuid.uuid4().hex[:12])
    base_out = Path(output_dir or Path("output") / run_id)
    prefs = dict(preferences or {})

    max_per_job = limits.max_outputs_per_job
    max_jobs = limits.max_jobs_per_batch
    batch_capacity = max_per_job * max_jobs

    if mode_norm == "single":
        if output_count > max_per_job:
            raise JobCapacityExceededError(
                f"Chế độ 'single' chỉ cho phép tối đa {max_per_job} outputs mỗi job. "
                f"Đã nhận {output_count} outputs. Hãy chuyển sang chế độ 'batch' "
                f"hoặc giảm số lượng output."
            )
        job_total = 1
        job_id = str(job_ids[0]) if (job_ids and len(job_ids) > 0) else generate_job_id()
        jobs = [
            JobSpec(
                job_id=job_id,
                run_id=run_id,
                job_index=0,
                job_total=job_total,
                engine=engine,
                outputs=output_list,
                preferences=prefs,
                output_dir=base_out / job_id,
            )
        ]
    else:  # mode == "batch"
        job_count = math.ceil(output_count / max_per_job)
        if job_count > max_jobs:
            raise JobCapacityExceededError(
                f"Số job cần tạo ({job_count}) vượt quá giới hạn batch tối đa ({max_jobs} jobs, "
                f"sức chứa tối đa {batch_capacity} outputs). Đã nhận {output_count} outputs."
            )

        jobs = []
        for idx in range(job_count):
            chunk = output_list[idx * max_per_job : (idx + 1) * max_per_job]
            job_id = str(job_ids[idx]) if (job_ids and len(job_ids) > idx) else generate_job_id()
            jobs.append(
                JobSpec(
                    job_id=job_id,
                    run_id=run_id,
                    job_index=idx,
                    job_total=job_count,
                    engine=engine,
                    outputs=chunk,
                    preferences=prefs,
                    output_dir=base_out / job_id,
                )
            )

    return JobPlan(
        run_id=run_id,
        engine=engine,
        mode=mode_norm,
        jobs=jobs,
        limits=limits,
        total_outputs=output_count,
    )
