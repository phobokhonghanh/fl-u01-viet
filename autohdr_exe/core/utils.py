import uuid
import platform
import hashlib
import os
import sys


import subprocess

def get_hwid() -> str:
    """
    Generate a unique Hardware ID for the current machine.
    Combines MAC address and hostname to create a stable hash.
    """
    node = str(uuid.getnode())
    hostname = platform.node()
    entropy = f"{node}-{hostname}-{platform.machine()}"
    return hashlib.sha256(entropy.encode()).hexdigest()[:16]


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
