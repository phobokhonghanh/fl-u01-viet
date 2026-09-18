"""Shared Jobs Checkpoint Store.

Manages persistent storage of JobPlans, JobRecords, attempts, and step checkpoints
under ~/.hdr_exe/jobs/{run_id}.json with atomic write operations.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from core.shared.config import get_app_dir
from core.shared.jobs.models import (
    JobAttempt,
    JobPlan,
    JobRecord,
)


def _sanitize_preferences(prefs: dict[str, Any]) -> dict[str, Any]:
    """Loại bỏ thông tin nhạy cảm (API key, token) khỏi preferences để tránh rò rỉ vào checkpoint."""
    sanitized = dict(prefs)
    for sensitive_key in ("api_key", "token", "id_token", "access_token", "refresh_token"):
        sanitized.pop(sensitive_key, None)
    return sanitized


def get_jobs_dir() -> Path:
    """Trả về thư mục lưu trữ checkpoint của các job: ~/.hdr_exe/jobs."""
    jobs_dir = get_app_dir() / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return jobs_dir


def get_run_checkpoint_path(run_id: str) -> Path:
    """Trả về đường dẫn file checkpoint cho run_id: ~/.hdr_exe/jobs/{run_id}.json."""
    return get_jobs_dir() / f"{run_id}.json"


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Ghi file JSON nguyên tử bằng file tạm và đổi tên os.replace."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp_path = parent / f".tmp_{os.getpid()}_{time.time_ns()}.json"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        if hasattr(os, "chmod"):
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


class JobStore:
    """Kho lưu trữ trạng thái và checkpoint của các phiên chạy (Run) và tác vụ (Job)."""

    def __init__(self, jobs_dir: Path | None = None) -> None:
        self.jobs_dir = jobs_dir or get_jobs_dir()
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def get_run_path(self, run_id: str) -> Path:
        return self.jobs_dir / f"{run_id}.json"

    def init_run(self, plan: JobPlan) -> None:
        """Khởi tạo file checkpoint cho toàn bộ run_id từ JobPlan ban đầu."""
        run_path = self.get_run_path(plan.run_id)
        jobs_map: dict[str, Any] = {}
        for spec in plan.jobs:
            initial_record = JobRecord(
                job_id=spec.job_id,
                run_id=plan.run_id,
                job_index=spec.job_index,
                job_total=spec.job_total,
                engine=spec.engine,
                status="queued",
                current_attempt=1,
                outputs=spec.outputs,
                preferences=_sanitize_preferences(spec.preferences),
                output_dir=spec.output_dir,
                attempts=[],
                server_resources={},
                latest_step="init",
            )
            jobs_map[spec.job_id] = initial_record.to_dict()

        doc: dict[str, Any] = {
            "run_id": plan.run_id,
            "engine": plan.engine,
            "mode": plan.mode,
            "created_at": plan.created_at,
            "status": "queued",
            "limits": plan.limits.to_dict(),
            "total_outputs": plan.total_outputs,
            "total_jobs": len(plan.jobs),
            "jobs": jobs_map,
        }
        _atomic_write_json(run_path, doc)

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        """Đọc tài liệu run từ checkpoint."""
        path = self.get_run_path(run_id)
        if not path.is_file():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else None
        except Exception:
            return None

    def update_run_status(self, run_id: str, status: str) -> None:
        """Cập nhật trạng thái tổng quan của run."""
        doc = self.load_run(run_id)
        if not doc:
            return
        doc["status"] = status
        doc["updated_at"] = time.time()
        _atomic_write_json(self.get_run_path(run_id), doc)

    def load_job_record(self, run_id: str, job_id: str) -> JobRecord | None:
        """Lấy hồ sơ JobRecord của một job cụ thể."""
        doc = self.load_run(run_id)
        if not doc or "jobs" not in doc:
            return None
        raw_job = doc["jobs"].get(job_id)
        if not raw_job:
            return None
        if isinstance(raw_job, dict) and "preferences" in raw_job:
            raw_job = dict(raw_job)
            raw_job["preferences"] = _sanitize_preferences(raw_job["preferences"])
        return JobRecord.from_dict(raw_job)

    def save_job_record(self, record: JobRecord) -> None:
        """Lưu hoặc cập nhật hồ sơ JobRecord vào checkpoint của run."""
        doc = self.load_run(record.run_id)
        if not doc:
            doc = {
                "run_id": record.run_id,
                "engine": record.engine,
                "created_at": time.time(),
                "status": "running",
                "jobs": {},
            }
        if "jobs" not in doc:
            doc["jobs"] = {}
        rec_dict = record.to_dict()
        rec_dict["preferences"] = _sanitize_preferences(rec_dict.get("preferences", {}))
        doc["jobs"][record.job_id] = rec_dict
        doc["updated_at"] = time.time()
        _atomic_write_json(self.get_run_path(record.run_id), doc)

    def record_step_progress(
        self,
        run_id: str,
        job_id: str,
        step: str,
        status: str,
        *,
        server_resources: dict[str, Any] | None = None,
        error_message: str | None = None,
        cancel_reason: str | None = None,
    ) -> None:
        """Ghi nhận nhanh tiến trình của một step vào job record."""
        record = self.load_job_record(run_id, job_id)
        if not record:
            return
        record.latest_step = step
        record.status = status
        if server_resources:
            record.server_resources.update(server_resources)
        if error_message:
            record.error_message = error_message
        if cancel_reason:
            record.cancel_reason = cancel_reason
        self.save_job_record(record)

    def add_job_attempt(self, run_id: str, job_id: str, attempt: JobAttempt) -> None:
        """Thêm một lần chạy (attempt) vào lịch sử của job.
        
        Quy định: Giữ nguyên checkpoint bước xử lý và server_resources khi xác thực
        bản quyền thất bại tại bước 'activation' trong các lần restart.
        """
        record = self.load_job_record(run_id, job_id)
        if not record:
            return
        record.attempts.append(attempt)
        record.current_attempt = attempt.attempt_no
        record.status = attempt.status
        if not (attempt.step == "activation" and attempt.status == "failed"):
            record.latest_step = attempt.step
            record.cancel_reason = attempt.cancel_reason
            record.error_message = attempt.error_message
            if attempt.server_resources:
                record.server_resources.update(attempt.server_resources)
        else:
            record.error_message = attempt.error_message
        self.save_job_record(record)
