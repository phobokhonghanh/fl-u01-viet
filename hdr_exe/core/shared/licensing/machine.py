"""Hardware and Operating System Machine Identification.

Extracts stable OS-level machine identifiers and computes deterministic 16-character
hexadecimal hashes compatible with existing deployed licenses.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys

from core.shared.licensing.models import MachineIdError


def get_raw_machine_id() -> str:
    """Đọc mã định danh phần cứng ổn định từ hệ điều hành.
    
    Hỗ trợ:
    - Windows: MachineGuid từ Registry Cryptography hoặc Win32_ComputerSystemProduct UUID qua PowerShell.
    - Linux: /etc/machine-id, /var/lib/dbus/machine-id, /sys/class/dmi/id/product_uuid.
    - macOS: IOPlatformUUID từ IOPlatformExpertDevice qua ioreg.
    
    Raises:
        MachineIdError: Nếu không thể đọc được ID từ hệ điều hành (không fallback ngẫu nhiên).
    """
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            )
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            winreg.CloseKey(key)
            if guid and isinstance(guid, str) and guid.strip():
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

    elif sys.platform.startswith("linux"):
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id", "/sys/class/dmi/id/product_uuid"):
            try:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            return content
            except Exception:
                pass

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

    raise MachineIdError(
        "Không thể xác định mã máy phần cứng (OS Machine ID). "
        "Vui lòng kiểm tra quyền truy cập hệ thống hoặc cấu hình môi trường."
    )


def get_machine_id() -> str:
    """Tạo mã băm nhận diện máy duy nhất (16 ký tự hex) tương thích với hệ thống cũ."""
    raw_id = get_raw_machine_id()
    return hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
