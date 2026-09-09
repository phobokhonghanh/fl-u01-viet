"""Autoenhance AI Processing Execution.

Maps caller options to Autoenhance v3 process payloads and triggers
the AI enhancement pipeline via POST /orders/{id}/process.
"""
from __future__ import annotations

from typing import Any, Callable

import requests

from core.autoenhance.constants import DEFAULT_PROCESS_OPTIONS, PRESET_MAP
from core.autoenhance.client import _api_post


def map_options_to_payload(
    options: dict[str, Any] | None,
    log_fn: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Ánh xạ các tùy chọn sang payload hợp lệ cho Autoenhance v3."""
    opts = options or {}
    process_payload: dict[str, Any] = {
        k: opts.get(k, default_val)
        for k, default_val in DEFAULT_PROCESS_OPTIONS.items()
    }

    # Ánh xạ preset phong cách
    preset_choice = str(opts.get("preset", "")).lower()
    if preset_choice in PRESET_MAP:
        process_payload["preset_id"] = PRESET_MAP[preset_choice]
        if log_fn:
            log_fn(f"Đã chọn preset: {preset_choice.capitalize()}", "info")
    elif opts.get("preset_id"):
        process_payload["preset_id"] = opts["preset_id"]

    return process_payload


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
