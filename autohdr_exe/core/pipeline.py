"""
Pipeline Orchestrator — runs Steps 1-7 in a background thread.

Optimizations:
  - Resource cleanup: session.close() + gc.collect()
  - Log rotation: cap at 500 lines per job
  - Proxy support: pass proxy config to HttpClient
"""

import os
import gc
import uuid
import logging
import threading
import datetime
import json
from typing import List, Optional, Callable
from dataclasses import dataclass, field

from core.http_client import HttpClient
from core.logger import log, setup_logger, setup_job_logger, get_job_log_path
from core.cache import cache
from core.utils import get_checkpoints_dir
from models.schemas import PipelineContext, SessionRecord
from steps import (
    step0_session,
    step1_presigned_urls,
    step2_upload_files,
    step3_finalize_upload,
    step4_associate_and_run,
    step5_poll_status,
    step6_get_processed_urls,
    step7_download_photos,
)

logger = setup_logger("autohdr_pipeline")

MAX_LOG_LINES = 500  # Prevent memory leak from very long jobs


@dataclass
class Job:
    """Represents a single processing job."""
    job_id: str
    address: str
    file_count: int
    status: str = "pending"  # pending, processing, completed, failed, stopped
    error: str = ""
    downloaded_count: int = 0
    stop_requested: bool = False
    log_lines: list = field(default_factory=list)
    output_path: str = ""


