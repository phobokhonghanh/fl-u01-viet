"""Centralized Configuration and Storage Manager for HDR Engines.

Defines base application storage paths rooted at ~/.hdr_exe (overridable via
HDR_EXE_HOME environment variable) and per-engine directory hierarchies.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def get_app_dir() -> Path:
    """Trả về thư mục gốc cấu hình và dữ liệu của ứng dụng hdr_exe.
    
    Mặc định là ~/.hdr_exe, có thể ghi đè qua biến môi trường HDR_EXE_HOME.
    """
    env_home = os.environ.get("HDR_EXE_HOME")
    if env_home:
        return Path(env_home).resolve()
    return (Path.home() / ".hdr_exe").resolve()


def get_engine_dir(engine: str) -> Path:
    """Trả về thư mục dành riêng cho một engine (ví dụ ~/.hdr_exe/autoenhance)."""
    return get_app_dir() / engine.lower()


def get_token_path(engine: str) -> Path:
    """Trả về đường dẫn file token của một engine (ví dụ ~/.hdr_exe/fotello/token.json)."""
    return get_engine_dir(engine) / "token.json"


def get_api_key_path(engine: str) -> Path:
    """Trả về đường dẫn file API key của một engine (ví dụ ~/.hdr_exe/autoenhance/api_key.json)."""
    return get_engine_dir(engine) / "api_key.json"


def get_metadata_dir(engine: str) -> Path:
    """Trả về thư mục metadata của một engine (ví dụ ~/.hdr_exe/autoenhance/metadata)."""
    return get_engine_dir(engine) / "metadata"


def load_app_config() -> dict[str, Any]:
    """Đọc cấu hình chung từ ~/.hdr_exe/config.json nếu tồn tại."""
    cfg_file = get_app_dir() / "config.json"
    if not cfg_file.is_file():
        return {}
    try:
        with open(cfg_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_engine_config(engine: str) -> dict[str, Any]:
    """Lấy cấu hình cho một engine cụ thể từ config.json."""
    all_cfg = load_app_config()
    engine_cfg = all_cfg.get(engine.lower(), {})
    return engine_cfg if isinstance(engine_cfg, dict) else {}
