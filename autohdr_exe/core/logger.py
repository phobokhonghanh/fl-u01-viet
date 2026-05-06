"""
Logger module for AutoHDR Client EXE v2.

Dual output:
  1. UI widget callback (real-time display)
  2. File log at {APP_DATA_DIR}/logs/{date}.log
  3. Per-job log at {APP_DATA_DIR}/logs/jobs/{job_id}.log

Format: [HH:MM:SS] <LEVEL: STEP: MESSAGE>
"""

import os
import logging
import datetime
from typing import Optional, Callable, List

from core.utils import get_logs_dir


class UILogHandler(logging.Handler):
    """
    Custom handler that forwards log records to a UI callback.
    Thread-safe: the callback should use widget.after() for thread safety.
    """

    def __init__(self, callback: Optional[Callable[[str], None]] = None):
        super().__init__()
        self.callback = callback
        self.records: List[str] = []

    def emit(self, record):
        msg = self.format(record)
        self.records.append(msg)
        if self.callback:
            try:
                self.callback(msg)
            except Exception:
                pass

    def set_callback(self, callback: Callable[[str], None]):
        self.callback = callback


class StepFormatter(logging.Formatter):
    """Format: [HH:MM:SS] <LEVEL: STEP: MESSAGE>"""

    LEVEL_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "ERROR",
    }

    def format(self, record: logging.LogRecord) -> str:
        level_name = self.LEVEL_MAP.get(record.levelno, "INFO")
        step = getattr(record, "step", "?")
        now = datetime.datetime.now().strftime("%H:%M:%S")
        message = record.getMessage()
        return f"[{now}] <{level_name}: {step}: {message}>"


def setup_logger(name: str = "autohdr_exe") -> logging.Logger:
    """
    Setup and return the application logger with file + console handlers.
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # File handler — daily log file
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(get_logs_dir(), f"{today}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(StepFormatter())
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    # Console handler (for development)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(StepFormatter())
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    return logger


def get_job_log_path(job_id: str) -> str:
    """Get the log file path for a specific job."""
    jobs_log_dir = os.path.join(get_logs_dir(), "jobs")
    os.makedirs(jobs_log_dir, exist_ok=True)
    return os.path.join(jobs_log_dir, f"{job_id}.log")


def setup_job_logger(job_id: str) -> logging.Logger:
    """
    Setup a dedicated logger for a specific job.
    Logs go to {APP_DATA_DIR}/logs/jobs/{job_id}.log
    """
    logger_name = f"autohdr_job_{job_id}"
    job_logger = logging.getLogger(logger_name)

    # Prevent propagation to root logger (avoid duplicates)
    job_logger.propagate = False

    if job_logger.handlers:
        return job_logger

    job_logger.setLevel(logging.DEBUG)

    log_path = get_job_log_path(job_id)
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(StepFormatter())
    file_handler.setLevel(logging.DEBUG)
    job_logger.addHandler(file_handler)

    return job_logger


def add_ui_handler(logger: logging.Logger, callback: Optional[Callable] = None) -> UILogHandler:
    """
    Add a UI log handler to the logger.
    Returns the handler so the callback can be updated later.
    """
    ui_handler = UILogHandler(callback)
    ui_handler.setFormatter(StepFormatter())
    ui_handler.setLevel(logging.INFO)
    logger.addHandler(ui_handler)
    return ui_handler


def log(logger: logging.Logger, level: str, step: int, message: str) -> None:
    """
    Convenience function to log with step number.
    """
    level_map = {
        "INFO": logging.INFO,
        "ERROR": logging.ERROR,
        "DEBUG": logging.DEBUG,
        "WARNING": logging.WARNING,
    }
    log_level = level_map.get(level.upper(), logging.INFO)
    logger.log(log_level, message, extra={"step": step})
