from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .constants import MAX_RETRIES


LogFn = Callable[[str, str], None] | None


def noop_log(msg: str, msg_type: str = "") -> None:
    _ = (msg, msg_type)


_request_logger: LogFn = None


def set_request_logger(log: LogFn) -> None:
    global _request_logger
    _request_logger = log


def request_logger() -> LogFn:
    return _request_logger


def print_system_exception(context: str, exc: BaseException | None = None) -> None:
    if exc is None:
        print(f"[Fotello][EXCEPTION] {context}")
        return
    print(f"[Fotello][EXCEPTION] {context}: {type(exc).__name__}: {exc}")


def _request_context(req: urllib.request.Request) -> str:
    return f"{req.get_method()} {req.full_url}"


def _read_error_body(exc: urllib.error.HTTPError, limit: int = 1200) -> str:
    try:
        body = exc.read(limit)
    except Exception as read_exc:
        print_system_exception("client._read_error_body", read_exc)
        return ""
    if not body:
        return ""
    return body.decode("utf-8", errors="replace").replace("\n", " ")[:limit]


def _log_bad_response(context: str, status: int | str, body: str = "") -> None:
    if not _request_logger:
        return
    detail = f"HTTP request failed: {context} -> status={status}"
    if body:
        detail += f" body={body}"
    _request_logger(detail, "error")


def open_checked(req: urllib.request.Request, timeout: int):
    context = _request_context(req)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        print_system_exception(f"client.open_checked HTTPError: {context}", exc)
        _log_bad_response(context, exc.code, _read_error_body(exc))
        raise
    except Exception as exc:
        print_system_exception(f"client.open_checked error: {context}", exc)
        _log_bad_response(context, "error", str(exc))
        raise

    status = getattr(resp, "status", None) or getattr(resp, "code", None)
    if isinstance(status, int) and not 200 <= status < 300:
        _log_bad_response(context, status)
        try:
            resp.close()
        except Exception as close_exc:
            print_system_exception(f"client.open_checked close failed: {context}", close_exc)
            pass
        raise RuntimeError(f"HTTP status {status} for {context}")
    return resp


def retry(fn: Callable[[], Any], max_retries: int = MAX_RETRIES) -> Any:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            return fn()
        except urllib.error.HTTPError as exc:
            print_system_exception(f"client.retry HTTPError attempt={attempt + 1}", exc)
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == max_retries - 1:
                raise
            time.sleep(min(2**attempt, 30))
        except Exception as exc:
            print_system_exception(f"client.retry error attempt={attempt + 1}", exc)
            last_error = exc
            if attempt == max_retries - 1:
                raise
            time.sleep(min(2**attempt, 30))
    if last_error:
        raise last_error
    raise RuntimeError("retry failed")


def json_request(req: urllib.request.Request, timeout: int = 15) -> Any:
    with open_checked(req, timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
