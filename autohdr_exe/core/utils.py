import uuid
import platform
import hashlib
import os
import sys


import subprocess

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


def get_hwid() -> str:
    """
    Generate a unique Hardware ID for the current machine.
    Uses hardware/OS level machine identifier to create a stable hash independent of network connection.
    """
    raw_id = _get_raw_machine_id()
    return hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]



def get_app_data_dir() -> str:
    """
    Get the application data directory for storing logs, cache, and session data.
    
    Windows: %APPDATA%/AutoHDR/
    Linux/Mac: ~/.autohdr/
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        app_dir = os.path.join(base, "AutoHDR")
    else:
        app_dir = os.path.join(os.path.expanduser("~"), ".autohdr")
    
    os.makedirs(app_dir, exist_ok=True)
    return app_dir


def get_logs_dir() -> str:
    """Get the logs directory inside app data."""
    logs_dir = os.path.join(get_app_data_dir(), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir


def get_checkpoints_dir() -> str:
    """Get the checkpoints directory inside app data."""
    checkpoints_dir = os.path.join(get_app_data_dir(), "checkpoints")
    os.makedirs(checkpoints_dir, exist_ok=True)
    return checkpoints_dir


def get_sessions_dir() -> str:
    """Get the sessions directory inside app data."""
    sessions_dir = os.path.join(get_app_data_dir(), "sessions")
    os.makedirs(sessions_dir, exist_ok=True)
    return sessions_dir


def open_folder(path: str):
    """Open a folder in the system file explorer (Cross-platform)."""
    if not os.path.exists(path):
        return
    
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":  # macOS
        subprocess.run(["open", path])
    else:  # Linux
        subprocess.run(["xdg-open", path])
