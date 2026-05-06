"""
Retry module with exponential backoff.

Provides a reusable retry mechanism used by pipeline steps.
The backoff follows the spec:
    - Initial delay: 15 seconds
    - After each retry: delay *= 3/2 (1.5x)
    - Maximum retries: 5 (default)
"""

import time
import logging
from typing import Callable, TypeVar, Optional, Any

from core.logger import log

T = TypeVar("T")


def retry_with_backoff(
    func: Callable[..., T],
    logger: logging.Logger,
    step: int,
    max_retries: int = 10,
    initial_delay: float = 15.0,
    backoff_factor: float = 1.5,
    on_retry_message: Optional[str] = None,
    retry_if_falsy: bool = False,
    check_cancelled: Optional[Callable[[], bool]] = None,
    *args: Any,
    **kwargs: Any,
) -> Optional[T]:
    """
    Execute a function with retry logic and exponential backoff.

    Args:
        check_cancelled: Optional callback that returns True if the job should be aborted.
    """
    delay = initial_delay
    last_exception = None

    for attempt in range(1, max_retries + 1):
        if check_cancelled and check_cancelled():
            log(logger, "WARNING", step, "Tiến trình bị hủy bởi người dùng.")
            raise InterruptedError("Job stopped by user")

        try:
            result = func(*args, **kwargs)
            
            if retry_if_falsy and not result:
                raise ValueError(f"Falsy result: {result}")
                
            return result
        except InterruptedError:
            raise
        except Exception as e:
            last_exception = e
            minutes_approx = delay / 60
            retry_msg = on_retry_message or "Đang retry"
            log(
                logger,
                "INFO",
                step,
                f"{retry_msg} - Lần thử {attempt}/{max_retries}, "
                f"đợi {delay:.0f}s (~{minutes_approx:.1f} phút) | Lỗi: {e}",
            )
            
            if attempt < max_retries:
                # Sleep in small chunks to check for cancellation faster
                sleep_start = time.time()
                while time.time() - sleep_start < delay:
                    if check_cancelled and check_cancelled():
                        log(logger, "WARNING", step, "Tiến trình bị hủy trong lúc chờ retry.")
                        raise InterruptedError("Job stopped by user")
                    time.sleep(1)
                delay *= backoff_factor

    log(
        logger,
        "ERROR",
        step,
        f"Đã retry {max_retries} lần nhưng vẫn thất bại. Lỗi cuối: {last_exception}",
    )
    return None

