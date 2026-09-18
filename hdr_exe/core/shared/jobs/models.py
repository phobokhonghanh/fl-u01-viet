"""Shared Jobs Data Models and Schemas.

Defines standardized data structures for job planning, execution tracking,
checkpointing, and result aggregation across all HDR engines.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class JobError(Exception):
    """Ngoại lệ cơ bản cho hệ thống Job."""
    pass


class JobCapacityExceededError(JobError):
    """Ngoại lệ báo vượt quá sức chứa cho phép của chế độ single hoặc batch."""
    pass


class JobValidationError(JobError):
    """Ngoại lệ dữ liệu đầu vào hoặc cấu hình job không hợp lệ."""
    pass


class JobExecutionError(JobError):
    """Ngoại lệ trong quá trình thực thi job."""
    pass


@dataclass(frozen=True)
class OutputSpec:
    """Đặc tả một output logic đầu ra (tương ứng 1 ảnh thành phẩm hoặc 1 nhóm bracket)."""
    output_id: str
    input_files: list[Path]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_id": self.output_id,
            "input_files": [str(p) for p in self.input_files],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutputSpec:
        return cls(
            output_id=str(data["output_id"]),
            input_files=[Path(p) for p in data.get("input_files", [])],
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class JobLimits:
    """Giới hạn sức chứa của engine cho một lần chạy."""
    max_outputs_per_job: int
    max_jobs_per_batch: int

    def __post_init__(self) -> None:
        if self.max_outputs_per_job <= 0:
            raise JobValidationError("max_outputs_per_job phải lớn hơn 0")
        if self.max_jobs_per_batch <= 0:
            raise JobValidationError("max_jobs_per_batch phải lớn hơn 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_outputs_per_job": self.max_outputs_per_job,
            "max_jobs_per_batch": self.max_jobs_per_batch,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobLimits:
        return cls(
            max_outputs_per_job=int(data["max_outputs_per_job"]),
            max_jobs_per_batch=int(data["max_jobs_per_batch"]),
        )


@dataclass(frozen=True)
class JobSpec:
    """Đặc tả một job đơn lẻ trong một plan."""
    job_id: str
    run_id: str
    job_index: int
    job_total: int
    engine: str
    outputs: list[OutputSpec]
    preferences: dict[str, Any]
    output_dir: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "job_index": self.job_index,
            "job_total": self.job_total,
            "engine": self.engine,
            "outputs": [o.to_dict() for o in self.outputs],
            "preferences": dict(self.preferences),
            "output_dir": str(self.output_dir),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobSpec:
        return cls(
            job_id=str(data["job_id"]),
            run_id=str(data["run_id"]),
            job_index=int(data["job_index"]),
            job_total=int(data["job_total"]),
            engine=str(data["engine"]),
            outputs=[OutputSpec.from_dict(o) for o in data.get("outputs", [])],
            preferences=dict(data.get("preferences", {})),
            output_dir=Path(data["output_dir"]),
        )


@dataclass
class JobAttempt:
    """Lưu lại thông tin chi tiết của một lần chạy (attempt) cho một job."""
    attempt_no: int
    status: str  # 'running' | 'success' | 'partial' | 'failed' | 'cancelled'
    step: str
    started_at: float
    finished_at: float | None = None
    cancel_reason: str | None = None
    error_message: str | None = None
    server_resources: dict[str, Any] = field(default_factory=dict)
    outputs_succeeded: list[str] = field(default_factory=list)
    outputs_failed: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_no": self.attempt_no,
            "status": self.status,
            "step": self.step,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancel_reason": self.cancel_reason,
            "error_message": self.error_message,
            "server_resources": dict(self.server_resources),
            "outputs_succeeded": list(self.outputs_succeeded),
            "outputs_failed": list(self.outputs_failed),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobAttempt:
        return cls(
            attempt_no=int(data["attempt_no"]),
            status=str(data["status"]),
            step=str(data.get("step", "")),
            started_at=float(data["started_at"]),
            finished_at=float(data["finished_at"]) if data.get("finished_at") is not None else None,
            cancel_reason=data.get("cancel_reason"),
            error_message=data.get("error_message"),
            server_resources=dict(data.get("server_resources", {})),
            outputs_succeeded=list(data.get("outputs_succeeded", [])),
            outputs_failed=list(data.get("outputs_failed", [])),
        )


@dataclass
class JobRecord:
    """Hồ sơ lưu trữ trạng thái của một job trong checkpoint store."""
    job_id: str
    run_id: str
    job_index: int
    job_total: int
    engine: str
    status: str
    outputs: list[OutputSpec]
    preferences: dict[str, Any]
    output_dir: Path
    current_attempt: int = 1
    attempts: list[JobAttempt] = field(default_factory=list)
    server_resources: dict[str, Any] = field(default_factory=dict)
    latest_step: str = ""
    cancel_reason: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "job_index": self.job_index,
            "job_total": self.job_total,
            "engine": self.engine,
            "status": self.status,
            "current_attempt": self.current_attempt,
            "outputs": [o.to_dict() for o in self.outputs],
            "preferences": dict(self.preferences),
            "output_dir": str(self.output_dir),
            "attempts": [a.to_dict() for a in self.attempts],
            "server_resources": dict(self.server_resources),
            "latest_step": self.latest_step,
            "cancel_reason": self.cancel_reason,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobRecord:
        return cls(
            job_id=str(data["job_id"]),
            run_id=str(data["run_id"]),
            job_index=int(data["job_index"]),
            job_total=int(data["job_total"]),
            engine=str(data["engine"]),
            status=str(data["status"]),
            current_attempt=int(data.get("current_attempt", 1)),
            outputs=[OutputSpec.from_dict(o) for o in data.get("outputs", [])],
            preferences=dict(data.get("preferences", {})),
            output_dir=Path(data["output_dir"]),
            attempts=[JobAttempt.from_dict(a) for a in data.get("attempts", [])],
            server_resources=dict(data.get("server_resources", {})),
            latest_step=str(data.get("latest_step", "")),
            cancel_reason=data.get("cancel_reason"),
            error_message=data.get("error_message"),
        )


@dataclass(frozen=True)
class JobPlan:
    """Kế hoạch thực thi gồm các job được chia theo giới hạn."""
    run_id: str
    engine: str
    mode: str  # 'single' | 'batch'
    jobs: list[JobSpec]
    limits: JobLimits
    total_outputs: int
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "engine": self.engine,
            "mode": self.mode,
            "jobs": [j.to_dict() for j in self.jobs],
            "limits": self.limits.to_dict(),
            "total_outputs": self.total_outputs,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobPlan:
        return cls(
            run_id=str(data["run_id"]),
            engine=str(data["engine"]),
            mode=str(data["mode"]),
            jobs=[JobSpec.from_dict(j) for j in data.get("jobs", [])],
            limits=JobLimits.from_dict(data["limits"]),
            total_outputs=int(data["total_outputs"]),
            created_at=float(data.get("created_at", time.time())),
        )


@dataclass
class JobResult:
    """Kết quả trả về của một job sau khi thực thi."""
    job_id: str
    status: str  # 'success' | 'partial' | 'failed' | 'cancelled'
    attempt_no: int
    step: str
    outputs_succeeded: list[str] = field(default_factory=list)
    outputs_failed: list[dict[str, Any]] = field(default_factory=list)
    server_resources: dict[str, Any] = field(default_factory=dict)
    cancel_reason: str | None = None
    error_message: str | None = None
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "attempt_no": self.attempt_no,
            "step": self.step,
            "outputs_succeeded": list(self.outputs_succeeded),
            "outputs_failed": list(self.outputs_failed),
            "server_resources": dict(self.server_resources),
            "cancel_reason": self.cancel_reason,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class BatchResult:
    """Kết quả tổng hợp của toàn bộ batch run."""
    run_id: str
    engine: str
    mode: str
    status: str  # 'success' | 'partial' | 'failed' | 'cancelled'
    job_results: list[JobResult]
    total_jobs: int
    succeeded_jobs: int
    failed_jobs: int
    cancelled_jobs: int
    total_outputs: int
    succeeded_outputs: int
    failed_outputs: int
    cancelled_outputs: int
    started_at: float
    finished_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "engine": self.engine,
            "mode": self.mode,
            "status": self.status,
            "job_results": [j.to_dict() for j in self.job_results],
            "total_jobs": self.total_jobs,
            "succeeded_jobs": self.succeeded_jobs,
            "failed_jobs": self.failed_jobs,
            "cancelled_jobs": self.cancelled_jobs,
            "total_outputs": self.total_outputs,
            "succeeded_outputs": self.succeeded_outputs,
            "failed_outputs": self.failed_outputs,
            "cancelled_outputs": self.cancelled_outputs,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
