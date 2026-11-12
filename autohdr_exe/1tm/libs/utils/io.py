"""
Common File I/O Utilities for JSON and local file storage.
Tập trung toàn bộ thao tác đọc/ghi/xóa file JSON an toàn.
"""
import os
import json
import logging
from pathlib import Path
from typing import Any, Union

logger = logging.getLogger(__name__)


def ensure_parent_dir(file_path: Union[str, Path]) -> Path:
    """Đảm bảo thư mục cha của file luôn tồn tại."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_json(file_path: Union[str, Path], default: Any = None) -> Any:
    """
    Đọc dữ liệu từ file JSON an toàn.
    Nếu file không tồn tại hoặc format lỗi -> trả về giá trị default (vd: [] hoặc {}).
    """
    path = Path(file_path)
    if not path.is_file():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.warning(f"Lỗi đọc JSON từ {path}: {e}")
        return default


def write_json(file_path: Union[str, Path], data: Any, indent: int = 2) -> bool:
    """
    Ghi dữ liệu ra file JSON, tự động tạo thư mục cha nếu chưa có.
    """
    try:
        path = ensure_parent_dir(file_path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        return True
    except (IOError, OSError, TypeError) as e:
        logger.error(f"Lỗi ghi JSON vào {file_path}: {e}")
        return False


def delete_file(file_path: Union[str, Path]) -> bool:
    """
    Xóa file an toàn nếu file tồn tại.
    """
    try:
        path = Path(file_path)
        if path.is_file():
            path.unlink(missing_ok=True)
            return True
        return False
    except OSError as e:
        logger.warning(f"Lỗi xóa file {file_path}: {e}")
        return False

