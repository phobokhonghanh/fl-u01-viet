"""Autoenhance Authentication & API Key Management.

Handles persistent API key storage in ~/.hdr_exe/autoenhance/api_key.json
and verification against Autoenhance API v3.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import requests

from core.autoenhance.constants import STORAGE_FILE, VALIDATION_ENDPOINT
from core.autoenhance.client import _api_get


def save_api_key(api_key: str) -> None:
    """Lưu API key Autoenhance vào file JSON cấu hình cục bộ."""
    STORAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump({"api_key": api_key, "updated_at": time.time()}, f, indent=2)


def load_api_key() -> str | None:
    """Đọc API key Autoenhance từ file cấu hình cục bộ nếu tồn tại."""
    if STORAGE_FILE.is_file():
        try:
            with open(STORAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("api_key")
        except Exception:
            return None
    return None


def clear_api_key() -> None:
    """Xóa file API key Autoenhance khỏi cấu hình cục bộ."""
    if STORAGE_FILE.is_file():
        try:
            STORAGE_FILE.unlink(missing_ok=True)
        except Exception:
            pass


def validate_api_key(
    api_key: str | None = None,
    log_fn: Callable[[str, str], None] | None = None,
) -> bool:
    """Xác thực API key của Autoenhance thông qua endpoint /orders/?per_page=1."""
    if api_key is None:
        api_key = load_api_key()
    if not api_key or not api_key.strip():
        if log_fn:
            log_fn("API key trống", "error")
        return False

    try:
        with requests.Session() as sess:
            _api_get(sess, VALIDATION_ENDPOINT, api_key)
            if log_fn:
                log_fn("Xác thực API key Autoenhance thành công.", "success")
            return True
    except Exception as e:
        if log_fn:
            log_fn(f"Lỗi xác thực API key Autoenhance: {e}", "error")
        return False
