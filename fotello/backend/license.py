from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


CACHE_DURATION = 12 * 60 * 60
DEFAULT_API_BASE = "https://u01-viet-backend.up.railway.app"


def get_app_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        app_dir = Path(base) / "FotelloClient"
    else:
        app_dir = Path.home() / ".fotello"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


CACHE_FILE = get_app_data_dir() / "license_cache.json"


def get_machine_id() -> str:
    node = str(uuid.getnode())
    hostname = platform.node()
    entropy = f"{node}-{hostname}-{platform.machine()}"
    return hashlib.sha256(entropy.encode()).hexdigest()[:16]


def _load_cache() -> dict[str, Any]:
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(data: dict[str, Any]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def clear_license_cache() -> None:
    try:
        CACHE_FILE.unlink(missing_ok=True)
    except Exception:
        _save_cache({})


@dataclass
class LicenseResult:
    ok: bool
    msg: str = ""
    cached: bool = False
    machine_id: str = ""
    key: str = ""
    data: dict[str, Any] | None = None


class LicenseClient:
    def __init__(self, base_url: str | None = None):
        base_url = base_url or os.getenv("AUTOHDR_API_BASE") or DEFAULT_API_BASE
        self.base_url = base_url.rstrip("/")

    def cached_key(self) -> str:
        return str(_load_cache().get("active_key") or "")

    def status(self) -> dict[str, Any]:
        cache = _load_cache()
        machine_id = get_machine_id()
        last_check = float(cache.get("license_last_check") or 0)
        active_key = str(cache.get("active_key") or "")
        cached_machine_id = str(cache.get("license_machine_id") or "")
        valid_cache = bool(
            active_key
            and cached_machine_id == machine_id
            and (time.time() - last_check) < CACHE_DURATION
        )
        return {
            "ok": valid_cache,
            "has_key": bool(active_key),
            "machine_id": machine_id,
            "key": active_key,
            "cached": valid_cache,
            "message": str(cache.get("license_message") or ""),
            "last_check": last_check,
        }

    def check_key(self, key: str, machine_id: str | None = None, use_cache: bool = True) -> LicenseResult:
        key = (key or "").strip()
        machine_id = machine_id or get_machine_id()
        if not key:
            return LicenseResult(False, "Vui lòng nhập license key", machine_id=machine_id)

        cache = _load_cache()
        now = time.time()
        if use_cache:
            last_check = float(cache.get("license_last_check") or 0)
            if (
                cache.get("active_key") == key
                and cache.get("license_machine_id") == machine_id
                and (now - last_check) < CACHE_DURATION
            ):
                return LicenseResult(
                    True,
                    str(cache.get("license_message") or "License đã được kích hoạt"),
                    cached=True,
                    machine_id=machine_id,
                    key=key,
                    data=cache,
                )

        try:
            res = requests.post(
                f"{self.base_url}/api/key/active",
                json={"key": key, "machine_id": machine_id},
                timeout=15,
            )
            if res.status_code == 403:
                clear_license_cache()
                return LicenseResult(False, "Key không hợp lệ hoặc đã gắn với máy khác", machine_id=machine_id)
            res.raise_for_status()
            data = res.json() if res.content else {}
            is_valid = bool(data.get("valid", False)) if isinstance(data, dict) else False
            message = ""
            if isinstance(data, dict):
                message = str(data.get("message") or data.get("msg") or "")
            if is_valid:
                payload = {
                    "active_key": key,
                    "license_last_check": now,
                    "license_machine_id": machine_id,
                    "license_message": message or "Kích hoạt thành công",
                    "license_data": data,
                }
                _save_cache(payload)
                return LicenseResult(True, payload["license_message"], machine_id=machine_id, key=key, data=data)
            clear_license_cache()
            return LicenseResult(False, message or "Key không hợp lệ hoặc hết hạn", machine_id=machine_id, data=data)
        except requests.exceptions.ConnectionError:
            return LicenseResult(False, "Không thể kết nối server kích hoạt", machine_id=machine_id)
        except Exception as exc:
            return LicenseResult(False, f"Lỗi kiểm tra license: {exc}", machine_id=machine_id)

    def ensure_active(self) -> LicenseResult:
        key = self.cached_key()
        if not key:
            return LicenseResult(False, "Chưa kích hoạt license", machine_id=get_machine_id())
        return self.check_key(key, get_machine_id(), use_cache=True)
