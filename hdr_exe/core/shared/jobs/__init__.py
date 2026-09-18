"""Shared Jobs Subsystem.

Provides generic capacity planning, sequential execution, state tracking,
checkpointing, and manual restart functionality for HDR engines.
"""
from __future__ import annotations

from core.shared.jobs.models import (
    BatchResult,
    JobAttempt,
    JobCapacityExceededError,
    JobError,
    JobExecutionError,
    JobLimits,
    JobPlan,
    JobRecord,
    JobResult,
    JobSpec,
    JobValidationError,
    OutputSpec,
)
from core.shared.jobs.planner import generate_job_id, plan_jobs
from core.shared.jobs.runner import JobContext, restart_job, run_jobs
from core.shared.jobs.store import JobStore, get_jobs_dir, get_run_checkpoint_path

__all__ = [
    "BatchResult",
    "JobAttempt",
    "JobCapacityExceededError",
    "JobContext",
    "JobError",
    "JobExecutionError",
    "JobLimits",
    "JobPlan",
    "JobRecord",
    "JobResult",
    "JobSpec",
    "JobStore",
    "JobValidationError",
    "OutputSpec",
    "generate_job_id",
    "get_jobs_dir",
    "get_run_checkpoint_path",
    "plan_jobs",
    "restart_job",
    "run_jobs",
]
