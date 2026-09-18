"""Centralized Configuration and Storage Manager for HDR Engines.

Defines base application storage paths rooted strictly at ~/.hdr_exe
and per-engine directory hierarchies.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
    """Ngoại lệ báo lỗi cấu hình không hợp lệ hoặc sai schema."""
    pass


def get_app_dir() -> Path:
    """Trả về thư mục gốc cấu hình và dữ liệu của ứng dụng hdr_exe.
    
    Mặc định là ~/.hdr_exe, có thể ghi đè qua biến môi trường HDR_EXE_HOME khi kiểm thử.
    """
    env_home = os.environ.get("HDR_EXE_HOME")
    if env_home:
        return Path(env_home).resolve()
    return (Path.home() / ".hdr_exe").resolve()


def get_engine_dir(engine: str) -> Path:
    """Trả về thư mục dành riêng cho một engine (ví dụ ~/.hdr_exe/fotello)."""
    return get_app_dir() / engine.lower()


def get_token_path(engine: str) -> Path:
    """Trả về đường dẫn file token của một engine."""
    return get_engine_dir(engine) / "token.json"


def get_tokens_path(engine: str) -> Path:
    """Trả về đường dẫn file tokens đa khóa của một engine (~/.hdr_exe/{engine}/tokens.json)."""
    return get_engine_dir(engine) / "tokens.json"


def get_api_key_path(engine: str) -> Path:
    """Trả về đường dẫn file API key của một engine."""
    return get_engine_dir(engine) / "api_key.json"


def get_metadata_dir(engine: str) -> Path:
    """Trả về thư mục metadata của một engine."""
    return get_engine_dir(engine) / "metadata"


def get_licensing_dir() -> Path:
    """Trả về thư mục cấu hình licensing (~/.hdr_exe/licensing)."""
    return get_app_dir() / "licensing"


def get_licensing_keys_path() -> Path:
    """Trả về đường dẫn file lưu trữ keys (~/.hdr_exe/licensing/keys.json)."""
    return get_licensing_dir() / "keys.json"


ALLOWED_ROOT_KEYS = {"shared", "autoenhance", "fotello", "autohdr", "licensing"}
ALLOWED_SHARED_KEYS = {"chrome_debugging_port", "chrome_user_data_dir", "chrome_path", "timeout_seconds"}
ALLOWED_LICENSING_KEYS = {"server_url", "timeout_seconds"}


def validate_shared_config(shared_cfg: dict[str, Any]) -> None:
    """Kiểm tra schema cấu hình mục shared."""
    for key, val in shared_cfg.items():
        if key not in ALLOWED_SHARED_KEYS:
            raise ConfigurationError(f"shared: khóa không được hỗ trợ '{key}'")
        if key == "chrome_debugging_port" and not (isinstance(val, int) and 1 <= val <= 65535):
            raise ConfigurationError(f"shared.chrome_debugging_port: cổng hợp lệ từ 1-65535, nhận được {val!r}")
        if key == "timeout_seconds" and not (isinstance(val, (int, float)) and val > 0):
            raise ConfigurationError(f"shared.timeout_seconds: thời gian chờ phải lớn hơn 0, nhận được {val!r}")
        if key in ("chrome_user_data_dir", "chrome_path") and not isinstance(val, str):
            raise ConfigurationError(f"shared.{key}: phải là chuỗi đường dẫn, nhận được {type(val).__name__}")


from urllib.parse import urlparse


def validate_licensing_config(licensing_cfg: dict[str, Any]) -> None:
    """Kiểm tra schema cấu hình mục licensing."""
    for key, val in licensing_cfg.items():
        if key not in ALLOWED_LICENSING_KEYS:
            raise ConfigurationError(f"licensing: khóa không được hỗ trợ '{key}'")
        if key == "server_url":
            if not (isinstance(val, str) and val.strip()):
                raise ConfigurationError(f"licensing.server_url: phải là chuỗi URL hợp lệ, nhận được {val!r}")
            parsed = urlparse(val.strip())
            is_localhost = (
                parsed.hostname in ("localhost", "127.0.0.1", "::1")
                or (parsed.netloc and parsed.netloc.startswith("testserver"))
            )
            if parsed.scheme != "https" and not is_localhost:
                raise ConfigurationError(f"licensing.server_url: bắt buộc phải sử dụng giao thức HTTPS, nhận được {val!r}")
        if key == "timeout_seconds" and not (isinstance(val, (int, float)) and val > 0):
            raise ConfigurationError(f"licensing.timeout_seconds: thời gian chờ phải lớn hơn 0, nhận được {val!r}")


def load_app_config() -> dict[str, Any]:
    """Đọc cấu hình chung từ ~/.hdr_exe/config.json.
    
    Nếu file chưa tồn tại: trả về dict mặc định rỗng (các engine tự nạp default tương ứng).
    Nếu file đã tồn tại: kiểm tra cú pháp JSON, kiểu root và khóa root hợp lệ.
    """
    cfg_file = get_app_dir() / "config.json"
    if not cfg_file.is_file():
        return {}
    
    try:
        with open(cfg_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"File cấu hình {cfg_file} không đúng định dạng JSON: {exc}") from exc
    except Exception as exc:
        raise ConfigurationError(f"Không thể đọc file cấu hình {cfg_file}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigurationError(f"Cấu hình {cfg_file} phải là một JSON object, nhận được {type(data).__name__}")

    for root_key in data:
        if root_key not in ALLOWED_ROOT_KEYS:
            raise ConfigurationError(f"Cấu hình root chứa khóa không hợp lệ: '{root_key}'. Hỗ trợ: {sorted(ALLOWED_ROOT_KEYS)}")

    shared_cfg = data.get("shared")
    if shared_cfg is not None:
        if not isinstance(shared_cfg, dict):
            raise ConfigurationError(f"Mục 'shared' phải là JSON object, nhận được {type(shared_cfg).__name__}")
        validate_shared_config(shared_cfg)

    licensing_cfg = data.get("licensing")
    if licensing_cfg is not None:
        if not isinstance(licensing_cfg, dict):
            raise ConfigurationError(f"Mục 'licensing' phải là JSON object, nhận được {type(licensing_cfg).__name__}")
        validate_licensing_config(licensing_cfg)

    return data


def get_engine_config(engine: str) -> dict[str, Any]:
    """Lấy cấu hình cho một engine cụ thể từ config.json."""
    all_cfg = load_app_config()
    engine_cfg = all_cfg.get(engine.lower(), {})
    if not isinstance(engine_cfg, dict):
        raise ConfigurationError(f"Mục '{engine}' trong config phải là JSON object, nhận được {type(engine_cfg).__name__}")
    return engine_cfg
