"""
Custom logger module for AutoHDR backend.

Log format follows the specification:
    <LEVEL: STEP_NUMBER: MESSAGE>

Levels:
    - INFO: Display information for each step
    - ERROR: Report errors
    - DEBUG: Display debug information without returning results
"""

import os
import logging
from typing import Optional
from contextvars import ContextVar

# Global context for job isolation (v5 fix for mixed logs)
job_id_context: ContextVar[Optional[str]] = ContextVar("job_id", default=None)


class StepFormatter(logging.Formatter):
    """
    Custom log formatter that outputs in the format:
        <LEVEL: STEP_NUMBER: MESSAGE>

    Attributes:
        LEVEL_MAP: Mapping from Python logging levels to custom level names.
    """

    LEVEL_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "INFO",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "ERROR",
    }

    def format(self, record: logging.LogRecord) -> str:
        """
        Format the log record into the custom format.

        Args:
            record: The log record to format.

        Returns:
            Formatted log string in <LEVEL: STEP: MESSAGE> format.
        """
        level_name = self.LEVEL_MAP.get(record.levelno, "INFO")
        # Default to "?" if step attribute is missing (v5 Fix for KeyError)
        step_number = getattr(record, "step", "?")
        message = record.getMessage()
        return f"<{level_name}: {step_number}: {message}>"


# Global LogRecordFactory to inject context into every log record (v5 Fix)
# We ONLY inject job_id here. Step is handled by explicitly passing extra={"step": ...}
# or by the Formatter's default value.
_old_factory = logging.getLogRecordFactory()

def _record_factory(*args, **kwargs):
    record = _old_factory(*args, **kwargs)
    # Inject job_id from contextvars (CRITICAL for isolation)
    record.job_id = job_id_context.get()
    return record

logging.setLogRecordFactory(_record_factory)


def get_logger(name: str, step: Optional[int] = None) -> logging.Logger:
    """
    Get a configured logger with the custom StepFormatter.

    Args:
        name: Logger name (typically module name).
        step: NOT USED globally for factory, legacy parameter.

    Returns:
        Configured logging.Logger instance.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(StepFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    return logger


def log(logger: logging.Logger, level: str, step: int, message: str) -> None:
    """
    Convenience function to log with an explicit step number.

    Args:
        logger: The logger instance to use.
        level: Log level string ("INFO", "ERROR", "DEBUG").
        step: Step number (1-7).
        message: Log message.
    """
    level_map = {
        "INFO": logging.INFO,
        "ERROR": logging.ERROR,
        "DEBUG": logging.DEBUG,
    }
    log_level = level_map.get(level.upper(), logging.INFO)
    logger.log(log_level, message, extra={"step": step})


class LogCollector(logging.Handler):
    """
    Custom logging handler that collects log records in a list.
    Filtered by job_id to prevent mixing logs between concurrent requests.
    """

    def __init__(self, job_id: Optional[str] = None):
        super().__init__()
        self.records = []
        self.job_id = job_id
        self.setFormatter(StepFormatter())

    def emit(self, record):
        # Only collect logs belonging to this specific job
        job_id = getattr(record, "job_id", None)
        if self.job_id and job_id != self.job_id:
            return
        self.records.append(self.format(record))

    def get_logs(self):
        return self.records


def add_file_handler(logger: logging.Logger, log_path: str, mode: str = "a", job_id: Optional[str] = None) -> logging.FileHandler:
    """
    Add a file handler to the logger with job-specific filtering.

    Args:
        logger: The logger instance to add the handler to.
        log_path: Path to the log file.
        mode: File open mode ("w" for overwrite, "a" for append).
        job_id: If provided, only logs for this job will be written to the file.

    Returns:
        The created logging.FileHandler instance.
    """
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    handler = logging.FileHandler(log_path, mode=mode, encoding="utf-8")
    handler.setFormatter(StepFormatter())
    
    if job_id:
        class JobIdFilter(logging.Filter):
            def filter(self, record):
                return getattr(record, "job_id", None) == job_id
        handler.addFilter(JobIdFilter())
        
    logger.addHandler(handler)
    return handler

