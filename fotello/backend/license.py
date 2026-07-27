from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
import uuid
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .client import print_system_exception
from .constants import CLIENT_VERSION


CACHE_DURATION = 12 * 60 * 60
DEFAULT_API_BASE = "https://u01-viet-backend.up.railway.app"
LICENSE_PRODUCT = "fotello"


def get_app_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        app_dir = Path(base) / "FotelloClient"
    else:
        app_dir = Path.home() / ".fotello"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


CACHE_FILE = get_app_data_dir() / "license_cache.json"


def _get_raw_machine_id() -> str:
    """
    Retrieves a stable, hardware/OS-level machine identifier independent of network interfaces.
    """
    # 1. Windows: Read MachineGuid from Registry (HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Cryptography)
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY
            )
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            winreg.CloseKey(key)
            if guid and isinstance(guid, str) and len(guid.strip()) > 0:
                return guid.strip()
        except Exception:
            pass

        try:
            cmd = 'powershell -command "(Get-CimInstance -Class Win32_ComputerSystemProduct).UUID"'
            output = subprocess.check_output(cmd, shell=True, text=True, timeout=3).strip()
            if output and len(output) > 10:
                return output
        except Exception:
            pass

    # 2. Linux: Read /etc/machine-id or /var/lib/dbus/machine-id or /sys/class/dmi/id/product_uuid
    elif sys.platform.startswith("linux"):
        for path in ["/etc/machine-id", "/var/lib/dbus/machine-id", "/sys/class/dmi/id/product_uuid"]:
            try:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            return content
            except Exception:
                pass

    # 3. macOS: Read IOPlatformUUID via ioreg
    elif sys.platform == "darwin":
        try:
            cmd = "ioreg -rd1 -c IOPlatformExpertDevice"
            output = subprocess.check_output(cmd, shell=True, text=True, timeout=3)
            for line in output.splitlines():
                if "IOPlatformUUID" in line:
                    parts = line.split("=")
                    if len(parts) > 1:
                        val = parts[1].replace('"', "").strip()
                        if val:
                            return val
        except Exception:
            pass

    # 4. Fallback: Save a persistent machine UUID file in app data directory
    try:
        app_dir = get_app_data_dir() if callable(globals().get("get_app_data_dir")) else os.path.expanduser("~")
        fallback_file = os.path.join(str(app_dir), ".device_id")
        if os.path.exists(fallback_file):
            with open(fallback_file, "r", encoding="utf-8") as f:
                saved_id = f.read().strip()
                if saved_id:
                    return saved_id

        new_id = f"{platform.node()}-{uuid.uuid4()}"
        with open(fallback_file, "w", encoding="utf-8") as f:
            f.write(new_id)
        return new_id
    except Exception:
        pass

    return f"{platform.node()}-{platform.machine()}"


def get_machine_id() -> str:
    """
    Generate a unique Hardware ID for the current machine.
    Uses hardware/OS level machine identifier to create a stable hash independent of network connection.
    """
    raw_id = _get_raw_machine_id()
    return hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]



