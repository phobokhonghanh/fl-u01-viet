"""Unified Options Catalog and Schema Contract for HDR Engines.

Defines the options catalog contract for UI and CLI interfaces:
- Labels, control types, valid choices, defaults, and dependency conditions.
- Prevents UI from re-declaring private enums or mismatched defaults.
- Marks unverified options as unsupported without silently changing them.
"""
from __future__ import annotations

from typing import Any

from core.autoenhance.options import AUTOENHANCE_OPTIONS_SCHEMA, DEFAULT_AUTOENHANCE_OPTIONS
from core.fotello.constants import FOTELLO_OPTIONS_SCHEMA


def get_options_schema(engine: str) -> list[dict[str, Any]]:
    """Lấy danh mục tùy chọn (schema catalog) chính thức của engine."""
    eng_norm = str(engine).strip().lower()
    if eng_norm == "autoenhance":
        return list(AUTOENHANCE_OPTIONS_SCHEMA)
    elif eng_norm == "fotello":
        return list(FOTELLO_OPTIONS_SCHEMA)
    elif eng_norm == "autohdr":
        return [
            {
                "field": "processing_mode",
                "label": "Chế độ xử lý AutoHDR",
                "type": "select",
                "default": "balanced",
                "options": [
                    {"value": "balanced", "label": "HDR Cân bằng"},
                    {"value": "vibrant", "label": "HDR Rực rỡ"},
                ],
                "category": "common",
                "supported": False,
                "status_note": "Core đang tích hợp, chưa hỗ trợ thực thi",
            }
        ]
    return []


def get_default_options(engine: str) -> dict[str, Any]:
    """Lấy cấu hình tùy chọn mặc định của engine."""
    eng_norm = str(engine).strip().lower()
    schema = get_options_schema(eng_norm)
    return {
        item["field"]: item["default"]
        for item in schema
        if "default" in item
    }