class PipelineManager:
    """
    Manages pipeline jobs. Each job runs Steps 1-7 in a background thread.
    """

    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._callbacks: dict[str, dict] = {}

    def update_callbacks(
        self,
        job_id: str,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_job_update: Optional[Callable[["Job"], None]] = None,
    ):
        """Update callbacks for an existing job (useful when screen is recreated)."""
        with self._lock:
            self._callbacks[job_id] = {
                "on_log": on_log,
                "on_job_update": on_job_update
            }

    def create_job(
        self,
        session: SessionRecord,
        file_paths: List[str],
        address: str,
        download_dir: str,
        indoor_model_id: int = 3,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_job_update: Optional[Callable[["Job"], None]] = None,
        proxy_config: Optional[dict] = None,
        outdoor_model_id: Optional[int] = None,
    ) -> Job:
        """Create and start a new pipeline job."""
        job_id = str(uuid.uuid4())[:8]
        job = Job(
            job_id=job_id,
            address=address,
            file_count=len(file_paths),
        )

        with self._lock:
            self.jobs[job_id] = job
            self._callbacks[job_id] = {
                "on_log": on_log,
                "on_job_update": on_job_update
            }

        thread = threading.Thread(
            target=self._run_pipeline,
            args=(job, session, file_paths, address, download_dir, indoor_model_id, proxy_config),
            kwargs={"outdoor_model_id": outdoor_model_id},
            daemon=True,
        )
        thread.start()
        return job

    def resume_job(
        self,
        checkpoint_id: str,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_job_update: Optional[Callable[["Job"], None]] = None,
    ) -> Optional[Job]:
        """Resume a job from a checkpoint file."""
        checkpoint_path = os.path.join(get_checkpoints_dir(), f"{checkpoint_id}.json")
        if not os.path.exists(checkpoint_path):
            return None

        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            cookie = data.get("cookies")
            user_id = data.get("user_id")
            unique_str = data.get("unique_str")
            address = data.get("address")
            download_dir = data.get("download_dir")
            proxy_config = data.get("proxy_config")

            # Reconstruct session
            session = SessionRecord(
                cookie=cookie,
                user_id=user_id,
                email="", # Not strictly needed for steps 5-7
                firstname="",
                lastname="",
                expires="" # Session is assumed valid for the retry
            )

            # Reconstruct context
            context = PipelineContext(
                file_paths=[],
                address=address,
                email="",
                firstname="",
                lastname="",
                user_id=user_id,
                unique_str=unique_str
            )

            job = Job(
                job_id=checkpoint_id,
                address=context.address,
                file_count=0, # Reset count as files are already uploaded
            )

            # Load existing logs from file to show in UI
            log_path = get_job_log_path(checkpoint_id)
            if os.path.exists(log_path):
                try:
                    with open(log_path, "r", encoding="utf-8") as lf:
                        # Keep only the last MAX_LOG_LINES
                        lines = lf.readlines()[-MAX_LOG_LINES:]
                        job.log_lines = [line.strip() for line in lines]
                except Exception:
                    pass

            job.log_lines.append("-" * 40)
            job.log_lines.append(f"RESTARTING JOB: {checkpoint_id}")
            job.log_lines.append(f"Project: {address}")
            job.log_lines.append(f"DownloadDir: {download_dir}")
            job.log_lines.append(f"UniqueStr: {unique_str}")
            job.log_lines.append("-" * 40)

            with self._lock:
                self.jobs[job.job_id] = job
                self._callbacks[job.job_id] = {
                    "on_log": on_log,
                    "on_job_update": on_job_update
                }

            thread = threading.Thread(
                target=self._run_pipeline,
                args=(job, session, [], address, download_dir, 3, proxy_config, context),
                kwargs={"start_step": 5},
                daemon=True,
            )
            thread.start()
            return job
        except Exception as e:
            logger.error(f"Lỗi khi resume job từ checkpoint {checkpoint_id}: {e}")
            return None

    def stop_job(self, job_id: str) -> bool:
        """Request a job to stop."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job and job.status == "processing":
                job.stop_requested = True
                return True
        return False

    def get_job(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def get_all_jobs(self) -> List[Job]:
        return list(self.jobs.values())

    def delete_job_log(self, job_id: str) -> bool:
        """Delete the log file for a job."""
        log_path = get_job_log_path(job_id)
        if os.path.exists(log_path):
            try:
                os.remove(log_path)
                return True
            except Exception:
                pass
        job = self.jobs.get(job_id)
        if job:
            job.log_lines.clear()
        return False

    def get_job_logs(self, job_id: str) -> List[str]:
        """Get log lines for a job from memory."""
        job = self.jobs.get(job_id)
        if job:
            return list(job.log_lines)
        return []

    def get_available_checkpoint_ids(self) -> List[str]:
        """Get list of IDs that have available checkpoints."""
        ids = []
        cp_dir = get_checkpoints_dir()
        if os.path.exists(cp_dir):
            for f in os.listdir(cp_dir):
                if f.endswith(".json"):
                    ids.append(f[:-5])
        return ids
    def load_recoverable_jobs(self) -> List["Job"]:
        """Scan checkpoints and load jobs that are not already in memory."""
        new_jobs = []
        cp_dir = get_checkpoints_dir()
        if not os.path.exists(cp_dir):
            return []

        for f in os.listdir(cp_dir):
            if f.endswith(".json"):
                job_id = f[:-5]
                with self._lock:
                    if job_id in self.jobs:
                        continue

                try:
                    cp_path = os.path.join(cp_dir, f)
                    with open(cp_path, "r", encoding="utf-8") as jf:
                        data = json.load(jf)
                    
                    address = data.get("address", "Khôi phục từ checkpoint")
                    job = Job(
                        job_id=job_id,
                        address=address,
                        file_count=0, # Số lượng ảnh sẽ được cập nhật khi Resume
                        status="failed" # Set as failed so UI shows Restart button
                    )
                    job.error = "Có thể khôi phục"
                    
                    with self._lock:
                        self.jobs[job_id] = job
                    new_jobs.append(job)
                except Exception:
                    continue
        return new_jobs

    def _run_pipeline(
        self,
        job: Job,
        session: SessionRecord,
        file_paths: List[str],
        address: str,
        download_dir: str,
        indoor_model_id: int,
        proxy_config: Optional[dict] = None,
        context: Optional[PipelineContext] = None,
        start_step: int = 1,
        outdoor_model_id: Optional[int] = None,
    ):
        """Execute the full pipeline (Steps 1-7) for a job."""
        job.status = "processing"
        self._notify_update(job)

        job_logger = setup_job_logger(job.job_id)
        client = None  # Will be initialized below

        def _log(level: str, step: int, msg: str):
            """Log to: main logger + job file + job memory + UI callback."""
            log(logger, level, step, msg)
            log(job_logger, level, step, msg)
            now = datetime.datetime.now().strftime("%H:%M:%S")
            formatted = f"[{now}] <{level}: {step}: {msg}>"

            # Log rotation — keep only last MAX_LOG_LINES
            if len(job.log_lines) >= MAX_LOG_LINES:
                job.log_lines.pop(0)
            job.log_lines.append(formatted)

            callbacks = self._callbacks.get(job.job_id, {})
            on_log_cb = callbacks.get("on_log")
            if on_log_cb:
                try:
                    on_log_cb(job.job_id, formatted)
                except Exception:
                    pass

        def check_cancelled():
            return job.stop_requested

        try:
            # Initialize HTTP client
            client = HttpClient(cookie=session.cookie)

            # Apply proxy if configured
            if proxy_config and proxy_config.get("ip"):
                _log("INFO", 0, f"Sử dụng proxy: {proxy_config['ip']}:{proxy_config.get('port', '')}")
                client.set_proxy(
                    ip=proxy_config["ip"],
                    port=proxy_config.get("port", ""),
                    user=proxy_config.get("user", ""),
                    password=proxy_config.get("password", ""),
                )

            # Build pipeline context if not provided
            if context is None:
                context = PipelineContext(
                    file_paths=file_paths,
                    address=address,
                    email=session.email,
                    firstname=session.firstname,
                    lastname=session.lastname,
                    user_id=session.user_id,
                )

            # Checkpoint management
            can_checkpoint = False

            def _save_checkpoint():
                try:
                    cp_path = os.path.join(get_checkpoints_dir(), f"{job.job_id}.json")
                    cp_data = {
                        "unique_str": context.unique_str,
                        "address": context.address,
                        "user_id": context.user_id,
                        "cookies": session.cookie,
                        "download_dir": download_dir,
                        "proxy_config": proxy_config,
                        "timestamp": datetime.datetime.now().isoformat()
                    }
                    with open(cp_path, "w", encoding="utf-8") as f:
                        json.dump(cp_data, f, ensure_ascii=False, indent=2)
                    _log("INFO", 0, f"Đã lưu checkpoint")
                except Exception as ex:
                    _log("ERROR", 0, f"Lỗi lưu checkpoint: {ex}")

            def _delete_checkpoint():
                try:
                    cp_path = os.path.join(get_checkpoints_dir(), f"{job.job_id}.json")
                    if os.path.exists(cp_path):
                        os.remove(cp_path)
                except Exception:
                    pass

            # === Step 1 ===
            if start_step <= 1:
                _log("INFO", 1, "=== Step 1: Tạo presigned URLs ===")
                if check_cancelled():
                    raise InterruptedError()
                context = step1_presigned_urls.execute(client, context)
                if context is None:
                    raise Exception("Step 1 thất bại: Không tạo được presigned URLs")

            # === Step 2 ===
            if start_step <= 2:
                _log("INFO", 2, f"=== Step 2: Upload {len(context.presigned_urls)} ảnh lên S3 ===")
                if check_cancelled():
                    raise InterruptedError()
                upload_ok = step2_upload_files.execute(client, context, check_cancelled)
                if not upload_ok:
                    raise Exception("Step 2 thất bại: Upload ảnh lỗi")
                # Memory cleanup after upload
                gc.collect()

            # === Step 3 ===
            if start_step <= 3:
                _log("INFO", 3, "=== Step 3: Finalize Upload ===")
                if check_cancelled():
                    raise InterruptedError()
                if not step3_finalize_upload.execute(client, context.unique_str):
                    raise Exception("Step 3 thất bại: Finalize upload lỗi")

            # === Step 4 ===
            if start_step <= 4:
                _log("INFO", 4, "=== Step 4: Kích hoạt xử lý HDR ===")
                if check_cancelled():
                    raise InterruptedError()
                if not step4_associate_and_run.execute(
                    client=client,
                    unique_str=context.unique_str,
                    email=context.email,
                    firstname=context.firstname,
                    lastname=context.lastname,
                    address=context.address,
                    files_count=len(context.filenames),
                    indoor_model_id=indoor_model_id,
                    outdoor_model_id=outdoor_model_id,
                ):
                    raise Exception("Step 4 thất bại: Không kích hoạt được xử lý")
            
            can_checkpoint = True

            # === Step 5 ===
            _log("INFO", 5, "=== Step 5: Chờ server xử lý ===")
            photoshoot_id = step5_poll_status.execute(
                client=client,
                user_id=context.user_id,
                unique_str=context.unique_str,
                address=context.address,
                check_cancelled=check_cancelled,
                on_log=_log,
            )
            if photoshoot_id is None:
                raise Exception("Step 5 thất bại: Server không xử lý xong")
            context.photoshoot_id = photoshoot_id

            # === Step 6 ===
            _log("INFO", 6, "=== Step 6: Lấy URLs ảnh đã xử lý ===")
            if check_cancelled():
                raise InterruptedError()
            context.processed_urls = step6_get_processed_urls.execute(
                client=client,
                photoshoot_id=context.photoshoot_id,
                unique_str=context.unique_str,
                input_filenames=context.filenames,
                on_log=_log,
            )
            if not context.processed_urls:
                raise Exception("Step 6 thất bại: Không tìm thấy ảnh đã xử lý")

            # === Step 7 ===
            _log("INFO", 7, f"=== Step 7: Tải {len(context.processed_urls)} ảnh HDR ===")
            downloaded = step7_download_photos.execute(
                client=client,
                cleaned_urls=context.processed_urls,
                unique_str=context.unique_str,
                download_dir=download_dir,
                check_cancelled=check_cancelled,
                folder_name=job.job_id,
                on_log=_log,
            )

            # Memory cleanup after download
            gc.collect()

            if downloaded:
                job.output_path = os.path.dirname(downloaded[0])

            job.downloaded_count = len(downloaded)
            job.status = "completed"
            _log("INFO", 0, f"=== Pipeline hoàn tất! Đã tải {len(downloaded)} ảnh ===")
            
            # Successful completion -> Delete checkpoint
            _delete_checkpoint()

        except InterruptedError:
            job.status = "stopped"
            _log("WARNING", 0, "Tiến trình đã dừng theo yêu cầu")
        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            _log("ERROR", 0, f"Pipeline lỗi: {e}")
            
            # Save checkpoint if failure occurred after Step 4
            if can_checkpoint:
                _save_checkpoint()
        finally:
            # === Resource Cleanup ===
            # Close HTTP session to release TCP connections
            if client:
                client.close()

            # Close job logger handlers
            for h in job_logger.handlers[:]:
                h.close()
                job_logger.removeHandler(h)

            # Final GC
            gc.collect()

        self._notify_update(job)

    def _notify_update(self, job: Job):
        callbacks = self._callbacks.get(job.job_id, {})
        callback = callbacks.get("on_job_update")
        if callback:
            try:
                callback(job)
            except Exception:
                pass