def _load_cache() -> dict[str, Any]:
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print_system_exception(f"license._load_cache: {CACHE_FILE}", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(data: dict[str, Any]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def clear_license_cache() -> None:
    try:
        CACHE_FILE.unlink(missing_ok=True)
    except Exception as exc:
        print_system_exception(f"license.clear_license_cache: {CACHE_FILE}", exc)
        _save_cache({})


VALID_KEY_LEVELS = ["lite", "plus"]


def check_level_access(current_level: str, required_level: str) -> bool:
    """
    Kiểm tra xem cấp độ hiện tại có đủ quyền sử dụng tính năng yêu cầu hay không.
    So sánh dựa trên chỉ mục trong danh sách VALID_KEY_LEVELS.
    """
    curr = str(current_level or "lite").strip().lower()
    req = str(required_level or "lite").strip().lower()
    curr = curr if curr in VALID_KEY_LEVELS else "lite"
    req = req if req in VALID_KEY_LEVELS else "lite"
    try:
        return VALID_KEY_LEVELS.index(curr) >= VALID_KEY_LEVELS.index(req)
    except ValueError:
        return False


@dataclass
class LicenseResult:
    ok: bool
    msg: str = ""
    cached: bool = False
    machine_id: str = ""
    key: str = ""
    data: dict[str, Any] | None = None

    @property
    def level(self) -> str:
        if not self.data:
            return "lite"
        # Nếu lấy từ cache, data sẽ có dạng {"license_data": {...}}
        if "license_data" in self.data:
            return self.data.get("license_data", {}).get("level", "lite")
        # Nếu lấy trực tiếp từ API response
        return self.data.get("level", "lite")


class LicenseClient:
    def __init__(self, base_url: str | None = None):
        base_url = base_url or os.getenv("AUTOHDR_API_BASE") or DEFAULT_API_BASE
        self.base_url = base_url.rstrip("/")

    def check_key(
        self,
        key: str | None = None,
        machine_id: str | None = None,
        use_cache: bool = True,
    ) -> LicenseResult:
        cache = _load_cache()
        key_was_provided = key is not None
        key = (key if key is not None else str(cache.get("active_key") or "")).strip()
        machine_id = machine_id or get_machine_id()
        # return LicenseResult(True, "License đã được kích hoạt", machine_id=machine_id, key=key, data={})
        if not key:
            msg = "Vui lòng nhập license key" if key_was_provided else "Chưa kích hoạt license"
            return LicenseResult(False, msg, machine_id=machine_id)

        now = time.time()
        if use_cache:
            try:
                last_check = float(cache.get("license_last_check") or 0)
            except (TypeError, ValueError) as exc:
                print_system_exception("license.check_key invalid license_last_check", exc)
                last_check = 0
            if (
                cache.get("active_key") == key
                and cache.get("license_machine_id") == machine_id
                and cache.get("license_product", LICENSE_PRODUCT) == LICENSE_PRODUCT
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
                json={
                    "key": key,
                    "machine_id": machine_id,
                    "product": LICENSE_PRODUCT,
                    "client_version": CLIENT_VERSION,
                },
                timeout=15,
            )
            if res.status_code == 403:
                clear_license_cache()
                return LicenseResult(False, "Key không hợp lệ hoặc đã gắn với máy khác", machine_id=machine_id)
            res.raise_for_status()
            try:
                data = res.json() if res.content else {}
            except ValueError as exc:
                print_system_exception("license.check_key invalid JSON response", exc)
                return LicenseResult(False, "Server kích hoạt trả response không hợp lệ", machine_id=machine_id, key=key)
            if not isinstance(data, dict):
                return LicenseResult(False, "Server kích hoạt trả response không hợp lệ", machine_id=machine_id, key=key)
            is_valid = bool(data.get("valid", False))
            message = str(data.get("message") or data.get("msg") or "")
            if is_valid:
                pricing = data.get("pricing")
                payload = {
                    "active_key": key,
                    "license_last_check": now,
                    "license_machine_id": machine_id,
                    "license_product": LICENSE_PRODUCT,
                    "license_message": message or "Kích hoạt thành công",
                    "license_data": data,
                }
                if pricing:
                    payload["pricing"] = pricing
                _save_cache(payload)
                return LicenseResult(True, payload["license_message"], machine_id=machine_id, key=key, data=data)
            clear_license_cache()
            return LicenseResult(False, message or "Key không hợp lệ hoặc hết hạn", machine_id=machine_id, key=key, data=data)
        except requests.exceptions.Timeout:
            print_system_exception("license.check_key timeout")
            return LicenseResult(False, "Server kích hoạt phản hồi quá lâu", machine_id=machine_id, key=key)
        except requests.exceptions.ConnectionError as exc:
            print_system_exception("license.check_key connection error", exc)
            return LicenseResult(False, "Không thể kết nối server kích hoạt", machine_id=machine_id, key=key)
        except requests.exceptions.HTTPError as exc:
            print_system_exception("license.check_key HTTP error", exc)
            return LicenseResult(False, f"Server kích hoạt lỗi HTTP: {exc}", machine_id=machine_id, key=key)
        except Exception as exc:
            print_system_exception("license.check_key unexpected error", exc)
            return LicenseResult(False, f"Lỗi kiểm tra license: {exc}", machine_id=machine_id, key=key)

    def get_pricing(self) -> dict[str, str]:
        try:
            cache = _load_cache()
            pricing = cache.get("pricing") or cache.get("license_data", {}).get("pricing")
            if pricing and isinstance(pricing, dict) and "lite" in pricing and "plus" in pricing:
                return pricing
        except Exception as e:
            print_system_exception("license.get_pricing error", e)
        return {
            "lite": "Liên hệ Admin",
            "plus": "Liên hệ Admin"
        }
