"""Autoenhance Configuration and Capacity Limits Manager.

Manages dedicated Autoenhance configuration and execution capacity limits.
Default: 20 outputs/job, 3 jobs/batch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.shared.config import ConfigurationError, get_engine_config
from core.shared.jobs.models import JobLimits


@dataclass(frozen=True)
class AutoenhanceJobLimitsConfig:
    max_outputs_per_job: int = 20
    max_jobs_per_batch: int = 3

    def to_limits(self) -> JobLimits:
        return JobLimits(
            max_outputs_per_job=self.max_outputs_per_job,
            max_jobs_per_batch=self.max_jobs_per_batch,
        )


@dataclass(frozen=True)
class AutoenhanceConfig:
    jobs: AutoenhanceJobLimitsConfig = field(default_factory=AutoenhanceJobLimitsConfig)


def load_autoenhance_config() -> AutoenhanceConfig:
    """Nạp cấu hình Autoenhance từ config tổng (~/.hdr_exe/config.json)."""
    engine_cfg = get_engine_config("autoenhance")
    if not isinstance(engine_cfg, dict):
        raise ConfigurationError(f"Mục 'autoenhance' phải là JSON object, nhận được {type(engine_cfg).__name__}")

    allowed_sections = {"jobs"}
    for sec in engine_cfg:
        if sec not in allowed_sections:
            raise ConfigurationError(f"autoenhance: mục cấu hình không hợp lệ '{sec}'")

    jobs_cfg = engine_cfg.get("jobs", {})
    if not isinstance(jobs_cfg, dict):
        raise ConfigurationError(f"autoenhance.jobs phải là JSON object, nhận được {type(jobs_cfg).__name__}")

    allowed_jobs_keys = {"max_outputs_per_job", "max_jobs_per_batch"}
    for k in jobs_cfg:
        if k not in allowed_jobs_keys:
            raise ConfigurationError(f"autoenhance.jobs: khóa không hợp lệ '{k}'")

    max_outputs = 20
    if "max_outputs_per_job" in jobs_cfg:
        val = jobs_cfg["max_outputs_per_job"]
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ConfigurationError(f"autoenhance.jobs.max_outputs_per_job phải là số nguyên > 0, nhận được {val!r}")
        max_outputs = val

    max_jobs = 3
    if "max_jobs_per_batch" in jobs_cfg:
        val = jobs_cfg["max_jobs_per_batch"]
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ConfigurationError(f"autoenhance.jobs.max_jobs_per_batch phải là số nguyên > 0, nhận được {val!r}")
        max_jobs = val

    return AutoenhanceConfig(
        jobs=AutoenhanceJobLimitsConfig(
            max_outputs_per_job=max_outputs,
            max_jobs_per_batch=max_jobs,
        )
    )
