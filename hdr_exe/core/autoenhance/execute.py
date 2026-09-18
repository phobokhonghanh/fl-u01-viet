"""Autoenhance AI Processing Execution.

Maps caller options to Autoenhance v3 process payloads and triggers
the AI enhancement pipeline via POST /orders/{id}/process.
"""
from __future__ import annotations

from typing import Any, Callable

import requests

from core.autoenhance.constants import DEFAULT_PROCESS_OPTIONS, PRESET_MAP
from core.autoenhance.client import _api_post


from core.autoenhance.options import map_user_options_to_api_payload


def map_options_to_payload(
    options: dict[str, Any] | None,
    log_fn: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Ánh xạ các tùy chọn sang payload hợp lệ cho Autoenhance v3."""
    return map_user_options_to_api_payload(options, log_fn=log_fn)


def trigger_process(
    api_key: str,
    order_id: str,
    payload: dict[str, Any],
    log_fn: Callable[[str, str], None] | None = None,
    session: requests.Session | None = None,
) -> bool:
    """Gửi yêu cầu kích hoạt xử lý AI trên Autoenhance."""
    if log_fn:
        log_fn("[Autoenhance][Execute] Đang gửi yêu cầu kích hoạt xử lý AI...", "info")

    sess = session or requests.Session()
    try:
        _api_post(sess, f"/orders/{order_id}/process", payload, api_key)
        return True
    except Exception as e:
        if log_fn:
            log_fn(f"[Autoenhance][Execute] Không kích hoạt được xử lý: {e}", "error")
        return False
