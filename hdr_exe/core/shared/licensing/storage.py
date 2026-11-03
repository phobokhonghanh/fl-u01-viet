"""Secure Local Storage for Engine License Keys.

Manages persistent storage of license keys at ~/.hdr_exe/licensing/keys.json
using atomic file replacement and strict file permissions.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore

try:
    import msvcrt
except ImportError:
    msvcrt = None  # type: ignore

from core.shared.config import get_licensing_keys_path
from core.shared.licensing.models import CODE_STORAGE_ERROR, LicensingError


@contextmanager
def _file_lock(lock_path: Path):
    """Quản lý khóa độc quyền file cho thao tác đọc-sửa-ghi file keys (cross-platform)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as f:
        if fcntl is not None and hasattr(fcntl, "flock"):
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        elif msvcrt is not None and hasattr(msvcrt, "locking"):
            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                yield
            finally:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except (OSError, ValueError):
                    pass
        else:
            yield


def _atomic_write_keys(path: Path, data: dict[str, str]) -> None:
    """Ghi file keys JSON an toàn nguyên tử và đặt quyền truy cập nghiêm ngặt (0o600 ngay khi tạo)."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "chmod"):
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass

    temp_path = parent / f".tmp_keys_{os.getpid()}_{time.time_ns()}.json"
    try:
        fd = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def load_keys() -> dict[str, str]:
    """Đọc danh sách các key đã lưu từ ~/.hdr_exe/licensing/keys.json."""
    path = get_licensing_keys_path()
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise LicensingError(
            f"File lưu trữ key {path} không đúng định dạng JSON: {exc}",
            code=CODE_STORAGE_ERROR,
        ) from exc
    except Exception as exc:
        raise LicensingError(
            f"Không thể đọc file lưu trữ key {path}: {exc}",
            code=CODE_STORAGE_ERROR,
        ) from exc

    if not isinstance(data, dict):
        raise LicensingError(
            f"File lưu trữ key {path} phải chứa một JSON object, nhận được {type(data).__name__}",
            code=CODE_STORAGE_ERROR,
        )

    return {str(k).strip().lower(): str(v).strip() for k, v in data.items() if v}


def get_key(engine: str) -> str | None:
    """Lấy key đã lưu cho một engine cụ thể."""
    keys = load_keys()
    return keys.get(engine.strip().lower())


def set_key(engine: str, key: str) -> None:
    """Lưu key cho một engine vào file ~/.hdr_exe/licensing/keys.json có khóa file."""
    eng_norm = engine.strip().lower()
    key_norm = key.strip()
    path = get_licensing_keys_path()
    with _file_lock(path.parent / ".keys.lock"):
        keys = load_keys()
        keys[eng_norm] = key_norm
        _atomic_write_keys(path, keys)


def remove_key(engine: str) -> None:
    """Xóa key của engine khỏi file lưu trữ cục bộ có khóa file."""
    eng_norm = engine.strip().lower()
    path = get_licensing_keys_path()
    with _file_lock(path.parent / ".keys.lock"):
        keys = load_keys()
        if eng_norm in keys:
            del keys[eng_norm]
            _atomic_write_keys(path, keys)
