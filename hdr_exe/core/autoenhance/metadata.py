"""Autoenhance Order Metadata Manager.

Centralizes reading, writing, and atomic persistence of order metadata
(such as original image dimensions) in the configured metadata directory.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.autoenhance.constants import META_DIR


def save_order_metadata(order_id: str, metadata: dict[str, Any]) -> Path:
    """Lưu metadata đơn hàng (kích thước ảnh gốc, v.v.) vào file JSON nguyên tử."""
    META_DIR.mkdir(parents=True, exist_ok=True)
    target_p = META_DIR / f"{order_id}.json"
    temp_p = META_DIR / f".tmp_{order_id}.json"

    with open(temp_p, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    temp_p.replace(target_p)
    return target_p


def load_order_metadata(order_id: str) -> dict[str, Any]:
    """Đọc metadata của một đơn hàng."""
    target_p = META_DIR / f"{order_id}.json"
    if target_p.is_file():
        try:
            with open(target_p, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}



def delete_order_metadata(order_id: str) -> bool:
    """Xóa metadata của một đơn hàng khi không còn cần thiết."""
    target_p = META_DIR / f"{order_id}.json"
    if target_p.is_file():
        target_p.unlink(missing_ok=True)
        return True
    return False
